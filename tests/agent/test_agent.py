"""Offline contract tests. These do not measure business-flow reconstruction quality."""

import asyncio
import json
import subprocess
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage

from api.agent.repositories import SourceReader, project_path, resolve_repository
from api.agent.router import current_user, router
from api.agent.runtime import Runtime
from api.agent.store import Store


def run_git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args]).decode().strip()


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repos" / "group_demo"
    root.mkdir(parents=True)
    run_git(root, "init", "-q")
    run_git(root, "config", "user.email", "agent-test@example.invalid")
    run_git(root, "config", "user.name", "Agent tests")
    run_git(root, "remote", "add", "origin", "https://gitlab.example/group/demo.git")
    (root / "order.py").write_text(
        "def submit():\n    state = 'submitted'\n    return state\n"
    )
    (root / ".env").write_text("SECRET=do-not-return\n")
    (root / "link.py").symlink_to("order.py")
    run_git(root, "add", ".")
    run_git(root, "commit", "-qm", "fixture")
    return tmp_path, resolve_repository(
        "group/demo", "https://gitlab.example", tmp_path
    )


def test_source_revision_scope_and_paths(repository):
    _root, repo = repository
    reader = SourceReader([repo])
    result = reader.read("group/demo", "order.py", 1, 3)
    assert "2:     state = 'submitted'" in result["content"]
    assert repo.commit in result["url"]
    (repo.root / "order.py").write_text("changed after conversation started\n")
    run_git(repo.root, "add", ".")
    run_git(repo.root, "commit", "-qm", "changed")
    assert reader.read("group/demo", "order.py")["content"] == result["content"]
    assert reader.search("submitted")["matches"][0]["line"] == 2
    assert reader.search("do-not-return")["matches"] == []
    for path in ("../order.py", "/etc/passwd", ".env", "link.py", "missing.py"):
        with pytest.raises(ValueError):
            reader.read("group/demo", path)
    with pytest.raises(ValueError):
        reader.read("private/repo", "order.py")


def test_project_validation_and_collision(repository):
    root, repo = repository
    assert (
        project_path("https://gitlab.example/group/demo.git", "https://gitlab.example")
        == "group/demo"
    )
    for value in (
        "https://evil.example/group/demo",
        "group/../demo",
        "group%2F..%2Fdemo",
    ):
        with pytest.raises(ValueError):
            project_path(value, "https://gitlab.example")
    run_git(
        repo.root, "remote", "set-url", "origin", "https://gitlab.example/private/repo"
    )
    with pytest.raises(ValueError, match="collision"):
        resolve_repository("group/demo", "https://gitlab.example", root)


def test_create_scope_names_all_repositories_without_commits(tmp_path, monkeypatch):
    from api.agent.access import Access

    names = ["group/empty-one", "group/empty-two"]
    for name in names:
        root = tmp_path / "repos" / name.replace("/", "_")
        root.mkdir(parents=True)
        run_git(root, "init", "-q")
        run_git(root, "remote", "add", "origin", f"https://gitlab.example/{name}.git")

    async def allowed(*_args):
        return True

    monkeypatch.setattr("api.config.GITLAB_URL", "https://gitlab.example")
    monkeypatch.setattr("api.gitlab_permission.check_repo_access", allowed)
    monkeypatch.setattr(
        "api.metadata_store.get_all_indexed_projects",
        lambda: {name: {"status": "indexed"} for name in names},
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            Access(tmp_path).create_scope(
                names,
                {"gitlab_user_id": 1, "gitlab_access_token": "fixture-token"},
            )
        )
    assert error.value.status_code == 409
    assert all(name in error.value.detail for name in names)
    assert error.value.detail.count("no readable HEAD commit") == 2


