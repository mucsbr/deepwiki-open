"""Native compaction and stopped-turn contracts; not model investigation quality."""

import asyncio
import json
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from api.agent.context import EmptySummaryError, ManagedSummarization, SnapshotBackend
from api.agent.runtime import Runtime
from api.agent.store import Store
from api.agent.tools import make_tools
from api.agent.repositories import SourceReader
from tests.agent.test_agent import FakeAccess, TestModel, repository


def test_stopped_task_accepts_corrections_and_old_resume_is_rejected(tmp_path):
    store = Store(tmp_path / "records.db")
    session = store.create_session("owner", [], "zh")
    first = store.create_run(session["id"], str(uuid4()), "trace", "fixture", "fixture")
    store.set_status(first["id"], "cancelled")
    compact = store.create_run(session["id"], str(uuid4()), "compact", "fixture", "fixture", kind="compact")
    assert store.can_resume(first)
    with pytest.raises(ValueError, match="Stop the active"):
        store.create_run(session["id"], str(uuid4()), "correction", "fixture", "fixture")
    store.set_status(compact["id"], "completed")
    correction = store.create_run(session["id"], str(uuid4()), "report the missing backend first", "fixture", "fixture")
    assert correction["kind"] == "analysis"
    assert not store.can_resume(first)
    assert store.can_resume(correction)
    assert store.get_run(first["id"])["status"] == "cancelled"


def test_summary_bounds_gateway_output_and_replaces_native_middleware(monkeypatch):
    monkeypatch.setenv("AGENT_SUMMARY_TOKENS", "256")
    model = TestModel(responses=[], profile={"max_input_tokens": 32000})
    summary = ManagedSummarization(model, SnapshotBackend({}), lambda *_args: None)
    shortened = summary.bounded_summary("verified fact and pointers. " * 10000)
    assert summary._lc_helper._partial_token_counter([HumanMessage(content=shortened)]) <= 256
    assert "saved history" in shortened
    assert summary.name == "SummarizationMiddleware"
    with pytest.raises(ValueError, match="Empty summary"):
        summary.bounded_summary("<think>hidden</think>")


def test_repeated_search_feedback_survives_resume_without_round_caps(repository):
    root, repo = repository
    store = Store(root / "search-feedback.sqlite3")
    session = store.create_session("1", [repo.public()], "en")
    run = store.create_run(session["id"], str(uuid4()), "find handler", "test", "test")

    async def authorize():
        pass

    async def check():
        tool = next(t for t in make_tools(SourceReader([repo]), root, store, run, authorize) if t.name == "search_source")
        assert "search_notice" not in json.loads(await tool.ainvoke({"query": "missing_handler"}))
        repeated = json.loads(await tool.ainvoke({"query": "missing_handler", "limit": 40}))
        assert repeated["matches"] == []
        assert "add its repository" in repeated["search_notice"]
        store.event(run["id"], "tool_start", {"id": "old", "name": "search_source", "input": {"query": "missing_handler"}})
        store.event(run["id"], "tool_end", {"id": "old", "name": "search_source", "error": False, "result": {"matches": 0}})
        resumed = next(t for t in make_tools(SourceReader([repo]), root, store, run, authorize) if t.name == "search_source")
        assert "search_notice" in json.loads(await resumed.ainvoke({"query": "missing_handler"}))

    asyncio.run(check())


def test_summary_input_keeps_objective_latest_correction_and_recent_evidence():
    model = TestModel(responses=[], profile={"max_input_tokens": 8000})
    summary = ManagedSummarization(model, SnapshotBackend({}), lambda *_args: None)
    prepared = summary.summary_input([
        HumanMessage(content="TASK_OBJECTIVE: find the missing handler. " + "old facts. " * 3000),
        HumanMessage(content="USER_CORRECTION: state missing repositories before continuing."),
        ToolMessage(content="source evidence. " * 5000 + " LAST_VERIFIED_EVIDENCE", tool_call_id="source"),
    ])
    rendered = "\n".join(str(m.content) for m in prepared)
    assert "TASK_OBJECTIVE" in rendered
    assert "USER_CORRECTION" in rendered
    assert "LAST_VERIFIED_EVIDENCE" in rendered
    assert summary._lc_helper._partial_token_counter(prepared) <= summary.summary_input_tokens


