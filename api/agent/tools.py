"""Explicit, bounded tools layered over DeepWiki's existing code and indexes."""

import asyncio
import json
import re
from pathlib import Path

from langchain_core.tools import tool

from .prompts import FLOW_GUIDE
from .repositories import IndexSearch, SourceReader


def make_tools(reader: SourceReader, data_root: Path, store, run: dict, authorize):
    index = IndexSearch(reader, data_root)
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
        """List authorized repositories and source revisions for this question."""
        return await call(lambda: [r.public() for r in reader.repos.values()])

    @tool
    async def list_source_files(repo: str, pattern: str = "*", offset: int = 0) -> str:
        """List source paths in a repository. Pattern is a glob; paginate with next_offset."""
        return await call(reader.list_files, repo, pattern, offset)

    @tool
    async def search_source(query: str, repo: str = "", limit: int = 30) -> str:
        """Find exact literal names, routes, states or topics in source. Empty repo searches selected repos only."""
        return await call(reader.search, query, repo, limit)

    @tool
    async def read_source(
        repo: str, path: str, start_line: int = 1, end_line: int = 160
    ) -> str:
        """Read revision-pinned source with actual line numbers and citation URL. Maximum 200 lines per call."""
        return await call(reader.read, repo, path, start_line, end_line)

    @tool
    async def search_index(query: str, repo: str = "", source: str = "code") -> str:
        """Search existing code or wiki vector indexes. Results are candidates; confirm claims using read_source."""
        return await call(index.search, query, repo, source)

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
        load_flow_guide,
        save_document,
    ]
