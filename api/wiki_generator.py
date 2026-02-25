"""
Wiki Generator Module

Generates wiki cache (structure + page content) for a repository by calling
LLM, so that users can load the wiki instantly without waiting for generation.

Used by BatchIndexer after embedding creation to pre-generate wiki content.
"""

import asyncio
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse

import google.generativeai as genai
from adalflow.components.model_client.ollama_client import OllamaClient
from adalflow.core.types import ModelType

from api.bedrock_client import BedrockClient
from api.config import configs, get_model_config
from api.azureai_client import AzureAIClient
from api.dashscope_client import DashscopeClient
from api.openai_client import OpenAIClient
from api.openrouter_client import OpenRouterClient

from api.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ADALFLOW_ROOT = os.path.expanduser(os.path.join("~", ".adalflow"))
WIKI_CACHE_DIR = os.path.join(ADALFLOW_ROOT, "wikicache")

# Language display names (same mapping used in the frontend)
LANGUAGE_NAMES = {
    "en": "English",
    "ja": "Japanese (日本語)",
    "zh": "Mandarin Chinese (中文)",
    "zh-tw": "Traditional Chinese (繁體中文)",
    "es": "Spanish (Español)",
    "kr": "Korean (한국어)",
    "vi": "Vietnamese (Tiếng Việt)",
    "pt-br": "Brazilian Portuguese (Português Brasileiro)",
    "fr": "Français (French)",
    "ru": "Русский (Russian)",
}

# Directories / files to skip when building the file tree
_SKIP_DIRS = {
    ".git", ".svn", ".hg", "__pycache__", "node_modules", ".venv",
    "venv", "env", ".idea", ".vscode", "dist", "build", ".tox",
}


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

def _wiki_structure_prompt(owner: str, repo: str, file_tree: str, readme: str, language: str) -> str:
    lang_name = LANGUAGE_NAMES.get(language, "English")
    return f"""Analyze this repository {owner}/{repo} and create a wiki structure for it.

1. The complete file tree of the project:
<file_tree>
{file_tree}
</file_tree>

2. The README file of the project:
<readme>
{readme}
</readme>

I want to create a wiki for this repository. Determine the most logical structure for a wiki based on the repository's content.

IMPORTANT: The wiki content will be generated in {lang_name} language.

When designing the wiki structure, include pages that would benefit from visual diagrams, such as:
- Architecture overviews
- Data flow descriptions
- Component relationships
- Process workflows
- State machines
- Class hierarchies

Create a structured wiki with the following main sections:
- Overview (general information about the project)
- System Architecture (how the system is designed)
- Core Features (key functionality)
- Data Management/Flow: If applicable, how data is stored, processed, accessed, and managed (e.g., database schema, data pipelines, state management).
- Frontend Components (UI elements, if applicable.)
- Backend Systems (server-side components)
- Model Integration (AI model connections)
- Deployment/Infrastructure (how to deploy, what's the infrastructure like)
- Extensibility and Customization: If the project architecture supports it, explain how to extend or customize its functionality (e.g., plugins, theming, custom modules, hooks).

Each section should contain relevant pages. For example, the "Frontend Components" section might include pages for "Home Page", "Repository Wiki Page", "Ask Component", etc.

Return your analysis in the following XML format:

<wiki_structure>
  <title>[Overall title for the wiki]</title>
  <description>[Brief description of the repository]</description>
  <sections>
    <section id="section-1">
      <title>[Section title]</title>
      <pages>
        <page_ref>page-1</page_ref>
        <page_ref>page-2</page_ref>
      </pages>
      <subsections>
        <section_ref>section-2</section_ref>
      </subsections>
    </section>
  </sections>
  <pages>
    <page id="page-1">
      <title>[Page title]</title>
      <description>[Brief description of what this page will cover]</description>
      <importance>high|medium|low</importance>
      <relevant_files>
        <file_path>[Path to a relevant file]</file_path>
      </relevant_files>
      <related_pages>
        <related>page-2</related>
      </related_pages>
      <parent_section>section-1</parent_section>
    </page>
  </pages>
</wiki_structure>

IMPORTANT FORMATTING INSTRUCTIONS:
- Return ONLY the valid XML structure specified above
- DO NOT wrap the XML in markdown code blocks (no ``` or ```xml)
- DO NOT include any explanation text before or after the XML
- Ensure the XML is properly formatted and valid
- Start directly with <wiki_structure> and end with </wiki_structure>

IMPORTANT:
1. Create 8-12 pages that would make a comprehensive wiki for this repository
2. Each page should focus on a specific aspect of the codebase (e.g., architecture, key features, setup)
3. The relevant_files should be actual files from the repository that would be used to generate that page
4. Return ONLY valid XML with the structure specified above, with no markdown code block delimiters"""


