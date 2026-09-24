"""Bridge existing GitLab authorization into every agent conversation."""

import asyncio
from pathlib import Path
from urllib.parse import quote

import httpx
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
        self._refresh_locks: dict[str, asyncio.Lock] = {}

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

    async def remote_versions(self, projects: list[str], user: dict) -> dict[str, dict]:
        """Read current default-branch commits using the requester's GitLab session."""
        from api.config import GITLAB_URL

        token = user["gitlab_access_token"]
        headers = {"Authorization": f"Bearer {token}"}
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
        """Refresh changed selected repos and their indexes before a new question."""
        from api.batch_indexer import BatchIndexer
        from api.config import GITLAB_SERVICE_TOKEN, GITLAB_URL

        projects = [item["project"] for item in scope]
        await self.check(projects, user)
        if on_progress:
            on_progress("", "checking")
        remote = await self.remote_versions(projects, user)
        readers = []
        for name in projects:
            async with self._refresh_locks.setdefault(name, asyncio.Lock()):
                if on_progress:
                    on_progress(name, "checking")
                try:
                    current = await asyncio.to_thread(
                        resolve_repository, name, GITLAB_URL, self.data_root
                    )
                except ValueError:
                    current = None
                index_path = (
                    self.data_root / "databases" / f"{name.replace('/', '_')}.pkl"
                )
                expected = remote[name]["commit"]
                if (
                    current is None
                    or current.commit != expected
                    or not index_path.is_file()
                ):
                    if on_progress:
                        on_progress(name, "updating")
                    if not GITLAB_SERVICE_TOKEN:
                        raise HTTPException(
                            503, "Configure GitLab service token to refresh code."
                        )
                    indexer = BatchIndexer(GITLAB_URL, GITLAB_SERVICE_TOKEN, [])
                    project = {
                        "path_with_namespace": name,
                        **{
                            key: value
                            for key, value in remote[name].items()
                            if key != "commit"
                        },
                    }
                    success = await indexer.reindex_project(project)
                    if not success:
                        raise HTTPException(
                            409, f"Could not update code and index for {name}."
                        )
                    try:
                        current = await asyncio.to_thread(
                            resolve_repository, name, GITLAB_URL, self.data_root
                        )
                    except ValueError as exc:
                        raise HTTPException(
                            409, f"Updated clone for {name} is unreadable: {exc}"
                        ) from exc
                    if current.commit != expected:
                        raise HTTPException(
                            409, f"{name} changed while refreshing. Retry this question."
                        )
                readers.append(current)
                if on_progress:
                    on_progress(name, "ready")
        return readers
