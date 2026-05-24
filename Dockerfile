FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml .
COPY meshflow/ ./meshflow/

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir aiohttp cryptography && \
    pip install --no-cache-dir .

FROM python:3.12-slim

WORKDIR /app

# Non-root user for security
RUN groupadd -r meshflow && useradd -r -g meshflow -u 1000 meshflow

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/meshflow /usr/local/bin/meshflow
COPY meshflow/ ./meshflow/

# Data directories: ledger, registry, budgets
RUN mkdir -p /data && chown meshflow:meshflow /data

USER meshflow

# ── Core ──────────────────────────────────────────────────────────────────────
ENV MESHFLOW_API_KEYS=""
ENV MESHFLOW_CORS_ORIGINS="*"
ENV MESHFLOW_WEBHOOK_SECRET="change-me-in-production"
ENV PYTHONUNBUFFERED=1

# ── LLM providers (set the ones you use) ─────────────────────────────────────
ENV ANTHROPIC_API_KEY=""
ENV OPENAI_API_KEY=""

# ── Observability ─────────────────────────────────────────────────────────────
ENV OTEL_EXPORTER_OTLP_ENDPOINT=""
ENV OTEL_SERVICE_NAME="meshflow"
ENV MESHFLOW_OTEL=""

# ── Persistence paths ─────────────────────────────────────────────────────────
ENV MESHFLOW_REGISTRY_PATH="/data/registry.db"
ENV MESHFLOW_BUDGET_PATH="/data/budgets.db"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/live')"

CMD ["meshflow", "serve", "--host", "0.0.0.0", "--port", "8000", "--ledger", "/data/runs.db"]