def _page_content_prompt(
    page_title: str, file_paths: List[str], language: str, rag_context: str = "",
) -> str:
    lang_name = LANGUAGE_NAMES.get(language, "English")
    file_list = "\n".join(f"- {p}" for p in file_paths)

    context_block = ""
    if rag_context.strip():
        context_block = f"""
<START_OF_CONTEXT>
{rag_context}
<END_OF_CONTEXT>

"""

    return f"""You are an expert technical writer and software architect.
Your task is to generate a comprehensive and accurate technical wiki page in Markdown format about a specific feature, system, or module within a given software project.

You will be given:
1. The "[WIKI_PAGE_TOPIC]" for the page you need to create.
2. A list of "[RELEVANT_SOURCE_FILES]" from the project that you MUST use as the sole basis for the content. You have access to the full content of these files. You MUST use AT LEAST 5 relevant source files for comprehensive coverage - if fewer are provided, search for additional related files in the codebase.
3. Retrieved code context from the repository to help you write accurate content.
{context_block}
CRITICAL STARTING INSTRUCTION:
The very first thing on the page MUST be a `<details>` block listing ALL the `[RELEVANT_SOURCE_FILES]` you used to generate the content. There MUST be AT LEAST 5 source files listed - if fewer were provided, you MUST find additional related files to include.
Format it exactly like this:
<details>
<summary>Relevant source files</summary>

The following files were used as context for generating this wiki page:

{file_list}
</details>

Immediately after the `<details>` block, the main title of the page should be a H1 Markdown heading: `# {page_title}`.

Based ONLY on the content of the `[RELEVANT_SOURCE_FILES]`:

1.  **Introduction:** Start with a concise introduction (1-2 paragraphs) explaining the purpose, scope, and high-level overview of "{page_title}" within the context of the overall project.

2.  **Detailed Sections:** Break down "{page_title}" into logical sections using H2 (`##`) and H3 (`###`) Markdown headings.

3.  **Mermaid Diagrams:**
    *   EXTENSIVELY use Mermaid diagrams to visually represent architectures, flows, relationships.
    *   CRITICAL: All diagrams MUST follow strict vertical orientation:
       - Use "graph TD" (top-down) directive for flow diagrams
       - NEVER use "graph LR" (left-right)

4.  **Tables:**
    *   Use Markdown tables to summarize information.

5.  **Code Snippets (ENTIRELY OPTIONAL):**
    *   Include short, relevant code snippets directly from the source files.

6.  **Source Citations (EXTREMELY IMPORTANT):**
    *   For EVERY piece of significant information, cite the specific source file(s).
    *   Use the exact format: `Sources: [filename.ext:start_line-end_line]()`

7.  **Technical Accuracy:** All information must be derived SOLELY from the `[RELEVANT_SOURCE_FILES]`.

8.  **Clarity and Conciseness:** Use clear, professional, and concise technical language.

IMPORTANT: Generate the content in {lang_name} language.

Remember:
- Ground every claim in the provided source files.
- Prioritize accuracy and direct representation of the code's functionality and structure.
- Structure the document logically for easy understanding by other developers.
"""


# ---------------------------------------------------------------------------
# LLM call helper
# ---------------------------------------------------------------------------

def _estimate_tokens(text: str) -> int:
    """Rough token estimate (words-based, ~1.3 tokens per word)."""
    return int(len(text.split()) * 1.3)