def test_refresh_checks_remote_and_reindexes_only_when_changed(repository, monkeypatch):
    from dataclasses import replace

    from api.agent.access import Access
    from api.batch_indexer import BatchIndexer

    root, repo = repository
    index_path = root / "databases" / "group_demo.pkl"
    index_path.parent.mkdir()
    index_path.write_bytes(b"existing index")
    state = {"local": repo.commit, "remote": repo.commit, "reindexes": 0}
    access = Access(root)

    async def allowed(_projects, _user):
        return None

    async def versions(_projects, _user):
        return {repo.project: {
            "commit": state["remote"],
            "id": 1,
            "last_activity_at": "now",
            "http_url_to_repo": repo.url + ".git",
        }}

    def resolve(_project, _host, _root):
        return replace(repo, commit=state["local"])

    async def reindex(_self, project, **_kwargs):
        assert project["path_with_namespace"] == repo.project
        assert _self.service_token == "fixture-service-token"
        state["reindexes"] += 1
        state["local"] = state["remote"]
        return True

    monkeypatch.setattr("api.config.GITLAB_URL", "https://gitlab.example")
    monkeypatch.setattr("api.config.GITLAB_SERVICE_TOKEN", "fixture-service-token")
    monkeypatch.setattr(access, "check", allowed)
    monkeypatch.setattr(access, "remote_versions", versions)
    monkeypatch.setattr("api.agent.access.resolve_repository", resolve)
    monkeypatch.setattr(BatchIndexer, "reindex_project", reindex)
    scope = [repo.public()]
    user = {"gitlab_access_token": "fixture-token"}
    assert asyncio.run(access.refresh_readers(scope, user))[0].commit == repo.commit
    assert state["reindexes"] == 0

    state["remote"] = "b" * 40
    refreshed = asyncio.run(access.refresh_readers(scope, user))
    assert refreshed[0].commit == state["remote"]
    assert state["reindexes"] == 1


def test_remote_revision_check_uses_gitlab_branch_and_user_token(tmp_path, monkeypatch):
    import httpx

    from api.agent.access import Access

    requests = []
    commit = "c" * 40

    def respond(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer fixture-token"
        if "/repository/branches/" in str(request.url):
            return httpx.Response(200, json={"commit": {"id": commit}})
        return httpx.Response(200, json={
            "id": 17,
            "default_branch": "main",
            "last_activity_at": "2026-09-24T00:00:00Z",
            "http_url_to_repo": "https://gitlab.example/group/demo.git",
        })

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(
        "api.agent.access.httpx.AsyncClient",
        lambda **kwargs: original_client(transport=transport, **kwargs),
    )
    monkeypatch.setattr("api.config.GITLAB_URL", "https://gitlab.example")
    versions = asyncio.run(Access(tmp_path).remote_versions(
        ["group/demo"], {"gitlab_access_token": "fixture-token"}
    ))
    assert versions["group/demo"]["commit"] == commit
    assert len(requests) == 2
    assert "/repository/branches/main" in str(requests[1].url)


def test_gitlab_pull_does_not_put_token_in_origin(monkeypatch):
    from types import SimpleNamespace

    from api.data_pipeline import _git_pull

    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout=b"Already up to date.\n")

    monkeypatch.setattr("api.data_pipeline.subprocess.run", fake_run)
    changed = _git_pull(
        "/unused/repo", "https://gitlab.example/group/demo.git", "gitlab", "fixture-token"
    )
    assert changed is False
    assert len(calls) == 1
    assert calls[0][0] == ["git", "pull"]
    assert calls[0][1]["env"]["GIT_CONFIG_KEY_0"].endswith(".extraheader")
    assert "fixture-token" not in str(calls[0][0])


def test_download_repo_propagates_pull_failure(tmp_path, monkeypatch):
    from api.data_pipeline import download_repo

    clone = tmp_path / "existing"
    clone.mkdir()
    (clone / ".git").mkdir()

    def failed_pull(*_args):
        raise subprocess.CalledProcessError(
            1, ["git", "pull"], stderr=b"remote repository unavailable"
        )

    monkeypatch.setattr("api.data_pipeline._git_pull", failed_pull)
    with pytest.raises(ValueError, match="Git pull failed"):
        download_repo(
            "https://gitlab.example/group/demo.git",
            str(clone),
            "gitlab",
            "fixture-token",
        )


def test_reindex_does_not_mark_empty_clone_indexed(tmp_path, monkeypatch):
    from api.batch_indexer import BatchIndexer

    name = "group/empty"
    root = tmp_path / "repos" / "group_empty"
    root.mkdir(parents=True)
    run_git(root, "init", "-q")
    run_git(root, "remote", "add", "origin", "https://gitlab.example/group/empty.git")
    monkeypatch.setattr(
        "adalflow.utils.get_adalflow_default_root_path", lambda: str(tmp_path)
    )
    monkeypatch.setattr(
        "api.data_pipeline.DatabaseManager.prepare_database",
        lambda *_args, **_kwargs: None,
    )
    statuses = []
    monkeypatch.setattr(
        "api.metadata_store.set_project_metadata",
        lambda **kwargs: statuses.append(kwargs["status"]),
    )

    project = {
        "path_with_namespace": name,
        "id": 1,
        "last_activity_at": "",
        "http_url_to_repo": "https://gitlab.example/group/empty.git",
    }
    result = asyncio.run(
        BatchIndexer("https://gitlab.example", "fixture-token", []).reindex_project(
            project
        )
    )
    assert result is False
    assert statuses == ["indexing", "error"]


