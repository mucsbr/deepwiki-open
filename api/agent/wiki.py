"""Read the platform's existing Wiki JSON within the selected repository scope.

Wiki is cached reference material, not a second embedding database and not
evidence of the current source revision. No generation or indexing happens here.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .repositories import SourceReader

NOTICE = (
    "Existing generated Wiki; its source revision is unknown and it may be stale. "
    "Use its file paths and symbols as leads, then verify current code with read_source."
)
PAGE_CHARS = 12000


class WikiReader:
    def __init__(self, reader: SourceReader, data_root: Path, language: str = "en"):
        self.reader = reader
        self.directory = data_root / "wikicache"
        self.language = language
        self.cache: dict[str, dict | None] = {}

    def _load(self, project: str) -> dict | None:
        self.reader.repository(project)
        if project in self.cache:
            return self.cache[project]
        self.cache[project] = None
        if not self.directory.is_dir():
            return None
        owner, repo = project.rsplit("/", 1)
        prefix = f"deepwiki_cache_gitlab_{owner.replace('/', '--')}_{repo}_"
        paths = {}
        try:
            files = list(self.directory.iterdir())
        except OSError:
            return None
        for path in files:
            if not path.name.startswith(prefix) or path.suffix != ".json":
                continue
            language = path.name[len(prefix) : -5]
            if re.fullmatch(r"[A-Za-z]+(?:-[A-Za-z]+)*", language):
                paths[language] = path
        languages = dict.fromkeys([self.language, "en", "zh", *sorted(paths)])
        for language in languages:
            path = paths.get(language)
            if not path or path.is_symlink() or not path.is_file():
                continue
            try:
                stat = path.stat()
                if stat.st_size > 8 * 1024 * 1024:
                    continue
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                identity = data.get("repo")
                if identity and (
                    not isinstance(identity, dict)
                    or f"{identity.get('owner')}/{identity.get('repo')}" != project
                    or identity.get("type", "gitlab") != "gitlab"
                ):
                    continue
                structure = data.get("wiki_structure") or {}
                pages = data.get("generated_pages") or {}
                if not isinstance(structure, dict) or not isinstance(pages, dict):
                    continue
                # Return only documentation fields, never cached provider/token metadata.
                value = {
                    "language": language,
                    "cache_updated_at": datetime.fromtimestamp(
                        stat.st_mtime, timezone.utc
                    ).isoformat(),
                    "title": str(structure.get("title") or ""),
                    "description": str(structure.get("description") or ""),
                    "pages": {
                        pid: page
                        for pid, page in pages.items()
                        if isinstance(page, dict)
                    },
                }
                self.cache[project] = value
                return value
            except (OSError, ValueError):
                continue
        return None

    def overview(self, project: str, description_chars: int = 400) -> dict:
        wiki = self._load(project)
        if wiki is None:
            return {"available": False}
        return {
            "available": True,
            "language": wiki["language"],
            "title": wiki["title"][:150],
            "description": wiki["description"][:description_chars],
            "page_count": len(wiki["pages"]),
            "cache_updated_at": wiki["cache_updated_at"],
        }

    def prompt_context(self) -> str:
        # Reuse existing descriptions without generating another per-repository file.
        size = max(1, len(self.reader.repos))
        chars = min(240, 6000 // size)
        rows = []
        for project in self.reader.repos:
            overview = self.overview(project, chars)
            rows.append(
                {
                    "repo": project,
                    "wiki_available": overview["available"],
                    "description": overview.get("description")
                    or overview.get("title", "")[:chars],
                }
            )
        return json.dumps(rows, ensure_ascii=False)

    def summary(self, project: str, offset: int = 0) -> dict:
        wiki = self._load(project)
        if wiki is None:
            return {
                "repo": project,
                "available": False,
                "notice": "No readable Wiki JSON. Use search_index, README and source files.",
            }
        pages = list(wiki["pages"].items())
        offset = max(0, offset)
        return {
            "repo": project,
            **self.overview(project, 2000),
            "pages": [
                {
                    "id": pid,
                    "title": page.get("title", pid),
                    "file_paths": page.get("filePaths", []),
                }
                for pid, page in pages[offset : offset + 50]
            ],
            "next_offset": offset + 50 if len(pages) > offset + 50 else None,
            "notice": NOTICE,
        }

    def page(self, project: str, page_id: str, offset: int = 0) -> dict:
        wiki = self._load(project)
        if wiki is None:
            raise ValueError(
                "No readable Wiki JSON. Use search_index and source files."
            )
        page = wiki["pages"].get(page_id)
        if page is None:
            raise ValueError(
                "Unknown Wiki page ID. Use get_wiki_summary to list pages."
            )
        content = str(page.get("content") or "")
        offset = max(0, offset)
        return {
            "repo": project,
            "page_id": page_id,
            "language": wiki["language"],
            "cache_updated_at": wiki["cache_updated_at"],
            "title": page.get("title", page_id),
            "file_paths": page.get("filePaths", []),
            "related_pages": page.get("relatedPages", []),
            "content": content[offset : offset + PAGE_CHARS],
            "offset": offset,
            "next_offset": offset + PAGE_CHARS
            if len(content) > offset + PAGE_CHARS
            else None,
            "notice": NOTICE,
        }

    def search(self, query: str, project: str = "") -> dict:
        if not query.strip() or len(query) > 2000:
            raise ValueError(
                "Use 1–2000 characters of Wiki keywords, separated by spaces."
            )
        projects = [project] if project else list(self.reader.repos)
        terms = list(dict.fromkeys(query.casefold().split()))
        matches, unavailable = [], []
        for name in projects:
            wiki = self._load(name)
            if wiki is None:
                unavailable.append(name)
                continue
            for pid, page in wiki["pages"].items():
                title = str(page.get("title") or pid)
                content = str(page.get("content") or "")
                folded = content.casefold()
                matched = [
                    term for term in terms if term in title.casefold() or term in folded
                ]
                if not matched:
                    continue
                # A generic title hit must not outrank a page covering all of the
                # user's domain terms (e.g. WeChat + media + task).
                score = (len(matched), sum(term in title.casefold() for term in terms))
                anchor = next(
                    (folded.find(term) for term in terms if term in folded), 0
                )
                start = max(0, anchor - 200)
                matches.append(
                    (
                        score,
                        {
                            "repo": name,
                            "page_id": pid,
                            "title": title,
                            "language": wiki["language"],
                            "file_paths": page.get("filePaths", []),
                            "matched_keywords": matched,
                            "text": content[start : start + 1600],
                        },
                    )
                )
        matches.sort(key=lambda item: item[0], reverse=True)
        return {
            "search_mode": "keyword",
            "matches": [item[1] for item in matches[:8]],
            "truncated": len(matches) > 8,
            "unavailable_repositories": unavailable,
            "notice": f"JSON Wiki keyword search, not vector search. {NOTICE}",
        }