async def _call_llm(provider: str, model: str, prompt: str, label: str = "") -> str:
    """Call an LLM and return the full text response (non-streaming)."""

    est_tokens = _estimate_tokens(prompt)
    logger.info(
        "[_call_llm] %s | provider=%s model=%s | prompt_chars=%d est_tokens=%d",
        label or "unnamed", provider, model, len(prompt), est_tokens,
    )

    try:
        result = await _call_llm_inner(provider, model, prompt, label)
        # Strip <think>...</think> blocks (e.g. from thinking models like grok-thinking, deepseek-r1)
        stripped = re.sub(r"<think>[\s\S]*?</think>", "", result).strip()
        logger.info(
            "[_call_llm] OK %s | raw_chars=%d stripped_chars=%d | first_200=%s",
            label or "unnamed", len(result), len(stripped), repr(stripped),
        )
        return stripped
    except Exception as exc:
        logger.error(
            "[_call_llm] FAILED %s | provider=%s model=%s | "
            "prompt_chars=%d est_tokens=%d | error=%s: %s",
            label or "unnamed", provider, model, len(prompt), est_tokens,
            type(exc).__name__, exc,
        )
        raise


def _parse_sse_text(raw: str) -> str:
    """Parse raw SSE text (data: {...} lines) and extract the concatenated content.

    Handles the case where the API returns streaming SSE format even though
    stream=False was requested.  Supports multiple SSE payload variants:
    - streaming: choices[].delta.content
    - non-streaming: choices[].message.content
    - simple: choices[].text
    - top-level: content / text / output
    """
    import json as _json
    content_parts = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            break
        try:
            obj = _json.loads(payload)
            # Try choices-based formats
            for choice in obj.get("choices", []):
                # Streaming: delta.content
                delta = choice.get("delta", {})
                text = delta.get("content")
                if text:
                    content_parts.append(text)
                    continue
                # Non-streaming: message.content
                message = choice.get("message", {})
                text = message.get("content")
                if text:
                    content_parts.append(text)
                    continue
                # Legacy: text field directly on choice
                text = choice.get("text")
                if text:
                    content_parts.append(text)
            # Top-level content (some non-standard providers)
            if not content_parts:
                for key in ("content", "text", "output", "response"):
                    val = obj.get(key)
                    if val and isinstance(val, str):
                        content_parts.append(val)
                        break
        except _json.JSONDecodeError:
            continue
    return "".join(content_parts)


def _extract_llm_content(response) -> str:
    """Extract text content from any LLM response format.

    Handles:
    - ChatCompletion object (normal non-streaming)
    - Raw SSE string   (API ignored stream=False)
    - AsyncStream       (returned a stream object)
    - Plain string      (already text)
    """
    # 1. Standard ChatCompletion object
    if hasattr(response, "choices") and response.choices:
        msg = response.choices[0].message
        content = getattr(msg, "content", None) or ""
        if not content.strip():
            reasoning = getattr(msg, "reasoning_content", None) or ""
            if reasoning:
                logger.info("[_extract_llm_content] content empty, using reasoning_content (%d chars)", len(reasoning))
                return reasoning
        return content

    # 2. Raw SSE string — detect by "data:" prefix
    if isinstance(response, str) and response.lstrip().startswith("data:"):
        logger.info("[_extract_llm_content] detected raw SSE text (%d chars), parsing chunks", len(response))
        parsed = _parse_sse_text(response)
        if parsed:
            return parsed
        logger.warning("[_extract_llm_content] SSE parsing yielded empty content; raw preview: %s", response[:1000])
        return ""

    # 3. Plain string
    if isinstance(response, str):
        return response

    # 4. Fallback
    return str(response)


