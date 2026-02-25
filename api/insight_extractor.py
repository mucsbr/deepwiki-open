"""
Insight Extractor

Extracts structured knowledge indexes from wiki cache and code RAG.
Results are stored as JSON at ~/.adalflow/metadata/insights/{owner}_{repo}.json

Two-step LLM extraction:
1. Wiki content -> modules, endpoints, tech stack, architecture pattern
2. Code RAG -> data models
"""

import json
import logging
import os
import re
from typing import Optional

from adalflow.utils import get_adalflow_default_root_path

from api.prompts import (
    INSIGHT_EXTRACT_FROM_WIKI_PROMPT,
    INSIGHT_EXTRACT_DATA_MODELS_PROMPT,
)

logger = logging.getLogger(__name__)

INSIGHTS_DIR = os.path.join(get_adalflow_default_root_path(), "metadata", "insights")


def _ensure_dir() -> None:
    os.makedirs(INSIGHTS_DIR, exist_ok=True)


def _insight_path(project_path: str) -> str:
    safe_name = project_path.replace("/", "_")
    return os.path.join(INSIGHTS_DIR, f"{safe_name}.json")


def load_insights(project_path: str) -> Optional[dict]:
    """Load cached insights for a project, or None if not extracted yet."""
    path = _insight_path(project_path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("Failed to load insights for %s: %s", project_path, e)
        return None


def save_insights(project_path: str, data: dict) -> None:
    """Persist insights for a project."""
    _ensure_dir()
    path = _insight_path(project_path)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info("Saved insights for %s", project_path)
    except Exception as e:
        logger.error("Failed to save insights for %s: %s", project_path, e)


def _parse_json_response(text: str) -> Optional[dict]:
    """Parse JSON from LLM response, stripping markdown fences if present."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse LLM JSON response: %s", e)
        return None


def _find_wiki_cache(project_path: str) -> Optional[dict]:
    """Locate and load wiki cache for a project."""
    wikicache_dir = os.path.join(get_adalflow_default_root_path(), "wikicache")
    if not os.path.isdir(wikicache_dir):
        return None

    parts = project_path.split("/")
    owner = "/".join(parts[:-1]) if len(parts) > 1 else parts[0]
    repo = parts[-1] if len(parts) > 1 else parts[0]
    safe_owner = owner.replace("/", "--")

    for repo_type in ("gitlab", "github", "bitbucket"):
        for lang in ("en", "zh", "ja"):
            filename = f"deepwiki_cache_{repo_type}_{safe_owner}_{repo}_{lang}.json"
            cache_path = os.path.join(wikicache_dir, filename)
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    continue
    return None


def _extract_wiki_text(cache: dict, max_chars: int = 15000) -> str:
    """Extract readable text from wiki cache for LLM consumption."""
    texts = []
    ws = cache.get("wiki_structure", {})
    texts.append(f"# {ws.get('title', 'Unknown')}")
    texts.append(ws.get("description", ""))

    pages = cache.get("generated_pages", {})
    for _pid, page in pages.items():
        title = page.get("title", "")
        content = page.get("content", "")
        texts.append(f"\n## {title}\n{content}")

    full = "\n".join(texts)
    if len(full) > max_chars:
        full = full[:max_chars] + "\n...(truncated)"
    return full


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


async def extract_project_insights(
    project_path: str,
    provider: str = None,
    model: str = None,
) -> dict:
    """Extract structured insights from a project's wiki and code.

    Two-step process:
    1. Read wiki cache -> LLM extracts modules, endpoints, tech stack
    2. RAG search for data models -> LLM extracts data model definitions

    Returns the combined insights dict and persists it to disk.
    """
    from api.config import GITLAB_SERVICE_TOKEN
    from datetime import datetime, timezone

    # Resolve provider/model from config if not explicitly given
    effective_provider = provider or _get_default_provider()
    effective_model = model or _get_default_model()

    logger.info(
        "Extracting insights for %s (provider=%s, model=%s)",
        project_path, effective_provider, effective_model,
    )

    # Step 1: Extract from wiki
    wiki_cache = _find_wiki_cache(project_path)
    wiki_insights = {}

    if wiki_cache:
        wiki_text = _extract_wiki_text(wiki_cache)
        prompt = INSIGHT_EXTRACT_FROM_WIKI_PROMPT.format(wiki_content=wiki_text)

        try:
            from api.wiki_generator import _call_llm_inner
            text = await _call_llm_inner(
                effective_provider, effective_model, prompt,
                label="insight_wiki_extract",
            )
            parsed = _parse_json_response(text)
            if parsed:
                wiki_insights = parsed
        except Exception as e:
            logger.error("Wiki insight extraction failed for %s: %s", project_path, e)
    else:
        logger.warning("No wiki cache found for %s, skipping wiki extraction", project_path)

    # Step 2: Extract data models from code RAG
    data_models = []
    try:
        from api.rag import RAG

        def _get_gitlab_url(path: str) -> str:
            from api.config import GITLAB_URL
            base = GITLAB_URL.rstrip("/") if GITLAB_URL else "https://gitlab.com"
            return f"{base}/{path}"

        repo_url = _get_gitlab_url(project_path)
        rag = RAG(provider=effective_provider, model=effective_model)
        rag.prepare_retriever(
            repo_url,
            type="gitlab",
            access_token=GITLAB_SERVICE_TOKEN or None,
        )

        # Search for model/schema definitions
        queries = [
            "data model class definition schema",
            "database model ORM table definition",
            "API request response schema Pydantic BaseModel",
        ]

        all_code_snippets = []
        for q in queries:
            try:
                results = rag(q)
                if results and len(results) > 0 and hasattr(results[0], 'documents'):
                    for doc in results[0].documents[:3]:
                        meta = getattr(doc, 'meta_data', {}) or {}
                        all_code_snippets.append(
                            f"# {meta.get('file_path', 'unknown')}\n"
                            f"{getattr(doc, 'text', '')[:600]}"
                        )
            except Exception:
                continue

        if all_code_snippets:
            code_context = "\n\n---\n\n".join(all_code_snippets[:12])
            prompt = INSIGHT_EXTRACT_DATA_MODELS_PROMPT.format(code_context=code_context)

            from api.wiki_generator import _call_llm_inner
            text = await _call_llm_inner(
                effective_provider, effective_model, prompt,
                label="insight_data_model_extract",
            )
            parsed = _parse_json_response(text)
            if parsed and "data_models" in parsed:
                data_models = parsed["data_models"]

    except Exception as e:
        logger.error("Data model extraction failed for %s: %s", project_path, e)

    # Combine results
    insights = {
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "project_path": project_path,
        "modules": wiki_insights.get("modules", []),
        "endpoints": wiki_insights.get("endpoints", []),
        "data_models": data_models,
        "tech_stack": wiki_insights.get("tech_stack", []),
        "architecture_pattern": wiki_insights.get("architecture_pattern", "unknown"),
    }

    save_insights(project_path, insights)
    return insights


def aggregate_product_insights(product_id: str) -> dict:
    """Aggregate insights from all repos in a product.

    Returns a product-level view with all modules, endpoints, data models,
    cross-repo dependencies, and tech stack summary.
    """
    from api.product_manager import get_product
    from api.repo_relations import load_relations

    product = get_product(product_id)
    if not product:
        return {"error": f"Product '{product_id}' not found"}

    repos = product.get("repos", [])
    modules_by_repo: dict = {}
    all_endpoints = []
    all_data_models = []
    tech_stack_counts: dict = {}
    repos_with_insights = 0
    repos_without_insights = []

    for repo_path in repos:
        insights = load_insights(repo_path)
        if not insights:
            repos_without_insights.append(repo_path)
            continue

        repos_with_insights += 1
        modules = insights.get("modules", [])
        if modules:
            modules_by_repo[repo_path] = modules

        for ep in insights.get("endpoints", []):
            ep["repo"] = repo_path
            all_endpoints.append(ep)

        for dm in insights.get("data_models", []):
            dm["repo"] = repo_path
            all_data_models.append(dm)

        for tech in insights.get("tech_stack", []):
            tech_stack_counts[tech] = tech_stack_counts.get(tech, 0) + 1

    # Get cross-repo dependencies
    relations_data = load_relations()
    repo_set = set(repos)
    cross_repo_deps = [
        e for e in relations_data.get("edges", [])
        if e.get("from") in repo_set and e.get("to") in repo_set
    ]

    # Determine overall architecture pattern
    patterns = {}
    for repo_path in repos:
        insights = load_insights(repo_path)
        if insights:
            p = insights.get("architecture_pattern", "unknown")
            patterns[p] = patterns.get(p, 0) + 1
    overall_pattern = max(patterns, key=patterns.get) if patterns else "unknown"

    return {
        "product_id": product_id,
        "product_name": product.get("name", ""),
        "product_description": product.get("description", ""),
        "total_repos": len(repos),
        "repos_with_insights": repos_with_insights,
        "repos_without_insights": repos_without_insights,
        "total_modules": sum(len(m) for m in modules_by_repo.values()),
        "total_endpoints": len(all_endpoints),
        "total_data_models": len(all_data_models),
        "modules_by_repo": modules_by_repo,
        "endpoints": all_endpoints,
        "data_models": all_data_models,
        "cross_repo_dependencies": cross_repo_deps,
        "tech_stack_summary": tech_stack_counts,
        "architecture_pattern": overall_pattern,
    }