@pytest.mark.parametrize("phase", ["before_tools", "before_model", "completed"])
def test_manual_compaction_preserves_raw_history_and_pending_analysis(repository, phase, monkeypatch):
    monkeypatch.setenv("AGENT_CONTEXT_TOKENS", "32000")
    async def check():
        root, repo = repository
        access = FakeAccess()

        async def readers(_scope, _user):
            return [repo]

        access.readers = readers
        rt = Runtime(root / "manual", root, access=access)
        await rt.start()
        try:
            session = rt.store.create_session("1", [repo.public()], "zh")
            original = rt.store.create_run(session["id"], str(uuid4()), "trace seed/clue", "test", "test")
            rt.store.set_status(original["id"], "cancelled")
            model = TestModel(responses=[AIMessage(content="Verified: group/demo order.py L1-3. Missing handler: ask for its repository.")])
            graph = rt.create_graph(original, session, [repo], {"gitlab_user_id": 1}, model)
            messages = [HumanMessage(content="Find the handler and report missing repositories", id=original["id"])]
            for number in range(12):
                messages.extend([
                    AIMessage(content="", tool_calls=[{"name": "read_source", "args": {"repo": repo.project, "path": "order.py"}, "id": f"old-{number}", "type": "tool_call"}]),
                    ToolMessage(content="verified source and pointers\n" * 300, tool_call_id=f"old-{number}"),
                ])
            messages.append(AIMessage(content="", tool_calls=[{"name": "read_source", "args": {"repo": repo.project, "path": "order.py"}, "id": "pending-read", "type": "tool_call"}],
                                      usage_metadata={"input_tokens": 23000, "output_tokens": 20, "total_tokens": 23020}))
            writer = "model"
            if phase == "before_model":
                messages.append(ToolMessage(content="verified last result", tool_call_id="pending-read"))
                writer = "tools"
            elif phase == "completed":
                messages[-1].tool_calls = []
                messages[-1].content = "Verified partial report"
                writer = "TodoListMiddleware.after_model"
            config = {"configurable": {"thread_id": session["id"]}}
            await graph.aupdate_state(config, {"messages": messages}, as_node=writer)
            before = await graph.aget_state(config)
            compact = rt.store.create_run(session["id"], str(uuid4()), "compact", "test", "test", kind="compact")
            rt.launch(compact, session, [repo], {"gitlab_user_id": 1}, model)
            await rt.tasks[compact["id"]]
            result = rt.store.get_run(compact["id"])
            assert result["status"] == "completed", result
            after = await graph.aget_state(config)
            assert after.next == before.next, (before.next, after.next)
            assert len(after.values["messages"]) == len(before.values["messages"])
            assert "_summarization_event" in after.values, rt.store.activity(compact["id"])
            assert after.values["_summarization_event"]["cutoff_index"] > 0
            assert any(path.startswith("/conversation_history/") for path in after.values["files"])
            assert not any(e["type"] == "tool_start" for e in rt.store.activity(compact["id"])["events"])
            assert rt.store.can_resume(original)
            assert rt.store.context_usage(session["id"])["source"] == "estimated"
        finally:
            await rt.close()

    asyncio.run(check())


