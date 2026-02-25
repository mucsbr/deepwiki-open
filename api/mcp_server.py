"""
DeepWiki MCP Server

Exposes DeepWiki's code understanding capabilities as MCP tools,
allowing external agents (Claude Code, Codex, OpenClaw, Dify) to
leverage project knowledge for technical decision-making.

Product-level tools aggregate data across multiple repositories,
enabling cross-repo analysis for questions like "how to make
product X agent-ready".
"""

import json
import logging
import os
from typing import Optional

from mcp.server.fastmcp import FastMCP

from api.product_manager import get_product, list_products as pm_list_products
from api.metadata_store import get_all_indexed_projects
from api.repo_relations import load_relations

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "DeepWiki",
    instructions=(
        "DeepWiki provides code understanding and project knowledge. "
        "Use these tools to explore indexed repositories, search code, "
        "read wiki documentation, and get structured project insights. "
        "Product-level tools aggregate data across multiple repositories."
    ),
    stateless_http=True,
    json_response=True,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ADALFLOW_ROOT = os.path.expanduser(os.path.join("~", ".adalflow"))
_WIKICACHE_DIR = os.path.join(_ADALFLOW_ROOT, "wikicache")


def _get_default_provider() -> str:
    """Read default LLM provider from configs (generator.json)."""
    from api.config import configs
    return configs.get("default_provider", "openai")


def _get_default_model() -> str:
    """Read default model for the configured provider."""
    from api.config import configs
    provider = _get_default_provider()
    provider_cfg = configs.get("providers", {}).get(provider, {})
    return provider_cfg.get("default_model", "")


async def _call_llm(prompt: str, label: str = "") -> str:
    """Call LLM using the configured provider, reusing wiki_generator logic."""
    from api.wiki_generator import _call_llm_inner
    provider = _get_default_provider()
    model = _get_default_model()
    return await _call_llm_inner(provider, model, prompt, label)


def _find_wiki_cache(owner: str, repo: str, language: str = "en") -> Optional[dict]:
    """Locate and load a wiki cache file for a given owner/repo.

    Tries common repo types (gitlab, github, bitbucket) and the
    requested language, falling back to English.
    """
    if not os.path.isdir(_WIKICACHE_DIR):
        return None
    safe_owner = owner.replace("/", "--")
    for repo_type in ("gitlab", "github", "bitbucket"):
        for lang in (language, "en"):
            filename = f"deepwiki_cache_{repo_type}_{safe_owner}_{repo}_{lang}.json"
            cache_path = os.path.join(_WIKICACHE_DIR, filename)
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    continue
    return None


def _split_project_path(project_path: str):
    """Split 'owner/repo' into (owner, repo)."""
    parts = project_path.split("/")
    if len(parts) < 2:
        return parts[0], parts[0]
    repo = parts[-1]
    owner = "/".join(parts[:-1])
    return owner, repo


def _build_wiki_cache_lookup() -> dict:
    """Scan wiki cache directory to build lookup by project path."""
    lookup: dict = {}
    if not os.path.isdir(_WIKICACHE_DIR):
        return lookup
    for filename in os.listdir(_WIKICACHE_DIR):
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


def _get_gitlab_url(project_path: str) -> str:
    """Construct a GitLab-style URL for a project path."""
    from api.config import GITLAB_URL
    base = GITLAB_URL.rstrip("/") if GITLAB_URL else "https://gitlab.com"
    return f"{base}/{project_path}"


# ---------------------------------------------------------------------------
# Product-level tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_products() -> str:
    """List all defined products with their repos and description.

    Products are logical groupings of repositories that represent a
    complete software system or platform.
    """
    products = pm_list_products()
    if not products:
        return json.dumps({"products": [], "message": "No products defined. Use the admin UI to create products."})
    return json.dumps({"products": products}, ensure_ascii=False)


@mcp.tool()
def get_product_overview(product_id: str, language: str = "en") -> str:
    """Get an aggregated overview of a product across all its repositories.

    Returns product description, list of repos with their wiki summaries,
    and inter-repo dependency graph.

    Args:
        product_id: Product identifier (e.g. 'bas')
        language: Wiki language code (default 'en')
    """
    product = get_product(product_id)
    if not product:
        return json.dumps({"error": f"Product '{product_id}' not found"})

    repos_info = []
    for repo_path in product.get("repos", []):
        owner, repo = _split_project_path(repo_path)
        cache = _find_wiki_cache(owner, repo, language)
        info: dict = {"path": repo_path}
        if cache:
            ws = cache.get("wiki_structure", {})
            info["title"] = ws.get("title", "")
            info["description"] = ws.get("description", "")
            pages = cache.get("generated_pages", {})
            info["page_count"] = len(pages)
            info["page_titles"] = [p.get("title", k) for k, p in pages.items()]
        else:
            info["title"] = repo_path
            info["description"] = "(no wiki cache available)"
            info["page_count"] = 0
            info["page_titles"] = []
        repos_info.append(info)

    # Get inter-repo relations
    relations_data = load_relations()
    repo_set = set(product.get("repos", []))
    cross_repo_edges = [
        e for e in relations_data.get("edges", [])
        if e.get("from") in repo_set and e.get("to") in repo_set
    ]

    return json.dumps({
        "product_id": product_id,
        "name": product.get("name", ""),
        "description": product.get("description", ""),
        "total_repos": len(product.get("repos", [])),
        "repos": repos_info,
        "cross_repo_dependencies": cross_repo_edges,
    }, ensure_ascii=False)


