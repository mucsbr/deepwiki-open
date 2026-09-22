# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is DeepWiki

DeepWiki is an enterprise AI-powered tool that generates interactive wikis from GitLab (primary), GitHub, and Bitbucket repositories. It features GitLab SSO authentication, batch indexing, an admin dashboard, MCP server integration for external agents, cross-repo product analysis, and repository dependency visualization. It clones repos, creates code embeddings, generates documentation via LLM, produces Mermaid diagrams, and organizes everything into a navigable wiki. It also supports conversational Q&A (Ask), cross-repo Global Ask, and multi-turn research (DeepResearch).

## Architecture

This is a two-process application:

- **Frontend**: Next.js 15 (React 19, TypeScript, Tailwind CSS 4) on port 3000
- **Backend**: Python FastAPI (uvicorn) on port 8001

The frontend proxies API requests to the backend via Next.js rewrites configured in `next.config.ts`. Any new backend route reachable from the browser must be added to the `rewrites()` list there, or it will 404 in dev.

### The two wiki generation paths

This is the single most important thing to understand before touching wiki code. There are **two independent implementations** that produce the same `wikicache` JSON:

1. **Interactive / frontend-driven** — The browser drives generation itself. `src/app/[owner]/[repo]/page.tsx` opens a WebSocket to `/ws/chat` (`api/websocket_wiki.py:handle_websocket_chat`), first asking the LLM for the wiki structure, then looping over pages and requesting each one's content. The frontend assembles the result and POSTs it to `/api/wiki_cache`. There is **no** `/ws/wiki/generate` endpoint — `/ws/chat` is the only WebSocket route (`api/api.py:419`). If the socket fails, the page falls back to HTTP `POST /api/chat/stream`, which proxies to the backend's `/chat/completions/stream` (`api/simple_chat.py:chat_completions_stream`, attached via `add_api_route` at `api/api.py:417`).

2. **Server-side / batch** — `api/wiki_generator.py:WikiGenerator.generate_wiki()` runs the whole pipeline in the backend (file tree → structure → pages → save cache). This is what `api/batch_indexer.py` and the admin `regenerate-wiki` endpoints use. No browser involved.

Changes to prompts or output shape usually need to land in **both** paths. `api/prompts.py` serves path 1; `wiki_generator.py` has its own `_wiki_structure_prompt` / `_page_content_prompt`.

### Backend (`api/`)

**Core:**
- `main.py` — Entry point. Loads `.env`, sets up logging, monkey-patches `watchfiles` to exclude `api/logs/` from hot-reload, optionally starts an APScheduler cron job for batch indexing (`BATCH_INDEX_SCHEDULE`), then runs uvicorn. Also supports `--batch-index` to run the indexer once and exit.
- `api.py` — Main FastAPI app. REST endpoints for wiki cache CRUD, repo structure, GitLab proxy, health. Mounts the auth router, admin router, and the MCP app at `/mcp`.
- `config.py` — Loads JSON configs from `api/config/`, reads API keys and GitLab settings from env, provides the model client factory. Config JSON supports `${ENV_VAR}` substitution.
- `logging_config.py` — Centralized logging (file + console).

**Authentication & Authorization:**
- `gitlab_auth.py` — GitLab OAuth2 SSO. Routes: `/auth/gitlab/login`, `/auth/gitlab/callback`, `/auth/me`, `/auth/mcp-token`. JWT creation (8h session + 30d MCP token), Fernet encryption for GitLab access tokens stored inside the JWT.
- `gitlab_permission.py` — Repository permission checks against the GitLab API. Two in-memory caches: per-project permission (`PERMISSION_CACHE_TTL`, default 300s) and per-user project list (24h). Functions: `check_repo_access`, `get_user_accessible_projects`, `verify_repo_permission`.

**Admin & Batch Operations:**
- `admin.py` — Admin router (`/api/admin/*`, ~26 routes). GitLab group/project listing, batch indexing triggers, per-project reindex/regenerate-wiki/extract-insights, product CRUD, repo-relations analysis, status polling, system stats.
- `batch_indexer.py` — `BatchIndexer.index_project()` runs three stages in order: `reindex_project` (clone + embeddings) → `regenerate_wiki` → `extract_insights`. `run_selected()` can run any single stage. Uses `GITLAB_SERVICE_TOKEN`.
- `metadata_store.py` — JSON store at `~/.adalflow/metadata/index_metadata.json`.

