"""Model changes must rebuild caches through both batch and database layers."""

import asyncio
from types import SimpleNamespace

import pytest
from adalflow.core.db import LocalDB
from adalflow.core.types import Document

from api.index_state import embedding_spec, index_problem
from tests.agent.test_agent import repository as source_repository
from tests.agent.test_agent import run_git


@pytest.fixture
def migration(tmp_path, monkeypatch):
    from api import data_pipeline, metadata_store
    from api.batch_indexer import BatchIndexer

    root, repo = source_repository.__wrapped__(tmp_path)
    config = {
        "client_class": "OpenAIClient",
        "model_kwargs": {"model": "old-model", "dimensions": 2},
    }
    monkeypatch.setattr("api.config.get_embedder_config", lambda: config)
    monkeypatch.setattr(
        "adalflow.utils.get_adalflow_default_root_path", lambda: str(root)
    )
    monkeypatch.setattr(metadata_store, "METADATA_DIR", str(root / "metadata"))
    monkeypatch.setattr(
        metadata_store, "METADATA_FILE", str(root / "metadata" / "index_metadata.json")
    )
    path = root / "databases" / "group_demo.pkl"
    path.parent.mkdir()
    documents = [
        Document(text="submit", vector=[0.1, 0.2], meta_data={"file_path": "order.py"})
    ]
    database = LocalDB(transformed_items={"split_and_embed": documents})
    database.index_embedding_spec = embedding_spec()
    database.source_revision = repo.commit
    database.save_state(str(path))
    project = {
        "path_with_namespace": repo.project,
        "id": 1,
        "last_activity_at": "unchanged",
        "http_url_to_repo": repo.url + ".git",
    }
    metadata_store.set_project_metadata(repo.project, 1, "unchanged", repo.project)
    state = {"embeddings": 0, "inputs": [], "fail": None, "git_changed": False}

    def create_repo(manager, *_args, **_kwargs):
        manager.repo_paths = {
            "save_repo_dir": str(repo.root),
            "save_db_file": str(path),
        }
        changed = state["git_changed"]
        state["git_changed"] = False
        return changed

    def embed(input):
        state["embeddings"] += 1
        state["inputs"].extend(input)
        if state["fail"] == "raise":
            raise RuntimeError("fixture provider unavailable secret-token")
        return SimpleNamespace(error=None, data=[
            SimpleNamespace(index=i, embedding=[] if state["fail"] == "partial" and text == "dispatch" else [0.25] * config["model_kwargs"]["dimensions"])
            for i, text in enumerate(input)
        ])

    monkeypatch.setattr(data_pipeline.DatabaseManager, "_create_repo", create_repo)
    monkeypatch.setattr(
        data_pipeline,
        "read_all_documents",
        lambda *_args, **_kwargs: [
            Document(text="submit", meta_data={"file_path": "order.py"}),
            Document(text="dispatch", meta_data={"file_path": "order.py"}),
        ],
    )
    monkeypatch.setattr("api.index_builder.get_embedder", lambda **_kwargs: embed)
    indexer = BatchIndexer("https://gitlab.example", "fixture-token", [])

    async def fetch(_pid):
        return project

    monkeypatch.setattr(indexer, "fetch_project_by_id", fetch)
    return SimpleNamespace(
        root=root,
        repo=repo,
        path=path,
        config=config,
        project=project,
        state=state,
        indexer=indexer,
    )


@pytest.mark.parametrize(
    "field,value", [("model", "Qwen/Qwen3-Embedding-4B"), ("dimensions", 3)]
)
def test_unchanged_git_with_new_embedding_config_rebuilds_then_skips(
    migration, field, value
):
    m = migration
    assert not m.indexer.should_reindex(m.project)
    m.config["model_kwargs"][field] = value
    assert m.indexer.should_reindex(m.project)
    progress = []
    result = asyncio.run(
        m.indexer.run_selected(
            project_ids=[1], operation="reindex", on_progress=progress.append
        )
    )
    assert result["indexed"] == 1 and result["skipped"] == 0 and result["errors"] == 0
    assert not any(item.get("status") == "skipped" for item in progress)
    assert m.state["embeddings"] == 1
    database = LocalDB.load_state(str(m.path))
    assert database.index_embedding_spec == embedding_spec()
    assert database.source_revision == m.repo.commit
    assert database.index_path == str(m.path)
    assert index_problem(m.path, m.repo.commit) is None
    result = asyncio.run(m.indexer.run_selected(project_ids=[1], operation="reindex"))
    assert result["skipped"] == 1 and result["indexed"] == 0
    assert m.state["embeddings"] == 1


def test_partial_migration_publishes_success_and_retries_only_failures(migration):
    from api.metadata_store import get_project_metadata

    m = migration
    old = m.path.read_bytes()
    m.config["model_kwargs"]["model"] = "Qwen/Qwen3-Embedding-4B"
    m.state["fail"] = "partial"
    assert asyncio.run(m.indexer.reindex_project(m.project, force=True))
    assert m.path.read_bytes() != old
    meta = get_project_metadata(m.repo.project)
    assert meta["status"] == "partial"
    assert meta["index_report"]["indexed_chunks"] == 1
    assert meta["index_report"]["failed_chunks"] == 1
    assert meta["index_report"]["failures"][0]["path"] == "order.py"
    assert not list(m.path.parent.glob("*.tmp"))
    m.state["inputs"] = []
    m.state["fail"] = None
    result = asyncio.run(m.indexer.run_selected(project_ids=[1], operation="reindex"))
    assert result["indexed"] == 1 and result["errors"] == 0
    assert get_project_metadata(m.repo.project)["status"] == "indexed"
    assert get_project_metadata(m.repo.project)["last_error"] is None
    assert m.state["inputs"] == ["dispatch"]
    assert m.path.read_bytes() != old


def test_failed_git_refresh_retries_even_after_git_is_already_updated(migration):
    from api.data_pipeline import DatabaseManager

    m = migration
    old = m.path.read_bytes()
    (m.repo.root / "order.py").write_text("def submit(): return 'new revision'\n")
    run_git(m.repo.root, "add", "order.py")
    run_git(m.repo.root, "commit", "-qm", "new source")
    revision = run_git(m.repo.root, "rev-parse", "HEAD")
    m.state.update(git_changed=True, fail="raise")
    with pytest.raises(ValueError, match="No usable embeddings"):
        DatabaseManager().prepare_database(m.repo.url, "gitlab", pull=True)
    assert m.path.read_bytes() == old
    m.state["fail"] = None
    DatabaseManager().prepare_database(m.repo.url, "gitlab", pull=True)
    assert m.state["embeddings"] == 2
    assert index_problem(m.path, revision) is None


def test_partial_vectors_do_not_count_as_up_to_date(migration):
    m = migration
    database = LocalDB.load_state(str(m.path))
    database.transformed_items["split_and_embed"].append(
        Document(text="missing", vector=[])
    )
    database.save_state(str(m.path))
    assert m.indexer.should_reindex(m.project)
    result = asyncio.run(m.indexer.run_selected(project_ids=[1], operation="reindex"))
    assert result["indexed"] == 1 and result["skipped"] == 0
    assert index_problem(m.path, m.repo.commit) is None