def test_correction_after_cancel_does_not_replay_pending_tools(repository):
    async def check():
        root, repo = repository
        rt = Runtime(root / "correction", root, access=FakeAccess())
        await rt.start()
        try:
            session = rt.store.create_session("1", [repo.public()], "zh")
            original = rt.store.create_run(session["id"], str(uuid4()), "old objective", "test", "test")
            rt.store.set_status(original["id"], "cancelled")
            model = TestModel(responses=[AIMessage(content="I will report the gap, not keep searching.")])
            graph = rt.create_graph(original, session, [repo], {"gitlab_user_id": 1}, model)
            config = {"configurable": {"thread_id": session["id"]}}
            await graph.aupdate_state(config, {"messages": [HumanMessage(content="old objective", id=original["id"]), AIMessage(content="", tool_calls=[
                {"name": "read_source", "args": {"repo": repo.project, "path": "order.py"}, "id": "cancelled-read", "type": "tool_call"}
            ])]}, as_node="model")
            correction = rt.store.create_run(session["id"], str(uuid4()), "report the gap now", "test", "test")
            rt.launch(correction, session, [repo], {"gitlab_user_id": 1}, model)
            await rt.tasks[correction["id"]]
            assert rt.store.get_run(correction["id"])["status"] == "completed"
            assert not any(e["type"] == "tool_start" for e in rt.store.activity(correction["id"])["events"])
            state = await graph.aget_state(config)
            assert len([m for m in state.values["messages"] if m.type == "human"]) == 2
            patch = next(m for m in state.values["messages"] if m.type == "tool")
            assert patch.status == "error" and "interrupted" in patch.content
        finally:
            await rt.close()

    asyncio.run(check())


def test_automatic_compaction_is_visible_bounded_and_not_an_answer(repository, monkeypatch):
    monkeypatch.setenv("AGENT_CONTEXT_TOKENS", "8000")
    from langchain_core.outputs import ChatGeneration, ChatResult

    calls = []

    class PurposeModel(TestModel):
        async def _agenerate(self, messages, **kwargs):
            # The SDK may copy model instances; select by request purpose, not
            # a shared fixture response index across the summary/main adapters.
            summary = any(str(m.content).startswith("Rewrite a compact working record") for m in messages)
            calls.append((summary, kwargs.get("max_tokens")))
            content = ("Short summary: facts verified, missing backend unresolved." if summary else
                       "User-facing verified answer with gaps.")
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

    async def check():
        root, repo = repository
        rt = Runtime(root / "automatic", root, access=FakeAccess())
        await rt.start()
        try:
            session = rt.store.create_session("1", [repo.public()], "en")
            entry = rt.store.create_run(session["id"], str(uuid4()), "Report verified facts and missing backend", "test", "test")
            model = PurposeModel(responses=[])
            graph = rt.create_graph(entry, session, [repo], {"gitlab_user_id": 1}, model)
            messages = [HumanMessage(content=entry["message"], id=entry["id"])]
            for number in range(12):
                messages.extend([
                    AIMessage(content="", tool_calls=[{"name": "read_source", "args": {"repo": repo.project, "path": "order.py"}, "id": f"read-{number}", "type": "tool_call"}]),
                    ToolMessage(content="verified facts and source pointers\n" * 100, tool_call_id=f"read-{number}"),
                ])
            config = {"configurable": {"thread_id": session["id"]}}
            await graph.aupdate_state(config, {"messages": messages}, as_node="tools")
            rt.launch(entry, session, [repo], {"gitlab_user_id": 1}, model)
            await rt.tasks[entry["id"]]
            result = rt.store.get_run(entry["id"])
            assert result["status"] == "completed", result
            activity = rt.store.activity(entry["id"])["events"]
            assert result["answer"] == "User-facing verified answer with gaps.", [e for e in activity if e["type"] in {"context", "compaction"}]
            assert any(e["type"] == "compaction" and e["data"]["phase"] == "running" for e in activity)
            assert any(e["type"] == "compaction" and e["data"]["phase"] == "completed" for e in activity)
            assert not any(e["type"] == "draft" and "Short summary" in e["data"].get("content", "") for e in activity)
            assert rt.store.context_usage(session["id"])["budget_tokens"] == 8000
            state = await graph.aget_state(config)
            assert len(state.values["_summarization_event"]["summary_message"].content) < 2000
            assert (True, 4000) in calls
            assert (False, None) in calls  # final answer has no summary limit
        finally:
            await rt.close()

    asyncio.run(check())


