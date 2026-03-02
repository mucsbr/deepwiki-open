# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is DeepWiki

DeepWiki is an enterprise AI-powered tool that generates interactive wikis from GitLab (primary), GitHub, and Bitbucket repositories. It features GitLab SSO authentication, batch indexing, an admin dashboard, MCP server integration for external agents, cross-repo product analysis, and repository dependency visualization. It clones repos, creates code embeddings, generates documentation via LLM, produces Mermaid diagrams, and organizes everything into a navigable wiki. It also supports conversational Q&A (Ask), cross-repo Global Ask, and multi-turn research (DeepResearch).

## Architecture

This is a two-process application:

- **Frontend**: Next.js 15 (React 19, TypeScript, Tailwind CSS 4) on port 3000
- **Backend**: Python FastAPI (uvicorn) on port 8001

The frontend proxies API requests to the backend via Next.js rewrites configured in `next.config.ts`. Wiki generation uses WebSocket connections (`/ws/wiki/generate` in backend, client in `src/utils/websocketClient.ts`). Chat/Ask uses WebSocket at `/ws/chat`.

### Backend (`api/`)

**Core:**
- `main.py` — Entry point. Loads `.env`, configures logging, starts uvicorn with hot-reload in dev.
- `api.py` — Main FastAPI app. REST endpoints for wiki cache CRUD, repo structure, health check, GitLab proxy. WebSocket endpoints for wiki generation and chat. Mounts auth router, admin router, and MCP server.
- `config.py` — Loads JSON configs, API keys from env, provides model client factory. Key constants: `GITLAB_URL`, `GITLAB_CLIENT_ID`, `GITLAB_CLIENT_SECRET`, `GITLAB_SERVICE_TOKEN`, `JWT_SECRET_KEY`, `ADMIN_USERNAMES`.
- `logging_config.py` — Centralized logging setup with file and console handlers.

**Authentication & Authorization:**
- `gitlab_auth.py` — GitLab OAuth2 SSO. Routes: `/auth/gitlab/login`, `/auth/gitlab/callback`, `/auth/me`, `/auth/mcp-token`. JWT creation (8h session + 30d MCP token), Fernet encryption for GitLab access tokens stored in JWT.
- `gitlab_permission.py` — Repository permission checking against GitLab API. In-memory caches: per-project permission (TTL from `PERMISSION_CACHE_TTL`, default 300s) and per-user project list (24h TTL). Functions: `check_repo_access`, `get_user_accessible_projects`, `verify_repo_permission`.

**Admin & Batch Operations:**
- `admin.py` — Admin API router (`/api/admin/*`). Endpoints for GitLab groups/projects listing, batch indexing triggers, indexed project management (update/remove/reindex), system stats.
- `batch_indexer.py` — Background batch indexing. Clones repos, builds embeddings, generates wiki for multiple projects. Uses `GITLAB_SERVICE_TOKEN` for repo access.
- `metadata_store.py` — JSON file store at `~/.adalflow/metadata/index_metadata.json`. Tracks indexed project status, timestamps, paths.

**Wiki Generation:**
- `websocket_wiki.py` — WebSocket handler for wiki generation. Orchestrates: clone repo → build embeddings → generate wiki structure → generate page content (streaming).
- `wiki_generator.py` — Core wiki generation logic. `_call_llm_inner` with retry, `<think>` block stripping, JSON extraction.
- `simple_chat.py` — Standalone FastAPI app for chat completions (DeepResearch iterations).
- `data_pipeline.py` — Repository cloning, file reading, text splitting, embedding creation. Uses adalflow for embeddings and local DB storage.
- `rag.py` — RAG (Retrieval Augmented Generation) implementation using FAISS retriever and adalflow.
- `multi_rag.py` — Multi-repo RAG. Searches code across multiple repositories simultaneously.
- `prompts.py` — All LLM prompt templates (wiki generation, RAG, DeepResearch).

**MCP Server:**
- `mcp_server.py` — MCP (Model Context Protocol) server exposing DeepWiki tools to external agents (Claude Code, Codex, etc.). JWT-authenticated via `AuthenticationMiddleware`. Tools: `list_products`, `get_product_overview`, `search_product_code`, `ask_product`, `list_projects`, `get_wiki_summary`, `get_wiki_page`, `search_code`, `get_repo_relations`, `ask_question`, `get_project_insights`, `extract_project_insights`, `get_product_insights`.

**Product & Relations:**
- `product_manager.py` — Product CRUD. Products are logical groupings of repositories stored in `~/.adalflow/metadata/products.json`.
- `repo_relations.py` — Repository dependency analysis. LLM-assisted import scanning, relation graph stored in `~/.adalflow/metadata/repo_relations.json`.
- `insight_extractor.py` — Structured knowledge extraction via LLM. Produces modules, API endpoints, data models, tech stack per project. Aggregates across products.