def test_failed_pull_cannot_be_marked_indexed(tmp_path, monkeypatch):
    from api.batch_indexer import BatchIndexer

    def failed_pull(*_args, **_kwargs):
        raise ValueError("Git pull failed: repository unavailable")

    monkeypatch.setattr(
        "api.data_pipeline.DatabaseManager.prepare_database", failed_pull
    )
    statuses = []
    monkeypatch.setattr(
        "api.metadata_store.set_project_metadata",
        lambda **kwargs: statuses.append(kwargs["status"]),
    )
    project = {
        "path_with_namespace": "group/demo",
        "id": 1,
        "last_activity_at": "now",
        "http_url_to_repo": "https://gitlab.example/group/demo.git",
    }
    result = asyncio.run(
        BatchIndexer("https://gitlab.example", "fixture-token", []).reindex_project(
            project
        )
    )
    assert result is False
    assert statuses == ["indexing", "error"]


def test_indexed_status_requires_commit_vectors_and_index_file(repository, monkeypatch):
    from types import SimpleNamespace

    from api.batch_indexer import BatchIndexer

    root, repo = repository
    index_path = root / "databases" / "group_demo.pkl"
    index_path.parent.mkdir()

    def prepared(*_args, **_kwargs):
        index_path.write_bytes(b"fixture index")
        return [SimpleNamespace(vector=[0.1, 0.2])]

    monkeypatch.setattr(
        "adalflow.utils.get_adalflow_default_root_path", lambda: str(root)
    )
    monkeypatch.setattr(
        "api.data_pipeline.DatabaseManager.prepare_database", prepared
    )
    statuses = []
    monkeypatch.setattr(
        "api.metadata_store.set_project_metadata",
        lambda **kwargs: statuses.append(kwargs["status"]),
    )
    monkeypatch.setattr("api.metadata_store.needs_reindex", lambda *_args: False)
    project = {
        "path_with_namespace": repo.project,
        "id": 1,
        "last_activity_at": "now",
        "http_url_to_repo": repo.url + ".git",
    }
    indexer = BatchIndexer("https://gitlab.example", "fixture-token", [])
    assert asyncio.run(indexer.reindex_project(project)) is True
    assert statuses == ["indexing", "indexed"]
    assert indexer.should_reindex(project) is False
    index_path.unlink()
    assert indexer.should_reindex(project) is True


def test_metadata_writes_are_atomic_and_interrupted_indexes_retry(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from api import metadata_store

    monkeypatch.setattr(metadata_store, "METADATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        metadata_store,
        "METADATA_FILE",
        str(tmp_path / "index_metadata.json"),
    )

    def write(index):
        metadata_store.set_project_metadata(
            f"group/repo-{index}", index, "activity", f"group/repo-{index}",
            status="indexing",
        )

    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(write, range(20)))
    projects = metadata_store.get_all_indexed_projects()
    assert len(projects) == 20
    assert metadata_store.needs_reindex("group/repo-0", "activity")
    assert projects["group/repo-0"]["indexed_at"] == ""
    metadata_store.set_project_metadata(
        "group/repo-0", 0, "activity", "group/repo-0", status="indexed"
    )
    assert not metadata_store.needs_reindex("group/repo-0", "activity")


