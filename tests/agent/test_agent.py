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
    assert statuses == ["error"]


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


class TestModel(FakeMessagesListChatModel):
    __test__ = False

    def bind_tools(self, tools, **kwargs):
        return self


class FakeAccess:
    denied = False

    async def check(self, projects, user):
        if self.denied:
            raise HTTPException(403, "permission revoked")


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
