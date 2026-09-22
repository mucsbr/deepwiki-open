"""
Wiki Embedder

Builds embedding databases from wiki cache so that Ask (Q&A) can retrieve
relevant wiki paragraphs alongside code snippets.

The resulting pkl is stored at ``~/.adalflow/databases/{repo_name}_wiki.pkl``
and loaded by ``RAG.prepare_wiki_retriever()`` / ``MultiRepoRAG.prepare_multi_wiki_retriever()``.
"""

import json
import logging
import os
from typing import List, Optional

from adalflow.core.types import Document
from adalflow.utils import get_adalflow_default_root_path

logger = logging.getLogger(__name__)

_ADALFLOW_ROOT = get_adalflow_default_root_path()
_WIKICACHE_DIR = os.path.join(_ADALFLOW_ROOT, "wikicache")
_DATABASES_DIR = os.path.join(_ADALFLOW_ROOT, "databases")


def _find_wiki_cache(project_path: str) -> Optional[dict]:
    """Locate and load wiki cache JSON for *project_path* (e.g. ``group/repo``).

    Mirrors the logic in ``insight_extractor._find_wiki_cache`` but is
    kept here to avoid circular imports.
    """
    if not os.path.isdir(_WIKICACHE_DIR):
        return None

    parts = project_path.split("/")
    owner = "/".join(parts[:-1]) if len(parts) > 1 else parts[0]
    repo = parts[-1] if len(parts) > 1 else parts[0]
    safe_owner = owner.replace("/", "--")

    for repo_type in ("gitlab", "github", "bitbucket"):
        for lang in ("en", "zh", "ja"):
            filename = f"deepwiki_cache_{repo_type}_{safe_owner}_{repo}_{lang}.json"
            cache_path = os.path.join(_WIKICACHE_DIR, filename)
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    continue
    return None


def _wiki_cache_to_documents(cache: dict) -> List[Document]:
    """Convert wiki cache pages into a list of ``Document`` objects.

    Each wiki page becomes one Document whose ``text`` is the page title
    followed by its content.  ``meta_data`` carries ``source: "wiki"`` so
    that consumers can distinguish wiki chunks from code chunks.
    """
    documents: List[Document] = []
    pages = cache.get("generated_pages", {})

    for page_id, page in pages.items():
        title = page.get("title", "")
        content = page.get("content", "")
        if not content:
            continue

        text = f"# {title}\n\n{content}" if title else content
        doc = Document(
            text=text,
            meta_data={
                "source": "wiki",
                "page_title": title,
                "page_id": page_id,
            },
        )
        documents.append(doc)

    return documents


def get_wiki_pkl_path(repo_url: str, repo_type: str = "gitlab") -> str:
    """Compute the path for the wiki embedding pkl.

    Uses the same dir-name logic as ``_compute_repo_dir_name`` from
    ``wiki_generator`` but appends ``_wiki`` to avoid collisions with code pkls.
    """
    from api.wiki_generator import _compute_repo_dir_name

    repo_dir_name = _compute_repo_dir_name(repo_url, repo_type)
    return os.path.join(_DATABASES_DIR, f"{repo_dir_name}_wiki.pkl")


def get_wiki_pkl_path_from_project(project_path: str) -> str:
    """Compute the wiki pkl path from a project path like ``group/repo``.

    Mimics the naming convention used by ``DatabaseManager`` for code pkls.
    """
    safe_name = project_path.replace("/", "_")
    return os.path.join(_DATABASES_DIR, f"{safe_name}_wiki.pkl")


def build_wiki_embeddings(project_path: str) -> Optional[str]:
    """Build wiki embeddings for a project and save to a pkl file.

    Args:
        project_path: Project identifier, e.g. ``"group/repo"``.

    Returns:
        The path to the saved pkl file, or ``None`` on failure.
    """
    cache = _find_wiki_cache(project_path)
    if not cache:
        logger.info("No wiki cache found for %s, skipping wiki embeddings", project_path)
        return None

    documents = _wiki_cache_to_documents(cache)
    if not documents:
        logger.warning("Wiki cache for %s has no page content", project_path)
        return None

    logger.info(
        "Building wiki embeddings for %s: %d pages -> %d documents",
        project_path, len(cache.get("generated_pages", {})), len(documents),
    )

    pkl_path = get_wiki_pkl_path_from_project(project_path)

    try:
        from api.data_pipeline import transform_documents_and_save_to_db

        transform_documents_and_save_to_db(documents, pkl_path)
        logger.info("Wiki embeddings saved to %s", pkl_path)
        return pkl_path
    except Exception as exc:
        logger.error("Failed to build wiki embeddings for %s: %s", project_path, exc)
        return None
