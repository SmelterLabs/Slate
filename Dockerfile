# Stage 1: board (Node is only ever present here, never on the host)
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build && test -s dist/index.html

# Stage 1b: normal clones retain their revision; archives need no .git directory.
# A worktree's .git file points outside the context: pass build args for its identity.
FROM python:3.12-alpine AS rev
RUN apk add --no-cache git
WORKDIR /src
COPY . .
ARG SLATE_REV=""
ARG SLATE_COMMIT=""
RUN python scripts/build_metadata.py --output /build.json --rev "$SLATE_REV" --commit "$SLATE_COMMIT"

# Stage 2: runtime
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
RUN uv sync --frozen --no-dev
COPY --from=web /web/dist ./src/slate/static
COPY --from=rev /build.json ./src/slate/build.json
ENV PATH="/app/.venv/bin:$PATH" \
    SLATE_DATABASE_PATH=/data/slate.db \
    SLATE_STATIC_DIR=/app/src/slate/static
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1
# exec: uvicorn replaces the shell so SIGTERM reaches it and in-flight work can finish.
CMD ["sh", "-c", "slate migrate && exec slate serve --host 0.0.0.0 --port 8000"]
