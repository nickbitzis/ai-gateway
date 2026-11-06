# Build stage: resolve and install dependencies with uv into a virtualenv.
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

# Runtime stage: slim image, non-root user, only the venv and app code.
FROM python:3.12-slim
RUN useradd --create-home --uid 10001 gateway
WORKDIR /app
COPY --from=build --chown=gateway:gateway /app /app
COPY --chown=gateway:gateway config ./config
RUN mkdir -p /app/data && chown gateway:gateway /app/data
USER gateway
ENV PATH="/app/.venv/bin:$PATH" HOST=0.0.0.0 PORT=8080 \
    GATEWAY_CONFIG=/app/config/gateway.yaml GATEWAY_DB=/app/data/audit.db
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"
CMD ["aigateway"]
