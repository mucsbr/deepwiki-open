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
from api.product_manager import (
    list_products as pm_list_products,
    get_product as pm_get_product,
    create_product as pm_create_product,
    update_product as pm_update_product,
    delete_product as pm_delete_product,
)

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


class ProductCreateRequest(BaseModel):
    id: str
    name: str
    description: str = ""
    repos: List[str] = []


class ProductUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    repos: Optional[List[str]] = None


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


def _build_wiki_cache_lookup() -> dict[str, dict]:
    """Scan wiki cache directory and build lookup by owner/repo path."""
    adalflow_root = os.path.expanduser(os.path.join("~", ".adalflow"))
    wikicache_dir = os.path.join(adalflow_root, "wikicache")
    lookup: dict[str, dict] = {}
    if not os.path.isdir(wikicache_dir):
        return lookup
    for filename in os.listdir(wikicache_dir):
        if not (filename.startswith("deepwiki_cache_") and filename.endswith(".json")):
            continue
        parts = filename.replace("deepwiki_cache_", "").replace(".json", "").split("_")
        if len(parts) >= 4:
            owner = parts[1].replace("--", "/")
            language = parts[-1]
            repo = "_".join(parts[2:-1])
            path = f"{owner}/{repo}"
            if path not in lookup:
                lookup[path] = {"has_cache": True, "languages": []}
            if language not in lookup[path]["languages"]:
                lookup[path]["languages"].append(language)
    return lookup


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
    wiki_lookup = _build_wiki_cache_lookup()

    status_counts: dict[str, int] = {}
    indexed_without_wiki = 0
    for path, meta in projects.items():
        s = meta.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1
        if s == "indexed" and path not in wiki_lookup:
            indexed_without_wiki += 1

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
        "indexed_without_wiki": indexed_without_wiki,
        "disk_usage": disk_usage,
        "last_batch_run": _batch_status.get("last_run"),
    }