async def _call_llm_inner(provider: str, model: str, prompt: str, label: str = "") -> str:
    """Actual LLM call implementation."""
    config = get_model_config(provider, model)
    model_kwargs_cfg = config["model_kwargs"]

    if provider == "google":
        genai_model = genai.GenerativeModel(
            model_name=model_kwargs_cfg["model"],
            generation_config={
                "temperature": model_kwargs_cfg.get("temperature", 0.7),
                "top_p": model_kwargs_cfg.get("top_p", 0.8),
                "top_k": model_kwargs_cfg.get("top_k", 40),
            },
        )
        response = genai_model.generate_content(prompt)
        return response.text

    if provider == "ollama":
        client = OllamaClient()
        kwargs = {
            "model": model_kwargs_cfg["model"],
            "stream": False,
            "options": {
                k: model_kwargs_cfg[k]
                for k in ("temperature", "top_p", "num_ctx")
                if k in model_kwargs_cfg
            },
        }
        api_kwargs = client.convert_inputs_to_api_kwargs(
            input=prompt, model_kwargs=kwargs, model_type=ModelType.LLM,
        )
        response = await client.acall(api_kwargs=api_kwargs, model_type=ModelType.LLM)
        # Ollama non-streaming returns a single response object
        if isinstance(response, str):
            return response
        msg = getattr(response, "message", None)
        if msg:
            return msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", str(msg))
        return str(response)

    if provider == "bedrock":
        client = BedrockClient()
        kwargs = {"model": model_kwargs_cfg["model"]}
        for k in ("temperature", "top_p"):
            if k in model_kwargs_cfg:
                kwargs[k] = model_kwargs_cfg[k]
        api_kwargs = client.convert_inputs_to_api_kwargs(
            input=prompt, model_kwargs=kwargs, model_type=ModelType.LLM,
        )
        response = await client.acall(api_kwargs=api_kwargs, model_type=ModelType.LLM)
        return response if isinstance(response, str) else str(response)

    # OpenAI-compatible providers: openai, openrouter, azure, dashscope
    client_map = {
        "openai": OpenAIClient,
        "openrouter": OpenRouterClient,
        "azure": AzureAIClient,
        "dashscope": DashscopeClient,
    }
    client_cls = client_map.get(provider)
    if client_cls is None:
        raise ValueError(f"Unsupported provider: {provider}")

    client = client_cls()
    kwargs = {"model": model_kwargs_cfg["model"], "stream": False}
    for k in ("temperature", "top_p"):
        if k in model_kwargs_cfg:
            kwargs[k] = model_kwargs_cfg[k]

    api_kwargs = client.convert_inputs_to_api_kwargs(
        input=prompt, model_kwargs=kwargs, model_type=ModelType.LLM,
    )
    response = await client.acall(api_kwargs=api_kwargs, model_type=ModelType.LLM)

    # Some proxies ignore stream=False and return an AsyncStream.
    # Consume it into text before passing to _extract_llm_content.
    if hasattr(response, "__aiter__"):
        logger.info("[_call_llm_inner] response is async iterable (stream), consuming chunks")
        content_parts = []
        async for chunk in response:
            if hasattr(chunk, "choices") and chunk.choices:
                delta = chunk.choices[0].delta
                text = getattr(delta, "content", None)
                if text:
                    content_parts.append(text)
        return "".join(content_parts)

    return _extract_llm_content(response)


# ---------------------------------------------------------------------------
# XML parsing
# ---------------------------------------------------------------------------

def _sanitize_xml(raw: str) -> str:
    """Clean up common LLM XML issues so ElementTree can parse it."""
    # Remove control characters
    raw = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", raw)
    # Escape bare '&' that are not already part of an entity (e.g. &amp; &lt;)
    raw = re.sub(r"&(?!(?:amp|lt|gt|apos|quot|#\d+|#x[0-9a-fA-F]+);)", "&amp;", raw)
    return raw


