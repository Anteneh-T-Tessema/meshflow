# Installation

## Requirements

- Python 3.11, 3.12, or 3.13
- pip ≥ 23.0

## Core Install

```bash
pip install meshflow
```

The core install includes: Anthropic provider, SQLite ledger, YAML loader, rich CLI, async HTTP client. No other dependencies required.

## Provider Extras

Install only the provider(s) you need:

```bash
pip install "meshflow[openai]"    # OpenAI, Azure OpenAI
pip install "meshflow[gemini]"    # Google Gemini
pip install "meshflow[bedrock]"   # AWS Bedrock
pip install "meshflow[ollama]"    # Local Ollama (no extra deps — Ollama has a REST API)
```

## Feature Extras

```bash
pip install "meshflow[rag]"       # numpy for better RAG quality (zero-dep TF-IDF works without it)
pip install "meshflow[postgres]"  # asyncpg for PostgreSQL ledger backend
pip install "meshflow[s3]"        # boto3 for S3 ledger archive backend
pip install "meshflow[dashboard]" # Streamlit dashboard
pip install "meshflow[otel]"      # OpenTelemetry distributed tracing
pip install "meshflow[mcp]"       # MCP server gateway (adds pydantic)
pip install "meshflow[swarm]"     # SwarmTRM neural consensus (adds torch + pydantic)
```

## Everything

```bash
pip install "meshflow[full]"
```

Includes all providers, RAG, postgres, S3, OTEL, MCP, and SwarmTRM.

## Development

```bash
git clone https://github.com/meshflow-ai/meshflow
cd meshflow
pip install -e ".[dev]"
pytest
```

## Verify

```bash
python -c "import meshflow; print(meshflow.__version__)"
meshflow doctor
```

`meshflow doctor` checks your environment for common issues before you deploy.

## Offline / Mock Mode

Set `MESHFLOW_MOCK=1` to run with the built-in `EchoProvider`. No API keys needed — useful for CI, testing, and demos.

```bash
MESHFLOW_MOCK=1 python my_script.py
```