def test_complete_batch_audit_marks_inaccessible_repositories_unavailable(monkeypatch):
    import httpx

    from api.batch_indexer import BatchIndexer

    metadata = {
        "group/gone": {"status": "indexed", "project_id": 1, "repo_path": "group/gone"},
        "group/present": {"status": "indexed", "project_id": 2, "repo_path": "group/present"},
    }
    changes = []

    def respond(request):
        if str(request.url).endswith("/1"):
            return httpx.Response(404)
        return httpx.Response(200, json={"path_with_namespace": "group/present"})

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(
        "api.batch_indexer.httpx.AsyncClient",
        lambda **kwargs: original_client(transport=transport, **kwargs),
    )
    monkeypatch.setattr(
        "api.metadata_store.get_all_indexed_projects", lambda: metadata
    )
    monkeypatch.setattr(
        "api.metadata_store.set_project_metadata",
        lambda **kwargs: changes.append((kwargs["project_path"], kwargs["status"])),
    )
    indexer = BatchIndexer("https://gitlab.example", "fixture-token", [1])
    assert asyncio.run(indexer.audit_unlisted_projects(set())) == 1
    assert changes == [("group/gone", "unavailable")]
    indexer._group_scan_complete = False
    assert asyncio.run(indexer.audit_unlisted_projects(set())) == 0
    assert changes == [("group/gone", "unavailable")]


def test_storage_idempotency_and_recovery(tmp_path):
    store = Store(tmp_path / "app.db")
    session = store.create_session("1", [], "en")
    assert store.get_session(session["id"], "2") is None
    request_id = str(uuid4())
    run = store.create_run(session["id"], request_id, "Locate submit", "test", "test")
    assert store.find_request(session["id"], request_id)["id"] == run["id"]
    with pytest.raises(ValueError):
        store.create_run(session["id"], str(uuid4()), "duplicate", "test", "test")
    first = store.event(run["id"], "text", {"content": "hello"})
    store.event(run["id"], "text", {"content": "hello world"})
    assert len(store.events(run["id"], first)) == 1
    store.recover()
    assert store.get_run(run["id"])["status"] == "interrupted"


