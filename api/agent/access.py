"""Bridge existing GitLab authorization into every agent conversation."""

import asyncio
from pathlib import Path

from fastapi import HTTPException, Request

from .repositories import project_path, resolve_repository


async def current_user(request: Request) -> dict:
    from api.gitlab_auth import get_current_user

    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(401, "Not authenticated")
    return await get_current_user(header[7:])


def owner_id(user: dict) -> str:
    value = user.get("gitlab_user_id")
    if value is None:
        raise HTTPException(401, "GitLab identity required")
    return str(value)


class Access:
    def __init__(self, data_root: Path):
        self.data_root = data_root

    async def check(self, projects: list[str], user: dict):
        from api.config import GITLAB_URL
        from api.gitlab_permission import check_repo_access

        token = user.get("gitlab_access_token")
        if not GITLAB_URL or not token:
            raise HTTPException(
                403, "A GitLab SSO session with repository access is required."
            )
        # Check every repository, including global/additional repositories.
        denied = []
        for project in projects:
            if not await check_repo_access(
                token, project, GITLAB_URL, user.get("gitlab_user_id")
            ):
                denied.append(project)
        if denied:
            raise HTTPException(403, "No GitLab access to: " + ", ".join(denied))

    async def create_scope(self, selected: list[str], user: dict) -> list[dict]:
        from api.config import GITLAB_URL
        from api.metadata_store import get_all_indexed_projects

        if not GITLAB_URL:
            raise HTTPException(503, "Configure GitLab SSO before using Ask Agent.")
        try:
            projects = list(
                dict.fromkeys(project_path(value, GITLAB_URL) for value in selected)
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not projects:
            raise HTTPException(422, "Select at least one indexed repository.")
        await self.check(projects, user)
        metadata = get_all_indexed_projects()
        not_indexed = [
            project
            for project in projects
            if metadata.get(project, {}).get("status") != "indexed"
        ]
        if not_indexed:
            raise HTTPException(
                409, "These repositories are not indexed: " + ", ".join(not_indexed)
            )

        def resolve_selected():
            repos, failures = [], []
            for project in projects:
                try:
                    repos.append(
                        resolve_repository(project, GITLAB_URL, self.data_root).public()
                    )
                except ValueError as exc:
                    failures.append(str(exc))
            if failures:
                raise ValueError(
                    "Selected repositories cannot be read:\n"
                    + "\n".join(f"- {failure}" for failure in failures)
                    + "\nReindex these repositories, or remove them from Search Scope."
                )
            return repos

        try:
            repos = await asyncio.to_thread(resolve_selected)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return repos

    async def readers(self, scope: list[dict], user: dict):
        from api.config import GITLAB_URL

        await self.check([r["project"] for r in scope], user)
        try:
            return await asyncio.to_thread(
                lambda: [
                    resolve_repository(
                        r["project"], GITLAB_URL, self.data_root, r["commit"]
                    )
                    for r in scope
                ]
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
