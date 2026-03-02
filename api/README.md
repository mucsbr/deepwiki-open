# DeepWiki API

Backend API server for DeepWiki — AI-powered wiki generation, code search, and cross-repo analysis.

## Features

- **Streaming AI Responses**: Real-time LLM generation via multiple providers
- **GitLab SSO**: OAuth2 authentication with JWT session management
- **RAG & Multi-repo RAG**: Semantic code search across single or multiple repositories
- **Wiki Generation**: WebSocket-based streaming wiki creation with Mermaid diagrams
- **MCP Server**: JWT-authenticated Model Context Protocol endpoint for external agents (Claude Code, Codex, etc.)
- **Admin & Batch Indexing**: Bulk project indexing, management, and monitoring
- **Product Management**: Logical groupings of repositories for cross-repo analysis
- **Repository Relations**: LLM-assisted dependency detection and graph visualization
- **Structured Insights**: LLM-extracted modules, API endpoints, data models, tech stack

## Quick Setup

### Step 1: Install Dependencies

```bash
# From the project root
python -m pip install poetry==2.0.1 && poetry install -C api
```

### Step 2: Set Up Environment Variables

Create a `.env` file in the project root:

```bash
# --- LLM Providers (at least one required) ---
GOOGLE_API_KEY=your_google_api_key
OPENAI_API_KEY=your_openai_api_key
OPENROUTER_API_KEY=your_openrouter_api_key        # Optional
AZURE_OPENAI_API_KEY=your_azure_key                # Optional
AZURE_OPENAI_ENDPOINT=your_azure_endpoint          # Optional
AZURE_OPENAI_VERSION=your_azure_version            # Optional
OLLAMA_HOST=http://localhost:11434                  # Optional

# Embedding provider: openai (default), google, ollama, bedrock
DEEPWIKI_EMBEDDER_TYPE=openai

# --- GitLab Enterprise (optional) ---
GITLAB_URL=https://gitlab.example.com
GITLAB_CLIENT_ID=your_oauth_app_id
GITLAB_CLIENT_SECRET=your_oauth_app_secret
GITLAB_SERVICE_TOKEN=glpat-xxxxxxxxxxxx
JWT_SECRET_KEY=your_jwt_secret
ADMIN_USERNAMES=admin1,admin2
FRONTEND_ORIGIN=http://localhost:3000

# --- Server ---
PORT=8001
```

### Step 3: Start the API Server

```bash
python -m api.main
```

The API will be available at `http://localhost:8001`.

## API Endpoints

### Authentication

| Method | Path | Description |
|--------|------|-------------|
| GET | `/auth/gitlab/login` | Redirect to GitLab OAuth2 |
| GET | `/auth/gitlab/callback` | OAuth2 callback, issues JWT |
| GET | `/auth/me` | Current user info (requires JWT) |
| GET | `/auth/mcp-token` | Issue 30-day MCP token (requires JWT) |

### Wiki Cache

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/wiki_cache` | Retrieve cached wiki |
| POST | `/api/wiki_cache` | Store wiki cache |
| DELETE | `/api/wiki_cache` | Delete wiki cache |
| GET | `/api/processed_projects` | List all cached wiki projects |

### Projects

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/projects` | List indexed projects accessible to current user |

### GitLab Proxy

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/gitlab/project_info` | Fetch project info via user's OAuth token |
| GET | `/api/gitlab/repository_tree` | Fetch repo file tree |
| GET | `/api/gitlab/file_raw` | Fetch raw file content |

### Admin (requires `ADMIN_USERNAMES`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/admin/groups` | List GitLab groups |
| GET | `/api/admin/groups/{id}/projects` | List projects in a group |
| GET | `/api/admin/projects/search` | Search GitLab projects |
| GET | `/api/admin/projects/all` | List all visible projects |
| POST | `/api/admin/batch-index` | Trigger batch indexing |
| GET | `/api/admin/batch-index/status` | Batch indexing progress |
| GET | `/api/admin/indexed-projects` | List indexed project metadata |
| POST | `/api/admin/indexed-projects/{path}/reindex` | Reindex a project |
| DELETE | `/api/admin/indexed-projects/{path}` | Remove project index |
| GET | `/api/admin/stats` | System statistics |

### MCP Server

| Path | Description |
|------|-------------|
| `/mcp` | MCP Streamable HTTP endpoint (JWT required) |

Supports 13 tools: `list_products`, `get_product_overview`, `search_product_code`, `ask_product`, `list_projects`, `get_wiki_summary`, `get_wiki_page`, `search_code`, `get_repo_relations`, `ask_question`, `get_project_insights`, `extract_project_insights`, `get_product_insights`.

### WebSocket

| Path | Description |
|------|-------------|
| `/ws/wiki/generate` | Wiki generation (streaming) |
| `/ws/chat` | Chat / Ask / DeepResearch |

### Other

| Method | Path | Description |
|--------|------|-------------|
| POST | `/chat/completions/stream` | HTTP chat completions |
| POST | `/export/wiki` | Export wiki as Markdown/JSON |
| GET | `/local_repo/structure` | Local repo file tree |
| GET | `/models/config` | Available LLM providers/models |
| GET | `/lang/config` | Language configuration |
| GET | `/health` | Health check |

## Module Overview

```
api/
├── main.py                 # Entry point (uvicorn)
├── api.py                  # FastAPI app, routes, models
├── gitlab_auth.py          # OAuth2 SSO, JWT, MCP token
├── gitlab_permission.py    # Permission checks + caching
├── admin.py                # Admin API router
├── batch_indexer.py        # Background batch indexing
├── mcp_server.py           # MCP server + JWT middleware
├── metadata_store.py       # Index metadata store
├── product_manager.py      # Product CRUD
├── repo_relations.py       # Repo dependency analysis
├── insight_extractor.py    # Structured knowledge extraction
├── wiki_generator.py       # Wiki generation logic
├── websocket_wiki.py       # WebSocket wiki handler
├── simple_chat.py          # Chat completions
├── rag.py                  # Single-repo RAG
├── multi_rag.py            # Multi-repo RAG
├── data_pipeline.py        # Repo cloning, embeddings
├── config.py               # Config loader
├── prompts.py              # LLM prompt templates
├── logging_config.py       # Logging setup
├── config/                 # JSON configs
│   ├── generator.json      # LLM models per provider
│   ├── embedder.json       # Embedding model config
│   ├── repo.json           # File filters
│   └── lang.json           # Language config
└── *_client.py             # LLM provider clients
    ├── openai_client.py
    ├── openrouter_client.py
    ├── bedrock_client.py
    ├── azureai_client.py
    ├── dashscope_client.py
    ├── google_embedder_client.py
    └── ollama_patch.py
```

## Storage

All data is stored locally under `~/.adalflow/`:
- `repos/` — Cloned repositories
- `databases/` — Embeddings and FAISS indexes
- `wikicache/` — Generated wiki JSON cache
- `metadata/` — Index metadata, products, repo relations, insights
