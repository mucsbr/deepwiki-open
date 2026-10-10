"""Dependency traversal and cache migration must not discard owned source."""

import os

import pytest
from adalflow.core.db import LocalDB
from adalflow.core.types import Document

from api.data_pipeline import DatabaseManager, read_all_documents
from api.index_state import index_problem
from api.source_filters import directory_matches, is_dependency_path
from tests.agent.test_index_migration import migration


@pytest.mark.parametrize("directory", [
    "vendor", "src/vendor", "node_modules", "web/node_modules",
    "bower_components", "jspm_packages", "venv", ".venv", ".tox",
    "__pypackages__/3.12/lib", "site-packages", "dist-packages",
    ".m2/repository", ".gradle/caches", "ios/Pods", "ios/Carthage/Checkouts",
    ".dart_tool", ".pub-cache", ".nuget/packages", ".cargo/registry",
    ".yarn/cache", ".yarn/unplugged",
])
def test_known_installed_dependency_paths(directory):
    assert is_dependency_path(directory)


@pytest.mark.parametrize("directory", [
    "src/vendor_api", "src/node_modules_adapter", "packages/order",
    "libs/client", "third_party/custom", ".cargo/config", ".yarn/patches",
    "Carthage/custom", "gradle/wrapper",
])
def test_owned_source_and_package_configuration_are_not_dependency_paths(directory):
    assert not is_dependency_path(directory)


def test_relative_component_matching_preserves_dot_names_and_nested_paths():
    assert directory_matches("app/.venv/lib", "./.venv/")
    assert directory_matches("app/generated/api", "generated/api")
    assert not directory_matches("app/generated_extra/api", "generated/api")
    assert not directory_matches("app/venv_config", "venv")


def test_reader_prunes_dependency_trees_and_keeps_owned_packages(tmp_path, monkeypatch):
    # A repository itself may be called vendor; only paths inside it are matched.
    root = tmp_path / "vendor"
    files = ["src/main.py", "packages/order.py", "libs/client.py", "third_party/custom.py",
             "src/vendor/dependency.py", "web/node_modules/dependency.js",
             "ios/Pods/dependency.py", "ios/Carthage/Checkouts/dependency.py",
             "venv/lib/dependency.py", "src/example.min.js", "build/generated.py"]
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def example(): return True\n", encoding="utf-8")

    visited = []
    walk = os.walk

    def recording_walk(*args, **kwargs):
        for item in walk(*args, **kwargs):
            visited.append(os.path.relpath(item[0], root))
            yield item

    monkeypatch.setattr("api.data_pipeline.os.walk", recording_walk)
    docs = read_all_documents(str(root))
    assert {d.meta_data["file_path"] for d in docs} == {
        "src/main.py", "packages/order.py", "libs/client.py", "third_party/custom.py",
    }
    assert all(not is_dependency_path(path) for path in visited)
    assert (root / "src/vendor/dependency.py").is_file()


def test_inclusion_mode_still_skips_nested_dependencies(tmp_path):
    for name in ("src/main.py", "src/vendor/dependency.py", "other.py"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("pass\n", encoding="utf-8")
    docs = read_all_documents(str(tmp_path), included_dirs=["src"])
    assert [d.meta_data["file_path"] for d in docs] == ["src/main.py"]


@pytest.mark.parametrize("signature", [None, "previous-rule-signature"])
def test_old_cache_drops_vendor_and_reuses_owned_vectors(migration, signature):
    m = migration
    database = LocalDB.load_state(str(m.path))
    database.index_dependency_filter_signature = signature
    database.get_transformed_data(key="split_and_embed").append(
        Document(text="dependency", vector=[0.2, 0.3], meta_data={"file_path": "vendor/dependency.py"})
    )
    database.save_state(str(m.path))

    # Old searchable indexes remain readable; a Reindex must update their policy.
    assert index_problem(m.path, m.repo.commit) is None
    assert index_problem(m.path, m.repo.commit, check_dependency_filter=True)
    assert m.indexer.should_reindex(m.project)
    DatabaseManager().prepare_database(m.repo.url, "gitlab", pull=True)

    updated = LocalDB.load_state(str(m.path))
    docs = updated.get_transformed_data(key="split_and_embed")
    assert {d.text for d in docs} == {"submit", "dispatch"}
    assert m.state["inputs"] == ["dispatch"]
    assert updated.reused_chunks == 1
    assert not m.indexer.should_reindex(m.project)
