"""
Index Metadata Store

Manages metadata about indexed (vectorized) projects.
Stored as JSON at ~/.adalflow/metadata/index_metadata.json
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from adalflow.utils import get_adalflow_default_root_path

logger = logging.getLogger(__name__)

METADATA_DIR = os.path.join(get_adalflow_default_root_path(), "metadata")
METADATA_FILE = os.path.join(METADATA_DIR, "index_metadata.json")


def _ensure_dir() -> None:
    os.makedirs(METADATA_DIR, exist_ok=True)


def _load() -> dict:
    _ensure_dir()
    if not os.path.exists(METADATA_FILE):
        return {"projects": {}}
    try:
        with open(METADATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("Failed to load metadata: %s", e)
        return {"projects": {}}


def _save(data: dict) -> None:
    _ensure_dir()
    try:
        with open(METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error("Failed to save metadata: %s", e)


def get_all_indexed_projects() -> Dict[str, dict]:
    """Return all indexed project entries."""
    return _load().get("projects", {})


def get_project_metadata(project_path: str) -> Optional[dict]:
    """Return metadata for a specific project path (e.g. 'group/project')."""
    return _load().get("projects", {}).get(project_path)


def set_project_metadata(
    project_path: str,
    project_id: int,
    last_activity_at: str,
    repo_path: str,
    status: str = "indexed",
) -> None:
    """Create or update metadata for a project."""
    data = _load()
    data.setdefault("projects", {})[project_path] = {
        "project_id": project_id,
        "last_activity_at": last_activity_at,
        "indexed_at": datetime.now(timezone.utc).isoformat(),
        "repo_path": repo_path,
        "status": status,
    }
    _save(data)


def remove_project_metadata(project_path: str) -> None:
    """Remove metadata for a project."""
    data = _load()
    data.get("projects", {}).pop(project_path, None)
    _save(data)


def get_indexed_project_paths() -> List[str]:
    """Return a list of all indexed project path_with_namespace values."""
    return list(_load().get("projects", {}).keys())


def is_project_indexed(project_path: str) -> bool:
    """Check if a project has been indexed."""
    meta = get_project_metadata(project_path)
    return meta is not None and meta.get("status") == "indexed"


def needs_reindex(project_path: str, last_activity_at: str) -> bool:
    """
    Check if a project needs re-indexing by comparing last_activity_at
    timestamps. Always re-index if the previous attempt resulted in an error.
    """
    meta = get_project_metadata(project_path)
    if meta is None:
        return True
    if meta.get("status") == "error":
        return True
    stored = meta.get("last_activity_at", "")
    return stored != last_activity_at
