"""
Batch Indexer for GitLab Group Projects

Scans specified GitLab groups, clones repositories using a service token,
and creates embeddings for each project.

Usage:
    python -m api.batch_indexer
    python -m api.main --batch-index
"""

import asyncio
import logging
import os
import sys
from typing import Callable, Dict, List, Optional
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

# Ensure .env is loaded
load_dotenv()

from api.config import configs
from api.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


class BatchIndexer:
    """Indexes all projects under specified GitLab groups."""

    def __init__(self, gitlab_url: str, service_token: str, group_ids: List[int]):
        self.gitlab_url = gitlab_url.rstrip("/")
        self.service_token = service_token
        self.group_ids = group_ids

    async def list_group_projects(self, group_id: int) -> List[dict]:
        """
        List all projects in a GitLab group (including subgroups).
        """
        projects: List[dict] = []
        page = 1
        per_page = 100

        async with httpx.AsyncClient(verify=False) as client:
            while True:
                try:
                    resp = await client.get(
                        f"{self.gitlab_url}/api/v4/groups/{group_id}/projects",
                        params={
                            "include_subgroups": "true",
                            "per_page": per_page,
                            "page": page,
                            "archived": "false",
                        },
                        headers={"PRIVATE-TOKEN": self.service_token},
                        timeout=30.0,
                    )
                    if resp.status_code != 200:
                        logger.error(
                            "Error listing projects for group %d (page %d): %s",
                            group_id,
                            page,
                            resp.text,
                        )
                        break

                    page_data = resp.json()
                    if not page_data:
                        break

                    projects.extend(page_data)
                    page += 1

                    if page > 100:
                        logger.warning("Pagination safety limit reached for group %d", group_id)
                        break
                except Exception as exc:
                    logger.error("Error listing projects for group %d: %s", group_id, exc)
                    break

        return projects

    def should_reindex(self, project: dict) -> bool:
        """Check if a project needs (re-)indexing based on last_activity_at."""
        from api.metadata_store import needs_reindex

        path = project.get("path_with_namespace", "")
        last_activity = project.get("last_activity_at", "")
        return needs_reindex(path, last_activity)

    async def reindex_project(
        self,
        project: dict,
        on_progress: Optional[Callable[[dict], None]] = None,
        force: bool = False,
    ) -> bool:
        """
        Only do vectorisation: clone/pull -> embedding -> save pkl + metadata.

        Does **not** generate wiki cache.
        """
        from api.data_pipeline import DatabaseManager
        from api.metadata_store import set_project_metadata

        path_with_ns = project.get("path_with_namespace", "")
        project_id = project.get("id", 0)
        last_activity = project.get("last_activity_at", "")
        http_url = project.get("http_url_to_repo", "")

        if not http_url:
            logger.warning("No http_url_to_repo for project %s, skipping", path_with_ns)
            return False

        logger.info("Reindexing project: %s (id=%d)", path_with_ns, project_id)

        # When force re-indexing, remove old pkl to avoid deserialization errors
        if force:
            from api.wiki_generator import _compute_repo_dir_name
            repo_dir_name = _compute_repo_dir_name(http_url, "gitlab")
            root_path = os.path.expanduser(os.path.join("~", ".adalflow"))
            pkl_path = os.path.join(root_path, "databases", f"{repo_dir_name}.pkl")
            if os.path.exists(pkl_path):
                logger.info("Force mode: removing old database %s", pkl_path)
                os.remove(pkl_path)

        try:
            db_manager = DatabaseManager()
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: db_manager.prepare_database(
                    repo_url_or_path=http_url,
                    repo_type="gitlab",
                    access_token=self.service_token,
                    pull=True,
                ),
            )

            repo_path = quote(path_with_ns, safe="")
            set_project_metadata(
                project_path=path_with_ns,
                project_id=project_id,
                last_activity_at=last_activity,
                repo_path=repo_path,
                status="indexed",
            )

            logger.info("Successfully reindexed: %s", path_with_ns)
            return True
        except Exception as exc:
            logger.error("Failed to reindex %s: %s", path_with_ns, exc)

            from api.metadata_store import set_project_metadata as set_meta
            set_meta(
                project_path=path_with_ns,
                project_id=project_id,
                last_activity_at=last_activity,
                repo_path=quote(path_with_ns, safe=""),
                status="error",
            )
            return False

    async def regenerate_wiki(
        self,
        project: dict,
        on_progress: Optional[Callable[[dict], None]] = None,
    ) -> bool:
        """
        Only regenerate wiki cache.  Depends on existing embeddings (pkl).
        """
        path_with_ns = project.get("path_with_namespace", "")
        http_url = project.get("http_url_to_repo", "")

        if not http_url:
            logger.warning("No http_url_to_repo for project %s, skipping", path_with_ns)
            return False

        logger.info("Regenerating wiki for project: %s", path_with_ns)

        try:
            from api.wiki_generator import WikiGenerator

            if on_progress:
                on_progress({
                    "current_project": path_with_ns,
                    "status": "generating_wiki",
                })

            parts = path_with_ns.split("/")
            owner = "/".join(parts[:-1]) if len(parts) >= 2 else path_with_ns
            repo_name = parts[-1] if len(parts) >= 2 else path_with_ns

            default_provider = configs.get("default_provider", "openai")
            provider_cfg = configs.get("providers", {}).get(default_provider, {})
            default_model = provider_cfg.get("default_model", "")

            generator = WikiGenerator(
                provider=default_provider,
                model=default_model,
            )
            await generator.generate_wiki(
                repo_url=http_url,
                owner=owner,
                repo=repo_name,
                repo_type="gitlab",
                access_token=self.service_token,
            )
            logger.info("Wiki cache regenerated for %s", path_with_ns)
            return True
        except Exception as exc:
            logger.warning("Wiki regeneration failed for %s: %s", path_with_ns, exc)
            return False

    async def index_project(
        self,
        project: dict,
        on_progress: Optional[Callable[[dict], None]] = None,
        force: bool = False,
    ) -> bool:
        """
        Full pipeline: reindex (clone/pull + embedding) then regenerate wiki cache.

        This is the original combined behaviour.
        """
        success = await self.reindex_project(project, on_progress=on_progress, force=force)
        if not success:
            return False

        # --- Generate wiki cache ---
        await self.regenerate_wiki(project, on_progress=on_progress)
        # Wiki generation failure should not affect overall index status
        return True

    async def fetch_project_by_id(self, project_id: int) -> Optional[dict]:
        """Fetch a single project's info from GitLab by its ID."""
        async with httpx.AsyncClient(verify=False) as client:
            try:
                resp = await client.get(
                    f"{self.gitlab_url}/api/v4/projects/{project_id}",
                    headers={"PRIVATE-TOKEN": self.service_token},
                    timeout=30.0,
                )
                if resp.status_code == 200:
                    return resp.json()
                logger.error(
                    "Error fetching project %d: %s", project_id, resp.text
                )
            except Exception as exc:
                logger.error("Error fetching project %d: %s", project_id, exc)
        return None

    async def run_selected(
        self,
        group_ids: Optional[List[int]] = None,
        project_ids: Optional[List[int]] = None,
        on_progress: Optional[Callable[[dict], None]] = None,
        force: bool = False,
        operation: str = "batch_index",
    ) -> dict:
        """
        Run an operation on selected groups and/or individual projects.

        Args:
            group_ids: Groups whose projects should be processed.
            project_ids: Individual project IDs to process.
            on_progress: Optional progress callback.
            force: If True, skip the should_reindex check and always process.
            operation: One of ``"batch_index"`` (full), ``"reindex"`` (embedding
                       only), or ``"regenerate_wiki"`` (wiki only).

        Returns a summary dict with counts.
        """
        total = 0
        indexed = 0
        skipped = 0
        errors = 0

        # Collect projects from selected groups
        all_projects: List[dict] = []
        seen_ids: set = set()

        for gid in (group_ids or []):
            logger.info("Processing group %d ...", gid)
            projects = await self.list_group_projects(gid)
            for p in projects:
                pid = p.get("id")
                if pid not in seen_ids:
                    seen_ids.add(pid)
                    all_projects.append(p)

        # Fetch individual projects
        for pid in (project_ids or []):
            if pid not in seen_ids:
                proj = await self.fetch_project_by_id(pid)
                if proj:
                    seen_ids.add(pid)
                    all_projects.append(proj)

        grand_total = len(all_projects)
        current = 0

        for project in all_projects:
            total += 1
            current += 1
            path = project.get("path_with_namespace", "unknown")

            # For regenerate_wiki we skip the should_reindex check (wiki regen
            # doesn't depend on code freshness).
            if operation != "regenerate_wiki" and not force and not self.should_reindex(project):
                logger.info("Skipping (up-to-date): %s", path)
                skipped += 1
                if on_progress:
                    on_progress(
                        {
                            "current": current,
                            "total": grand_total,
                            "current_project": path,
                            "status": "skipped",
                        }
                    )
                continue

            status_label = {
                "batch_index": "indexing",
                "reindex": "reindexing",
                "regenerate_wiki": "generating_wiki",
            }.get(operation, "indexing")

            if on_progress:
                on_progress(
                    {
                        "current": current,
                        "total": grand_total,
                        "current_project": path,
                        "status": status_label,
                    }
                )

            # Create a sub-progress callback that preserves current/total
            def _wiki_progress(info: dict) -> None:
                if on_progress:
                    on_progress({
                        "current": current,
                        "total": grand_total,
                        **info,
                    })

            # Dispatch to the right method
            if operation == "reindex":
                success = await self.reindex_project(project, on_progress=_wiki_progress, force=force)
            elif operation == "regenerate_wiki":
                success = await self.regenerate_wiki(project, on_progress=_wiki_progress)
            else:
                success = await self.index_project(project, on_progress=_wiki_progress, force=force)

            if success:
                indexed += 1
            else:
                errors += 1

        summary = {
            "total_projects": total,
            "indexed": indexed,
            "skipped": skipped,
            "errors": errors,
        }
        logger.info("Batch %s (selected) complete: %s", operation, summary)
        return summary

    async def run(
        self, on_progress: Optional[Callable[[dict], None]] = None
    ) -> dict:
        """
        Main entry point: iterate groups -> list projects -> index each.

        Args:
            on_progress: Optional callback invoked after each project with
                         a dict like {"current": n, "total": total,
                         "current_project": path, "status": "indexing"}.

        Returns a summary dict with counts.
        """
        total = 0
        indexed = 0
        skipped = 0
        errors = 0

        # First pass: collect all projects to know the total count
        all_projects = []
        for group_id in self.group_ids:
            logger.info("Processing group %d ...", group_id)
            projects = await self.list_group_projects(group_id)
            logger.info("Found %d projects in group %d", len(projects), group_id)
            all_projects.extend(projects)

        grand_total = len(all_projects)
        current = 0

        for project in all_projects:
            total += 1
            current += 1
            path = project.get("path_with_namespace", "unknown")

            if not self.should_reindex(project):
                logger.info("Skipping (up-to-date): %s", path)
                skipped += 1
                if on_progress:
                    on_progress(
                        {
                            "current": current,
                            "total": grand_total,
                            "current_project": path,
                            "status": "skipped",
                        }
                    )
                continue

            if on_progress:
                on_progress(
                    {
                        "current": current,
                        "total": grand_total,
                        "current_project": path,
                        "status": "indexing",
                    }
                )

            # Create a sub-progress callback that preserves current/total
            def _wiki_progress(info: dict) -> None:
                if on_progress:
                    on_progress({
                        "current": current,
                        "total": grand_total,
                        **info,
                    })

            success = await self.index_project(project, on_progress=_wiki_progress)
            if success:
                indexed += 1
            else:
                errors += 1

        summary = {
            "total_projects": total,
            "indexed": indexed,
            "skipped": skipped,
            "errors": errors,
        }
        logger.info("Batch indexing complete: %s", summary)
        return summary


async def main():
    """CLI entry point for batch indexing."""
    from api.config import GITLAB_BATCH_GROUPS, GITLAB_SERVICE_TOKEN, GITLAB_URL

    if not GITLAB_URL:
        logger.error("GITLAB_URL is not set")
        sys.exit(1)

    if not GITLAB_SERVICE_TOKEN:
        logger.error("GITLAB_SERVICE_TOKEN is not set")
        sys.exit(1)

    if not GITLAB_BATCH_GROUPS:
        logger.error("GITLAB_BATCH_GROUPS is not set")
        sys.exit(1)

    group_ids = [int(g.strip()) for g in GITLAB_BATCH_GROUPS.split(",") if g.strip()]
    if not group_ids:
        logger.error("No valid group IDs in GITLAB_BATCH_GROUPS")
        sys.exit(1)

    logger.info("Starting batch indexer for groups: %s", group_ids)
    indexer = BatchIndexer(
        gitlab_url=GITLAB_URL,
        service_token=GITLAB_SERVICE_TOKEN,
        group_ids=group_ids,
    )
    await indexer.run()


if __name__ == "__main__":
    asyncio.run(main())