@mcp.tool()
async def search_product_code(product_id: str, query: str, top_k: int = 10) -> str:
    """Search code across ALL repositories in a product using semantic search.

    Results include the source repo for each snippet, enabling cross-repo
    code understanding.

    Args:
        product_id: Product identifier (e.g. 'bas')
        query: Natural language search query
        top_k: Number of results (default 10)
    """
    product = get_product(product_id)
    if not product:
        return json.dumps({"error": f"Product '{product_id}' not found"})

    repos = product.get("repos", [])
    if not repos:
        return json.dumps({"error": "Product has no repositories"})

    # Build repo URLs
    repo_urls = [_get_gitlab_url(r) for r in repos]

    try:
        from api.multi_rag import MultiRepoRAG
        from api.config import GITLAB_SERVICE_TOKEN

        provider = _get_default_provider()
        model = _get_default_model()
        rag = MultiRepoRAG(provider=provider, model=model)
        rag.prepare_multi_retriever(
            repo_urls=repo_urls,
            repo_type="gitlab",
            access_token=GITLAB_SERVICE_TOKEN or None,
        )
        results = rag(query)

        snippets = []
        if results and len(results) > 0 and hasattr(results[0], 'documents'):
            for doc in results[0].documents[:top_k]:
                meta = getattr(doc, 'meta_data', {}) or {}
                snippets.append({
                    "file_path": meta.get("file_path", "unknown"),
                    "source_repo": meta.get("source_repo", "unknown"),
                    "content": getattr(doc, 'text', '')[:500],
                })

        return json.dumps({
            "product_id": product_id,
            "query": query,
            "total_results": len(snippets),
            "results": snippets,
        }, ensure_ascii=False)
    except Exception as e:
        logger.error("search_product_code failed: %s", e)
        return json.dumps({"error": str(e)})


@mcp.tool()
async def ask_product(product_id: str, question: str) -> str:
    """Ask a question about a product across all its repositories.

    Uses multi-repo RAG to search code from all repos and generate an answer.
    This is the main tool for understanding cross-repo behavior and
    architecture of a product.

    Args:
        product_id: Product identifier (e.g. 'bas')
        question: Your question about the product
    """
    product = get_product(product_id)
    if not product:
        return json.dumps({"error": f"Product '{product_id}' not found"})

    repos = product.get("repos", [])
    if not repos:
        return json.dumps({"error": "Product has no repositories"})

    repo_urls = [_get_gitlab_url(r) for r in repos]

    try:
        from api.multi_rag import MultiRepoRAG
        from api.config import GITLAB_SERVICE_TOKEN

        provider = _get_default_provider()
        model = _get_default_model()
        rag = MultiRepoRAG(provider=provider, model=model)
        rag.prepare_multi_retriever(
            repo_urls=repo_urls,
            repo_type="gitlab",
            access_token=GITLAB_SERVICE_TOKEN or None,
        )

        # Retrieve relevant documents
        results = rag(question)
        contexts = []
        if results and len(results) > 0 and hasattr(results[0], 'documents'):
            for doc in results[0].documents[:10]:
                meta = getattr(doc, 'meta_data', {}) or {}
                contexts.append(
                    f"[{meta.get('source_repo', 'unknown')}] "
                    f"{meta.get('file_path', 'unknown')}:\n"
                    f"{getattr(doc, 'text', '')[:800]}"
                )

        # Generate answer using configured LLM
        context_text = "\n\n---\n\n".join(contexts) if contexts else "(no relevant code found)"
        prompt = (
            f"You are a code expert for the product '{product.get('name', product_id)}'.\n"
            f"Product description: {product.get('description', 'N/A')}\n"
            f"Repositories: {', '.join(repos)}\n\n"
            f"Based on the following code context, answer the question.\n\n"
            f"## Code Context\n{context_text}\n\n"
            f"## Question\n{question}\n\n"
            f"Provide a detailed, structured answer in markdown."
        )

        answer = await _call_llm(prompt, label="ask_product")

        return json.dumps({
            "product_id": product_id,
            "question": question,
            "answer": answer,
            "sources_count": len(contexts),
        }, ensure_ascii=False)

    except Exception as e:
        logger.error("ask_product failed: %s", e)
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Repository-level tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_projects() -> str:
    """List all indexed projects with their status and wiki availability.

    Returns project paths, index status, and whether wiki cache exists.
    """
    projects = get_all_indexed_projects()
    wiki_lookup = _build_wiki_cache_lookup()

    result = []
    for path, meta in projects.items():
        wiki_info = wiki_lookup.get(path, {})
        result.append({
            "path": path,
            "status": meta.get("status", "unknown"),
            "indexed_at": meta.get("indexed_at", ""),
            "has_wiki_cache": wiki_info.get("has_cache", False),
            "wiki_languages": wiki_info.get("languages", []),
        })

    result.sort(key=lambda x: x.get("indexed_at", ""), reverse=True)
    return json.dumps({"total": len(result), "projects": result}, ensure_ascii=False)


