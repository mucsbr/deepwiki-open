"""
Repository Relations Analysis Engine

Discovers relationships between indexed repositories through:
1. Code dependency scanning (package.json, requirements.txt, go.mod, etc.)
2. LLM-based semantic analysis of repository summaries

Results are persisted to ~/.adalflow/metadata/repo_relations.json
"""

import glob as glob_mod
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from adalflow.utils import get_adalflow_default_root_path

from api.metadata_store import get_all_indexed_projects

logger = logging.getLogger(__name__)

RELATIONS_FILE = os.path.join(
    get_adalflow_default_root_path(), "metadata", "repo_relations.json"
)

# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _ensure_dir() -> None:
    os.makedirs(os.path.dirname(RELATIONS_FILE), exist_ok=True)


def load_relations() -> dict:
    """Load the persisted relations graph, or return empty structure."""
    _ensure_dir()
    if not os.path.exists(RELATIONS_FILE):
        return {"analyzed_at": None, "repos": {}, "edges": []}
    try:
        with open(RELATIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("Failed to load repo relations: %s", e)
        return {"analyzed_at": None, "repos": {}, "edges": []}


def save_relations(data: dict) -> None:
    _ensure_dir()
    try:
        with open(RELATIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error("Failed to save repo relations: %s", e)


# ---------------------------------------------------------------------------
# Code dependency scanning
# ---------------------------------------------------------------------------

# Map of dependency file -> parser function
# Each parser returns a list of package name strings.


def _parse_package_json(path: str) -> List[str]:
    """Extract dependency names from package.json."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        deps: List[str] = []
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            if key in data and isinstance(data[key], dict):
                deps.extend(data[key].keys())
        return deps
    except Exception as e:
        logger.debug("Failed to parse %s: %s", path, e)
        return []


def _parse_requirements_txt(path: str) -> List[str]:
    """Extract package names from requirements.txt."""
    deps = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                # Remove version specifiers
                name = re.split(r"[>=<!\[\];@]", line)[0].strip()
                if name:
                    deps.append(name.lower())
    except Exception as e:
        logger.debug("Failed to parse %s: %s", path, e)
    return deps


def _parse_pyproject_toml(path: str) -> List[str]:
    """Extract dependency names from pyproject.toml (basic regex approach)."""
    deps = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        # Match dependencies = [...] sections
        for m in re.finditer(r'dependencies\s*=\s*\[(.*?)\]', content, re.DOTALL):
            block = m.group(1)
            for dep_match in re.finditer(r'"([^"]+)"', block):
                name = re.split(r"[>=<!\[\];@]", dep_match.group(1))[0].strip()
                if name:
                    deps.append(name.lower())
    except Exception as e:
        logger.debug("Failed to parse %s: %s", path, e)
    return deps


def _parse_go_mod(path: str) -> List[str]:
    """Extract module paths from go.mod require block."""
    deps = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        # Match require (...) block
        for m in re.finditer(r'require\s*\((.*?)\)', content, re.DOTALL):
            block = m.group(1)
            for line in block.strip().splitlines():
                parts = line.strip().split()
                if parts and not parts[0].startswith("//"):
                    deps.append(parts[0])
        # Match single-line require
        for m in re.finditer(r'^require\s+(\S+)', content, re.MULTILINE):
            deps.append(m.group(1))
    except Exception as e:
        logger.debug("Failed to parse %s: %s", path, e)
    return deps


def _parse_pom_xml(path: str) -> List[str]:
    """Extract groupId:artifactId from pom.xml dependencies (regex)."""
    deps = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        for m in re.finditer(
            r"<dependency>\s*<groupId>(.*?)</groupId>\s*<artifactId>(.*?)</artifactId>",
            content,
            re.DOTALL,
        ):
            deps.append(f"{m.group(1)}:{m.group(2)}")
    except Exception as e:
        logger.debug("Failed to parse %s: %s", path, e)
    return deps


def _parse_build_gradle(path: str) -> List[str]:
    """Extract dependency strings from build.gradle."""
    deps = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        # Match patterns like implementation 'group:artifact:version'
        for m in re.finditer(r"(?:implementation|api|compile)\s+['\"]([^'\"]+)['\"]", content):
            deps.append(m.group(1).rsplit(":", 1)[0])  # drop version
    except Exception as e:
        logger.debug("Failed to parse %s: %s", path, e)
    return deps


# Dependency file name -> parser
_DEPENDENCY_PARSERS = {
    "package.json": _parse_package_json,
    "requirements.txt": _parse_requirements_txt,
    "pyproject.toml": _parse_pyproject_toml,
    "go.mod": _parse_go_mod,
    "pom.xml": _parse_pom_xml,
    "build.gradle": _parse_build_gradle,
}


def scan_repo_dependencies(repo_dir: str) -> Tuple[List[str], List[str]]:
    """Scan a repo directory for dependencies.

    Returns:
        (dependency_names, tech_stack)
    """
    deps: List[str] = []
    tech_stack: set = set()

    for dep_file, parser in _DEPENDENCY_PARSERS.items():
        dep_path = os.path.join(repo_dir, dep_file)
        if os.path.exists(dep_path):
            found = parser(dep_path)
            deps.extend(found)
            # Infer tech stack
            if dep_file == "package.json":
                tech_stack.add("javascript")
            elif dep_file in ("requirements.txt", "pyproject.toml"):
                tech_stack.add("python")
            elif dep_file == "go.mod":
                tech_stack.add("go")
            elif dep_file in ("pom.xml", "build.gradle"):
                tech_stack.add("java")

    return deps, sorted(tech_stack)


# ---------------------------------------------------------------------------
# Import statement scanning (Layer 2)
# ---------------------------------------------------------------------------

# Source file extensions to scan for imports
_IMPORT_EXTENSIONS = (".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".java")

# Standard library / common packages to filter out per language
_PYTHON_STDLIB = frozenset({
    "os", "sys", "re", "json", "math", "time", "datetime", "collections",
    "functools", "itertools", "operator", "string", "textwrap", "unicodedata",
    "struct", "codecs", "io", "pathlib", "tempfile", "shutil", "glob",
    "fnmatch", "stat", "filecmp", "hashlib", "hmac", "secrets", "pickle",
    "shelve", "marshal", "dbm", "sqlite3", "csv", "configparser", "tomllib",
    "netrc", "plistlib", "typing", "dataclasses", "enum", "abc", "copy",
    "pprint", "reprlib", "numbers", "decimal", "fractions", "random",
    "statistics", "logging", "warnings", "traceback", "threading",
    "multiprocessing", "concurrent", "subprocess", "sched", "queue",
    "contextvars", "socket", "ssl", "select", "selectors", "signal",
    "asyncio", "http", "urllib", "email", "html", "xml", "webbrowser",
    "cgi", "wsgiref", "xmlrpc", "ftplib", "poplib", "imaplib", "smtplib",
    "uuid", "ctypes", "unittest", "doctest", "pdb", "profile", "timeit",
    "dis", "inspect", "importlib", "pkgutil", "zipimport", "compileall",
    "platform", "errno", "token", "tokenize", "ast", "symtable", "types",
    "builtins", "contextlib", "atexit", "gc", "site", "argparse",
    "getopt", "getpass", "curses", "readline", "rlcompleter",
    "__future__", "_thread",
})

_JS_COMMON_PACKAGES = frozenset({
    "react", "react-dom", "next", "vue", "angular", "svelte",
    "express", "koa", "fastify", "hapi", "nest", "nestjs",
    "lodash", "underscore", "ramda", "moment", "dayjs", "date-fns",
    "axios", "node-fetch", "got", "superagent", "request",
    "webpack", "vite", "rollup", "esbuild", "parcel", "turbopack",
    "babel", "typescript", "ts-node", "tsx",
    "jest", "mocha", "chai", "vitest", "cypress", "playwright",
    "eslint", "prettier", "stylelint",
    "tailwindcss", "sass", "less", "postcss", "autoprefixer",
    "redux", "mobx", "zustand", "recoil", "jotai", "pinia", "vuex",
    "path", "fs", "url", "util", "crypto", "http", "https", "os",
    "stream", "events", "buffer", "child_process", "cluster", "net",
    "querystring", "zlib", "assert", "process",
    "dotenv", "cors", "helmet", "morgan", "body-parser", "cookie-parser",
    "jsonwebtoken", "bcrypt", "uuid", "chalk", "commander", "yargs",
    "winston", "pino", "debug",
})

_GO_STDLIB = frozenset({
    "fmt", "os", "io", "log", "net", "sync", "time", "math", "sort",
    "strings", "strconv", "bytes", "bufio", "errors", "context",
    "encoding", "encoding/json", "encoding/xml", "encoding/csv",
    "encoding/base64", "encoding/binary", "encoding/hex",
    "net/http", "net/url", "net/smtp", "net/rpc",
    "io/ioutil", "io/fs", "os/exec", "os/signal",
    "path", "path/filepath", "regexp", "reflect", "runtime",
    "crypto", "crypto/sha256", "crypto/md5", "crypto/tls",
    "database/sql", "flag", "testing", "embed", "html", "html/template",
    "text/template", "archive", "compress",
})

_JAVA_STDLIB_PREFIXES = (
    "java.", "javax.", "jakarta.",
    "org.springframework", "org.apache.commons", "org.junit",
    "org.slf4j", "org.mockito", "com.google.common", "com.fasterxml",
    "org.hibernate", "org.gradle", "org.jetbrains",
)


def _extract_python_imports(content: str) -> Set[str]:
    """Extract top-level package names from Python import statements."""
    imports = set()
    for line in content.splitlines():
        stripped = line.strip()
        m = re.match(r'^import\s+(\S+)', stripped)
        if m:
            imports.add(m.group(1).split(".")[0])
            continue
        m = re.match(r'^from\s+(\S+)\s+import', stripped)
        if m:
            pkg = m.group(1)
            if pkg.startswith("."):  # skip relative imports
                continue
            top = pkg.split(".")[0]
            if top:
                imports.add(top)
    return imports


def _extract_js_imports(content: str) -> Set[str]:
    """Extract package names from JS/TS import/require statements."""
    imports = set()
    # ES module imports: import ... from "pkg" / import "pkg"
    for m in re.finditer(r'''(?:from|import)\s+['"]([^'"]+)['"]''', content):
        pkg = m.group(1)
        if not pkg.startswith("."):  # skip relative imports
            # For scoped packages like @org/pkg, keep @org/pkg
            if pkg.startswith("@"):
                parts = pkg.split("/")
                imports.add("/".join(parts[:2]) if len(parts) >= 2 else pkg)
            else:
                imports.add(pkg.split("/")[0])
    # CommonJS require
    for m in re.finditer(r'''require\(\s*['"]([^'"]+)['"]\s*\)''', content):
        pkg = m.group(1)
        if not pkg.startswith("."):
            if pkg.startswith("@"):
                parts = pkg.split("/")
                imports.add("/".join(parts[:2]) if len(parts) >= 2 else pkg)
            else:
                imports.add(pkg.split("/")[0])
    return imports


def _extract_go_imports(content: str) -> Set[str]:
    """Extract module paths from Go import statements."""
    imports = set()
    # import block: import ( ... )
    for m in re.finditer(r'import\s*\((.*?)\)', content, re.DOTALL):
        block = m.group(1)
        for line in block.strip().splitlines():
            line = line.strip().strip('"').strip()
            if line and not line.startswith("//"):
                # Remove alias if present
                parts = line.split()
                path = parts[-1].strip('"') if parts else line.strip('"')
                if path:
                    imports.add(path)
    # single import: import "path"
    for m in re.finditer(r'^import\s+"([^"]+)"', content, re.MULTILINE):
        imports.add(m.group(1))
    return imports


def _extract_java_imports(content: str) -> Set[str]:
    """Extract package names from Java import statements."""
    imports = set()
    for m in re.finditer(r'^import\s+(?:static\s+)?([\w.]+)', content, re.MULTILINE):
        imports.add(m.group(1))
    return imports


_IMPORT_EXTRACTORS = {
    ".py": _extract_python_imports,
    ".js": _extract_js_imports,
    ".ts": _extract_js_imports,
    ".jsx": _extract_js_imports,
    ".tsx": _extract_js_imports,
    ".go": _extract_go_imports,
    ".java": _extract_java_imports,
}


def _filter_imports(imports: Set[str], ext: str) -> Set[str]:
    """Remove stdlib and well-known public packages from import set."""
    if ext == ".py":
        return imports - _PYTHON_STDLIB
    elif ext in (".js", ".ts", ".jsx", ".tsx"):
        return {i for i in imports if i.lower().split("/")[-1] not in _JS_COMMON_PACKAGES
                and i.lower() not in _JS_COMMON_PACKAGES}
    elif ext == ".go":
        return {i for i in imports if i not in _GO_STDLIB
                and not any(i.startswith(p) for p in ("fmt", "net/", "os/",
                            "io/", "encoding/", "crypto/", "archive/",
                            "compress/", "database/", "html/", "text/"))}
    elif ext == ".java":
        return {i for i in imports
                if not any(i.startswith(p) for p in _JAVA_STDLIB_PREFIXES)}
    return imports


def scan_repo_imports(repo_dir: str) -> List[str]:
    """Scan source files for import statements (root + 1-level subdirs only).

    Returns a deduplicated list of top-level non-stdlib import names.
    """
    all_imports: Set[str] = set()

    # Collect files from root and one-level subdirectories only
    source_files: List[str] = []
    for ext in _IMPORT_EXTENSIONS:
        # Root directory files
        source_files.extend(glob_mod.glob(os.path.join(repo_dir, f"*{ext}")))
        # One-level subdirectory files
        source_files.extend(glob_mod.glob(os.path.join(repo_dir, f"*/*{ext}")))

    for file_path in source_files:
        _, ext = os.path.splitext(file_path)
        extractor = _IMPORT_EXTRACTORS.get(ext)
        if not extractor:
            continue
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(64 * 1024)  # Read at most 64KB per file
            raw_imports = extractor(content)
            filtered = _filter_imports(raw_imports, ext)
            all_imports.update(filtered)
        except Exception as e:
            logger.debug("Failed to extract imports from %s: %s", file_path, e)

    return sorted(all_imports)


# ---------------------------------------------------------------------------
# Import-based LLM relation analysis (Layer 2)
# ---------------------------------------------------------------------------


async def _analyze_import_relations(
    repos_imports: Dict[str, List[str]],
    repos_info_list: List[Dict[str, Any]],
    provider: str = None,
    model: str = None,
) -> List[dict]:
    """Use LLM to match import statements against indexed repository list.

    Returns edges with confidence levels (high/medium).
    """
    # Filter repos that actually have imports
    repos_with_imports = {k: v for k, v in repos_imports.items() if v}
    if not repos_with_imports or len(repos_info_list) < 2:
        return []

    # Build indexed repos summary
    repos_summary = ""
    for info in repos_info_list:
        desc = info.get("description", "No description")
        tech = ", ".join(info.get("tech_stack", [])) or "unknown"
        repos_summary += f"- {info['path']}: {desc} ({tech})\n"

    # Build import summaries (max 50 imports per repo)
    MAX_IMPORTS_PER_REPO = 50
    BATCH_SIZE = 30

    # Split into batches if needed
    repo_keys = list(repos_with_imports.keys())
    all_edges: List[dict] = []

    for batch_start in range(0, len(repo_keys), BATCH_SIZE):
        batch_keys = repo_keys[batch_start:batch_start + BATCH_SIZE]

        import_summaries = ""
        for repo_path in batch_keys:
            imports = repos_with_imports[repo_path][:MAX_IMPORTS_PER_REPO]
            import_summaries += f"### {repo_path}\nImports: {', '.join(imports)}\n\n"

        prompt = f"""Below are import statement summaries from multiple repositories, and a list of all indexed repositories.
Determine which imports actually reference other repositories in the indexed list.

## Indexed Repositories
{repos_summary}

## Repository Import Summaries
{import_summaries}

## Task
Return a JSON array. Each element:
{{"from": "owner/repo-a", "to": "owner/repo-b", "type": "depends_on", "description": "imports xyz.client", "confidence": "high"}}

confidence levels:
- "high": import name directly matches or clearly corresponds to a repository name
- "medium": inferred match based on naming patterns or context

Only return results with confidence "high" or "medium".
If no cross-repo imports are found, return an empty array [].
Respond ONLY with the JSON array, no other text."""

        try:
            from api.wiki_generator import _call_llm_inner
            from api.config import configs

            effective_provider = provider or configs.get("default_provider", "openai")
            effective_model = model
            if not effective_model:
                provider_cfg = configs.get("providers", {}).get(effective_provider, {})
                effective_model = provider_cfg.get("default_model", "")

            text = await _call_llm_inner(
                effective_provider, effective_model, prompt,
                label="import_relation_analysis",
            )

            # Parse JSON from response
            text = text.strip()
            if text.startswith("```"):
                text = re.sub(r"^```\w*\n?", "", text)
                text = re.sub(r"\n?```$", "", text)
            text = text.strip()

            relations = json.loads(text)
            if not isinstance(relations, list):
                continue

            valid_paths = {info["path"] for info in repos_info_list}
            for rel in relations:
                if (
                    isinstance(rel, dict)
                    and rel.get("from") in valid_paths
                    and rel.get("to") in valid_paths
                    and rel.get("from") != rel.get("to")
                    and rel.get("confidence") in ("high", "medium")
                ):
                    # Map medium confidence to likely_depends_on
                    edge_type = "depends_on" if rel["confidence"] == "high" else "likely_depends_on"
                    all_edges.append({
                        "from": rel["from"],
                        "to": rel["to"],
                        "type": edge_type,
                        "description": f"Import analysis: {rel.get('description', '')}",
                        "confidence": rel["confidence"],
                    })

        except Exception as e:
            logger.error("LLM import analysis failed (batch %d): %s", batch_start, e)

    return all_edges


# ---------------------------------------------------------------------------
# Wiki cache reader (for summaries)
# ---------------------------------------------------------------------------


def _get_repo_summary(owner: str, repo: str) -> Optional[Dict[str, str]]:
    """Read wiki cache to extract title + description for a repo."""
    wikicache_dir = os.path.join(get_adalflow_default_root_path(), "wikicache")
    # Try common patterns
    for repo_type in ("gitlab", "github", "bitbucket"):
        for lang in ("en", "zh", "ja"):
            safe_owner = owner.replace("/", "--")
            filename = f"deepwiki_cache_{repo_type}_{safe_owner}_{repo}_{lang}.json"
            cache_path = os.path.join(wikicache_dir, filename)
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    ws = data.get("wiki_structure", {})
                    return {
                        "title": ws.get("title", ""),
                        "description": ws.get("description", ""),
                    }
                except Exception:
                    continue
    return None


# ---------------------------------------------------------------------------
# Dependency matching across indexed repos
# ---------------------------------------------------------------------------


def _match_dependencies_to_repos(
    all_repos: Dict[str, Dict],
    repo_deps: Dict[str, List[str]],
) -> List[dict]:
    """Match dependency names against indexed repo names to find edges."""
    edges: List[dict] = []
    # Build a lookup: repo short name -> full path
    name_to_path: Dict[str, str] = {}
    for path in all_repos:
        parts = path.split("/")
        short_name = parts[-1].lower() if parts else path.lower()
        name_to_path[short_name] = path
        # Also add with hyphens/underscores normalized
        normalized = short_name.replace("-", "_")
        name_to_path[normalized] = path

    for repo_path, deps in repo_deps.items():
        for dep in deps:
            dep_lower = dep.lower().split("/")[-1]  # handle scoped packages
            dep_normalized = dep_lower.replace("-", "_")
            matched_path = name_to_path.get(dep_lower) or name_to_path.get(dep_normalized)
            if matched_path and matched_path != repo_path:
                edges.append({
                    "from": repo_path,
                    "to": matched_path,
                    "type": "depends_on",
                    "description": f"Code dependency: {dep}",
                })

    return edges


# ---------------------------------------------------------------------------
# LLM semantic relation analysis
# ---------------------------------------------------------------------------


async def _analyze_semantic_relations(
    repos_info: List[Dict[str, Any]],
    provider: str = None,
    model: str = None,
) -> List[dict]:
    """Use LLM to infer semantic relationships between repositories."""
    if len(repos_info) < 2:
        return []

    # Build the prompt
    repo_list_text = ""
    for info in repos_info:
        repo_list_text += (
            f"- **{info['path']}**: {info.get('description', 'No description')}"
            f" (tech: {', '.join(info.get('tech_stack', []))})\n"
        )

    prompt = f"""Analyze the following list of software repositories and identify relationships between them.

## Repositories

{repo_list_text}

## Task

For each pair of repositories that have a meaningful relationship, output a JSON array of relationship objects.
Each relationship object should have:
- "from": repository path (the one that depends on or relates to the other)
- "to": repository path (the one being depended on or related to)
- "type": one of "provides_api_for", "shares_protocol", "upstream", "downstream", "related"
- "description": brief explanation of the relationship

Only include relationships where you are reasonably confident based on the names, descriptions, and tech stacks.
If no relationships are found, return an empty array.

Respond ONLY with the JSON array, no other text."""

    try:
        from api.wiki_generator import _call_llm_inner
        from api.config import configs

        effective_provider = provider or configs.get("default_provider", "openai")
        effective_model = model
        if not effective_model:
            provider_cfg = configs.get("providers", {}).get(effective_provider, {})
            effective_model = provider_cfg.get("default_model", "")

        text = await _call_llm_inner(
            effective_provider, effective_model, prompt,
            label="semantic_relation_analysis",
        )

        # Parse JSON from response
        # Strip markdown code fences if present
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        text = text.strip()

        relations = json.loads(text)
        if not isinstance(relations, list):
            return []

        # Validate each relation
        valid = []
        valid_paths = {info["path"] for info in repos_info}
        for rel in relations:
            if (
                isinstance(rel, dict)
                and rel.get("from") in valid_paths
                and rel.get("to") in valid_paths
                and rel.get("from") != rel.get("to")
                and rel.get("type")
                in (
                    "provides_api_for",
                    "shares_protocol",
                    "upstream",
                    "downstream",
                    "related",
                )
            ):
                valid.append(rel)
        return valid

    except Exception as e:
        logger.error("LLM semantic analysis failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# Main analysis orchestrator
# ---------------------------------------------------------------------------

# Module-level analysis status
_analysis_status: dict = {
    "running": False,
    "progress": "",
    "error": None,
}


def get_analysis_status() -> dict:
    return dict(_analysis_status)


async def analyze_all_relations(
    provider: str = None,
    model: str = None,
) -> dict:
    """Run full relation analysis across all indexed repos.

    Three-layer analysis:
    1. Dependency declaration matching (package.json, requirements.txt, etc.)
    2. Import statement scanning + LLM matching (new)
    3. Wiki semantic analysis via LLM
    """
    global _analysis_status

    if _analysis_status["running"]:
        raise RuntimeError("Analysis already in progress")

    _analysis_status = {"running": True, "progress": "Starting analysis...", "error": None}

    try:
        repos_root = os.path.join(get_adalflow_default_root_path(), "repos")
        indexed = get_all_indexed_projects()

        if not indexed:
            _analysis_status["progress"] = "No indexed projects found"
            result = {"analyzed_at": datetime.now(timezone.utc).isoformat(), "repos": {}, "edges": []}
            save_relations(result)
            return result

        _analysis_status["progress"] = f"Scanning {len(indexed)} repositories..."

        # Step 1 & 2: gather info, scan dependencies, and scan imports
        repos_info: Dict[str, Dict] = {}
        repo_deps: Dict[str, List[str]] = {}
        repos_imports: Dict[str, List[str]] = {}
        repos_info_list: List[Dict[str, Any]] = []

        for project_path, meta in indexed.items():
            if meta.get("status") != "indexed":
                continue

            parts = project_path.split("/")
            owner = "/".join(parts[:-1]) if len(parts) > 1 else parts[0]
            repo = parts[-1] if len(parts) > 1 else parts[0]

            # Get wiki summary
            summary = _get_repo_summary(owner, repo)

            # Scan code dependencies
            repo_dir = meta.get("repo_path", "")
            if not repo_dir or not os.path.isdir(repo_dir):
                # Try default path
                safe_name = project_path.replace("/", "_")
                repo_dir = os.path.join(repos_root, safe_name)

            deps: List[str] = []
            tech_stack: List[str] = []
            imports: List[str] = []
            if os.path.isdir(repo_dir):
                deps, tech_stack = scan_repo_dependencies(repo_dir)
                imports = scan_repo_imports(repo_dir)

            repo_deps[project_path] = deps
            repos_imports[project_path] = imports

            info = {
                "path": project_path,
                "summary": summary.get("description", "") if summary else "",
                "tech_stack": tech_stack,
                "related": [],
            }
            repos_info[project_path] = info
            repos_info_list.append({
                "path": project_path,
                "description": summary.get("description", "") if summary else "",
                "tech_stack": tech_stack,
            })

        # Step 3: Match code dependencies (Layer 1)
        _analysis_status["progress"] = "Matching code dependencies..."
        code_edges = _match_dependencies_to_repos(repos_info, repo_deps)
        logger.info("Found %d code dependency edges", len(code_edges))

        # Step 4: Import scanning + LLM matching (Layer 2)
        import_edges: List[dict] = []
        if len(repos_info_list) >= 2:
            _analysis_status["progress"] = "Analyzing import statements with LLM..."
            try:
                import_edges = await _analyze_import_relations(
                    repos_imports, repos_info_list, provider=provider, model=model
                )
                logger.info("Found %d import-based edges", len(import_edges))
            except Exception as e:
                logger.warning("Import analysis failed (non-fatal): %s", e)

        # Step 5: LLM semantic analysis (Layer 3)
        semantic_edges: List[dict] = []
        if len(repos_info_list) >= 2:
            _analysis_status["progress"] = "Running LLM semantic analysis..."
            try:
                semantic_edges = await _analyze_semantic_relations(
                    repos_info_list, provider=provider, model=model
                )
                logger.info("Found %d semantic edges", len(semantic_edges))
            except Exception as e:
                logger.warning("Semantic analysis failed (non-fatal): %s", e)

        # Step 6: Merge edges with priority (Layer 1 > Layer 2 > Layer 3)
        _analysis_status["progress"] = "Merging results..."
        # Track seen (from, to) pairs to enforce priority
        seen_pairs: set = set()
        all_edges: List[dict] = []

        # Layer 1: code dependency edges (highest priority)
        for edge in code_edges:
            pair = (edge["from"], edge["to"])
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                all_edges.append(edge)

        # Layer 2: import-based edges (high confidence first, then medium)
        import_high = [e for e in import_edges if e.get("confidence") == "high"]
        import_medium = [e for e in import_edges if e.get("confidence") == "medium"]
        for edge in import_high + import_medium:
            pair = (edge["from"], edge["to"])
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                all_edges.append(edge)

        # Layer 3: semantic edges (supplementary)
        for edge in semantic_edges:
            pair = (edge["from"], edge["to"])
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                all_edges.append(edge)

        # Update related lists in repos_info
        for edge in all_edges:
            src = edge["from"]
            dst = edge["to"]
            if src in repos_info and dst not in repos_info[src].get("related", []):
                repos_info[src].setdefault("related", []).append(dst)
            if dst in repos_info and src not in repos_info[dst].get("related", []):
                repos_info[dst].setdefault("related", []).append(src)

        result = {
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "repos": repos_info,
            "edges": all_edges,
        }
        save_relations(result)

        _analysis_status["progress"] = f"Analysis complete. Found {len(all_edges)} relationships."
        return result

    except Exception as e:
        logger.error("Relation analysis failed: %s", e)
        _analysis_status["error"] = str(e)
        raise
    finally:
        _analysis_status["running"] = False


def get_related_repos(repo_path: str) -> List[str]:
    """Get related repository paths for a given repo from cached relations."""
    data = load_relations()
    repo_info = data.get("repos", {}).get(repo_path, {})
    return repo_info.get("related", [])


def generate_mermaid_graph(data: Optional[dict] = None) -> str:
    """Generate a Mermaid graph LR diagram from relations data."""
    if data is None:
        data = load_relations()

    edges = data.get("edges", [])
    if not edges:
        return "graph LR\n  A[No relationships found]"

    lines = ["graph LR"]

    # Collect all unique nodes
    nodes: set = set()
    for edge in edges:
        nodes.add(edge["from"])
        nodes.add(edge["to"])

    # Create node IDs (sanitize for Mermaid)
    node_ids: Dict[str, str] = {}
    for i, node in enumerate(sorted(nodes)):
        safe_id = f"N{i}"
        short_name = node.split("/")[-1]
        node_ids[node] = safe_id
        lines.append(f'  {safe_id}["{short_name}"]')

    # Add edges with labels
    edge_styles = {
        "depends_on": "-->",
        "likely_depends_on": "-.->",
        "provides_api_for": "-.->",
        "shares_protocol": "<-->",
        "upstream": "-->",
        "downstream": "-->",
        "related": "---",
    }

    for edge in edges:
        src_id = node_ids.get(edge["from"], "")
        dst_id = node_ids.get(edge["to"], "")
        if src_id and dst_id:
            arrow = edge_styles.get(edge["type"], "-->")
            label = edge["type"].replace("_", " ")
            lines.append(f"  {src_id} {arrow}|{label}| {dst_id}")

    return "\n".join(lines)