@admin_router.get("/projects")
async def get_projects(_admin: dict = Depends(require_admin)):
    """Return all indexed projects with metadata."""
    projects = get_all_indexed_projects()
    wiki_lookup = _build_wiki_cache_lookup()
    result = []
    for path, meta in projects.items():
        wiki_info = wiki_lookup.get(path, {})
        result.append(
            {
                "path": path,
                "project_id": meta.get("project_id"),
                "status": meta.get("status", "unknown"),
                "indexed_at": meta.get("indexed_at", ""),
                "last_activity_at": meta.get("last_activity_at", ""),
                "repo_path": meta.get("repo_path", ""),
                "has_wiki_cache": wiki_info.get("has_cache", False),
                "wiki_languages": wiki_info.get("languages", []),
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


# ---------------------------------------------------------------------------
# Update detection endpoint
# ---------------------------------------------------------------------------


@admin_router.get("/check-updates")
async def check_updates(_admin: dict = Depends(require_admin)):
    """Compare GitLab last_activity_at with stored metadata for all indexed projects.

    Returns a dict mapping project_path to update info:
    ``{ "stored": "...", "current": "...", "needs_update": bool }``
    """
    from api.config import GITLAB_SERVICE_TOKEN

    if not GITLAB_URL or not GITLAB_SERVICE_TOKEN:
        raise HTTPException(
            status_code=400,
            detail="GITLAB_URL and GITLAB_SERVICE_TOKEN must be set",
        )

    projects = get_all_indexed_projects()
    if not projects:
        return {}

    # Collect project_ids that have a valid id
    id_to_path: dict[int, str] = {}
    for path, meta in projects.items():
        pid = meta.get("project_id")
        if pid:
            id_to_path[int(pid)] = path

    if not id_to_path:
        return {}

    result: dict[str, dict] = {}

    async with httpx.AsyncClient(verify=False) as client:
        for pid, path in id_to_path.items():
            stored_activity = projects[path].get("last_activity_at", "")
            try:
                resp = await client.get(
                    f"{GITLAB_URL.rstrip('/')}/api/v4/projects/{pid}",
                    headers={"PRIVATE-TOKEN": GITLAB_SERVICE_TOKEN},
                    timeout=15.0,
                )
                if resp.status_code == 200:
                    current_activity = resp.json().get("last_activity_at", "")
                    result[path] = {
                        "stored": stored_activity,
                        "current": current_activity,
                        "needs_update": stored_activity != current_activity,
                    }
                else:
                    result[path] = {
                        "stored": stored_activity,
                        "current": None,
                        "needs_update": False,
                    }
            except Exception as exc:
                logger.warning("Failed to check update for %s: %s", path, exc)
                result[path] = {
                    "stored": stored_activity,
                    "current": None,
                    "needs_update": False,
                }

    return result


# ---------------------------------------------------------------------------
# Single project operation endpoints
# ---------------------------------------------------------------------------


@admin_router.post("/projects/{project_path:path}/reindex")
async def reindex_single_project(
    project_path: str,
    _admin: dict = Depends(require_admin),
):
    """Reindex a single project (git pull + re-embedding)."""
    if _batch_status["running"]:
        raise HTTPException(
            status_code=409,
            detail=f"An operation is already running ({_batch_status.get('operation', 'unknown')})",
        )

    from api.config import GITLAB_SERVICE_TOKEN

    if not GITLAB_URL or not GITLAB_SERVICE_TOKEN:
        raise HTTPException(
            status_code=400,
            detail="GITLAB_URL and GITLAB_SERVICE_TOKEN must be set",
        )

    meta = get_project_metadata(project_path)
    if not meta or not meta.get("project_id"):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_path}")

    project_id = int(meta["project_id"])

    from api.batch_indexer import BatchIndexer

    indexer = BatchIndexer(
        gitlab_url=GITLAB_URL,
        service_token=GITLAB_SERVICE_TOKEN,
        group_ids=[],
    )
    project_info = await indexer.fetch_project_by_id(project_id)
    if not project_info:
        raise HTTPException(status_code=404, detail=f"Could not fetch project from GitLab: {project_path}")

    def on_progress(info: dict) -> None:
        _batch_status["progress"] = info

    async def _run():
        _batch_status["running"] = True
        _batch_status["operation"] = "reindex"
        _batch_status["progress"] = {"status": "starting", "current_project": project_path}
        try:
            success = await indexer.reindex_project(project_info, on_progress=on_progress, force=True)
            _batch_status["last_result"] = {"project": project_path, "success": success}
            _batch_status["last_run"] = datetime.now(timezone.utc).isoformat()
        except Exception as exc:
            logger.error("Single reindex failed for %s: %s", project_path, exc)
            _batch_status["last_result"] = {"project": project_path, "error": str(exc)}
        finally:
            _batch_status["running"] = False
            _batch_status["operation"] = ""
            _batch_status["progress"] = {}

    asyncio.create_task(_run())
    return {"message": f"Reindex started for {project_path}"}


@admin_router.post("/projects/{project_path:path}/regenerate-wiki")
async def regenerate_wiki_single_project(
    project_path: str,
    _admin: dict = Depends(require_admin),
):
    """Regenerate wiki cache for a single project."""
    if _batch_status["running"]:
        raise HTTPException(
            status_code=409,
            detail=f"An operation is already running ({_batch_status.get('operation', 'unknown')})",
        )

    from api.config import GITLAB_SERVICE_TOKEN

    if not GITLAB_URL or not GITLAB_SERVICE_TOKEN:
        raise HTTPException(
            status_code=400,
            detail="GITLAB_URL and GITLAB_SERVICE_TOKEN must be set",
        )

    meta = get_project_metadata(project_path)
    if not meta or not meta.get("project_id"):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_path}")

    project_id = int(meta["project_id"])

    from api.batch_indexer import BatchIndexer

    indexer = BatchIndexer(
        gitlab_url=GITLAB_URL,
        service_token=GITLAB_SERVICE_TOKEN,
        group_ids=[],
    )
    project_info = await indexer.fetch_project_by_id(project_id)
    if not project_info:
        raise HTTPException(status_code=404, detail=f"Could not fetch project from GitLab: {project_path}")

    def on_progress(info: dict) -> None:
        _batch_status["progress"] = info

    async def _run():
        _batch_status["running"] = True
        _batch_status["operation"] = "regenerate_wiki"
        _batch_status["progress"] = {"status": "starting", "current_project": project_path}
        try:
            success = await indexer.regenerate_wiki(project_info, on_progress=on_progress)
            _batch_status["last_result"] = {"project": project_path, "success": success}
            _batch_status["last_run"] = datetime.now(timezone.utc).isoformat()
        except Exception as exc:
            logger.error("Single wiki regen failed for %s: %s", project_path, exc)
            _batch_status["last_result"] = {"project": project_path, "error": str(exc)}
        finally:
            _batch_status["running"] = False
            _batch_status["operation"] = ""
            _batch_status["progress"] = {}

    asyncio.create_task(_run())
    return {"message": f"Wiki regeneration started for {project_path}"}


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


# ---------------------------------------------------------------------------
# Repository relations endpoints
# ---------------------------------------------------------------------------


