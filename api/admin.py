"""
Admin API Module

Provides admin-only endpoints for system overview, project management,
batch indexing control, and configuration viewing.

All endpoints require admin privileges (ADMIN_USERNAMES whitelist).
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Callable, Optional

from fastapi import APIRouter, Depends, HTTPException

from api.config import (
    ADMIN_USERNAMES,
    EMBEDDER_TYPE,
    GITLAB_BATCH_GROUPS,
    GITLAB_URL,
    PERMISSION_CACHE_TTL,
)
from api.gitlab_auth import get_current_user
from api.metadata_store import get_all_indexed_projects

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/api/admin", tags=["admin"])

# ---------------------------------------------------------------------------
# Batch index status (module-level state)
# ---------------------------------------------------------------------------

_batch_status: dict = {
    "running": False,
    "progress": {},
    "last_result": {},
    "last_run": None,
}


# ---------------------------------------------------------------------------
# Admin dependency
# ---------------------------------------------------------------------------


async def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """Require the current user to be in the ADMIN_USERNAMES whitelist."""
    if current_user["username"] not in ADMIN_USERNAMES:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


# ---------------------------------------------------------------------------
# Helper: calculate directory size in MB
# ---------------------------------------------------------------------------


def _dir_size_mb(path: str) -> float:
    """Return total size of a directory in megabytes."""
    total = 0
    if not os.path.isdir(path):
        return 0.0
    for dirpath, _dirnames, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return round(total / (1024 * 1024), 2)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@admin_router.get("/stats")
async def get_stats(_admin: dict = Depends(require_admin)):
    """Return system overview statistics."""
    adalflow_root = os.path.expanduser(os.path.join("~", ".adalflow"))

    projects = get_all_indexed_projects()
    status_counts: dict[str, int] = {}
    for meta in projects.values():
        s = meta.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    # Count wiki cache files
    wikicache_dir = os.path.join(adalflow_root, "wikicache")
    wiki_cache_count = 0
    if os.path.isdir(wikicache_dir):
        wiki_cache_count = len(
            [f for f in os.listdir(wikicache_dir) if f.endswith(".json")]
        )

    # Disk usage
    disk_usage = {
        "repos_mb": _dir_size_mb(os.path.join(adalflow_root, "repos")),
        "databases_mb": _dir_size_mb(os.path.join(adalflow_root, "databases")),
        "wikicache_mb": _dir_size_mb(wikicache_dir),
    }

    return {
        "total_indexed_projects": len(projects),
        "status_counts": status_counts,
        "total_wiki_caches": wiki_cache_count,
        "disk_usage": disk_usage,
        "last_batch_run": _batch_status.get("last_run"),
    }


@admin_router.get("/projects")
async def get_projects(_admin: dict = Depends(require_admin)):
    """Return all indexed projects with metadata."""
    projects = get_all_indexed_projects()
    result = []
    for path, meta in projects.items():
        result.append(
            {
                "path": path,
                "project_id": meta.get("project_id"),
                "status": meta.get("status", "unknown"),
                "indexed_at": meta.get("indexed_at", ""),
                "last_activity_at": meta.get("last_activity_at", ""),
                "repo_path": meta.get("repo_path", ""),
            }
        )
    # Sort by indexed_at descending
    result.sort(key=lambda x: x.get("indexed_at", ""), reverse=True)
    return result


@admin_router.get("/config")
async def get_config(_admin: dict = Depends(require_admin)):
    """Return sanitized system configuration (no secrets)."""
    return {
        "gitlab_url": GITLAB_URL or "(not set)",
        "embedder_type": EMBEDDER_TYPE,
        "batch_groups": GITLAB_BATCH_GROUPS or "(not set)",
        "permission_cache_ttl": PERMISSION_CACHE_TTL,
        "admin_usernames": ADMIN_USERNAMES,
    }


@admin_router.post("/batch-index")
async def trigger_batch_index(_admin: dict = Depends(require_admin)):
    """Trigger a batch index run in the background."""
    if _batch_status["running"]:
        raise HTTPException(status_code=409, detail="Batch index is already running")

    from api.config import GITLAB_BATCH_GROUPS, GITLAB_SERVICE_TOKEN, GITLAB_URL

    if not GITLAB_URL or not GITLAB_SERVICE_TOKEN or not GITLAB_BATCH_GROUPS:
        raise HTTPException(
            status_code=400,
            detail="Batch indexing requires GITLAB_URL, GITLAB_SERVICE_TOKEN, and GITLAB_BATCH_GROUPS to be set",
        )

    group_ids = [
        int(g.strip()) for g in GITLAB_BATCH_GROUPS.split(",") if g.strip()
    ]
    if not group_ids:
        raise HTTPException(status_code=400, detail="No valid group IDs configured")

    # Progress callback
    def on_progress(info: dict) -> None:
        _batch_status["progress"] = info

    # Launch background task
    async def _run_batch():
        from api.batch_indexer import BatchIndexer

        _batch_status["running"] = True
        _batch_status["progress"] = {"status": "starting"}
        try:
            indexer = BatchIndexer(
                gitlab_url=GITLAB_URL,
                service_token=GITLAB_SERVICE_TOKEN,
                group_ids=group_ids,
            )
            result = await indexer.run(on_progress=on_progress)
            _batch_status["last_result"] = result
            _batch_status["last_run"] = datetime.now(timezone.utc).isoformat()
        except Exception as exc:
            logger.error("Batch index failed: %s", exc)
            _batch_status["last_result"] = {"error": str(exc)}
        finally:
            _batch_status["running"] = False
            _batch_status["progress"] = {}

    asyncio.create_task(_run_batch())
    return {"message": "Batch index started"}


@admin_router.get("/batch-index/status")
async def get_batch_index_status(_admin: dict = Depends(require_admin)):
    """Return the current batch index progress/result."""
    return {
        "running": _batch_status["running"],
        "progress": _batch_status["progress"],
        "last_result": _batch_status["last_result"],
        "last_run": _batch_status.get("last_run"),
    }