**LLM Provider Clients** (each wraps a provider's API with streaming support):
`openai_client.py`, `openrouter_client.py`, `bedrock_client.py`, `azureai_client.py`, `dashscope_client.py`, `google_embedder_client.py`, `ollama_patch.py`

**Config files** (`api/config/`):
`generator.json` (LLM models per provider), `embedder.json` (embedding model config), `repo.json` (file filters), `lang.json` (language config).

### Frontend (`src/`)

**Pages:**
- `app/page.tsx` — Home page. GitLab SSO login, authenticated project list grouped by namespace, search, MCP token modal.
- `app/[owner]/[repo]/page.tsx` — Wiki viewer page. Displays generated wiki with tree navigation, Ask panel.
- `app/[owner]/[repo]/slides/page.tsx` — Slides view of wiki content.
- `app/[owner]/[repo]/workshop/page.tsx` — Workshop view.
- `app/admin/page.tsx` — Admin dashboard. Tabs: indexed projects management, batch indexing, product management, system stats.
- `app/admin/relations/page.tsx` — Repository dependency graph visualization (ReactFlow). Group/focus/full view modes, edge filtering.
- `app/ask/page.tsx` — Global Ask. Cross-repo Q&A across all indexed projects.
- `app/auth/callback/page.tsx` — OAuth callback handler. Stores JWT from GitLab SSO redirect.
- `app/wiki/projects/page.tsx` — Lists previously generated wiki projects.

**Key Components:**
- `components/Ask.tsx` — Chat panel with RAG-powered Q&A.
- `components/Mermaid.tsx` — Mermaid diagram renderer.
- `components/Markdown.tsx` — Markdown renderer.
- `components/WikiTreeView.tsx` — Wiki navigation tree.
- `contexts/AuthContext.tsx` — Authentication context. JWT storage, user state, `getAuthHeaders()` helper.
- `contexts/LanguageContext.tsx` — i18n context.
- `utils/websocketClient.ts` — WebSocket client for chat.
- `messages/` — i18n translation files (en, zh, ja, es, kr, vi, fr, ru, pt-br, zh-tw).

### Data Storage

All persistent data is stored under `~/.adalflow/`:
- `repos/` — Cloned repositories
- `databases/` — Embeddings and FAISS indexes
- `wikicache/` — Generated wiki JSON cache
- `metadata/` — Index metadata (`index_metadata.json`), products (`products.json`), repo relations (`repo_relations.json`), project insights

## Development Commands

### Backend

```bash
# Install Python dependencies (Poetry, from project root)
python -m pip install poetry==2.0.1 && poetry install -C api

# Start API server (from project root)
python -m api.main

# Alternative via uv
uv run -m api.main
```

### Frontend

```bash
# Install JS dependencies
yarn install

# Start dev server (Turbopack, port 3000)
yarn dev

# Production build
yarn build

# Lint
yarn lint
```

### Docker

```bash
# Build and run both services
docker-compose up

# Build image locally
docker build -t deepwiki-open .
```

### Tests

```bash
# Run all Python tests (from project root)
pytest

# Run a single test file
pytest test/test_extract_repo_name.py

# Run by marker
pytest -m unit
pytest -m integration

# Tests are in two locations:
#   test/  — contains test_extract_repo_name.py
#   tests/ — structured with tests/unit/, tests/integration/, tests/api/
```

## Environment Variables

A `.env` file in the project root is required. Key variables:

**LLM Providers:**
- `GOOGLE_API_KEY` — For Gemini models and Google embeddings
- `OPENAI_API_KEY` — For OpenAI models and default embeddings
- `OPENROUTER_API_KEY` — For OpenRouter models
- `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_VERSION` — For Azure OpenAI
- `OLLAMA_HOST` — Ollama server URL (default: http://localhost:11434)
- `DEEPWIKI_EMBEDDER_TYPE` — `openai` (default), `google`, `ollama`, or `bedrock`

**GitLab Enterprise Integration:**
- `GITLAB_URL` — GitLab instance URL (e.g. `https://gitlab.example.com`)
- `GITLAB_CLIENT_ID` — OAuth2 application ID for SSO
- `GITLAB_CLIENT_SECRET` — OAuth2 application secret for SSO
- `GITLAB_SERVICE_TOKEN` — Service account token for batch indexing and MCP server repo access
- `JWT_SECRET_KEY` — Secret for signing JWT tokens (session + MCP)
- `ADMIN_USERNAMES` — Comma-separated list of GitLab usernames with admin access
- `PERMISSION_CACHE_TTL` — Per-project permission cache TTL in seconds (default: 300)
- `FRONTEND_ORIGIN` — Frontend URL for OAuth callbacks (default: http://localhost:3000)

**Server:**
- `DEEPWIKI_CONFIG_DIR` — Custom path for config JSON files (default: `api/config/`)
- `SERVER_BASE_URL` — Backend URL for frontend proxy (default: http://localhost:8001)
- `PORT` — Backend API port (default: 8001)

## Key Patterns

- **Authentication flow**: GitLab OAuth2 SSO → JWT (8h) stored in frontend `AuthContext`. MCP clients use long-lived JWT (30d) obtained via `/auth/mcp-token`.
- **Permission model**: Frontend calls pass user's OAuth token; backend verifies against GitLab API. Results cached in-memory (per-project: 5min, project list: 24h).
- **MCP endpoint** (`/mcp`): Wrapped with Starlette `AuthenticationMiddleware`. Accepts both session JWT and MCP token via `Authorization: Bearer <token>`.
- The frontend communicates with the backend primarily through WebSockets for wiki generation and chat. REST is used for cache management, metadata, and auth.
- LLM provider clients all follow a similar pattern: they wrap provider SDKs and expose streaming generation methods. New providers should follow the existing client pattern (see `openai_client.py` as reference).
- Model configuration is declarative in `api/config/generator.json`. Adding a new model means updating this JSON, not code.
- The `@/*` path alias maps to `./src/*` (configured in `tsconfig.json`).
- Python package mode is `false` in Poetry — the api directory is not an installable package, it's run as `python -m api.main`.
