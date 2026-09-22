# syntax=docker/dockerfile:1-labs

# Build argument for custom certificates directory
ARG CUSTOM_CERT_DIR="certs"

FROM node:20-alpine3.22 AS node_base

FROM node_base AS node_deps
WORKDIR /app
COPY package.json package-lock.json ./
ENV SHARP_DIST_BASE_URL=https://npmmirror.com/mirrors/sharp-libvips/
RUN npm config set registry https://registry.npmmirror.com && \
    npm install --legacy-peer-deps

FROM node_base AS node_builder
WORKDIR /app
COPY --from=node_deps /app/node_modules ./node_modules
# Copy only necessary files for Next.js build
COPY package.json package-lock.json next.config.ts tsconfig.json tailwind.config.js postcss.config.mjs ./
COPY src/ ./src/
COPY public/ ./public/
# Increase Node.js memory limit for build and disable telemetry
ENV NODE_OPTIONS="--max-old-space-size=4096"
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm config set registry https://registry.npmmirror.com && \
    NODE_ENV=production npm run build

FROM python:3.11-slim AS py_deps
WORKDIR /api
ARG PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_DEFAULT_TIMEOUT=120
ARG PIP_RETRIES=5
# Bootstrap tools have their own layer: application lock changes must not force
# downloading Poetry again. The cache also survives interrupted builds.
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    python -m pip install --disable-pip-version-check \
    --index-url "${PYPI_INDEX_URL}" --timeout "${PIP_DEFAULT_TIMEOUT}" --retries "${PIP_RETRIES}" \
    poetry==2.0.1 poetry-plugin-export==1.9.0
COPY api/pyproject.toml .
COPY api/poetry.lock .
# Export locally from the committed lock, then use the SAME index for runtime
# packages. PIP_INDEX_URL alone would not redirect Poetry's own installer.
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    poetry check --lock && \
    poetry export --format requirements.txt --only main --output /tmp/requirements.txt && \
    python -m venv --copies /api/.venv && \
    /api/.venv/bin/python -m pip install --disable-pip-version-check \
    --index-url "${PYPI_INDEX_URL}" --timeout "${PIP_DEFAULT_TIMEOUT}" --retries "${PIP_RETRIES}" \
    --no-deps --require-hashes --requirement /tmp/requirements.txt && \
    /api/.venv/bin/python -m pip check

# AdalFlow loads cl100k_base at import time. Ship tokenizers in the image so
# starting the API never depends on downloading these files from Azure.
ARG TIKTOKEN_ENCODINGS_BASE_URL=https://openaipublic.blob.core.windows.net/encodings
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache
RUN /api/.venv/bin/python - <<'PY'
import os
import time
import requests
import tiktoken
import tiktoken.load

base_url = os.environ["TIKTOKEN_ENCODINGS_BASE_URL"].rstrip("/")

def download_encoding(original_url):
    url = base_url + "/" + original_url.rsplit("/", 1)[-1]
    for attempt in range(5):
        try:
            with requests.get(url, timeout=(15, 120)) as response:
                response.raise_for_status()
                return response.content
        except requests.RequestException:
            if attempt == 4:
                raise
            time.sleep(min(2 ** attempt, 10))

# read_file_cached still uses the canonical URL as its cache key and checks
# each encoding's SHA-256 supplied by the pinned tiktoken package.
tiktoken.load.read_file = download_encoding
for name in ("cl100k_base", "o200k_base"):
    tiktoken.get_encoding(name)
    print(f"Cached tokenizer: {name}", flush=True)
PY

# Use Python 3.11 as final image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

ARG DEBIAN_MIRROR=https://mirrors.tuna.tsinghua.edu.cn
# Install Node.js and npm
RUN sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR%/}|g" /etc/apt/sources.list.d/debian.sources && \
    apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=60 -o Acquire::https::Timeout=60 update && \
    apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=60 -o Acquire::https::Timeout=60 install -y \
    curl \
    gnupg \
    git \
    ca-certificates \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list \
    && apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=60 -o Acquire::https::Timeout=60 update \
    && apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=60 -o Acquire::https::Timeout=60 install -y nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Update certificates if custom ones were provided and copied successfully
RUN if [ -n "${CUSTOM_CERT_DIR}" ]; then \
        mkdir -p /usr/local/share/ca-certificates && \
        if [ -d "${CUSTOM_CERT_DIR}" ]; then \
            cp -r ${CUSTOM_CERT_DIR}/* /usr/local/share/ca-certificates/ 2>/dev/null || true; \
            update-ca-certificates; \
            echo "Custom certificates installed successfully."; \
        else \
            echo "Warning: ${CUSTOM_CERT_DIR} not found. Skipping certificate installation."; \
        fi \
    fi

ENV PATH="/opt/venv/bin:$PATH"
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache

# Copy Python dependencies
COPY --from=py_deps /api/.venv /opt/venv
COPY --from=py_deps /opt/tiktoken-cache /opt/tiktoken-cache
COPY api/ ./api/
COPY tools/ ./tools/

# Copy Node app
COPY --from=node_builder /app/public ./public
COPY --from=node_builder /app/.next/standalone ./
COPY --from=node_builder /app/.next/static ./.next/static

# Expose the port the app runs on
EXPOSE ${PORT:-8001} 3000

# Create a script to run both backend and frontend
RUN echo '#!/bin/bash\n\
# Load environment variables from .env file if it exists\n\
if [ -f .env ]; then\n\
  export $(grep -v "^#" .env | xargs -r)\n\
fi\n\
\n\
# Check for required environment variables\n\
if [ -z "$OPENAI_API_KEY" ] || [ -z "$GOOGLE_API_KEY" ]; then\n\
  echo "Warning: OPENAI_API_KEY and/or GOOGLE_API_KEY environment variables are not set."\n\
  echo "These are required for DeepWiki to function properly."\n\
  echo "You can provide them via a mounted .env file or as environment variables when running the container."\n\
fi\n\
\n\
# Start the API server in the background with the configured port\n\
python -m api.main &\n\
PORT=3000 HOSTNAME=0.0.0.0 node server.js &\n\
wait -n\n\
exit $?' > /app/start.sh && chmod +x /app/start.sh

# Set environment variables
ENV PORT=8001
ENV NODE_ENV=production
ENV SERVER_BASE_URL=http://localhost:${PORT:-8001}

# Create empty .env file (will be overridden if one exists at runtime)
RUN touch .env

# Command to run the application
CMD ["/app/start.sh"]
