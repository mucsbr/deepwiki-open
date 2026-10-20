"""Durable, daily Ask-only refresh queue; indexing never blocks an Ask answer.

Workers are subprocesses so shutdown can stop the actual embedding work, not
just cancel an asyncio waiter while its executor thread keeps writing.
No visitor credentials are persisted: workers use the platform service token.
"""

import asyncio
from contextlib import contextmanager
import fcntl
import logging
import os
from pathlib import Path
import signal
import sqlite3
import sys
import time
from uuid import uuid4

logger = logging.getLogger(__name__)
DAILY_SECONDS = 24 * 60 * 60


class AskUpdateQueue:
    def __init__(self, data_root: Path, *, runner=None, clock=time.time):
        self.data_root = data_root
        self.path = data_root / "metadata" / "ask_updates.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.runner = runner or self._subprocess
        self.task = None
        self._worker_lock = None
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS updates (
                project TEXT PRIMARY KEY, job_id TEXT NOT NULL,
                status TEXT NOT NULL, requested_at REAL NOT NULL,
                finished_at REAL, error TEXT
            )""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def request(self, projects: list[str]) -> dict[str, str]:
        """Atomically coalesce all users and persist the rolling 24-hour gate."""
        result, stamp = {}, self.clock()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for project in dict.fromkeys(projects):
                row = db.execute("SELECT * FROM updates WHERE project=?", (project,)).fetchone()
                if row and row["status"] in {"queued", "running"}:
                    result[project] = "background"
                elif row and stamp - row["requested_at"] < DAILY_SECONDS:
                    result[project] = "cached"
                else:
                    db.execute("""INSERT INTO updates(project,job_id,status,requested_at)
                        VALUES (?,?,'queued',?) ON CONFLICT(project) DO UPDATE SET
                        job_id=excluded.job_id,status='queued',requested_at=excluded.requested_at,
                        finished_at=NULL,error=NULL""", (project, uuid4().hex, stamp))
                    result[project] = "background"
        return result

    def get(self, project):
        with self.connect() as db:
            row = db.execute("SELECT * FROM updates WHERE project=?", (project,)).fetchone()
            return dict(row) if row else None

    def finish(self, project, job_id, *, error=None):
        with self.connect() as db:
            db.execute("""UPDATE updates SET status=?,finished_at=?,error=?
                WHERE project=? AND job_id=?""",
                       ("failed" if error else "completed", self.clock(), error, project, job_id))

    def pending(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM updates WHERE status='queued' ORDER BY requested_at,project"
            )]

    async def start(self):
        if self.task:
            return
        self._worker_lock = self.path.with_suffix(".worker.lock").open("a+")
        try:
            fcntl.flock(self._worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._worker_lock.close()
            self._worker_lock = None
            raise RuntimeError("Ask updates require one worker per repository data directory.")
        with self.connect() as db:
            # Interrupted jobs resume regardless of the daily throttle: the same
            # job is being recovered, not a fresh Ask-triggered update.
            db.execute("UPDATE updates SET status='queued' WHERE status='running'")
        self.task = asyncio.create_task(self._serve(), name="ask-repository-updates")

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        if self._worker_lock:
            self._worker_lock.close()
            self._worker_lock = None

    async def _serve(self):
        # A dedicated queue does not occupy an analysis slot. Two repositories
        # at a time bound provider load independently of the number of Ask users.
        slots = asyncio.Semaphore(2)

        async def execute(job):
            async with slots:
                with self.connect() as db:
                    claimed = db.execute("""UPDATE updates SET status='running'
                        WHERE project=? AND job_id=? AND status='queued'""",
                                         (job["project"], job["job_id"])).rowcount
                if not claimed:
                    return
                try:
                    await self.runner(job)
                except asyncio.CancelledError:
                    with self.connect() as db:
                        db.execute("""UPDATE updates SET status='queued'
                            WHERE project=? AND job_id=? AND status='running'""",
                                   (job["project"], job["job_id"]))
                    raise
                except Exception as exc:
                    logger.warning("Ask repository update failed for %s (%s)", job["project"], type(exc).__name__)
                    self.finish(job["project"], job["job_id"], error="Background update failed; existing source remains available.")

        while True:
            jobs = self.pending()
            if jobs:
                tasks = [asyncio.create_task(execute(job)) for job in jobs]
                try:
                    await asyncio.gather(*tasks)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
            else:
                await asyncio.sleep(0.5)

    async def _subprocess(self, job):
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "api.ask_updates", str(self.data_root),
            job["project"], job["job_id"],
            start_new_session=True,
        )
        try:
            code = await process.wait()
            if code:
                raise RuntimeError("Repository update worker exited unsuccessfully.")
        except asyncio.CancelledError:
            # A killed process releases the shared writer lock and cannot leave
            # an orphan embedding thread publishing after service shutdown.
            if process.returncode is None:
                self._signal_group(process, signal.SIGTERM)
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except TimeoutError:
                    self._signal_group(process, signal.SIGKILL)
                    await process.wait()
            raise

    @staticmethod
    def _signal_group(process, sig):
        try:
            # Include git/fetch subprocesses, which must not keep changing the
            # clone after their owning worker has released its repository lock.
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass


async def update_repository(data_root: Path, project: str, job_id: str):
    from api.agent.access import Access
    from api.agent.repositories import project_path, resolve_repository
    from api.batch_indexer import BatchIndexer
    from api.config import GITLAB_SERVICE_TOKEN, GITLAB_URL
    from api.index_state import index_problem
    from api.metadata_store import get_project_metadata
    from api.repository_lock import repository_write_lock

    if project_path(project, GITLAB_URL) != project:
        raise ValueError("Invalid queued repository.")
    queue = AskUpdateQueue(data_root)
    async with repository_write_lock(data_root, project):
        job = queue.get(project)
        if not job or job["job_id"] != job_id or job["status"] not in {"queued", "running"}:
            return
        try:
            # All GitLab/network work happens here, not in the analysis request.
            remote = (await Access(data_root).remote_versions([project], {"public_ask": True}))[project]
            try:
                repo = await asyncio.to_thread(resolve_repository, project, GITLAB_URL, data_root)
                index = data_root / "databases" / f"{repo.root.name}.pkl"
                problem = await asyncio.to_thread(index_problem, index, remote["commit"], check_dependency_filter=True)
                changed = repo.commit != remote["commit"] or problem is not None
            except ValueError:
                changed = True
            # Legacy timeouts could leave an already-published PKL permanently
            # marked indexing. Reconcile it through the normal validated path.
            metadata = get_project_metadata(project) or {}
            if changed or metadata.get("status") not in {"indexed", "partial"}:
                if not GITLAB_SERVICE_TOKEN:
                    raise ValueError("The GitLab service token is not configured.")
                indexer = BatchIndexer(GITLAB_URL, GITLAB_SERVICE_TOKEN, [])
                success = await indexer._reindex_project_locked({
                    "path_with_namespace": project,
                    **{key: value for key, value in remote.items() if key != "commit"},
                })
                if not success:
                    raise RuntimeError("Repository indexing failed.")
            queue.finish(project, job_id)
        except Exception as exc:
            logger.warning("Ask repository update failed for %s (%s)", project, type(exc).__name__)
            queue.finish(project, job_id, error="Background update failed; existing source remains available.")


if __name__ == "__main__":
    asyncio.run(update_repository(Path(sys.argv[1]), sys.argv[2], sys.argv[3]))
