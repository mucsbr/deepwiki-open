"""
GitLab Repository Permission Checking Module

Provides:
- check_repo_access: check if a user has access to a specific project
- get_user_accessible_projects: list all projects a user can access
- verify_repo_permission: FastAPI Dependency for endpoint protection
- In-memory cache with configurable TTL
"""

import logging
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx
from fastapi import Depends, HTTPException, Query, Request

from api.config import GITLAB_URL, PERMISSION_CACHE_TTL
from api.gitlab_auth import get_current_user

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory permission cache
# Key format: "{gitlab_user_id}:{project_path}"
# Value: (has_access: bool, timestamp: float)
# ---------------------------------------------------------------------------

_permission_cache: Dict[str, Tuple[bool, float]] = {}


def _cache_key(user_id: int, project_path: str) -> str:
    return f"{user_id}:{project_path}"


def _get_cached(user_id: int, project_path: str) -> Optional[bool]:
    key = _cache_key(user_id, project_path)
    entry = _permission_cache.get(key)
    if entry is None:
        return None
    has_access, ts = entry
    if time.time() - ts > PERMISSION_CACHE_TTL:
        del _permission_cache[key]
        return None
    return has_access


def _set_cached(user_id: int, project_path: str, has_access: bool) -> None:
    key = _cache_key(user_id, project_path)
    _permission_cache[key] = (has_access, time.time())


def clear_user_cache(user_id: int) -> None:
    """Remove all cached entries for a given user (e.g. on permission change event)."""
    prefix = f"{user_id}:"
    keys_to_delete = [k for k in _permission_cache if k.startswith(prefix)]
    for k in keys_to_delete:
        del _permission_cache[k]


# ---------------------------------------------------------------------------
# Core permission functions
# ---------------------------------------------------------------------------


async def check_repo_access(
    gitlab_token: str,
    project_path: str,
    gitlab_url: str,
    user_id: int | None = None,
) -> bool:
    """
    Check if the user (identified by their OAuth token) has access to the project.

    Calls GET {gitlab_url}/api/v4/projects/{encoded_path} with the user's token.
    200 = access, 403/404 = no access.
    """
    # Check cache first
    if user_id is not None:
        cached = _get_cached(user_id, project_path)
        if cached is not None:
            logger.debug("Permission cache hit for user %s project %s: %s", user_id, project_path, cached)
            return cached

    encoded_path = quote(project_path, safe="")
    url = f"{gitlab_url}/api/v4/projects/{encoded_path}"

    try:
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.get(
                url,
                headers={"PRIVATE-TOKEN": gitlab_token},
                timeout=10.0,
            )
            has_access = resp.status_code == 200
    except Exception as exc:
        logger.error("Error checking repo access for %s: %s", project_path, exc)
        has_access = False

    # Update cache
    if user_id is not None:
        _set_cached(user_id, project_path, has_access)

    return has_access


async def get_user_accessible_projects(
    gitlab_token: str,
    gitlab_url: str,
) -> List[dict]:
    """
    Return all projects the user has access to (membership=true).
    Handles pagination automatically.
    """
    projects: List[dict] = []
    page = 1
    per_page = 100

    async with httpx.AsyncClient(verify=False) as client:
        while True:
            try:
                resp = await client.get(
                    f"{gitlab_url}/api/v4/projects",
                    params={
                        "min_access_level": 10,
                        "per_page": per_page,
                        "page": page,
                    },
                    headers={"PRIVATE-TOKEN": gitlab_token},
                    timeout=30.0,
                )
                if resp.status_code != 200:
                    logger.error("Error listing projects (page %d): %s", page, resp.text)
                    break

                page_data = resp.json()
                if not page_data:
                    break

                projects.extend(page_data)
                page += 1

                # Safety limit
                if page > 50:
                    logger.warning("Stopped pagination at page 50")
                    break
            except Exception as exc:
                logger.error("Error listing projects: %s", exc)
                break

    return projects


# ---------------------------------------------------------------------------
# FastAPI Dependencies
# ---------------------------------------------------------------------------


async def verify_repo_permission(
    owner: str = Query(...),
    repo: str = Query(...),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """
    FastAPI dependency that verifies the current user has access to the
    specified repository on GitLab. Raises 403 if access is denied.

    Expects owner and repo as query parameters.
    The project path is constructed as '{owner}/{repo}'.
    """
    project_path = f"{owner}/{repo}"
    gitlab_token = current_user.get("gitlab_access_token", "")
    user_id = current_user.get("gitlab_user_id")

    has_access = await check_repo_access(
        gitlab_token=gitlab_token,
        project_path=project_path,
        gitlab_url=GITLAB_URL,
        user_id=user_id,
    )

    if not has_access:
        raise HTTPException(
            status_code=403,
            detail=f"You do not have access to {project_path}",
        )

    return current_user


async def verify_repo_permission_from_body(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """
    Alternative dependency for POST endpoints where owner/repo are in the body.
    Reads the JSON body to extract repo info.
    """
    try:
        body = await request.json()
        repo_info = body.get("repo", {})
        owner = repo_info.get("owner", "")
        repo = repo_info.get("repo", "")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body")

    if not owner or not repo:
        raise HTTPException(status_code=400, detail="Missing owner/repo in request")

    project_path = f"{owner}/{repo}"
    gitlab_token = current_user.get("gitlab_access_token", "")
    user_id = current_user.get("gitlab_user_id")

    has_access = await check_repo_access(
        gitlab_token=gitlab_token,
        project_path=project_path,
        gitlab_url=GITLAB_URL,
        user_id=user_id,
    )

    if not has_access:
        raise HTTPException(
            status_code=403,
            detail=f"You do not have access to {project_path}",
        )

    return current_user
