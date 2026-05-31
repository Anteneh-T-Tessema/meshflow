# Provider Overview

MeshFlow routes to any LLM with one environment variable — or no configuration at all.

```python
from meshflow import Agent

# Simplest form: pass a model name, provider inferred automatically
agent = Agent(name="analyst", role="researcher", model="claude-sonnet-4-6")
agent = Agent(name="gpt",     role="executor",   model="gpt-4o")
agent = Agent(name="local",   role="executor",   model="llama3.2")   # local Ollama
```

## Universal Entry Point: `LLM`

`LLM("model-name")` infers the right backend from the model string alone.

```python
from meshflow.agents.providers import LLM

llm = LLM("claude-opus-4-7")              # → AnthropicProvider
llm = LLM("gpt-4o")                       # → OpenAICompatibleProvider
llm = LLM("gemini-2.0-flash")             # → GeminiProvider
llm = LLM("llama3.2")                     # → OllamaProvider (local, free)
llm = LLM("groq/llama-3.1-70b-versatile") # → LiteLLMProvider
llm = LLM("mistral")                      # → OllamaProvider

# Override connection details
llm = LLM("llama3.2", host="http://10.0.0.5:11434")
llm = LLM("gpt-4o", api_key="sk-...", base_url="https://proxy/v1")

agent = Agent(name="a", role="researcher", llm=llm)
```

## All 8 Providers

### AnthropicProvider (default)

Uses the Anthropic Python SDK. Picked automatically when `ANTHROPIC_API_KEY` is set.

```python
from meshflow.agents.base import AnthropicProvider

agent = Agent(name="a", role="planner", provider=AnthropicProvider())
```

Install: `pip install anthropic`

### OpenAICompatibleProvider

Works with OpenAI, Groq, any OpenAI-compatible endpoint.

```python
from meshflow.agents.base import OpenAICompatibleProvider

provider = OpenAICompatibleProvider(
    model="gpt-4o",
    api_key="sk-...",
    base_url="https://api.openai.com/v1",  # optional override
    input_rate=0.005,   # USD per 1k input tokens
    output_rate=0.015,  # USD per 1k output tokens
)
```

Install: `pip install openai`

### GeminiProvider

Google Gemini via the `google-generativeai` SDK.

```python
from meshflow.agents.providers import GeminiProvider

provider = GeminiProvider(model="gemini-2.0-flash", api_key="")
# api_key defaults to GOOGLE_API_KEY env var
```

Install: `pip install google-generativeai`

### BedrockProvider

AWS Bedrock for Claude models. Uses boto3 and AWS credentials.

```python
from meshflow.agents.providers import BedrockProvider

provider = BedrockProvider(
    model="anthropic.claude-3-5-sonnet-20241022-v2:0",
    region="us-east-1",
)
```

Install: `pip install boto3`

### AzureOpenAIProvider

Azure OpenAI via the `openai` SDK's Azure support.

```python
from meshflow.agents.providers import AzureOpenAIProvider

provider = AzureOpenAIProvider(
    model="gpt-4o",
    endpoint="https://my-resource.openai.azure.com/",
    api_key="",        # defaults to AZURE_OPENAI_API_KEY
    api_version="2025-01-01-preview",
)
```

### OllamaProvider

Local models via Ollama — free, no API key, runs on your machine.

```python
from meshflow.agents.providers import OllamaProvider

provider = OllamaProvider(model="llama3.2")
provider = OllamaProvider(model="mistral", host="http://10.0.0.1:11434")

# Check if Ollama is reachable
OllamaProvider.is_reachable()  # → True / False
```

Install Ollama: <https://ollama.com/download>, then `ollama pull llama3.2`

### LiteLLMProvider

100+ models via a single interface. Model format: `<provider>/<model>`.

```python
from meshflow.agents.providers import LiteLLMProvider

provider = LiteLLMProvider(model="openai/gpt-4o")
provider = LiteLLMProvider(model="gemini/gemini-2.0-flash")
provider = LiteLLMProvider(model="groq/llama-3.1-70b-versatile")
provider = LiteLLMProvider(model="cohere/command-r-plus")
# Or set LITELLM_MODEL=openai/gpt-4o and leave model=""
```

Install: `pip install litellm`

### EchoProvider

Zero-dependency offline provider for tests and demos. No API key, no network calls.

```python
from meshflow.agents.base import EchoProvider

agent = Agent(name="test", role="executor", provider=EchoProvider())

# Fixed response for deterministic tests
agent = Agent(name="test", role="executor", provider=EchoProvider(response="OK"))
```

## `provider_for()` Factory

```python
from meshflow.agents.providers import provider_for

p = provider_for("anthropic")
p = provider_for("openai", model="gpt-4o")
p = provider_for("gemini", model="gemini-2.0-flash")
p = provider_for("ollama", model="mistral", host="http://localhost:11434")
p = provider_for("litellm", model="groq/llama-3.1-70b-versatile")
p = provider_for("bedrock", model="anthropic.claude-3-5-sonnet-20241022-v2:0")
p = provider_for("azure", endpoint="https://...", api_key="sk-...")
p = provider_for("echo")
```

Valid names: `anthropic` · `openai` · `gemini` · `bedrock` · `azure` · `ollama` · `litellm` · `echo`

## `auto_detect_provider()` — Environment-Based Selection

```python
from meshflow.agents.providers import auto_detect_provider

provider = auto_detect_provider(verbose=True)
```

Detection order (first match wins):

| Priority | Condition | Provider |
|----------|-----------|----------|
| 1 | `MESHFLOW_MOCK=1` | `EchoProvider` |
| 2 | `MESHFLOW_PROVIDER=<name>` | Named provider |
| 3 | `ANTHROPIC_API_KEY` set | `AnthropicProvider` |
| 4 | `OPENAI_API_KEY` set | `OpenAICompatibleProvider` |
| 5 | `GOOGLE_API_KEY` / `GEMINI_API_KEY` set | `GeminiProvider` |
| 6 | `AWS_ACCESS_KEY_ID` set | `BedrockProvider` |
| 7 | `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT` | `AzureOpenAIProvider` |
| 8 | Ollama reachable at localhost | `OllamaProvider` |
| 9 | `LITELLM_MODEL` set | `LiteLLMProvider` |
| 10 | Nothing found | `EchoProvider` + warning |

Override the model at any tier with `MESHFLOW_MODEL=<model-id>`.

## Offline Mode: `MESHFLOW_MOCK=1`

Set `MESHFLOW_MOCK=1` to run all agents using `EchoProvider` — no API key, no network calls.
Useful for CI pipelines and orchestration-logic tests.

```bash
MESHFLOW_MOCK=1 python my_workflow.py
```

## Pricing Registry

MeshFlow tracks token costs for Claude models automatically.

```python
from meshflow.agents.base import _PRICING, update_pricing

# Current registry (USD per 1k tokens):
# "claude-opus-4-7"   → (0.015, 0.075)
# "claude-sonnet-4-6" → (0.003, 0.015)
# "claude-haiku-4-5"  → (0.0008, 0.004)

# Register a custom or third-party model:
update_pricing("my-fine-tuned-model", input_per_1k=0.002, output_per_1k=0.008)
```

The registry is matched by substring against the model name — more-specific keys should be added before less-specific ones. Each `Agent.step()` returns `cost_usd` computed from this registry.