@mcp.tool()
def get_wiki_summary(project_path: str, language: str = "en") -> str:
    """Get the wiki structure and summary for a project.

    Returns title, description, and list of page titles.

    Args:
        project_path: Project path (e.g. 'group/repo')
        language: Wiki language code (default 'en')
    """
    owner, repo = _split_project_path(project_path)
    cache = _find_wiki_cache(owner, repo, language)
    if not cache:
        return json.dumps({"error": f"No wiki cache found for '{project_path}' (lang={language})"})

    ws = cache.get("wiki_structure", {})
    pages = cache.get("generated_pages", {})

    page_list = []
    for pid, page in pages.items():
        page_list.append({
            "id": pid,
            "title": page.get("title", pid),
            "importance": page.get("importance", ""),
        })

    return json.dumps({
        "project_path": project_path,
        "title": ws.get("title", ""),
        "description": ws.get("description", ""),
        "total_pages": len(page_list),
        "pages": page_list,
    }, ensure_ascii=False)


@mcp.tool()
def get_wiki_page(project_path: str, page_id: str, language: str = "en") -> str:
    """Read the full content of a specific wiki page.

    Args:
        project_path: Project path (e.g. 'group/repo')
        page_id: Page identifier from get_wiki_summary
        language: Wiki language code (default 'en')
    """
    owner, repo = _split_project_path(project_path)
    cache = _find_wiki_cache(owner, repo, language)
    if not cache:
        return json.dumps({"error": f"No wiki cache found for '{project_path}'"})

    pages = cache.get("generated_pages", {})
    page = pages.get(page_id)
    if not page:
        available = list(pages.keys())
        return json.dumps({
            "error": f"Page '{page_id}' not found",
            "available_pages": available,
        })

    return json.dumps({
        "project_path": project_path,
        "page_id": page_id,
        "title": page.get("title", page_id),
        "content": page.get("content", ""),
        "file_paths": page.get("filePaths", []),
        "related_pages": page.get("relatedPages", []),
    }, ensure_ascii=False)


@mcp.tool()
async def search_code(project_path: str, query: str, top_k: int = 5) -> str:
    """Search code snippets in a single project using semantic search.

    Args:
        project_path: Project path (e.g. 'group/repo')
        query: Natural language search query
        top_k: Number of results (default 5)
    """
    try:
        from api.rag import RAG
        from api.config import GITLAB_SERVICE_TOKEN

        provider = _get_default_provider()
        model = _get_default_model()
        repo_url = _get_gitlab_url(project_path)
        rag = RAG(provider=provider, model=model)
        rag.prepare_retriever(
            repo_url,
            type="gitlab",
            access_token=GITLAB_SERVICE_TOKEN or None,
        )
        results = rag(query)

        snippets = []
        if results and len(results) > 0 and hasattr(results[0], 'documents'):
            for doc in results[0].documents[:top_k]:
                meta = getattr(doc, 'meta_data', {}) or {}
                snippets.append({
                    "file_path": meta.get("file_path", "unknown"),
                    "content": getattr(doc, 'text', '')[:500],
                })

        return json.dumps({
            "project_path": project_path,
            "query": query,
            "total_results": len(snippets),
            "results": snippets,
        }, ensure_ascii=False)
    except Exception as e:
        logger.error("search_code failed for %s: %s", project_path, e)
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_repo_relations(project_path: Optional[str] = None) -> str:
    """Get dependency relationships between repositories.

    If project_path is provided, returns only relations involving that project.
    Otherwise returns the full relation graph.

    Args:
        project_path: Optional project path to filter relations
    """
    data = load_relations()
    edges = data.get("edges", [])

    if project_path:
        edges = [
            e for e in edges
            if e.get("from") == project_path or e.get("to") == project_path
        ]

    return json.dumps({
        "analyzed_at": data.get("analyzed_at"),
        "total_edges": len(edges),
        "edges": edges,
    }, ensure_ascii=False)