def test_existing_run_snapshots_survive_session_refresh(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.db"
    old_scope = [{"project": "group/demo", "url": "https://gitlab.example/group/demo", "commit": "a" * 40}]
    new_scope = [{**old_scope[0], "commit": "b" * 40}]
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
                repos TEXT NOT NULL, language TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE runs (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                request_id TEXT NOT NULL, message TEXT NOT NULL,
                provider TEXT NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL,
                answer TEXT NOT NULL DEFAULT '', error TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(session_id, request_id)
            );
        """)
        db.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
            ("session", "1", "", json.dumps(old_scope), "en", "before", "before"),
        )
        db.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("old-run", "session", "old-request", "first", "test", "test", "completed", "done", None, "before", "before"),
        )
    store = Store(path)
    assert store.get_run("old-run")["repos"] == old_scope
    next_run = store.create_run("session", "new-request", "second", "test", "test")
    store.set_run_repos(next_run["id"], new_scope)
    assert store.get_run("old-run")["repos"] == old_scope
    assert store.get_run(next_run["id"])["repos"] == new_scope
    assert store.get_session("session", "1")["repos"] == new_scope


class TestModel(FakeMessagesListChatModel):
    __test__ = False

    def bind_tools(self, tools, **kwargs):
        return self


class FakeAccess:
    denied = False

    async def check(self, projects, user):
        if self.denied:
            raise HTTPException(403, "permission revoked")


def test_refresh_failure_does_not_analyze_stale_source(repository):
    async def check():
        root, repo = repository
        access = FakeAccess()

        async def unavailable(_scope, _user, on_progress=None):
            raise HTTPException(502, "Cannot check current code for group/demo.")

        access.refresh_readers = unavailable
        rt = Runtime(root / "refresh-failure", root, access=access)
        await rt.start()
        try:
            session = rt.store.create_session("1", [repo.public()], "en")
            run = rt.store.create_run(
                session["id"], str(uuid4()), "Read latest code", "test", "test"
            )
            rt.launch(
                run,
                session,
                None,
                {"gitlab_user_id": 1},
                TestModel(responses=[AIMessage(content="stale answer")]),
            )
            await rt.tasks[run["id"]]
            result = rt.store.get_run(run["id"])
            assert result["status"] == "failed"
            assert result["answer"] == ""
            assert result["repos"] is None
            assert "Cannot check current code" in result["error"]
        finally:
            await rt.close()

    asyncio.run(check())


def test_real_deepagents_tool_loop_checkpoint_and_followup(repository):
    async def check():
        root, repo = repository
        access = FakeAccess()
        rt = Runtime(root / "agent", root, access=access)
        await rt.start()
        try:
            session = rt.store.create_session("1", [repo.public()], "en")
            first = rt.store.create_run(
                session["id"], str(uuid4()), "Where is submit?", "test", "test"
            )
            model = TestModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "read_source",
                                "args": {"repo": "group/demo", "path": "order.py"},
                                "id": "read-1",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    AIMessage(content="submit is implemented in order.py, lines 1–3."),
                ]
            )
            rt.launch(first, session, [repo], {"gitlab_user_id": 1}, model)
            await rt.tasks[first["id"]]
            assert rt.store.get_run(first["id"])["status"] == "completed"
            events = rt.store.events(first["id"])
            assert any(
                e["type"] == "tool_start" and e["data"]["name"] == "read_source"
                for e in events
            )
            second = rt.store.create_run(
                session["id"], str(uuid4()), "Save this as a document", "test", "test"
            )
            model2 = TestModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "save_document",
                                "args": {
                                    "name": "submit.md",
                                    "content": "# Submit\nSource: order.py",
                                },
                                "id": "save-1",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    AIMessage(content="Saved submit.md"),
                ]
            )
            rt.launch(second, session, [repo], {"gitlab_user_id": 1}, model2)
            await rt.tasks[second["id"]]
            assert rt.store.get_run(second["id"])["status"] == "completed"
            assert repo.commit in rt.store.documents(session["id"])[0]["content"]
            graph = rt.create_graph(
                second, session, [repo], {"gitlab_user_id": 1}, model2
            )
            saved = await graph.aget_state(
                {"configurable": {"thread_id": session["id"]}}
            )
            messages = saved.values["messages"]
            assert any(
                isinstance(m, ToolMessage) and "submitted" in m.content
                for m in messages
            )
            assert len([m for m in messages if m.type == "human"]) == 2
            # Simulate app-state update loss after graph completion. Resume must
            # reconcile without invoking the model or duplicating the message.
            rt.store.set_status(second["id"], "interrupted")
            rt.launch(
                second,
                session,
                [repo],
                {"gitlab_user_id": 1},
                TestModel(responses=[]),
                resume=True,
            )
            await rt.tasks[second["id"]]
            assert rt.store.get_run(second["id"])["status"] == "completed"
            assert model2.i == 0
        finally:
            await rt.close()

    asyncio.run(check())


def test_cancel_and_resume_after_process_restart(repository):
    from pydantic import Field

    class WaitingModel(TestModel):
        started: object = Field(default_factory=asyncio.Event, exclude=True)

        async def _agenerate(self, *args, **kwargs):
            self.started.set()
            await asyncio.sleep(30)
            return await super()._agenerate(*args, **kwargs)

    async def check():
        root, repo = repository
        rt = Runtime(root / "cancel", root, access=FakeAccess())
        await rt.start()
        session = rt.store.create_session("1", [repo.public()], "en")
        run = rt.store.create_run(
            session["id"], str(uuid4()), "Find submit", "test", "test"
        )
        slow = WaitingModel(responses=[AIMessage(content="unused")])
        try:
            rt.launch(run, session, [repo], {"gitlab_user_id": 1}, slow)
            await asyncio.wait_for(slow.started.wait(), timeout=5)
            await rt.cancel(run["id"])
            assert rt.store.get_run(run["id"])["status"] == "cancelled"
        finally:
            await rt.close()
        resumed = Runtime(root / "cancel", root, access=FakeAccess())
        await resumed.start()
        try:
            model = TestModel(responses=[AIMessage(content="Resumed answer")])
            resumed.launch(
                run, session, [repo], {"gitlab_user_id": 1}, model, resume=True
            )
            await resumed.tasks[run["id"]]
            assert resumed.store.get_run(run["id"])["answer"] == "Resumed answer"
            graph = resumed.create_graph(
                run, session, [repo], {"gitlab_user_id": 1}, model
            )
            state = await graph.aget_state(
                {"configurable": {"thread_id": session["id"]}}
            )
            assert len([m for m in state.values["messages"] if m.type == "human"]) == 1
        finally:
            await resumed.close()

    asyncio.run(check())


def test_second_worker_cannot_recover_an_active_workers_runs(tmp_path):
    async def check():
        first = Runtime(tmp_path / "worker", tmp_path)
        second = Runtime(tmp_path / "worker", tmp_path)
        await first.start()
        try:
            session = first.store.create_session("1", [], "en")
            run = first.store.create_run(
                session["id"], str(uuid4()), "active", "test", "test"
            )
            with pytest.raises(RuntimeError, match="one worker"):
                await second.start()
            await second.close()
            assert first.store.get_run(run["id"])["status"] == "queued"
        finally:
            await first.close()

    asyncio.run(check())


def test_existing_index_is_filtered_and_never_rebuilt(repository, monkeypatch):
    from types import SimpleNamespace

    from adalflow.core.db import LocalDB
    from adalflow.core.types import Document

    from api.agent.repositories import IndexSearch

    root, repo = repository
    (root / "databases").mkdir()
    docs = [
        Document(
            text="submit function",
            vector=[1.0, 0.0],
            meta_data={"file_path": "order.py"},
        ),
        Document(
            text="sensitive fixture", vector=[1.0, 0.0], meta_data={"file_path": ".env"}
        ),
    ]
    db = LocalDB(transformed_items={"split_and_embed": docs})
    db.save_state(str(root / "databases" / "group_demo.pkl"))

    class Embedder:
        def __call__(self, queries):
            return SimpleNamespace(data=[SimpleNamespace(embedding=[1.0, 0.0])])

    monkeypatch.setattr("api.tools.embedder.get_embedder", lambda **kwargs: Embedder())
    search = IndexSearch(SourceReader([repo]), root)
    result = search.search("submit")
    assert len(result["matches"]) == 1
    assert result["matches"][0]["path"] == "order.py"
    with pytest.raises(ValueError, match="authorized scope"):
        search.search("submit", "other/private")


def test_api_ownership_revocation_and_replay(repository):
    root, repo = repository
    rt = Runtime(root / "api-test", root, access=FakeAccess())
    session = rt.store.create_session("1", [repo.public()], "en")
    run = rt.store.create_run(session["id"], str(uuid4()), "question", "test", "test")
    seq = rt.store.event(run["id"], "text", {"content": "first"})
    rt.store.event(run["id"], "text", {"content": "second"})
    rt.store.set_status(run["id"], "completed", answer="second")
    app = FastAPI()
    app.state.ask_agent = rt
    app.include_router(router)
    app.dependency_overrides[current_user] = lambda: {"gitlab_user_id": 2}
    client = TestClient(app)
    assert client.get(f"/api/agent/sessions/{session['id']}").status_code == 404
    assert client.get(f"/api/agent/runs/{run['id']}/events").status_code == 404
    app.dependency_overrides[current_user] = lambda: {"gitlab_user_id": 1}
    response = client.get(f"/api/agent/runs/{run['id']}/events?after={seq}")
    assert response.status_code == 200
    assert '"first"' not in response.text and '"second"' in response.text
    rt.access.denied = True
    assert client.get(f"/api/agent/sessions/{session['id']}").status_code == 403
    assert client.get("/api/agent/sessions").json()["sessions"] == []


def test_api_selected_scope_and_duplicate_submission(repository, monkeypatch):
    import time
    from contextlib import asynccontextmanager

    from api.agent.access import Access

    root, repo = repository
    checked = []

    async def allowed(token, project, host, user_id):
        checked.append(project)
        return project == repo.project

    monkeypatch.setattr("api.config.GITLAB_URL", "https://gitlab.example")
    monkeypatch.setattr("api.gitlab_permission.check_repo_access", allowed)
    monkeypatch.setattr(
        "api.metadata_store.get_all_indexed_projects",
        lambda: {
            "group/demo": {"status": "indexed"},
            "private/repo": {"status": "indexed"},
        },
    )
    rt = Runtime(
        root / "api-live",
        root,
        access=Access(root),
        model_factory=lambda *_: TestModel(responses=[AIMessage(content="done")]),
    )

    async def refreshed(_scope, _user, on_progress=None):
        return [resolve_repository("group/demo", "https://gitlab.example", root)]

    monkeypatch.setattr(rt.access, "refresh_readers", refreshed)

    @asynccontextmanager
    async def lifespan(app):
        await rt.start()
        try:
            yield
        finally:
            await rt.close()

    app = FastAPI(lifespan=lifespan)
    app.state.ask_agent = rt
    app.include_router(router)
    app.dependency_overrides[current_user] = lambda: {
        "gitlab_user_id": 1,
        "gitlab_access_token": "fixture-private-token",
    }
    with TestClient(app) as client:
        assert client.post("/api/agent/sessions", json={"repos": []}).status_code == 422
        assert (
            client.post(
                "/api/agent/sessions",
                json={"repos": ["https://other.example/group/demo"]},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/agent/sessions", json={"repos": ["group/demo", "private/repo"]}
            ).status_code
            == 403
        )
        response = client.post(
            "/api/agent/sessions", json={"repos": ["group/demo"], "owner": "2"}
        )
        assert response.status_code == 201
        session = response.json()
        assert session["owner"] == "1"
        assert [r["project"] for r in session["repos"]] == ["group/demo"]
        body = {
            "message": "where?",
            "request_id": str(uuid4()),
            "provider": "openai",
            "model": "offline-test",
        }
        first = client.post(f"/api/agent/sessions/{session['id']}/runs", json=body)
        second = client.post(f"/api/agent/sessions/{session['id']}/runs", json=body)
        assert first.status_code == second.status_code == 202
        assert first.json()["id"] == second.json()["id"]
        for _ in range(100):
            run = rt.store.get_run(first.json()["id"])
            if run["status"] == "completed":
                break
            time.sleep(0.02)
        assert run["status"] == "completed"
        assert len(rt.store.runs(session["id"])) == 1
        first_commit = run["repos"][0]["commit"]
        (repo.root / "order.py").write_text("def submit():\n    return 'changed'\n")
        run_git(repo.root, "add", "order.py")
        run_git(repo.root, "commit", "-qm", "second fixture revision")
        body["request_id"] = str(uuid4())
        next_response = client.post(
            f"/api/agent/sessions/{session['id']}/runs", json=body
        )
        assert next_response.status_code == 202
        for _ in range(100):
            next_run = rt.store.get_run(next_response.json()["id"])
            if next_run["status"] == "completed":
                break
            time.sleep(0.02)
        assert next_run["status"] == "completed"
        assert rt.store.get_run(first.json()["id"])["repos"][0]["commit"] == first_commit
        assert next_run["repos"][0]["commit"] != first_commit
        assert rt.store.get_session(session["id"], "1")["repos"] == next_run["repos"]
    for path in (root / "api-live").glob("*.sqlite3"):
        assert b"fixture-private-token" not in path.read_bytes()


def test_openai_compatible_streaming_tool_protocol(repository):
    import httpx
    from langchain_openai import ChatOpenAI

    async def check():
        root, repo = repository
        calls = []

        async def respond(request):
            body = json.loads(request.content)
            calls.append(body)
            assert request.url.path == "/v1/chat/completions"
            assert body["stream"] is True
            names = {tool["function"]["name"] for tool in body["tools"]}
            assert (
                "read_source" in names
                and "task" not in names
                and "execute" not in names
                and "grep" not in names
                and "glob" not in names
                and "read_file" not in names
            )
            if any(message["role"] == "tool" for message in body["messages"]):
                delta = {"content": "Source confirmed."}
                finish = "stop"
            else:
                delta = {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "protocol-read",
                            "type": "function",
                            "function": {
                                "name": "read_source",
                                "arguments": json.dumps(
                                    {"repo": "group/demo", "path": "order.py"}
                                ),
                            },
                        }
                    ]
                }
                finish = "tool_calls"
            chunks = [
                {
                    "id": "completion",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "offline-test",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", **delta},
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "id": "completion",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "offline-test",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                },
            ]
            stream = (
                "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                + "data: [DONE]\n\n"
            )
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, text=stream
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = ChatOpenAI(
                model="offline-test",
                api_key="fixture-key",
                base_url="https://fixture.invalid/v1",
                http_async_client=client,
                streaming=True,
                use_responses_api=False,
            )
            rt = Runtime(root / "protocol", root, access=FakeAccess())
            await rt.start()
            try:
                session = rt.store.create_session("1", [repo.public()], "en")
                run = rt.store.create_run(
                    session["id"], str(uuid4()), "find submit", "openai", "offline-test"
                )
                rt.launch(run, session, [repo], {"gitlab_user_id": 1}, model)
                await rt.tasks[run["id"]]
                assert rt.store.get_run(run["id"])["answer"] == "Source confirmed."
                assert len(calls) == 2
                assert any(
                    m["role"] == "tool" and "submitted" in m["content"]
                    for m in calls[1]["messages"]
                )
            finally:
                await rt.close()

    asyncio.run(check())


def test_tool_only_model_is_forced_to_answer_before_graph_limit(repository):
    import httpx
    from langchain_openai import ChatOpenAI

    from api.agent.runtime import RESEARCH_MODEL_CALLS

    async def check():
        root, repo = repository
        calls = []

        async def respond(request):
            body = json.loads(request.content)
            calls.append(body)
            if body.get("tools"):
                delta = {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": f"loop-{len(calls)}",
                            "type": "function",
                            "function": {
                                "name": "list_repositories",
                                "arguments": "{}",
                            },
                        }
                    ]
                }
                finish = "tool_calls"
            else:
                delta = {
                    "content": (
                        "The selected repository does not contain the "
                        "backend implementation."
                    )
                }
                finish = "stop"
            chunks = [
                {
                    "id": f"completion-{len(calls)}",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "offline-test",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", **delta},
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "id": f"completion-{len(calls)}",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "offline-test",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                },
            ]
            stream = (
                "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                + "data: [DONE]\n\n"
            )
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, text=stream
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = ChatOpenAI(
                model="offline-test",
                api_key="fixture-key",
                base_url="https://fixture.invalid/v1",
                http_async_client=client,
                streaming=True,
                use_responses_api=False,
            )
            rt = Runtime(root / "bounded", root, access=FakeAccess())
            await rt.start()
            try:
                session = rt.store.create_session("1", [repo.public()], "en")
                run = rt.store.create_run(
                    session["id"],
                    str(uuid4()),
                    "Trace the backend flow",
                    "openai",
                    "offline-test",
                )
                rt.launch(run, session, [repo], {"gitlab_user_id": 1}, model)
                await rt.tasks[run["id"]]
                assert rt.store.get_run(run["id"])["status"] == "completed"
                assert len(calls) == RESEARCH_MODEL_CALLS + 1
                assert not calls[-1].get("tools")
                assert (
                    "backend implementation"
                    in rt.store.get_run(run["id"])["answer"]
                )
            finally:
                await rt.close()

    asyncio.run(check())


def test_empty_provider_reply_can_be_retried(repository):
    async def check():
        root, repo = repository
        rt = Runtime(root / "empty", root, access=FakeAccess())
        await rt.start()
        try:
            session = rt.store.create_session("1", [repo.public()], "en")
            run = rt.store.create_run(
                session["id"], str(uuid4()), "find submit", "test", "test"
            )
            rt.launch(
                run,
                session,
                [repo],
                {"gitlab_user_id": 1},
                TestModel(responses=[AIMessage(content="")]),
            )
            await rt.tasks[run["id"]]
            assert rt.store.get_run(run["id"])["status"] == "failed"
            model = TestModel(responses=[AIMessage(content="Recovered reply")])
            rt.launch(run, session, [repo], {"gitlab_user_id": 1}, model, resume=True)
            await rt.tasks[run["id"]]
            assert rt.store.get_run(run["id"])["answer"] == "Recovered reply"
        finally:
            await rt.close()

    asyncio.run(check())


def test_reasoning_filter():
    from api.agent.runtime import text_content, visible_text

    assert visible_text("<think>private reasoning") == ""
    assert visible_text("<think>private reasoning</think>answer") == "answer"
    assert visible_text("answer<thi") == "answer"
    assert (
        text_content(
            [
                {"type": "reasoning", "text": "private"},
                {"type": "text", "text": "answer"},
            ]
        )
        == "answer"
    )


def test_custom_model_context_budget(repository, monkeypatch):
    root, repo = repository
    monkeypatch.setenv("AGENT_CONTEXT_TOKENS", "12000")
    rt = Runtime(root / "budget", root, access=FakeAccess())
    session = rt.store.create_session("1", [repo.public()], "en")
    run = rt.store.create_run(session["id"], str(uuid4()), "find", "test", "test")
    model = TestModel(
        responses=[AIMessage(content="done")], profile={"max_input_tokens": 8000}
    )
    rt.create_graph(run, session, [repo], {"gitlab_user_id": 1}, model)
    assert model.profile["max_input_tokens"] == 8000
    unknown = TestModel(responses=[AIMessage(content="done")])
    rt.create_graph(run, session, [repo], {"gitlab_user_id": 1}, unknown)
    assert unknown.profile["max_input_tokens"] == 12000