def _parse_wiki_structure_xml(xml_text: str) -> dict:
    """Parse the wiki structure XML returned by the LLM.

    Returns a dict with keys: title, description, pages (list of dicts).
    Falls back to regex extraction if ET parsing fails.
    """
    # Strip markdown code fences if present
    xml_text = re.sub(r"^```(?:xml)?\s*", "", xml_text.strip())
    xml_text = re.sub(r"```\s*$", "", xml_text.strip())

    # Strip <think>...</think> blocks from thinking models (e.g. grok-thinking, deepseek-r1)
    xml_text = re.sub(r"<think>[\s\S]*?</think>", "", xml_text).strip()

    # Extract <wiki_structure>...</wiki_structure>
    match = re.search(r"<wiki_structure>[\s\S]*?</wiki_structure>", xml_text)
    if not match:
        raise ValueError("No <wiki_structure> block found in LLM response")

    raw = _sanitize_xml(match.group(0))

    # Try ET parsing; fall back to regex on failure
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        logger.warning("ET.fromstring failed (%s), falling back to regex", exc)
        return _parse_wiki_structure_regex(raw)

    title_el = root.find("title")
    desc_el = root.find("description")

    # Parse sections
    sections = []
    root_sections: List[str] = []
    all_sub_refs: set = set()

    for sec_el in root.iter("section"):
        sec_id = sec_el.get("id", f"section-{len(sections) + 1}")
        sec_title = (sec_el.findtext("title") or "").strip()
        page_refs = [
            pr.text.strip()
            for pr in sec_el.findall("pages/page_ref")
            if pr.text
        ]
        sub_refs = [
            sr.text.strip()
            for sr in sec_el.findall("subsections/section_ref")
            if sr.text
        ]
        all_sub_refs.update(sub_refs)
        sections.append({
            "id": sec_id,
            "title": sec_title,
            "pages": page_refs,
            "subsections": sub_refs if sub_refs else None,
        })

    # Root sections = those not referenced as subsections of another
    for sec in sections:
        if sec["id"] not in all_sub_refs:
            root_sections.append(sec["id"])

    # Parse pages
    pages = []
    for page_el in root.iter("page"):
        page_id = page_el.get("id", f"page-{len(pages) + 1}")
        p_title = (page_el.findtext("title") or "").strip()
        importance = (page_el.findtext("importance") or "medium").strip()
        if importance not in ("high", "medium", "low"):
            importance = "medium"

        file_paths = [
            fp.text.strip()
            for fp in page_el.findall(".//file_path")
            if fp.text
        ]
        related = [
            r.text.strip()
            for r in page_el.findall(".//related")
            if r.text
        ]

        pages.append({
            "id": page_id,
            "title": p_title,
            "importance": importance,
            "filePaths": file_paths,
            "relatedPages": related,
        })

    return {
        "title": (title_el.text or "").strip() if title_el is not None else "",
        "description": (desc_el.text or "").strip() if desc_el is not None else "",
        "pages": pages,
        "sections": sections,
        "rootSections": root_sections,
    }


def _parse_wiki_structure_regex(xml_text: str) -> dict:
    """Regex fallback when ElementTree cannot parse the LLM XML."""
    # Top-level title / description
    title = ""
    desc = ""
    title_m = re.search(r"<wiki_structure>\s*<title>(.*?)</title>", xml_text, re.S)
    if title_m:
        title = title_m.group(1).strip()
    desc_m = re.search(r"<description>(.*?)</description>", xml_text, re.S)
    if desc_m:
        desc = desc_m.group(1).strip()

    pages: List[dict] = []
    for page_m in re.finditer(r"<page\s+id=[\"']([^\"']+)[\"']>(.*?)</page>", xml_text, re.S):
        page_id = page_m.group(1)
        body = page_m.group(2)

        p_title_m = re.search(r"<title>(.*?)</title>", body, re.S)
        imp_m = re.search(r"<importance>(.*?)</importance>", body, re.S)
        p_title = p_title_m.group(1).strip() if p_title_m else ""
        importance = imp_m.group(1).strip() if imp_m else "medium"
        if importance not in ("high", "medium", "low"):
            importance = "medium"

        file_paths = [fp.strip() for fp in re.findall(r"<file_path>(.*?)</file_path>", body, re.S) if fp.strip()]
        related = [r.strip() for r in re.findall(r"<related>(.*?)</related>", body, re.S) if r.strip()]

        pages.append({
            "id": page_id,
            "title": p_title,
            "importance": importance,
            "filePaths": file_paths,
            "relatedPages": related,
        })

    if not pages:
        raise ValueError("Failed to extract any pages from LLM XML (regex fallback)")

    return {"title": title, "description": desc, "pages": pages}


# ---------------------------------------------------------------------------
# File tree helper
# ---------------------------------------------------------------------------

def _get_local_file_tree_and_readme(repo_path: str) -> tuple:
    """Walk a local repository and return (file_tree_str, readme_content)."""
    file_tree_lines: List[str] = []
    readme_content = ""

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for fname in files:
            if fname.startswith(".") or fname == "__init__.py" or fname == ".DS_Store":
                continue
            rel = os.path.relpath(os.path.join(root, fname), repo_path)
            file_tree_lines.append(rel)
            if fname.lower() == "readme.md" and not readme_content:
                try:
                    with open(os.path.join(root, fname), "r", encoding="utf-8") as f:
                        readme_content = f.read()
                except Exception:
                    pass

    return "\n".join(sorted(file_tree_lines)), readme_content


