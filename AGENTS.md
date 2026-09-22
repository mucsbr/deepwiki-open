# Repository Guidelines

## Project Structure & Module Organization

DeepWiki is a two-process application. The Next.js frontend lives in `src/`: routes are under `src/app/`, reusable UI in `src/components/`, shared state in `src/contexts/`, and translations in `src/messages/`. Static assets belong in `public/`.

The FastAPI backend is in `api/`. `api/main.py` starts the server, `api/api.py` defines core routes, provider integrations use `*_client.py`, and JSON settings live in `api/config/`. Tests are split between `test/` and `tests/{unit,integration,api}/`; documentation belongs in `docs/`.

## Build, Test, and Development Commands

- `yarn install` — install frontend dependencies.
- `yarn dev` — run the frontend with Turbopack on port 3000.
- `yarn build` / `yarn lint` — validate the production build and ESLint rules.
- `python -m pip install poetry==2.0.1 && poetry install -C api` — install Python dependencies.
- `python -m api.main` (or `uv run -m api.main`) — run FastAPI on port 8001.
- `pytest test tests` — run both Python test trees explicitly.
- `python tests/run_tests.py --unit` — run the standalone runner; other groups use `--integration` or `--api`.
- `docker-compose up` — build and start the combined stack.

## Coding Style & Naming Conventions

Use two-space indentation, single quotes, semicolons, and PascalCase component names for TypeScript/TSX; hooks begin with `use`, and `@/` points to `src/`. Keep TypeScript strict and run ESLint. Follow PEP 8 for Python: four-space indentation, `snake_case` functions/modules, `PascalCase` classes, and type hints where practical. No repository-wide Black or Prettier configuration is enforced.

## Testing Guidelines

Name Python files and functions `test_*.py`. Put isolated tests in `tests/unit`, service-spanning tests in `tests/integration`, and live endpoint checks in `tests/api`. Integration/API tests may require provider keys and a running backend; copy `.env.example` to `.env` locally. No coverage threshold is configured, so add focused regression tests for changed behavior.

## Commit & Pull Request Guidelines

Recent history mostly uses Conventional Commit subjects such as `feat:`, `fix:`, `docs:`, and `perf:`. Keep commits imperative and narrowly scoped. Pull requests should explain the change, list verification commands, link relevant issues, and include screenshots for UI changes. Ensure the Docker build workflow remains green.

## Architecture & Security Notes

Add browser-facing backend routes to `next.config.ts` rewrites. Wiki-generation changes may need updates in both the WebSocket/frontend-driven path and `api/wiki_generator.py`. Never commit `.env`, API keys, GitLab tokens, JWT secrets, or generated data from `~/.adalflow/`; use `.env.example` for configuration documentation.
