"""Partial indexing contracts; real-provider validation is performed separately."""

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest
import tiktoken
from adalflow.core.db import LocalDB
from adalflow.core.types import Document

from api.index_builder import build_index, prepare_chunks, IndexBuildError
from api.index_state import database_problem, embedding_spec, index_coverage
from tests.agent.test_index_migration import migration
from tests.agent.test_agent import repository


@pytest.fixture
def engine(monkeypatch):
    config = {"client_class": "OpenAIClient", "batch_size": 10,
              "model_kwargs": {"model": "fixture-model", "dimensions": 2}}
    monkeypatch.setattr("api.config.get_embedder_config", lambda: config)
    monkeypatch.setattr("api.index_builder.get_embedder_config", lambda: config)
    state = SimpleNamespace(config=config, calls=[], mode="input_error")

    def embed(input):
        state.calls.append(input)
        if state.mode == "outage":
            return SimpleNamespace(data=[], error="Error code: 503 - secret-token source-payload")
        if state.mode == "input_error" and "bad" in input:
            return SimpleNamespace(data=[], error="Error code: 400 - secret-token source-payload")
        data = [SimpleNamespace(index=i, embedding=[float(i + 1), 0.5]) for i in range(len(input))]
        return SimpleNamespace(data=data, error=None)

    monkeypatch.setattr("api.index_builder.get_embedder", lambda **_kwargs: embed)
    return state


def docs(*texts):
    return [Document(text=text, meta_data={"file_path": f"file-{i}.go"}) for i, text in enumerate(texts)]


def test_long_base64_and_unbroken_unicode_are_bounded(engine):
    source = '<p>before</p><img src="data:image/png;base64,' + "A" * 150225 + '"><p>after</p>'
    chunks, removed = prepare_chunks(docs(source, "中文注释" * 9000))
    assert removed == 1
    text = "".join(d.text for d in chunks)
    assert "before" in text and "after" in text and "A" * 100 not in text
    enc = tiktoken.get_encoding("cl100k_base")
    assert all(len(d.text) <= 6000 and len(enc.encode(d.text)) <= 2048 for d in chunks)
    assert all("�" not in d.text for d in chunks)


def test_bad_batch_isolates_bad_chunk_and_retry_reuses_good_vectors(engine):
    original = docs("good", "bad", "also good")
    db = build_index(original, embedding_spec(), "revision")
    report = index_coverage(db)
    assert report["status"] == "partial"
    assert report["indexed_chunks"] == 2 and report["failed_chunks"] == 1
    assert report["failures"][0]["path"] == "file-1.go"
    assert report["failures"][0]["code"] == "http_400"
    assert "secret-token" not in json.dumps(report)
    assert database_problem(db, embedding_spec(), "revision", allow_partial=True) is None
    assert database_problem(db, embedding_spec(), "revision") is not None
    assert db.transformer_setups == {}  # no provider instance/API request serialized
    vectors = [list(db.get_transformed_data(key="split_and_embed")[i].vector) for i in [0, 2]]
    engine.mode, engine.calls = "success", []
    retried = build_index(original, embedding_spec(), "revision", previous=db)
    assert engine.calls == [["bad"]]
    assert index_coverage(retried)["status"] == "indexed"
    assert index_coverage(retried)["reused_chunks"] == 2
    assert [retried.get_transformed_data(key="split_and_embed")[i].vector for i in [0, 2]] == vectors


@pytest.mark.parametrize("change", ["model", "dimensions", "revision"])
def test_retry_never_reuses_other_model_dimension_or_revision(engine, change):
    original = docs("good", "bad")
    previous = build_index(original, embedding_spec(), "revision")
    if change == "model":
        engine.config["model_kwargs"]["model"] = "different"
    elif change == "dimensions":
        engine.config["model_kwargs"]["dimensions"] = 3
    engine.calls = []
    try:
        build_index(original, embedding_spec(), "other" if change == "revision" else "revision", previous=previous)
    except IndexBuildError:
        pass  # fake provider's 2D vectors are intentionally invalid for 3D
    assert engine.calls[0] == ["good", "bad"]


def test_outage_does_not_fan_out_and_keeps_prior_success(engine):
    original = docs("good", "bad", "another")
    previous = build_index(original, embedding_spec(), "revision")
    engine.calls, engine.mode = [], "outage"
    partial = build_index(original, embedding_spec(), "revision", previous=previous)
    assert engine.calls == [["bad"]]
    assert index_coverage(partial)["indexed_chunks"] == 2
    engine.calls = []
    with pytest.raises(IndexBuildError) as error:
        build_index(original, embedding_spec(), "other revision")
    assert len(engine.calls) == 1
    assert error.value.report["failed_chunks"] == 3
    assert "secret-token" not in str(error.value) + json.dumps(error.value.report)