class RelationsAnalyzeRequest(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None


@admin_router.get("/repo-relations")
async def get_repo_relations(_admin: dict = Depends(require_admin)):
    """Return the cached repository relations graph."""
    from api.repo_relations import load_relations, generate_mermaid_graph

    data = load_relations()
    data["mermaid"] = generate_mermaid_graph(data)
    return data


@admin_router.post("/repo-relations/analyze")
async def trigger_relation_analysis(
    body: Optional[RelationsAnalyzeRequest] = None,
    _admin: dict = Depends(require_admin),
):
    """Trigger async relation analysis across all indexed repos."""
    from api.repo_relations import analyze_all_relations, get_analysis_status

    status = get_analysis_status()
    if status["running"]:
        raise HTTPException(status_code=409, detail="Analysis already running")

    from api.config import configs
    default_provider = configs.get("default_provider", "openai")
    provider = (body.provider if body and body.provider else default_provider)
    model = body.model if body else None

    async def _run():
        try:
            await analyze_all_relations(provider=provider, model=model)
        except Exception as exc:
            logger.error("Relation analysis background task failed: %s", exc)

    asyncio.create_task(_run())
    return {"message": "Relation analysis started"}


@admin_router.get("/repo-relations/status")
async def get_relation_analysis_status(_admin: dict = Depends(require_admin)):
    """Return the current relation analysis status."""
    from api.repo_relations import get_analysis_status

    return get_analysis_status()


# ---------------------------------------------------------------------------
# Product management endpoints
# ---------------------------------------------------------------------------


@admin_router.get("/products")
async def list_products_endpoint(_admin: dict = Depends(require_admin)):
    """Return all defined products."""
    return pm_list_products()


@admin_router.post("/products")
async def create_product_endpoint(
    body: ProductCreateRequest,
    _admin: dict = Depends(require_admin),
):
    """Create a new product."""
    try:
        return pm_create_product(
            product_id=body.id,
            name=body.name,
            description=body.description,
            repos=body.repos,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@admin_router.put("/products/{product_id}")
async def update_product_endpoint(
    product_id: str,
    body: ProductUpdateRequest,
    _admin: dict = Depends(require_admin),
):
    """Update an existing product."""
    try:
        return pm_update_product(
            product_id=product_id,
            name=body.name,
            description=body.description,
            repos=body.repos,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@admin_router.delete("/products/{product_id}")
async def delete_product_endpoint(
    product_id: str,
    _admin: dict = Depends(require_admin),
):
    """Delete a product."""
    try:
        pm_delete_product(product_id)
        return {"message": f"Product '{product_id}' deleted"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------------------------------------------------------------------------
# Insight extraction endpoints
# ---------------------------------------------------------------------------

# Module-level status for insight extraction
_insight_status: dict = {
    "running": False,
    "progress": "",
    "error": None,
}


@admin_router.post("/projects/{project_path:path}/extract-insights")
async def extract_single_project_insights(
    project_path: str,
    _admin: dict = Depends(require_admin),
):
    """Extract structured insights for a single project."""
    if _insight_status["running"]:
        raise HTTPException(status_code=409, detail="Insight extraction already running")

    async def _run():
        _insight_status["running"] = True
        _insight_status["progress"] = f"Extracting insights for {project_path}..."
        _insight_status["error"] = None
        try:
            from api.insight_extractor import extract_project_insights
            await extract_project_insights(project_path)
            _insight_status["progress"] = f"Done: {project_path}"
        except Exception as exc:
            logger.error("Insight extraction failed for %s: %s", project_path, exc)
            _insight_status["error"] = str(exc)
        finally:
            _insight_status["running"] = False

    asyncio.create_task(_run())
    return {"message": f"Insight extraction started for {project_path}"}


@admin_router.post("/products/{product_id}/extract-insights")
async def extract_product_insights(
    product_id: str,
    _admin: dict = Depends(require_admin),
):
    """Extract structured insights for all repos in a product."""
    if _insight_status["running"]:
        raise HTTPException(status_code=409, detail="Insight extraction already running")

    product = pm_get_product(product_id)
    if not product:
        raise HTTPException(status_code=404, detail=f"Product '{product_id}' not found")

    repos = product.get("repos", [])
    if not repos:
        raise HTTPException(status_code=400, detail="Product has no repositories")

    async def _run():
        _insight_status["running"] = True
        _insight_status["error"] = None
        try:
            from api.insight_extractor import extract_project_insights
            for i, repo_path in enumerate(repos):
                _insight_status["progress"] = (
                    f"Extracting [{i + 1}/{len(repos)}]: {repo_path}"
                )
                try:
                    await extract_project_insights(repo_path)
                except Exception as exc:
                    logger.error(
                        "Insight extraction failed for %s: %s (continuing)", repo_path, exc
                    )
            _insight_status["progress"] = f"Done: {len(repos)} repos"
        except Exception as exc:
            logger.error("Product insight extraction failed: %s", exc)
            _insight_status["error"] = str(exc)
        finally:
            _insight_status["running"] = False

    asyncio.create_task(_run())
    return {"message": f"Insight extraction started for product '{product_id}' ({len(repos)} repos)"}


@admin_router.get("/insights/status")
async def get_insight_extraction_status(_admin: dict = Depends(require_admin)):
    """Return the current insight extraction status."""
    return dict(_insight_status)