@mcp.tool()
async def ask_question(project_path: str, question: str) -> str:
    """Ask a question about a single project's codebase.

    Uses RAG to retrieve relevant code and generate an answer.

    Args:
        project_path: Project path (e.g. 'group/repo')
        question: Your question about the project
    """
    try:
        from api.rag import RAG
        from api.config import GITLAB_SERVICE_TOKEN

        provider = _get_default_provider()
        model = _get_default_model()
        repo_url = _get_gitlab_url(project_path)
        rag = RAG(provider=provider, model=model)
        rag.prepare_retriever(
            repo_url,
            type="gitlab",
            access_token=GITLAB_SERVICE_TOKEN or None,
        )
        results = rag(question)

        contexts = []
        if results and len(results) > 0 and hasattr(results[0], 'documents'):
            for doc in results[0].documents[:8]:
                meta = getattr(doc, 'meta_data', {}) or {}
                contexts.append(
                    f"{meta.get('file_path', 'unknown')}:\n"
                    f"{getattr(doc, 'text', '')[:800]}"
                )

        context_text = "\n\n---\n\n".join(contexts) if contexts else "(no relevant code found)"

        owner, repo = _split_project_path(project_path)
        cache = _find_wiki_cache(owner, repo)
        wiki_desc = ""
        if cache:
            ws = cache.get("wiki_structure", {})
            wiki_desc = f"\nProject: {ws.get('title', '')}\nDescription: {ws.get('description', '')}\n"

        prompt = (
            f"You are a code expert for the project '{project_path}'.{wiki_desc}\n"
            f"Based on the following code context, answer the question.\n\n"
            f"## Code Context\n{context_text}\n\n"
            f"## Question\n{question}\n\n"
            f"Provide a detailed, structured answer in markdown."
        )

        answer = await _call_llm(prompt, label="ask_question")

        return json.dumps({
            "project_path": project_path,
            "question": question,
            "answer": answer,
            "sources_count": len(contexts),
        }, ensure_ascii=False)

    except Exception as e:
        logger.error("ask_question failed for %s: %s", project_path, e)
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Insight tools (Phase 2 - Structured Knowledge)
# ---------------------------------------------------------------------------


@mcp.tool()
def get_project_insights(project_path: str) -> str:
    """Get the structured knowledge index for a single project.

    Returns modules, API endpoints, data models, tech stack, and
    architecture pattern. Run extract_project_insights first if no
    data is available.

    Args:
        project_path: Project path (e.g. 'group/repo')
    """
    from api.insight_extractor import load_insights

    insights = load_insights(project_path)
    if not insights:
        return json.dumps({
            "error": f"No insights found for '{project_path}'",
            "hint": "Use extract_project_insights to generate insights first.",
        })
    return json.dumps(insights, ensure_ascii=False)


@mcp.tool()
async def extract_project_insights(project_path: str) -> str:
    """Extract structured knowledge index from a project's wiki and code.

    This uses LLM to analyze the wiki documentation and code to produce
    a structured index of modules, endpoints, data models, and architecture.
    Run this first if get_project_insights returns no data.

    Args:
        project_path: Project path (e.g. 'group/repo')
    """
    try:
        from api.insight_extractor import extract_project_insights as _extract

        insights = await _extract(project_path)
        return json.dumps({
            "status": "success",
            "project_path": project_path,
            "modules_count": len(insights.get("modules", [])),
            "endpoints_count": len(insights.get("endpoints", [])),
            "data_models_count": len(insights.get("data_models", [])),
            "tech_stack": insights.get("tech_stack", []),
            "architecture_pattern": insights.get("architecture_pattern", "unknown"),
        }, ensure_ascii=False)
    except Exception as e:
        logger.error("extract_project_insights failed for %s: %s", project_path, e)
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_product_insights(product_id: str) -> str:
    """Get aggregated structured knowledge for an entire product across all repos.

    Returns all modules, all API endpoints, all data models, cross-repo
    dependencies, and the overall architecture pattern of the product.

    This is the primary tool for high-level architectural analysis like
    agent transformation, API design review, or tech migration planning.

    Args:
        product_id: Product identifier (e.g. 'bas')
    """
    from api.insight_extractor import aggregate_product_insights

    result = aggregate_product_insights(product_id)
    return json.dumps(result, ensure_ascii=False)
