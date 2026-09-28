"""Explicit, bounded tools layered over DeepWiki's existing code and indexes."""

import asyncio
import json
import re
from pathlib import Path

from langchain_core.tools import tool

from .prompts import FLOW_GUIDE
from .repositories import IndexSearch, SourceReader
from .wiki import WikiReader


def make_tools(
    reader: SourceReader,
    data_root: Path,
    store,
    run: dict,
    authorize,
    *,
    wiki: WikiReader | None = None,
):
    index = IndexSearch(reader, data_root)
    wiki = wiki or WikiReader(reader, data_root)
    session_id = run["session_id"]

    async def call(function, *args):
        # Recheck permissions for every invocation. The existing permission cache
        # bounds network traffic; no service-account token is used here.
        await authorize()
        try:
            value = await asyncio.to_thread(function, *args)
            return json.dumps(value, ensure_ascii=False)
        except (ValueError, TimeoutError) as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        except Exception:  # noqa: BLE001 -- external provider errors must not disclose credentials
            # Provider/storage errors can include endpoint credentials. Do not
            # put raw exceptions into model context, events or responses.
            return json.dumps(
                {
                    "error": "Tool unavailable. Try exact source search, or report this gap."
                }
            )

    @tool
    async def list_repositories() -> str:
        """Understand the selected repositories from existing Wiki titles and descriptions.

        Use when you do not know which repo owns a feature. Includes Wiki availability
        and current source revisions; use get_wiki_summary for a repo's page directory.
        """
        return await call(
            lambda: [
                {**r.public(), "wiki": wiki.overview(r.project)}
                for r in reader.repos.values()
            ]
        )

    @tool
    async def list_source_files(repo: str, pattern: str = "*", offset: int = 0) -> str:
        """List source paths in a repository. Pattern is a glob; paginate with next_offset."""
        return await call(reader.list_files, repo, pattern, offset)

    @tool
    async def search_source(query: str, repo: str = "", limit: int = 30) -> str:
        """Find a known literal symbol, API route, message topic or UI label in source.

        This is case-insensitive literal search, not regex or semantic search. Use
        search_index when you only know a business concept or behavior, not its code
        name. Empty repo searches selected repos only; prefer a relevant repo once known.
        """
        return await call(reader.search, query, repo, limit)

    @tool
    async def read_source(
        repo: str, path: str, start_line: int = 1, end_line: int = 160
    ) -> str:
        """Read revision-pinned source with actual line numbers and citation URL. Maximum 200 lines per call."""
        return await call(reader.read, repo, path, start_line, end_line)

    @tool
    async def search_index(query: str, repo: str = "", source: str = "code") -> str:
        """Locate code by meaning using the already-built CODE vector index.

        Use natural-language behavior/business concepts when file and symbol names
        are unknown (for example: how a WeChat media task is submitted). Empty repo
        searches the selected repos together. Requires a query embedding from the
        configured embedding service, but does not reindex code. Read matching
        files with read_source;
        a weak or empty result is not proof the feature is absent. Normally leave
        source='code'. Legacy source='wiki' performs JSON keyword search, not vector
        search; prefer search_wiki/get_wiki_summary/get_wiki_page for documentation.
        """
        def search():
            if source == "wiki":
                return wiki.search(query, repo)
            if source != "code":
                raise ValueError("Use source=code, or search_wiki for Wiki JSON.")
            return index.search(query, repo)

        return await call(search)

    @tool
    async def get_wiki_summary(repo: str, offset: int = 0) -> str:
        """Read existing Wiki overview and page directory, with source-file pointers.

        Use to understand a relevant repository before guessing its implementation
        names. No Wiki generation or embedding call. Read relevant page IDs with
        get_wiki_page; cached documentation must be checked against current source.
        """
        return await call(wiki.summary, repo, offset)

    @tool
    async def get_wiki_page(repo: str, page_id: str, offset: int = 0) -> str:
        """Read a Wiki JSON page by the ID from get_wiki_summary or search_wiki.

        Returns documentation, source-file pointers and related page IDs. Follow
        next_offset for longer pages, then read_source to verify implementation.
        """
        return await call(wiki.page, repo, page_id, offset)

    @tool
    async def search_wiki(query: str, repo: str = "") -> str:
        """Find existing Wiki pages by literal keywords in their titles and content.

        Use a phrase or space-separated terms such as '微信 新媒体' to find domain
        descriptions and source-file pointers. This reads JSON, not a Wiki vector
        index. Empty repo searches selected repos; search_index searches CODE by meaning.
        """
        return await call(wiki.search, query, repo)

    @tool
    async def load_flow_guide() -> str:
        """Load the investigation method for a business flow. Use for multi-step flow analysis, not simple lookups."""
        return FLOW_GUIDE

    @tool
    async def save_document(name: str, content: str) -> str:
        """Save a requested Markdown report in this conversation (never in source). Reusing a name updates it."""
        await authorize()
        if (
            not re.fullmatch(
                r"[\w\-\u4e00-\u9fff][\w\-\u4e00-\u9fff .]{0,99}\.md", name
            )
            or ".." in name
        ):
            return "Use a plain Markdown filename such as refund-flow.md, without directories."
        if not content.strip() or len(content.encode()) > 200_000:
            return "Document must contain 1–200000 bytes."
        scope = "\n".join(
            f"- {r.project} @ `{r.commit}`" for r in reader.repos.values()
        )
        document = f"<!-- DeepWiki source snapshot; generated analysis, not runtime verification -->\n\n{content}\n\n---\n\nSource revisions:\n{scope}\n"
        store.save_document(session_id, name, document)
        store.event(run["id"], "document", {"name": name})
        return json.dumps({"saved": name, "location": "conversation documents"})

    return [
        list_repositories,
        list_source_files,
        search_source,
        read_source,
        search_index,
        get_wiki_summary,
        get_wiki_page,
        search_wiki,
        load_flow_guide,
        save_document,
    ]