def _compute_repo_dir_name(repo_url: str, repo_type: str) -> str:
    """Compute the local directory name the same way DatabaseManager does."""
    if repo_url.startswith("https://") or repo_url.startswith("http://"):
        try:
            parsed = urlparse(repo_url)
            path = parsed.path.strip("/").replace(".git", "")
            return path.replace("/", "_")
        except Exception:
            parts = repo_url.rstrip("/").split("/")
            owner = parts[-2]
            repo = parts[-1].replace(".git", "")
            return f"{owner}_{repo}"
    parts = repo_url.rstrip("/").split("/")
    return parts[-1].replace(".git", "")


# ---------------------------------------------------------------------------
# WikiGenerator
# ---------------------------------------------------------------------------

class WikiGenerator:
    """Generate wiki cache for a repository using LLM.

    Typical usage::

        gen = WikiGenerator(provider="openai", model="gpt-4o-mini")
        await gen.generate_wiki(
            repo_url="https://gitlab.example.com/group/project",
            owner="group", repo="project", repo_type="gitlab",
        )
    """

    def __init__(
        self,
        provider: str,
        model: Optional[str] = None,
        language: str = "zh",
    ):
        # Fall back to configured defaults
        self.provider = provider or configs.get("default_provider", "openai")
        if not model:
            provider_cfg = configs.get("providers", {}).get(self.provider, {})
            model = provider_cfg.get("default_model", "")
        self.model = model
        self.language = language

    # ---- public API ----

    async def generate_wiki(
        self,
        repo_url: str,
        owner: str,
        repo: str,
        repo_type: str = "gitlab",
        access_token: Optional[str] = None,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> dict:
        """Full pipeline: file tree -> structure -> pages -> save cache.

        Args:
            repo_url: Clone URL of the repository.
            owner: Repository owner / group path.
            repo: Repository name.
            repo_type: One of github, gitlab, bitbucket.
            access_token: Optional access token (unused here; repo already cloned).
            on_progress: Optional callback with status strings.

        Returns:
            The saved wiki cache dict.
        """
        def _progress(msg: str) -> None:
            logger.info("[WikiGenerator] %s/%s: %s", owner, repo, msg)
            if on_progress:
                on_progress(msg)

        # Step 1 — Locate the local clone and read file tree + README
        _progress("reading file tree")
        repo_dir_name = _compute_repo_dir_name(repo_url, repo_type)
        repo_path = os.path.join(ADALFLOW_ROOT, "repos", repo_dir_name)

        if not os.path.isdir(repo_path):
            raise FileNotFoundError(
                f"Local repo not found at {repo_path}. Was it cloned?"
            )

        file_tree, readme = _get_local_file_tree_and_readme(repo_path)
        if not file_tree:
            raise ValueError("Repository file tree is empty")

        # Step 1.5 — Initialize RAG retriever (loads existing embeddings)
        _progress("preparing RAG retriever")
        # Diagnose pkl path
        expected_pkl = os.path.join(ADALFLOW_ROOT, "databases", f"{repo_dir_name}.pkl")
        pkl_exists = os.path.exists(expected_pkl)
        pkl_size = os.path.getsize(expected_pkl) if pkl_exists else 0
        logger.info(
            "[WikiGenerator] PKL diagnosis: expected=%s exists=%s size=%d bytes",
            expected_pkl, pkl_exists, pkl_size,
        )
        if pkl_exists:
            # Quick check: list all pkl files in databases dir
            db_dir = os.path.join(ADALFLOW_ROOT, "databases")
            all_pkls = [f for f in os.listdir(db_dir) if f.endswith(".pkl")] if os.path.isdir(db_dir) else []
            logger.info("[WikiGenerator] All pkl files in databases/: %s", all_pkls)

        rag_instance = None
        try:
            from api.rag import RAG
            rag_instance = RAG(provider=self.provider, model=self.model)
            rag_instance.prepare_retriever(
                repo_url, repo_type, access_token,
            )
            logger.info("RAG retriever ready with %d documents",
                        len(rag_instance.transformed_docs))
        except Exception as exc:
            logger.warning("RAG init failed, will generate pages without context: %s", exc)
            rag_instance = None

        # Step 2 — Generate wiki structure via LLM
        _progress("generating wiki structure")
        structure_prompt = _wiki_structure_prompt(
            owner, repo, file_tree, readme, self.language,
        )
        structure_response = await _call_llm(
            self.provider, self.model, structure_prompt,
            label=f"wiki_structure:{owner}/{repo}",
        )
        parsed = _parse_wiki_structure_xml(structure_response)

        wiki_structure = {
            "id": "root",
            "title": parsed["title"],
            "description": parsed["description"],
            "pages": [],       # will be filled with full page objects
            "sections": parsed.get("sections", []),
            "rootSections": parsed.get("rootSections", []),
        }

        # Step 3 — Generate content for each page (in parallel)
        generated_pages: Dict[str, dict] = {}
        total_pages = len(parsed["pages"])
        _progress(f"generating {total_pages} pages in parallel")

        async def _generate_one(idx: int, page_stub: dict) -> dict:
            # Retrieve relevant code context via RAG
            rag_context = ""
            if rag_instance is not None:
                try:
                    query = f"{page_stub['title']} " + " ".join(page_stub["filePaths"][:5])
                    retrieved = rag_instance(query, language=self.language)
                    if retrieved and retrieved[0].documents:
                        docs_by_file: Dict[str, List[str]] = {}
                        for doc in retrieved[0].documents:
                            fp = doc.meta_data.get("file_path", "unknown")
                            if fp not in docs_by_file:
                                docs_by_file[fp] = []
                            docs_by_file[fp].append(doc.text)
                        parts = []
                        for fp, texts in docs_by_file.items():
                            parts.append(f"## File Path: {fp}\n\n" + "\n\n".join(texts))
                        rag_context = "\n\n----------\n\n".join(parts)
                except Exception as exc:
                    logger.warning("RAG retrieval failed for page '%s': %s",
                                   page_stub["title"], exc)

            logger.info(
                "[WikiGenerator] %s/%s: page %d/%d RAG context chars=%d for '%s'",
                owner, repo, idx, total_pages, len(rag_context), page_stub["title"],
            )

            page_prompt = _page_content_prompt(
                page_stub["title"], page_stub["filePaths"], self.language,
                rag_context=rag_context,
            )
            content = await _call_llm(
                self.provider, self.model, page_prompt,
                label=f"page:{owner}/{repo}:{page_stub['title']}",
            )
            logger.info(
                "[WikiGenerator] %s/%s: page %d/%d done: %s (response chars=%d)",
                owner, repo, idx, total_pages, page_stub["title"], len(content),
            )
            return {
                "id": page_stub["id"],
                "title": page_stub["title"],
                "content": content,
                "filePaths": page_stub["filePaths"],
                "importance": page_stub["importance"],
                "relatedPages": page_stub["relatedPages"],
            }

        page_results = await asyncio.gather(
            *[
                _generate_one(idx, stub)
                for idx, stub in enumerate(parsed["pages"], 1)
            ]
        )

        for page_obj in page_results:
            wiki_structure["pages"].append(page_obj)
            generated_pages[page_obj["id"]] = page_obj

        # Step 4 — Save to wiki cache
        _progress("saving wiki cache")
        cache_data = {
            "wiki_structure": wiki_structure,
            "generated_pages": generated_pages,
            "repo": {
                "owner": owner,
                "repo": repo,
                "type": repo_type,
                "repoUrl": repo_url,
            },
            "provider": self.provider,
            "model": self.model,
        }

        os.makedirs(WIKI_CACHE_DIR, exist_ok=True)
        # Encode "/" as "--" for nested GitLab group owners (e.g. "bas/rpc" → "bas--rpc")
        safe_owner = owner.replace("/", "--")
        filename = f"deepwiki_cache_{repo_type}_{safe_owner}_{repo}_{self.language}.json"
        cache_path = os.path.join(WIKI_CACHE_DIR, filename)

        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=2, ensure_ascii=False)

        logger.info("Wiki cache saved to %s", cache_path)
        _progress("done")
        return cache_data