**Retrieval (code + wiki, dual-source):**
- `data_pipeline.py` — Cloning, file reading, splitting, embedding creation. Writes `~/.adalflow/databases/{repo_dir_name}.pkl`.
- `wiki_embedder.py` — Builds a **second** embedding DB from the generated wiki cache, at `{repo_dir_name}_wiki.pkl`. This lets Ask retrieve prose wiki paragraphs alongside raw code. Called at the end of `wiki_generator` and `batch_indexer`.
- `rag.py` — Single-repo RAG (FAISS + adalflow). `prepare_retriever()` for code, `prepare_wiki_retriever()` for wiki. Callers generally want both.
- `multi_rag.py` — Cross-repo equivalents, incl. `prepare_multi_wiki_retriever()`.
- `prompts.py` — Prompt templates for the WebSocket chat path, RAG, and DeepResearch.

**MCP Server:**
- `mcp_server.py` — MCP server exposing DeepWiki tools to external agents (Claude Code, Codex, etc.), mounted at `/mcp` behind Starlette `AuthenticationMiddleware`. Tools: `list_products`, `get_product_overview`, `search_product_code`, `ask_product`, `list_projects`, `get_wiki_summary`, `get_wiki_page`, `search_code`, `get_repo_relations`, `ask_question`, `get_project_insights`, `extract_project_insights`, `get_product_insights`.

**Product & Relations:**
- `product_manager.py` — Products = logical groupings of repos, in `~/.adalflow/metadata/products.json`.
- `repo_relations.py` — Dependency analysis via LLM-assisted import scanning → `~/.adalflow/metadata/repo_relations.json`.
- `insight_extractor.py` — LLM extraction of modules, API endpoints, data models, tech stack per project; aggregates across products.

**LLM Provider Clients** (each wraps a provider SDK with streaming support):
`openai_client.py`, `openrouter_client.py`, `bedrock_client.py`, `azureai_client.py`, `dashscope_client.py`, `google_embedder_client.py`, `ollama_patch.py`

**Shared LLM helper:** `wiki_generator._call_llm_inner` (retry, `<think>`-block stripping, SSE parsing, JSON extraction) is imported by `insight_extractor.py`, `repo_relations.py`, and `mcp_server.py`. Changing its signature ripples into all three.

**Config files** (`api/config/`):
`generator.json` (LLM models per provider), `embedder.json`, `repo.json` (file filters), `lang.json`.

### Frontend (`src/`)

**Pages:** `app/page.tsx` (home, SSO login, project list), `app/[owner]/[repo]/page.tsx` (wiki viewer — also the generation driver, see above), plus `slides/` and `workshop/` views, `app/admin/page.tsx` (dashboard), `app/admin/relations/page.tsx` (ReactFlow dependency graph), `app/ask/page.tsx` (Global Ask), `app/auth/callback/page.tsx`, `app/wiki/projects/page.tsx`.

**Key modules:** `components/Ask.tsx`, `components/Mermaid.tsx`, `components/Markdown.tsx`, `components/WikiTreeView.tsx`, `components/RelationGraph.tsx`; `contexts/AuthContext.tsx` (JWT storage, `getAuthHeaders()`), `contexts/LanguageContext.tsx`; `utils/websocketClient.ts`; `messages/` (10 locales: en, zh, zh-tw, ja, es, kr, vi, fr, ru, pt-br — adding a UI string means updating all of them).

### Data Storage

All persistent data lives under `~/.adalflow/`:
- `repos/` — Cloned repositories
- `databases/` — `{repo}.pkl` (code embeddings) and `{repo}_wiki.pkl` (wiki embeddings)
- `wikicache/` — Generated wiki JSON, named `deepwiki_cache_{repo_type}_{owner}_{repo}_{lang}.json` with `/` in owner replaced by `--`
- `metadata/` — `index_metadata.json`, `products.json`, `repo_relations.json`, project insights

## Development Commands

### Backend

