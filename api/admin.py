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
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.config import (
    ADMIN_USERNAMES,
    EMBEDDER_TYPE,
    GITLAB_BATCH_GROUPS,
    GITLAB_URL,
    PERMISSION_CACHE_TTL,
)
from api.gitlab_auth import get_current_user
from api.metadata_store import get_all_indexed_projects, get_project_metadata

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/api/admin", tags=["admin"])

# ---------------------------------------------------------------------------
# Batch index status (module-level state)
# ---------------------------------------------------------------------------

_batch_status: dict = {
    "running": False,
    "operation": "",  # "reindex" | "regenerate_wiki" | "batch_index"
    "progress": {},
    "last_result": {},
    "last_run": None,
}


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class BatchIndexRequest(BaseModel):
    group_ids: Optional[List[int]] = None
    project_ids: Optional[List[int]] = None
    force: bool = False


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


def _get_configured_group_ids() -> List[int]:
    """Parse GITLAB_BATCH_GROUPS into a list of integer group IDs."""
    if not GITLAB_BATCH_GROUPS:
        return []
    return [int(g.strip()) for g in GITLAB_BATCH_GROUPS.split(",") if g.strip()]


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


# ---------------------------------------------------------------------------
# Group / project browsing endpoints
# ---------------------------------------------------------------------------


@admin_router.get("/groups")
async def get_groups(_admin: dict = Depends(require_admin)):
    """Return all GitLab groups visible to the service token."""
    from api.config import GITLAB_SERVICE_TOKEN

    if not GITLAB_URL or not GITLAB_SERVICE_TOKEN:
        raise HTTPException(
            status_code=400,
            detail="GITLAB_URL and GITLAB_SERVICE_TOKEN must be set",
        )

    results = []
    page = 1
    per_page = 100

    async with httpx.AsyncClient(verify=False) as client:
        while True:
            try:
                resp = await client.get(
                    f"{GITLAB_URL.rstrip('/')}/api/v4/groups",
                    params={
                        "per_page": per_page,
                        "page": page,
                        "order_by": "name",
                        "sort": "asc",
                    },
                    headers={"PRIVATE-TOKEN": GITLAB_SERVICE_TOKEN},
                    timeout=15.0,
                )
                if resp.status_code != 200:
                    logger.warning("Failed to fetch groups (page %d): %s", page, resp.text)
                    break

                page_data = resp.json()
                if not page_data:
                    break

                for data in page_data:
                    results.append(
                        {
                            "id": data["id"],
                            "name": data.get("name", ""),
                            "full_path": data.get("full_path", ""),
                            "description": data.get("description", ""),
                        }
                    )
                page += 1
                if page > 50:
                    break
            except Exception as exc:
                logger.error("Error fetching groups: %s", exc)
                break

    return results


@admin_router.get("/groups/{group_id}/projects")
async def get_group_projects(
    group_id: int,
    _admin: dict = Depends(require_admin),
):
    """Return all projects in a GitLab group with their index status."""
    from api.config import GITLAB_SERVICE_TOKEN

    if not GITLAB_URL or not GITLAB_SERVICE_TOKEN:
        raise HTTPException(
            status_code=400,
            detail="GITLAB_URL and GITLAB_SERVICE_TOKEN must be set",
        )

    from api.batch_indexer import BatchIndexer

    indexer = BatchIndexer(
        gitlab_url=GITLAB_URL,
        service_token=GITLAB_SERVICE_TOKEN,
        group_ids=[group_id],
    )
    projects = await indexer.list_group_projects(group_id)

    result = []
    for p in projects:
        path = p.get("path_with_namespace", "")
        meta = get_project_metadata(path)
        result.append(
            {
                "id": p.get("id"),
                "name": p.get("name", ""),
                "path_with_namespace": path,
                "last_activity_at": p.get("last_activity_at", ""),
                "is_indexed": meta is not None and meta.get("status") == "indexed",
                "index_status": meta.get("status") if meta else None,
            }
        )

    result.sort(key=lambda x: x["path_with_namespace"])
    return result


# ---------------------------------------------------------------------------
# Project search endpoint
# ---------------------------------------------------------------------------


