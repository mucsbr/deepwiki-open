"""Activity transport contracts, not business-analysis accuracy tests."""

import json
from uuid import uuid4

from langchain_core.messages import ToolMessage

from api.agent.activity import public_input, tool_result
from api.agent.store import Store


def test_public_tool_payloads_are_generic_bounded_and_redacted():
    value = public_input({
        "repo": "group/repo", "new_tool_parameter": {"query": "entry"},
        "api_key": "SECRET", "headers": {"Authorization": "SECRET"},
        "url": "https://user:SECRET@example.test/api?token=SECRET",
        "content": "x" * 2000,
    })
    assert "SECRET" not in json.dumps(value)
    assert value["new_tool_parameter"] == {"query": "entry"}
    assert len(value["content"]) == 1001
    output = ToolMessage(content=json.dumps({"error": "private provider details"}), tool_call_id="a")
    assert tool_result(output) == {"error": True, "result": {}}
    assert tool_result(ToolMessage(content="raw exception", status="error", tool_call_id="b"))["error"]
    output = ToolMessage(content=json.dumps({"content": "secret source\nline two", "matches": [1, 2]}), tool_call_id="c")
    assert tool_result(output) == {"error": False, "result": {"matches": 2, "lines": 2}}


def test_activity_compacts_snapshots_across_pages_but_preserves_rounds(tmp_path):
    store = Store(tmp_path / "events.sqlite")
    session = store.create_session("1", [], "en")
    run = store.create_run(session["id"], str(uuid4()), "question", "test", "test")
    rid = run["id"]
    for i in range(240):
        store.event(rid, "draft", {"id": "first", "content": f"checking {i}"})
    store.event(rid, "tool_start", {"id": "read", "name": "read_source"})
    store.event(rid, "tool_end", {"id": "read", "error": False})
    for i in range(240):
        store.event(rid, "draft", {"id": "second", "content": f"answer {i}"})
    cursor = store.event(rid, "text", {"id": "second", "content": "answer 239"})
    replay = store.activity(rid)
    assert replay["cursor"] == cursor
    assert len(replay["events"]) == 5
    assert replay["events"][0]["data"]["content"] == "checking 239"
    assert replay["events"][3]["data"]["content"] == "answer 239"
    assert store.events(rid, replay["cursor"]) == []
    assert store.activity(rid) == replay


def test_activity_keeps_legacy_rounds_separate(tmp_path):
    store = Store(tmp_path / "legacy.sqlite")
    session = store.create_session("1", [], "en")
    rid = store.create_run(session["id"], str(uuid4()), "question", "test", "test")["id"]
    store.event(rid, "draft", {"content": "first"})
    store.event(rid, "draft", {"content": "first complete"})
    store.event(rid, "tool_start", {"id": "read", "name": "read_source"})
    store.event(rid, "draft", {"content": "second"})
    assert [e["data"]["content"] for e in store.activity(rid)["events"] if e["type"] == "draft"] == ["first complete", "second"]