```bash
# Install Python dependencies (Poetry, from project root)
python -m pip install poetry==2.0.1 && poetry install -C api

# Start API server (from project root) — hot-reload unless NODE_ENV=production
python -m api.main

# Alternative via uv (this is what run.sh does)
uv run -m api.main

# Run batch indexing once and exit
python -m api.main --batch-index
```

Python 3.12 (`.python-version`); `pyproject.toml` declares `^3.11`.

### Frontend

```bash
yarn install
yarn dev      # Turbopack, port 3000
yarn build
yarn lint
```

### Docker

```bash
docker-compose up
docker build -t deepwiki-open .
```

### Tests

```bash
# From project root
pytest

# Single file / single test
pytest tests/unit/test_all_embedders.py
pytest tests/unit/test_all_embedders.py::TestEmbedderConfiguration::test_config_loading

# Standalone runner (executes test files as scripts, not via pytest)
python tests/run_tests.py
```

Tests live in two directories: `test/` (just `test_extract_repo_name.py`) and `tests/` (`unit/`, `integration/`, `api/`).

**Known config issue:** `pytest.ini` uses the header `[tool:pytest]`, which is only valid in `setup.cfg`. In a `pytest.ini` file the section must be `[pytest]`. As a result none of its settings apply — `testpaths = test`, the `-v`/`--tb=short` options, and the `unit`/`integration`/`slow`/`network` markers are all silently ignored, so pytest collects both `test/` and `tests/`. The markers are declared but never used anywhere in the suite, so `pytest -m unit` deselects everything. Fix the section header before relying on markers or `testpaths`.

Collection of `test/test_extract_repo_name.py` and `tests/api/test_api.py` fails unless backend deps (`adalflow`, `requests`) are installed in the active environment.

## Environment Variables

A `.env` file in the project root is required. `.env.example` covers only a subset; the authoritative list is `api/config.py`.

**LLM Providers:** `GOOGLE_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_VERSION`, `OLLAMA_HOST` (default `http://localhost:11434`), AWS Bedrock (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_REGION`, `AWS_ROLE_ARN`), and `DEEPWIKI_EMBEDDER_TYPE` (`openai` default, `google`, `ollama`, `bedrock`).

`main.py` warns at startup if `GOOGLE_API_KEY` or `OPENAI_API_KEY` is missing, but does not exit.

**GitLab Enterprise:** `GITLAB_URL`, `GITLAB_CLIENT_ID`, `GITLAB_CLIENT_SECRET`, `GITLAB_SERVICE_TOKEN`, `GITLAB_BATCH_GROUPS` (comma-separated group IDs for scheduled indexing), `JWT_SECRET_KEY`, `ADMIN_USERNAMES` (comma-separated), `PERMISSION_CACHE_TTL` (default 300), `BATCH_INDEX_SCHEDULE` (crontab string; empty disables the scheduler), `FRONTEND_ORIGIN` (default `http://localhost:3000`).

**Server:** `DEEPWIKI_CONFIG_DIR` (default `api/config/`), `SERVER_BASE_URL` (default `http://localhost:8001`), `PORT` (default 8001), `NODE_ENV` (`production` disables backend hot-reload).

## Key Patterns

- **Auth flow**: GitLab OAuth2 SSO → 8h JWT held in `AuthContext`. MCP clients use a 30d JWT from `/auth/mcp-token`. Both are accepted at `/mcp` via `Authorization: Bearer <token>`. WebSocket auth passes the JWT as a `?token=` query parameter (`_verify_ws_auth` in `websocket_wiki.py`), since browsers can't set WS headers.
- **Permission model**: Frontend calls carry the user's token; the backend verifies against GitLab and caches the result in memory. Batch/MCP paths instead use `GITLAB_SERVICE_TOKEN`, which bypasses per-user checks — be careful not to leak service-token-scoped data into user-facing responses.
- **Adding a model** is a `api/config/generator.json` edit, not a code change. Adding a *provider* means a new client following `openai_client.py`.
- **Circular imports** are a live concern in `api/`. The codebase deliberately uses function-local imports (e.g. `from api.wiki_generator import _call_llm_inner` inside a function body) to break cycles. Keep that style when adding cross-module calls.
- `@/*` maps to `./src/*` (`tsconfig.json`). Poetry `package-mode = false` — `api/` is not installable; always run it as `python -m api.main` from the project root.