def test_empty_invalid_and_reordered_vectors(engine, monkeypatch):
    original = docs("a", "b", "c", "d")
    def embed(input):
        return SimpleNamespace(data=[
            SimpleNamespace(index=2, embedding=[1, 2]),
            SimpleNamespace(index=0, embedding=[float("nan"), 1]),
            SimpleNamespace(index=3, embedding=[0, 0]),
            SimpleNamespace(index=1, embedding=[1, 2, 3]),
        ], error=None)
    monkeypatch.setattr("api.index_builder.get_embedder", lambda **_kwargs: embed)
    db = build_index(original, embedding_spec(), "revision")
    assert index_coverage(db)["indexed_chunks"] == 1
    assert db.get_transformed_data(key="split_and_embed")[2].vector == [1, 2]
    assert len(db.index_failures) == 3


def test_zero_success_keeps_previous_file_and_records_error(migration):
    from api.metadata_store import get_project_metadata
    m = migration
    before = m.path.read_bytes()
    m.config["model_kwargs"]["model"] = "new-model"
    m.state["fail"] = "raise"
    assert not asyncio.run(m.indexer.reindex_project(m.project))
    assert m.path.read_bytes() == before
    meta = get_project_metadata(m.repo.project)
    assert meta["status"] == "error"
    assert meta["index_report"]["failed_chunks"] == 2
    assert "secret-token" not in json.dumps(meta)


def test_partial_success_is_counted_separately_in_batch(migration):
    m = migration
    m.config["model_kwargs"]["model"] = "new-model"
    m.state["fail"] = "partial"
    result = asyncio.run(m.indexer.run_selected(project_ids=[1], operation="reindex"))
    assert result["partial"] == 1 and result["indexed"] == 0 and result["errors"] == 0
    assert m.indexer.should_reindex(m.project)


def test_reading_partial_index_does_not_retry_provider(migration):
    from api.data_pipeline import DatabaseManager
    m = migration
    m.config["model_kwargs"]["model"] = "new-model"
    m.state["fail"] = "partial"
    assert asyncio.run(m.indexer.reindex_project(m.project))
    m.state["inputs"] = []
    loaded = DatabaseManager().prepare_database(m.repo.url, "gitlab", pull=False)
    assert len(loaded) == 1
    assert m.state["inputs"] == []


def test_source_changes_during_embedding_cannot_replace_index(migration, monkeypatch):
    from api.data_pipeline import DatabaseManager
    m = migration
    before = m.path.read_bytes()
    m.config["model_kwargs"]["model"] = "new-model"
    revisions = iter([m.repo.commit, "changed-mid-build"])
    monkeypatch.setattr("api.index_state.source_revision", lambda _path: next(revisions))
    with pytest.raises(ValueError, match="Source revision changed"):
        DatabaseManager().prepare_database(m.repo.url, "gitlab")
    assert m.path.read_bytes() == before


def test_sdk_payload_logging_is_suppressed():
    from api.logging_config import ProviderPayloadFilter
    guard = ProviderPayloadFilter()
    record = logging.LogRecord("adalflow.core.component", logging.INFO, "", 1, "Restoring api_key=SECRET", (), None)
    assert not guard.filter(record)
    record = logging.LogRecord("backoff", logging.ERROR, "", 1, "Error code: 400 - SECRET SOURCE", (), None)
    assert guard.filter(record)
    assert "SECRET" not in record.getMessage() and "400" in record.getMessage()


def test_partial_repository_can_be_selected_in_ask(repository, monkeypatch):
    from api.agent.access import Access
    root, repo = repository

    async def allowed(*_args):
        return True

    monkeypatch.setattr("api.config.GITLAB_URL", "https://gitlab.example")
    monkeypatch.setattr("api.gitlab_permission.check_repo_access", allowed)
    monkeypatch.setattr("api.metadata_store.get_all_indexed_projects", lambda: {repo.project: {"status": "partial"}})
    scope = asyncio.run(Access(root).create_scope([repo.project], {"gitlab_user_id": 1, "gitlab_access_token": "fixture"}))
    assert scope[0]["project"] == repo.project


def test_http_400_is_not_blindly_retried_before_batch_isolation():
    import httpx
    from openai import BadRequestError
    from api.openai_client import OpenAIClient
    from adalflow.core.types import ModelType
    calls = []

    def reject(**_kwargs):
        calls.append(1)
        raise BadRequestError("invalid input", response=httpx.Response(400, request=httpx.Request("POST", "https://example.invalid")), body={})

    client = OpenAIClient(api_key="fixture-only")
    client.sync_client = SimpleNamespace(embeddings=SimpleNamespace(create=reject))
    with pytest.raises(BadRequestError):
        client.call({"model": "fixture-model", "input": ["fixture"]}, ModelType.EMBEDDER)
    assert len(calls) == 1