def test_unknown_gateway_model_defaults_to_1m_and_85_percent(repository, monkeypatch):
    monkeypatch.delenv("AGENT_CONTEXT_TOKENS", raising=False)
    root, repo = repository
    rt = Runtime(root / "1m", root, access=FakeAccess())
    session = rt.store.create_session("1", [repo.public()], "en")
    entry = rt.store.create_run(session["id"], str(uuid4()), "question", "test", "test")
    model = TestModel(responses=[])
    rt.create_graph(entry, session, [repo], {"gitlab_user_id": 1}, model)
    assert model.profile["max_input_tokens"] == 1_000_000
    summary = ManagedSummarization(model, SnapshotBackend({}), lambda *_args: None)
    assert not summary._should_summarize([HumanMessage(content="small")], 849_999)
    assert summary._should_summarize([HumanMessage(content="small")], 850_000)
    assert summary.summary_tokens == 2000
    assert summary.generation_tokens == 8192


def test_empty_summary_retries_without_publishing_partial_history():
    async def scenario():
        model = TestModel(responses=[AIMessage(content="", response_metadata={"finish_reason": "length"}), AIMessage(content="Verified facts, source pointers and unresolved gaps.")], profile={"max_input_tokens": 32000})
        events = []
        backend = SnapshotBackend({})
        summary = ManagedSummarization(model, backend, lambda kind, data: events.append((kind, data)), manual=True)
        text = await summary._acreate_summary([HumanMessage(content="task"), AIMessage(content="evidence")])
        assert "Verified facts" in text
        assert any(kind == "compaction" and data["phase"] == "retrying" for kind, data in events)
        diagnostics = [data for kind, data in events if kind == "compaction_diagnostics"]
        assert len(diagnostics) == 2 and diagnostics[0]["finish_reason"] == "length"
        assert not backend.updates
        assert "Verified facts" not in json.dumps(events)

    asyncio.run(scenario())


def test_persistent_empty_summary_never_archives_or_replaces_context():
    async def scenario():
        import asyncio

        backend = SnapshotBackend({})
        model = TestModel(responses=[AIMessage(content=""), AIMessage(content="<think>internal-only</think>")], profile={"max_input_tokens": 32000})
        events = []
        summary = ManagedSummarization(model, backend, lambda kind, data: events.append((kind, data)))
        summary._summary_gate = asyncio.Event()
        summary_task = asyncio.create_task(summary._acreate_summary([HumanMessage(content="keep original task")]))
        archive_task = asyncio.create_task(summary._aoffload_to_backend(backend, [HumanMessage(content="keep original task")], "fixture"))
        with pytest.raises(EmptySummaryError):
            await summary_task
        assert await archive_task is None
        assert not backend.updates
        assert events[-1][1]["phase"] == "interrupted"

    asyncio.run(scenario())


