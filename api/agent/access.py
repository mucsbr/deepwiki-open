"""Bridge existing GitLab authorization into every agent conversation."""

import asyncio
import logging
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import HTTPException, Request

from .repositories import project_path
from .snapshots import available_repository

logger = logging.getLogger(__name__)


async def current_user(request: Request) -> dict:
    from api.gitlab_auth import get_current_user

    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(401, "Not authenticated")
    return await get_current_user(header[7:])


def owner_id(user: dict) -> str:
    if user.get("public_ask"):
        return "public:" + user["visitor_owner"]
    value = user.get("gitlab_user_id")
    if value is None:
        raise HTTPException(401, "GitLab identity required")
    return str(value)


def scoped_user(session: dict, user: dict) -> dict:
    if user.get("public_ask"):
        return {**user, "public_groups": session.get("public_groups", [])}
    return user


class Access:
    def __init__(self, data_root: Path):
        from api.ask_updates import AskUpdateQueue

        self.data_root = data_root
        self.updates = AskUpdateQueue(data_root)

    async def start(self):
        await self.updates.start()

    async def close(self):
        await self.updates.close()

    async def check(self, projects: list[str], user: dict):
        if user.get("public_ask"):
            from api.public_ask import check_public_scope

            check_public_scope(projects, user.get("public_groups", []))
            return
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
            if metadata.get(project, {}).get("status") not in {"indexed", "partial", "indexing", "error"}
        ]
        if not_indexed:
            raise HTTPException(
                409, "These repositories are not indexed: " + ", ".join(not_indexed)
            )

        def resolve_selected():
            repos, failures = [], []
            for project in projects:
                try:
                    repo = available_repository(project, GITLAB_URL, self.data_root)
                    if metadata[project]["status"] in {"indexing", "error"} and not repo.index_snapshot:
                        raise ValueError(f"{project}: no previously published index is available.")
                    repos.append(repo.public())
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
                    available_repository(
                        r["project"], GITLAB_URL, self.data_root,
                        commit=r["commit"], snapshot=r.get("index_snapshot"),
                    )
                    for r in scope
                ]
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    async def remote_versions(self, projects: list[str], user: dict) -> dict[str, dict]:
        """Read current default-branch commits using the requester's GitLab session."""
        from api.config import GITLAB_URL

        if user.get("public_ask"):
            from api.config import GITLAB_SERVICE_TOKEN

            if not GITLAB_SERVICE_TOKEN:
                raise HTTPException(503, "Configure GitLab service token to refresh published code.")
            headers = {"PRIVATE-TOKEN": GITLAB_SERVICE_TOKEN}
        else:
            headers = {"Authorization": f"Bearer {user['gitlab_access_token']}"}
        semaphore = asyncio.Semaphore(5)

        async with httpx.AsyncClient(verify=False, timeout=20.0) as client:

            async def get_version(name: str) -> tuple[str, dict]:
                async with semaphore:
                    project_url = (
                        f"{GITLAB_URL.rstrip('/')}/api/v4/projects/"
                        f"{quote(name, safe='')}"
                    )
                    try:
                        response = await client.get(project_url, headers=headers)
                        if response.status_code != 200:
                            raise HTTPException(
                                502,
                                f"Cannot check current code for {name} "
                                f"(GitLab HTTP {response.status_code}).",
                            )
                        project = response.json()
                        branch = project.get("default_branch")
                        if not branch:
                            raise HTTPException(
                                409, f"{name} has no default branch to analyze."
                            )
                        branch_url = (
                            f"{project_url}/repository/branches/{quote(branch, safe='')}"
                        )
                        response = await client.get(branch_url, headers=headers)
                        if response.status_code != 200:
                            raise HTTPException(
                                502,
                                f"Cannot check current branch for {name} "
                                f"(GitLab HTTP {response.status_code}).",
                            )
                        revision = response.json().get("commit", {}).get("id", "")
                        if len(revision) not in {40, 64} or any(
                            char not in "0123456789abcdef" for char in revision
                        ):
                            raise HTTPException(
                                502, f"GitLab returned an invalid commit for {name}."
                            )
                        clone_url = project.get("http_url_to_repo") or (
                            f"{GITLAB_URL.rstrip('/')}/{name}.git"
                        )
                        if project_path(clone_url, GITLAB_URL) != name:
                            raise HTTPException(
                                502, f"GitLab returned an invalid clone URL for {name}."
                            )
                        return name, {
                            "commit": revision,
                            "id": project.get("id", 0),
                            "last_activity_at": project.get("last_activity_at", ""),
                            "http_url_to_repo": clone_url,
                        }
                    except httpx.HTTPError as exc:
                        raise HTTPException(
                            502, f"Cannot reach GitLab to check {name}."
                        ) from exc
                    except ValueError as exc:
                        raise HTTPException(
                            502, f"GitLab returned invalid project data for {name}."
                        ) from exc

            return dict(await asyncio.gather(*(get_version(name) for name in projects)))

    async def refresh_readers(self, scope: list[dict], user: dict, on_progress=None):
        """Read locally available source now; queue daily network work separately."""
        from api.config import GITLAB_SERVICE_TOKEN, GITLAB_URL

        projects = [item["project"] for item in scope]
        await self.check(projects, user)
        try:
            readers = await asyncio.to_thread(lambda: [
                available_repository(name, GITLAB_URL, self.data_root) for name in projects
            ])
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        phases = {name: "local" for name in projects}
        if GITLAB_SERVICE_TOKEN:
            try:
                phases = await asyncio.to_thread(self.updates.request, projects)
            except Exception as exc:
                logger.warning("Could not schedule Ask repository updates (%s)", type(exc).__name__)
        if on_progress:
            for name, phase in phases.items():
                on_progress(name, phase)
        return readers
