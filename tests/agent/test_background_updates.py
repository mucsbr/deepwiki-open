"""Offline refresh, concurrency, snapshot publication and restart contracts."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import sys
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest
from adalflow.core.db import LocalDB
from fastapi import HTTPException
from langchain_core.messages import AIMessage

from api.agent.access import Access
from api.agent.repositories import IndexSearch, SourceReader
from api.agent.runtime import Runtime
from api.agent.snapshots import available_repository
from api.ask_updates import AskUpdateQueue, DAILY_SECONDS, update_repository
from api.repository_lock import repository_write_lock
from tests.agent.test_agent import repository, run_git, TestModel
from tests.agent.test_index_migration import migration


def test_daily_gate_is_persistent_and_shared_by_concurrent_visitors(tmp_path):
    clock = [1000.0]
    first = AskUpdateQueue(tmp_path, clock=lambda: clock[0])
    with ThreadPoolExecutor(max_workers=8) as executor:
        phases = list(executor.map(lambda _: AskUpdateQueue(tmp_path, clock=lambda: clock[0]).request(["group/demo"]), range(20)))
    job = first.get("group/demo")
    assert all(phase == {"group/demo": "background"} for phase in phases)
    assert len(first.pending()) == 1
    first.finish("group/demo", job["job_id"])
    clock[0] += DAILY_SECONDS - 1
    assert AskUpdateQueue(tmp_path, clock=lambda: clock[0]).request(["group/demo"])["group/demo"] == "cached"
    assert first.get("group/demo")["job_id"] == job["job_id"]
    clock[0] += 1
    assert first.request(["group/demo"])["group/demo"] == "background"
    assert first.get("group/demo")["job_id"] != job["job_id"]


def test_unfinished_and_failed_jobs_do_not_cause_update_storms(tmp_path):
    clock = [1000.0]
    queue = AskUpdateQueue(tmp_path, clock=lambda: clock[0])
    queue.request(["group/demo"])
    job = queue.get("group/demo")
    clock[0] += DAILY_SECONDS * 2
    assert queue.request(["group/demo"])["group/demo"] == "background"
    assert queue.get("group/demo")["job_id"] == job["job_id"]
    queue.finish("group/demo", job["job_id"], error="provider unavailable")
    queue.request(["group/demo"])
    failed = queue.get("group/demo")
    queue.finish("group/demo", failed["job_id"], error="provider unavailable")
    assert queue.request(["group/demo"])["group/demo"] == "cached"


def test_index_publication_preserves_current_answer_and_advances_next_question(migration):
    m = migration
    old = available_repository(m.repo.project, "https://gitlab.example", m.root)
    snapshot = m.path.parent / "ask-snapshots" / m.repo.root.name / old.index_snapshot
    before = snapshot.read_bytes()
    # Pull can move HEAD before the slow embedding phase has completed.
    (m.repo.root / "order.py").write_text("def submit(): return 'new source'\n")
    run_git(m.repo.root, "add", ".")
    run_git(m.repo.root, "commit", "-qm", "new source")
    revision = run_git(m.repo.root, "rev-parse", "HEAD")
    during = available_repository(m.repo.project, "https://gitlab.example", m.root)
    assert during.commit == old.commit
    assert "submitted" in SourceReader([old]).read(old.project, "order.py")["content"]
    database = LocalDB.load_state(str(m.path))
    database.source_revision = revision
    temporary = m.path.with_suffix(".new")
    database.save_state(str(temporary))
    os.replace(temporary, m.path)
    assert snapshot.read_bytes() == before
    current = available_repository(m.repo.project, "https://gitlab.example", m.root)
    assert current.commit == revision
    assert current.index_snapshot != old.index_snapshot
    assert "new source" in SourceReader([current]).read(current.project, "order.py")["content"]
    restored = available_repository(m.repo.project, "https://gitlab.example", m.root,
                                    commit=old.commit, snapshot=old.index_snapshot)
    assert restored == old
    with pytest.raises(ValueError, match="invalid index snapshot"):
        available_repository(m.repo.project, "https://gitlab.example", m.root, snapshot="../../secret")


def test_semantic_search_uses_its_saved_snapshot_after_background_publication(migration, monkeypatch):
    m = migration
    old = available_repository(m.repo.project, "https://gitlab.example", m.root)
    database = LocalDB.load_state(str(m.path))
    database.transformed_items["split_and_embed"][0].text = "newly published index"
    temporary = m.path.with_suffix(".new")
    database.save_state(str(temporary))
    os.replace(temporary, m.path)
    latest = available_repository(m.repo.project, "https://gitlab.example", m.root)
    monkeypatch.setattr("api.tools.embedder.get_embedder", lambda **_kwargs: lambda _queries:
                        SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2])], error=None))
    first = IndexSearch(SourceReader([old]), m.root).search("submit")
    second = IndexSearch(SourceReader([latest]), m.root).search("submit")
    assert first["matches"][0]["text"] == "submit"
    assert second["matches"][0]["text"] == "newly published index"


@pytest.mark.parametrize("status", ["indexing", "error"])
def test_existing_index_remains_selectable_while_updates_run_or_fail(migration, monkeypatch, status):
    m = migration
    access = Access(m.root)

    async def allowed(*_args):
        pass

    monkeypatch.setattr(access, "check", allowed)
    monkeypatch.setattr("api.config.GITLAB_URL", "https://gitlab.example")
    monkeypatch.setattr("api.metadata_store.get_all_indexed_projects", lambda: {m.repo.project: {"status": status}})
    selected = asyncio.run(access.create_scope([m.repo.project], {}))
    assert selected[0]["commit"] == m.repo.commit
    assert selected[0]["index_snapshot"]
    m.path.write_bytes(b"corrupt index")
    with pytest.raises(HTTPException) as error:
        asyncio.run(access.create_scope([m.repo.project], {}))
    assert "no previously published index" in error.value.detail


def test_background_update_cannot_delay_or_cancel_current_answer(repository, monkeypatch):
    async def scenario():
        root, repo = repository
        access = Access(root)
        started, release = asyncio.Event(), asyncio.Event()

        async def allowed(*_args):
            pass

        async def slow_job(job):
            started.set()
            await release.wait()
            access.updates.finish(job["project"], job["job_id"])

        access.updates.runner = slow_job
        monkeypatch.setattr(access, "check", allowed)
        monkeypatch.setattr("api.config.GITLAB_URL", "https://gitlab.example")
        monkeypatch.setattr("api.config.GITLAB_SERVICE_TOKEN", "fixture-service-token")
        rt = Runtime(root / "background-answer", root, access=access)
        await rt.start()
        try:
            session = rt.store.create_session("1", [repo.public()], "en")
            run = rt.store.create_run(session["id"], str(uuid4()), "Locate submit", "fixture", "fixture")
            rt.launch(run, session, None, {}, TestModel(responses=[AIMessage(content="Existing source is available.")]))
            await asyncio.wait_for(rt.tasks[run["id"]], 5)
            await asyncio.wait_for(started.wait(), 2)
            result = rt.store.get_run(run["id"])
            assert result["status"] == "completed", result
            assert access.updates.get(repo.project)["status"] == "running"
            await rt.cancel(run["id"])
            assert access.updates.get(repo.project)["status"] == "running"
        finally:
            release.set()
            await rt.close()
    asyncio.run(scenario())


def test_shutdown_requeues_update_without_losing_daily_gate_or_ask_history(repository):
    async def scenario():
        root, repo = repository
        started = asyncio.Event()
        first = Access(root)

        async def unfinished(_job):
            started.set()
            await asyncio.Event().wait()

        first.updates.runner = unfinished
        first.updates.request([repo.project])
        job = first.updates.get(repo.project)
        rt = Runtime(root / "restart", root, access=first)
        await rt.start()
        session = rt.store.create_session("1", [repo.public()], "en")
        run = rt.store.create_run(session["id"], str(uuid4()), "preserve this question", "fixture", "fixture")
        await asyncio.wait_for(started.wait(), 2)
        await rt.close()
        assert first.updates.get(repo.project)["status"] == "queued"
        assert rt.store.get_run(run["id"])["status"] == "interrupted"
        second = Access(root)
        recovered = asyncio.Event()

        async def complete(saved):
            assert saved["job_id"] == job["job_id"]
            second.updates.finish(saved["project"], saved["job_id"])
            recovered.set()

        second.updates.runner = complete
        reopened = Runtime(root / "restart", root, access=second)
        await reopened.start()
        try:
            await asyncio.wait_for(recovered.wait(), 2)
            assert reopened.store.get_run(run["id"])["message"] == "preserve this question"
            assert reopened.store.get_run(run["id"])["status"] == "interrupted"
            assert second.updates.request([repo.project])[repo.project] == "cached"
            assert not reopened.tasks  # never auto-resume user analysis
        finally:
            await reopened.close()
    asyncio.run(scenario())


def test_admin_and_ask_share_one_writer_and_admin_ignores_daily_gate(migration, monkeypatch):
    async def scenario():
        m = migration
        from api.batch_indexer import BatchIndexer

        queue = AskUpdateQueue(m.root)
        queue.request([m.repo.project])
        job = queue.get(m.repo.project)
        queue.finish(m.repo.project, job["job_id"])
        assert queue.request([m.repo.project])[m.repo.project] == "cached"
        active, maximum, completed = 0, 0, []

        async def indexing(self, project, *_args):
            nonlocal active, maximum
            active += 1
            maximum = max(active, maximum)
            await asyncio.sleep(0.03)
            completed.append(project["path_with_namespace"])
            active -= 1
            return True

        monkeypatch.setattr(BatchIndexer, "_reindex_project_locked", indexing)
        async with repository_write_lock(m.root, m.repo.project):
            tasks = [asyncio.create_task(m.indexer.reindex_project(m.project)) for _ in range(3)]
            await asyncio.sleep(0.02)
            assert not completed
        assert all(await asyncio.gather(*tasks))
        assert maximum == 1
        assert len(completed) == 3
        assert queue.get(m.repo.project)["job_id"] == job["job_id"]
    asyncio.run(scenario())


def test_cancelled_admin_writer_retains_lock_until_real_work_finishes(migration, monkeypatch):
    async def scenario():
        m = migration
        entered, release, other_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def indexing(*_args):
            entered.set()
            await release.wait()
            return True

        monkeypatch.setattr(m.indexer, "_reindex_project_locked", indexing)
        first = asyncio.create_task(m.indexer.reindex_project(m.project))
        await entered.wait()
        first.cancel()

        async def contender():
            async with repository_write_lock(m.root, m.repo.project):
                other_entered.set()

        second = asyncio.create_task(contender())
        await asyncio.sleep(0.02)
        assert not other_entered.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await asyncio.wait_for(second, 2)
        assert other_entered.is_set()
    asyncio.run(scenario())


def test_executor_cancellation_cannot_release_lock_or_skip_metadata_publication(migration, monkeypatch):
    async def scenario():
        from api.data_pipeline import DatabaseManager
        from api.metadata_store import get_project_metadata

        m = migration
        entered, release = threading.Event(), threading.Event()
        original = DatabaseManager.prepare_database

        def slow(manager, *args, **kwargs):
            entered.set()
            assert release.wait(timeout=5)
            return original(manager, *args, **kwargs)

        monkeypatch.setattr(DatabaseManager, "prepare_database", slow)
        task = asyncio.create_task(m.indexer.reindex_project(m.project))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0.01)
            task.cancel()  # repeated shutdown signals must not detach the writer
            async def next_writer():
                async with repository_write_lock(m.root, m.repo.project):
                    assert get_project_metadata(m.repo.project)["status"] == "indexed"
            contender = asyncio.create_task(next_writer())
            await asyncio.sleep(0.02)
            assert not contender.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(contender, 2)
        finally:
            release.set()
    asyncio.run(scenario())


def test_worker_checks_current_index_and_recovers_stale_metadata(migration, monkeypatch):
    from api import metadata_store
    m = migration
    queue = AskUpdateQueue(m.root)
    queue.request([m.repo.project])
    job = queue.get(m.repo.project)
    checked = []

    async def versions(self, projects, principal):
        assert principal == {"public_ask": True}
        checked.extend(projects)
        return {m.repo.project: {"commit": m.repo.commit, **m.project}}

    monkeypatch.setattr(Access, "remote_versions", versions)
    monkeypatch.setattr("api.config.GITLAB_URL", "https://gitlab.example")
    monkeypatch.setattr("api.config.GITLAB_SERVICE_TOKEN", "fixture-service-token")
    asyncio.run(update_repository(m.root, m.repo.project, job["job_id"]))
    assert checked == [m.repo.project]
    assert queue.get(m.repo.project)["status"] == "completed"
    assert m.state["embeddings"] == 0
    # A duplicate recovered child must not repeat even the GitLab check.
    asyncio.run(update_repository(m.root, m.repo.project, job["job_id"]))
    assert checked == [m.repo.project]

    queue.request(["group/other"])  # the day gate belongs to each repository
    # Recover an interrupted pre-upgrade metadata write without embedding again.
    metadata_store.set_project_metadata(m.repo.project, 1, "unchanged", m.repo.project, status="indexing")
    recovered = AskUpdateQueue(m.root, clock=lambda: job["requested_at"] + DAILY_SECONDS)
    recovered.request([m.repo.project])
    next_job = recovered.get(m.repo.project)
    asyncio.run(update_repository(m.root, m.repo.project, next_job["job_id"]))
    assert metadata_store.get_project_metadata(m.repo.project)["status"] == "indexed"
    assert m.state["embeddings"] == 0
    assert queue.get(m.repo.project)["status"] == "completed"
    assert checked == [m.repo.project, m.repo.project]


def test_shutdown_terminates_the_embedding_subprocess(tmp_path, monkeypatch):
    async def scenario():
        queue = AskUpdateQueue(tmp_path)
        entered = asyncio.Event()

        class Process:
            pid = 12345
            returncode = None
            terminated = False

            async def wait(self):
                entered.set()
                if self.returncode is None:
                    await asyncio.Event().wait()
                return self.returncode

        child = Process()
        async def spawn(*args, **kwargs):
            assert args[:3] == (sys.executable, "-m", "api.ask_updates")
            assert kwargs["start_new_session"]
            return child

        def terminate_group(pid, sig):
            assert pid == child.pid
            child.terminated = True
            child.returncode = -sig

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(os, "killpg", terminate_group)
        task = asyncio.create_task(queue._subprocess({"project": "group/demo", "job_id": "fixture"}))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert child.terminated
    asyncio.run(scenario())


def test_gitlab_update_failure_preserves_code_and_does_not_bypass_daily_gate(migration, monkeypatch):
    m = migration
    queue = AskUpdateQueue(m.root)
    queue.request([m.repo.project])
    job = queue.get(m.repo.project)
    before = m.path.read_bytes()

    async def unavailable(*_args):
        raise RuntimeError("private-provider-secret")

    monkeypatch.setattr(Access, "remote_versions", unavailable)
    monkeypatch.setattr("api.config.GITLAB_URL", "https://gitlab.example")
    asyncio.run(update_repository(m.root, m.repo.project, job["job_id"]))
    failed = queue.get(m.repo.project)
    assert failed["status"] == "failed"
    assert "private-provider-secret" not in failed["error"]
    assert m.path.read_bytes() == before
    assert queue.request([m.repo.project])[m.repo.project] == "cached"
    repo = available_repository(m.repo.project, "https://gitlab.example", m.root)
    assert "submitted" in SourceReader([repo]).read(repo.project, "order.py")["content"]


def test_update_queue_failure_does_not_block_reading_current_code(repository, monkeypatch):
    root, repo = repository
    access = Access(root)

    async def allowed(*_args):
        pass

    def unavailable(*_args):
        raise RuntimeError("private-storage-detail")

    monkeypatch.setattr(access, "check", allowed)
    monkeypatch.setattr(access.updates, "request", unavailable)
    monkeypatch.setattr("api.config.GITLAB_URL", "https://gitlab.example")
    monkeypatch.setattr("api.config.GITLAB_SERVICE_TOKEN", "fixture-service-token")
    progress = []
    readers = asyncio.run(access.refresh_readers([repo.public()], {}, lambda *args: progress.append(args)))
    assert readers[0].commit == repo.commit
    assert progress == [(repo.project, "local")]


def test_real_worker_entrypoint_does_not_replay_completed_jobs(tmp_path, monkeypatch):
    # Exercise the actual subprocess/module entrypoint, with no valid remote
    # credentials. A completed durable job must exit before any GitLab request.
    monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.invalid")
    monkeypatch.setenv("GITLAB_SERVICE_TOKEN", "")
    queue = AskUpdateQueue(tmp_path)
    queue.request(["group/demo"])
    job = queue.get("group/demo")
    queue.finish(job["project"], job["job_id"])
    asyncio.run(asyncio.wait_for(queue._subprocess(job), timeout=15))
    assert queue.get("group/demo")["status"] == "completed"
