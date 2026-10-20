"""Keep a cheap, immutable index view while another process publishes a new one."""

from dataclasses import replace
from functools import lru_cache
import os
from pathlib import Path
import re
import tempfile

from .repositories import resolve_repository

SNAPSHOT_ID = re.compile(r"[0-9a-f]+(?:-[0-9a-f]+){3}\.pkl\Z")


@lru_cache(maxsize=256)
def index_revision(path: str) -> str | None:
    # Snapshot filenames identify an immutable inode, not a mutable repository.
    from adalflow.core.db import LocalDB

    import api.ollama_patch  # noqa: F401 -- legacy serialized transformers

    database = LocalDB.load_state(path)
    from api.index_state import valid_vector

    if not any(valid_vector(getattr(doc, "vector", None)) for doc in
               database.get_transformed_data(key="split_and_embed") or []):
        raise ValueError("The index has no usable embeddings.")
    return getattr(database, "source_revision", None)


def capture_index(data_root: Path, clone_name: str) -> Path | None:
    source = data_root / "databases" / f"{clone_name}.pkl"
    if not source.is_file() or source.is_symlink():
        return None
    directory = source.parent / "ask-snapshots" / clone_name
    directory.mkdir(parents=True, exist_ok=True)
    # The publisher atomically replaces the PKL. Link first, then identify the
    # linked inode, so replacement between stat() and link() cannot mix versions.
    fd, temporary = tempfile.mkstemp(prefix=".capture-", dir=directory)
    os.close(fd)
    os.unlink(temporary)
    try:
        os.link(source, temporary, follow_symlinks=False)
        if Path(temporary).is_symlink():
            raise ValueError("Index snapshots must be regular files.")
        stat = os.stat(temporary)
        target = directory / f"{stat.st_dev:x}-{stat.st_ino:x}-{stat.st_size:x}-{stat.st_mtime_ns:x}.pkl"
        try:
            os.link(temporary, target)
        except FileExistsError:
            pass
        return target
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def available_repository(project, gitlab_url, data_root, *, commit=None, snapshot=None):
    """Use the last published index's source, not an in-progress clone's HEAD.

    Legacy tasks may have only a commit. Preserve that source even when their
    original index is no longer available; semantic search will report the gap.
    """
    repo = resolve_repository(project, gitlab_url, data_root, commit)
    if snapshot:
        if not SNAPSHOT_ID.fullmatch(snapshot):
            raise ValueError(f"{project}: invalid index snapshot identifier.")
        path = data_root / "databases" / "ask-snapshots" / repo.root.name / snapshot
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"{project}: saved index snapshot is unavailable.")
    else:
        path = capture_index(data_root, repo.root.name)
    if path is None:
        return repo
    try:
        revision = index_revision(str(path))
    except Exception:
        # Source tools remain usable if an old index is corrupt/incompatible.
        # IndexSearch reports the semantic-search gap without blocking the answer.
        return repo
    if revision and not commit:
        repo = resolve_repository(project, gitlab_url, data_root, revision)
    return replace(repo, index_snapshot=path.name)
