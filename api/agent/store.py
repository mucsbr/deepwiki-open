"""Application records and replayable events, separate from graph checkpoints."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ACTIVE = {"queued", "running"}
RESUMABLE = {"interrupted", "cancelled", "failed"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
                    repos TEXT NOT NULL, language TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_owner ON sessions(owner, updated_at);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                    request_id TEXT NOT NULL, message TEXT NOT NULL,
                    provider TEXT NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL,
                    answer TEXT NOT NULL DEFAULT '', error TEXT, repos TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(session_id, request_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs(session_id)
                    WHERE status IN ('queued', 'running');
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    kind TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_run ON events(run_id, seq);
                CREATE TABLE IF NOT EXISTS documents (
                    session_id TEXT NOT NULL REFERENCES sessions(id),
                    name TEXT NOT NULL, content TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(session_id, name)
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(runs)")}
            if "repos" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN repos TEXT")
                # Existing runs retain the source version that their session used.
                db.execute(
                    "UPDATE runs SET repos=(SELECT repos FROM sessions "
                    "WHERE sessions.id=runs.session_id)"
                )
            if "system_prompt" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN system_prompt TEXT NOT NULL DEFAULT ''")
            if "kind" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN kind TEXT NOT NULL DEFAULT 'analysis'")
            session_columns = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
            if "public_groups" not in session_columns:
                db.execute("ALTER TABLE sessions ADD COLUMN public_groups TEXT")
            if "system_prompt" not in session_columns:
                db.execute("ALTER TABLE sessions ADD COLUMN system_prompt TEXT NOT NULL DEFAULT ''")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def session_row(row):
        if row is None:
            return None
        result = dict(row)
        result["repos"] = json.loads(result["repos"])
        result["public_groups"] = json.loads(result.get("public_groups") or "[]")
        return result

    @staticmethod
    def run_row(row):
        if row is None:
            return None
        result = dict(row)
        result["repos"] = json.loads(result["repos"]) if result["repos"] else None
        return result

    def create_session(self, owner: str, repos: list[dict], language: str, *,
                       public_groups: list[str] | None = None, system_prompt: str = "") -> dict:
        sid, stamp = str(uuid4()), now()
        with self.connect() as db:
            db.execute(
                "INSERT INTO sessions (id,owner,title,repos,language,created_at,updated_at,public_groups,system_prompt) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sid, owner, "", json.dumps(repos), language, stamp, stamp,
                 json.dumps(public_groups) if public_groups is not None else None, system_prompt),
            )
        return self.get_session(sid, owner)

    def get_session(self, sid: str, owner: str) -> dict | None:
        with self.connect() as db:
            return self.session_row(
                db.execute(
                    "SELECT * FROM sessions WHERE id=? AND owner=?", (sid, owner)
                ).fetchone()
            )

    def set_system_prompt(self, sid: str, owner: str, prompt: str):
        with self.connect() as db:
            db.execute("UPDATE sessions SET system_prompt=? WHERE id=? AND owner=?", (prompt, sid, owner))

    def active_for_owner(self, owner: str) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT runs.id,runs.session_id,runs.status FROM runs JOIN sessions "
                "ON sessions.id=runs.session_id WHERE sessions.owner=? AND runs.status IN ('queued','running')",
                (owner,),
            )]

    def list_sessions(self, owner: str, limit: int = 50, offset: int = 0) -> list[dict]:
        with self.connect() as db:
            return [
                self.session_row(row)
                for row in db.execute(
                    "SELECT * FROM sessions WHERE owner=? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                    (owner, limit, offset),
                )
            ]

    def runs(self, sid: str) -> list[dict]:
        with self.connect() as db:
            return [
                self.run_row(row)
                for row in db.execute(
                    "SELECT * FROM runs WHERE session_id=? ORDER BY created_at", (sid,)
                )
            ]

    def get_run(self, rid: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
            return self.run_row(row)

    def find_request(self, sid: str, request_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM runs WHERE session_id=? AND request_id=?",
                (sid, request_id),
            ).fetchone()
            return self.run_row(row)

    def set_run_repos(self, rid: str, repos: list[dict]) -> None:
        snapshot, stamp = json.dumps(repos), now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE runs SET repos=?,updated_at=? WHERE id=?", (snapshot, stamp, rid))
            db.execute(
                "UPDATE sessions SET repos=?,updated_at=? "
                "WHERE id=(SELECT session_id FROM runs WHERE id=?)",
                (snapshot, stamp, rid),
            )

    def create_run(
        self, sid: str, request_id: str, message: str, provider: str, model: str,
        *, kind: str = "analysis",
        planned_scope: list[dict] | None = None,
    ) -> dict:
        rid, stamp = str(uuid4()), now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT * FROM runs WHERE session_id=? ORDER BY created_at DESC LIMIT 1",
                (sid,),
            ).fetchone()
            if previous and previous["status"] in ACTIVE:
                raise ValueError(
                    "Stop the active task before sending another message or compacting."
                )
            db.execute(
                "INSERT INTO runs (id,session_id,request_id,message,provider,model,status,created_at,updated_at,system_prompt,kind) "
                "VALUES (?,?,?,?,?,?,'queued',?,?,(SELECT system_prompt FROM sessions WHERE id=?),?)",
                (rid, sid, request_id, message, provider, model, stamp, stamp, sid, kind),
            )
            db.execute(
                "UPDATE sessions SET title=CASE WHEN title='' THEN ? ELSE title END, "
                "repos=COALESCE(?,repos),updated_at=? WHERE id=?",
                (message[:80], json.dumps(planned_scope) if planned_scope is not None else None, stamp, sid),
            )
        return self.get_run(rid)

    def can_resume(self, run: dict) -> bool:
        """Old analyses cannot replay after a newer user turn; compaction is not a turn."""
        with self.connect() as db:
            latest = db.execute(
                "SELECT id FROM runs WHERE session_id=? AND kind='analysis' "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1", (run["session_id"],),
            ).fetchone()
            return run.get("kind", "analysis") == "analysis" and latest is not None and latest["id"] == run["id"]

    def set_status(
        self,
        rid: str,
        status: str,
        *,
        answer: str | None = None,
        error: str | None = None,
    ):
        with self.connect() as db:
            db.execute(
                "UPDATE runs SET status=?,answer=COALESCE(?,answer),error=?,updated_at=? WHERE id=?",
                (status, answer, error, now(), rid),
            )
            db.execute(
                "UPDATE sessions SET updated_at=? WHERE id=(SELECT session_id FROM runs WHERE id=?)",
                (now(), rid),
            )
            db.execute(
                "INSERT INTO events(run_id,kind,data) VALUES (?,?,?)",
                (rid, "status", json.dumps({"status": status, "error": error})),
            )

    def recover(self):
        with self.connect() as db:
            ids = [
                row[0]
                for row in db.execute(
                    "SELECT id FROM runs WHERE status IN ('queued','running')"
                )
            ]
        for rid in ids:
            self.set_status(
                rid, "interrupted", error="Server restarted. Resume to continue."
            )

    def set_model(self, rid: str, provider: str, model: str):
        with self.connect() as db:
            db.execute(
                "UPDATE runs SET provider=?,model=? WHERE id=?", (provider, model, rid)
            )

    def event(self, rid: str, kind: str, data: dict) -> int:
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO events(run_id,kind,data) VALUES (?,?,?)",
                (rid, kind, json.dumps(data, ensure_ascii=False)),
            )
            return cursor.lastrowid

    def events(self, rid: str, after: int = 0, limit: int = 200) -> list[dict]:
        with self.connect() as db:
            return [
                {"id": row["seq"], "type": row["kind"], "data": json.loads(row["data"])}
                for row in db.execute(
                    "SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?",
                    (rid, after, limit),
                )
            ]

    def activity(self, rid: str) -> dict:
        """Replay all rounds, compacting token snapshots, not completed messages.

        Keep each message's first position and newest body; cursor still covers
        every persisted event so attaching SSE cannot duplicate the replay.
        """
        cursor, legacy_round = 0, 0
        events, drafts = [], {}
        while batch := self.events(rid, cursor):
            for event in batch:
                cursor = event["id"]
                if event["type"] == "draft":
                    key = event["data"].get("id") or f"legacy-{legacy_round}"
                    if key in drafts:
                        events[drafts[key]]["data"] = event["data"]
                        continue
                    drafts[key] = len(events)
                elif event["type"] in {"tool_start", "status"}:
                    legacy_round += 1
                events.append(event)
        return {"events": events, "cursor": cursor}

    def context_usage(self, sid: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT e.data FROM events e JOIN runs r ON r.id=e.run_id "
                "WHERE r.session_id=? AND e.kind='context' ORDER BY e.seq DESC LIMIT 1",
                (sid,),
            ).fetchone()
            return json.loads(row["data"]) if row else None

    def save_document(self, sid: str, name: str, content: str):
        with self.connect() as db:
            db.execute(
                "INSERT INTO documents VALUES (?,?,?,?) ON CONFLICT(session_id,name) "
                "DO UPDATE SET content=excluded.content,updated_at=excluded.updated_at",
                (sid, name, content, now()),
            )

    def documents(self, sid: str) -> list[dict]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT name,content,updated_at FROM documents WHERE session_id=? ORDER BY name",
                    (sid,),
                )
            ]