def test_legacy_failed_model_with_file_write_resumes_without_replaying_tools(repository):
    async def scenario():
        root, repo = repository
        rt = Runtime(root / "legacy-failure", root, access=FakeAccess())
        await rt.start()
        try:
            session = rt.store.create_session("1", [repo.public()], "en")
            entry = rt.store.create_run(session["id"], str(uuid4()), "original question", "test", "test")
            rt.store.set_run_repos(entry["id"], [repo.public()])
            rt.store.set_status(entry["id"], "failed")
            model = TestModel(responses=[AIMessage(content="Recovered final answer.")])
            graph = rt.create_graph(entry, session, [repo], {"gitlab_user_id": 1}, model)
            config = {"configurable": {"thread_id": session["id"]}}
            await graph.aupdate_state(config, {"messages": [
                HumanMessage(content=entry["message"], id=entry["id"]),
                AIMessage(content="", tool_calls=[{"name": "read_source", "args": {"repo": repo.project, "path": "order.py"}, "id": "already-read", "type": "tool_call"}]),
                ToolMessage(content="previously verified source evidence", tool_call_id="already-read"),
            ]}, as_node="tools")
            snapshot = await graph.aget_state(config)
            # Reproduce the persisted legacy model ERROR + files partial write.
            task = next(task for task in snapshot.tasks if task.name == "model")
            await rt.checkpointer.aput_writes(snapshot.config, [
                ("files", {"/conversation_history/legacy.md": {"content": "old history", "created_at": "before", "modified_at": "before"}}),
                ("__error__", "ValueError('Empty summary')"),
            ], task.id)
            failed = await graph.aget_state(config)
            assert not failed.next and any(task.error for task in failed.tasks)
            rt.launch(rt.store.get_run(entry["id"]), session, [repo], {"gitlab_user_id": 1}, model, resume=True)
            await rt.tasks[entry["id"]]
            result = rt.store.get_run(entry["id"])
            assert result["status"] == "completed", result
            assert result["answer"] == "Recovered final answer."
            events = rt.store.activity(entry["id"])["events"]
            assert any(e["type"] == "recovery" for e in events)
            assert not any(e["type"] == "tool_start" for e in events)
            state = await graph.aget_state(config)
            assert len([m for m in state.values["messages"] if m.type == "human"]) == 1
            assert any(m.content == "previously verified source evidence" for m in state.values["messages"])
        finally:
            await rt.close()

    asyncio.run(scenario())


def test_empty_auto_summary_is_recoverable_and_does_not_commit_file_writes(repository, monkeypatch):
    monkeypatch.setenv("AGENT_CONTEXT_TOKENS", "8000")
    from langchain_core.outputs import ChatGeneration, ChatResult

    class EmptySummaryModel(TestModel):
        async def _agenerate(self, messages, **kwargs):
            assert any(str(m.content).startswith("Rewrite a compact working record") for m in messages)
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", response_metadata={"finish_reason": "length"}))])

    async def scenario():
        root, repo = repository
        rt = Runtime(root / "blank-summary", root, access=FakeAccess())
        await rt.start()
        try:
            session = rt.store.create_session("1", [repo.public()], "en")
            entry = rt.store.create_run(session["id"], str(uuid4()), "keep original evidence", "test", "test")
            model = EmptySummaryModel(responses=[])
            graph = rt.create_graph(entry, session, [repo], {"gitlab_user_id": 1}, model)
            config = {"configurable": {"thread_id": session["id"]}}
            messages = [HumanMessage(content=entry["message"], id=entry["id"])]
            for number in range(12):
                messages.extend([
                    AIMessage(content="", tool_calls=[{"name": "read_source", "args": {"repo": repo.project, "path": "order.py"}, "id": f"read-{number}", "type": "tool_call"}]),
                    ToolMessage(content="source facts and pointers\n" * 120, tool_call_id=f"read-{number}"),
                ])
            await graph.aupdate_state(config, {"messages": messages}, as_node="tools")
            rt.launch(entry, session, [repo], {"gitlab_user_id": 1}, model)
            await rt.tasks[entry["id"]]
            result = rt.store.get_run(entry["id"])
            assert result["status"] == "interrupted", result
            assert "空摘要" in result["error"]
            snapshot = await graph.aget_state(config)
            assert snapshot.next and any(task.error for task in snapshot.tasks)
            assert len(snapshot.values["messages"]) == len(messages)
            assert "_summarization_event" not in snapshot.values
            assert not snapshot.values.get("files")
            events = rt.store.activity(entry["id"])["events"]
            diagnostics = [e for e in events if e["type"] == "compaction_diagnostics"]
            assert len(diagnostics) == 2
        finally:
            await rt.close()

    asyncio.run(scenario())