@admin_router.get("/projects/search")
async def search_projects(
    q: str = "",
    _admin: dict = Depends(require_admin),
):
    """Search GitLab projects visible to the service token."""
    from api.config import GITLAB_SERVICE_TOKEN

    if not GITLAB_URL or not GITLAB_SERVICE_TOKEN:
        raise HTTPException(
            status_code=400,
            detail="GITLAB_URL and GITLAB_SERVICE_TOKEN must be set",
        )

    if not q.strip():
        return []

    results = []
    async with httpx.AsyncClient(verify=False) as client:
        try:
            resp = await client.get(
                f"{GITLAB_URL.rstrip('/')}/api/v4/projects",
                params={
                    "search": q.strip(),
                    "per_page": 50,
                    "page": 1,
                    "order_by": "name",
                    "sort": "asc",
                },
                headers={"PRIVATE-TOKEN": GITLAB_SERVICE_TOKEN},
                timeout=15.0,
            )
            if resp.status_code == 200:
                for data in resp.json():
                    path = data.get("path_with_namespace", "")
                    meta = get_project_metadata(path)
                    results.append(
                        {
                            "id": data["id"],
                            "name": data.get("name", ""),
                            "path_with_namespace": path,
                            "last_activity_at": data.get("last_activity_at", ""),
                            "is_indexed": meta is not None
                            and meta.get("status") == "indexed",
                            "index_status": meta.get("status") if meta else None,
                        }
                    )
        except Exception as exc:
            logger.error("Error searching projects: %s", exc)

    return results


# ---------------------------------------------------------------------------
# Batch index endpoints
# ---------------------------------------------------------------------------


async def _launch_batch_operation(
    body: Optional[BatchIndexRequest],
    operation: str,
) -> dict:
    """Shared logic to start a background batch operation.

    Args:
        body: Request body with group_ids, project_ids, force.
        operation: One of ``"batch_index"``, ``"reindex"``,
                   ``"regenerate_wiki"``.

    Returns:
        A dict with a ``message`` key on success.

    Raises:
        HTTPException on validation errors or conflict.
    """
    if _batch_status["running"]:
        raise HTTPException(
            status_code=409,
            detail=f"An operation is already running ({_batch_status.get('operation', 'unknown')})",
        )

    from api.config import GITLAB_SERVICE_TOKEN, GITLAB_URL

    if not GITLAB_URL or not GITLAB_SERVICE_TOKEN:
        raise HTTPException(
            status_code=400,
            detail="GITLAB_URL and GITLAB_SERVICE_TOKEN must be set",
        )

    selected_group_ids = (body.group_ids if body and body.group_ids else None)
    selected_project_ids = (body.project_ids if body and body.project_ids else None)
    force = body.force if body else False

    if not selected_group_ids and not selected_project_ids:
        raise HTTPException(
            status_code=400,
            detail="Please select at least one group or project",
        )

    def on_progress(info: dict) -> None:
        _batch_status["progress"] = info

    async def _run():
        from api.batch_indexer import BatchIndexer

        _batch_status["running"] = True
        _batch_status["operation"] = operation
        _batch_status["progress"] = {"status": "starting"}
        try:
            indexer = BatchIndexer(
                gitlab_url=GITLAB_URL,
                service_token=GITLAB_SERVICE_TOKEN,
                group_ids=selected_group_ids or [],
            )
            result = await indexer.run_selected(
                group_ids=selected_group_ids,
                project_ids=selected_project_ids,
                on_progress=on_progress,
                force=force,
                operation=operation,
            )
            _batch_status["last_result"] = result
            _batch_status["last_run"] = datetime.now(timezone.utc).isoformat()
        except Exception as exc:
            logger.error("Batch %s failed: %s", operation, exc)
            _batch_status["last_result"] = {"error": str(exc)}
        finally:
            _batch_status["running"] = False
            _batch_status["operation"] = ""
            _batch_status["progress"] = {}

    asyncio.create_task(_run())

    labels = {
        "batch_index": "Full index",
        "reindex": "Reindex (embedding only)",
        "regenerate_wiki": "Wiki regeneration",
    }
    return {"message": f"{labels.get(operation, operation)} started"}


@admin_router.post("/batch-index")
async def trigger_batch_index(
    body: Optional[BatchIndexRequest] = None,
    _admin: dict = Depends(require_admin),
):
    """Trigger a full batch index (embedding + wiki generation) in the background."""
    return await _launch_batch_operation(body, operation="batch_index")


@admin_router.post("/reindex")
async def trigger_reindex(
    body: Optional[BatchIndexRequest] = None,
    _admin: dict = Depends(require_admin),
):
    """Trigger reindex only (git pull + embedding) without regenerating wiki cache."""
    return await _launch_batch_operation(body, operation="reindex")


@admin_router.post("/regenerate-wiki")
async def trigger_regenerate_wiki(
    body: Optional[BatchIndexRequest] = None,
    _admin: dict = Depends(require_admin),
):
    """Trigger wiki cache regeneration only, relying on existing embeddings."""
    return await _launch_batch_operation(body, operation="regenerate_wiki")


@admin_router.get("/batch-index/status")
async def get_batch_index_status(_admin: dict = Depends(require_admin)):
    """Return the current batch operation progress/result."""
    return {
        "running": _batch_status["running"],
        "operation": _batch_status.get("operation", ""),
        "progress": _batch_status["progress"],
        "last_result": _batch_status["last_result"],
        "last_run": _batch_status.get("last_run"),
    }
