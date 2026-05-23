FROM python:3.11-slim AS builder

WORKDIR /build
COPY pyproject.toml .
COPY meshflow/ ./meshflow/

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir aiohttp && \
    pip install --no-cache-dir .

FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin/meshflow /usr/local/bin/meshflow
COPY meshflow/ ./meshflow/

ENV MESHFLOW_API_KEYS=""
ENV MESHFLOW_CORS_ORIGINS="*"
ENV MESHFLOW_WEBHOOK_SECRET="change-me-in-production"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["meshflow", "serve", "--host", "0.0.0.0", "--port", "8000"]
