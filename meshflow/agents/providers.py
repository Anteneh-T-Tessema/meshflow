"""Extended LLM provider implementations for MeshFlow.

MeshFlow works with any LLM.  Set one environment variable and go:

    MESHFLOW_PROVIDER=anthropic   ANTHROPIC_API_KEY=sk-ant-...
    MESHFLOW_PROVIDER=openai      OPENAI_API_KEY=sk-...
    MESHFLOW_PROVIDER=gemini      GOOGLE_API_KEY=...
    MESHFLOW_PROVIDER=bedrock     (uses boto3 / AWS env vars)
    MESHFLOW_PROVIDER=azure       AZURE_OPENAI_API_KEY=...  AZURE_OPENAI_ENDPOINT=...
    MESHFLOW_PROVIDER=ollama      (free, local — no key needed)
    MESHFLOW_PROVIDER=litellm     LITELLM_MODEL=gpt-4o  (100+ models)
    MESHFLOW_MOCK=1               (offline echo — no key at all)

Or let MeshFlow auto-detect: set any of the API keys above and it picks
the right provider automatically.  Override the model with MESHFLOW_MODEL.

Factory::

    from meshflow.agents.providers import provider_for
    p = provider_for("gemini", model="gemini-2.0-flash")
    p = provider_for("ollama", model="llama3.2", host="http://localhost:11434")
    p = provider_for("litellm", model="gpt-4o")
    p = provider_for("bedrock", model="anthropic.claude-3-5-sonnet-20241022-v2:0")
    p = provider_for("azure", endpoint="https://...", api_key="sk-...")
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, cast

from meshflow.agents.base import (
    LLMProvider,
    _cost_usd,
)
from meshflow.core.schemas import TokenChunk


# ── Gemini ────────────────────────────────────────────────────────────────────


class GeminiProvider(LLMProvider):
    """Google Gemini provider via the google-generativeai SDK.

    Install: pip install google-generativeai
    Set GOOGLE_API_KEY environment variable or pass api_key.
    """

    def __init__(self, model: str = "gemini-2.0-flash", api_key: str = "") -> None:
        self._model = model
        self._api_key = api_key

    def _client(self) -> Any:
        try:
            import google.generativeai as genai
        except ImportError as exc:
            raise ImportError(
                "GeminiProvider requires google-generativeai: pip install google-generativeai"
            ) from exc
        import os

        key = self._api_key or os.environ.get("GOOGLE_API_KEY", "")
        genai.configure(api_key=key)
        return genai

    async def complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
    ) -> tuple[str, int, float]:
        genai = self._client()
        m = genai.GenerativeModel(
            model_name=model or self._model,
            system_instruction=system,
        )
        last = messages[-1]["content"] if messages else ""

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: m.generate_content(
                last,
                generation_config={"max_output_tokens": max_tokens},
            ),
        )
        text = response.text or ""
        # Gemini usage metadata
        usage = getattr(response, "usage_metadata", None)
        in_tok = getattr(usage, "prompt_token_count", 0) or 0
        out_tok = getattr(usage, "candidates_token_count", 0) or 0
        cost = (in_tok / 1000) * 0.0001 + (out_tok / 1000) * 0.0004  # gemini-2.0-flash approx
        return text, in_tok + out_tok, cost

    async def complete_with_tools(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        tool_schemas: list[dict[str, Any]],
        tool_fns: dict[str, Any],
    ) -> tuple[str, int, float]:
        # Gemini tool use is via function declarations — for now fall back to no-tool complete
        return await self.complete(model, messages, system, max_tokens)

    async def stream_complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        agent_id: str,
        step_id: str,
        run_id: str,
    ) -> AsyncIterator[TokenChunk]:
        genai = self._client()
        m = genai.GenerativeModel(
            model_name=model or self._model,
            system_instruction=system,
        )
        last = messages[-1]["content"] if messages else ""
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: m.generate_content(
                last, stream=True, generation_config={"max_output_tokens": max_tokens}
            ),
        )
        for chunk in response:
            text = getattr(chunk, "text", "") or ""
            if text:
                yield TokenChunk(text=text, agent_id=agent_id, step_id=step_id, run_id=run_id)


# ── AWS Bedrock ───────────────────────────────────────────────────────────────


class BedrockProvider(LLMProvider):
    """AWS Bedrock provider for Claude models via boto3.

    Install: pip install boto3
    Requires AWS credentials in environment or ~/.aws/credentials.
    """

    def __init__(
        self,
        model: str = "anthropic.claude-3-5-sonnet-20241022-v2:0",
        region: str = "us-east-1",
    ) -> None:
        self._model = model
        self._region = region

    def _client(self) -> Any:
        try:
            import boto3
        except ImportError as exc:
            raise ImportError("BedrockProvider requires boto3: pip install boto3") from exc
        return boto3.client("bedrock-runtime", region_name=self._region)

    async def complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
    ) -> tuple[str, int, float]:
        import json as _json

        client = self._client()
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: client.invoke_model(
                modelId=model or self._model,
                body=_json.dumps(body),
                contentType="application/json",
                accept="application/json",
            ),
        )
        result = _json.loads(response["body"].read())
        text = result["content"][0]["text"] if result.get("content") else ""
        usage = result.get("usage", {})
        in_tok = usage.get("input_tokens", 0)
        out_tok = usage.get("output_tokens", 0)
        cost = _cost_usd(model or self._model, in_tok, out_tok)
        return text, in_tok + out_tok, cost

    async def complete_with_tools(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        tool_schemas: list[dict[str, Any]],
        tool_fns: dict[str, Any],
    ) -> tuple[str, int, float]:
        return await self.complete(model, messages, system, max_tokens)

    async def stream_complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        agent_id: str,
        step_id: str,
        run_id: str,
    ) -> AsyncIterator[TokenChunk]:
        # Bedrock streaming requires more complex SSE parsing — fall back to single call
        text, _, _ = await self.complete(model, messages, system, max_tokens)
        if text:
            yield TokenChunk(text=text, agent_id=agent_id, step_id=step_id, run_id=run_id)


# ── Azure OpenAI ──────────────────────────────────────────────────────────────


class AzureOpenAIProvider(LLMProvider):
    """Azure OpenAI provider via the openai SDK's Azure support.

    Install: pip install openai
    Required: endpoint (https://your-resource.openai.azure.com/),
              api_key, and api_version.
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        endpoint: str = "",
        api_key: str = "",
        api_version: str = "2025-01-01-preview",
        input_rate: float = 0.005,
        output_rate: float = 0.015,
    ) -> None:
        self._model = model
        self._endpoint = endpoint
        self._api_key = api_key
        self._api_version = api_version
        self._in_rate = input_rate
        self._out_rate = output_rate

    def _client(self) -> Any:
        try:
            from openai import AsyncAzureOpenAI
        except ImportError as exc:
            raise ImportError("AzureOpenAIProvider requires openai: pip install openai") from exc
        import os

        return AsyncAzureOpenAI(
            azure_endpoint=self._endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT", ""),
            api_key=self._api_key or os.environ.get("AZURE_OPENAI_API_KEY", ""),
            api_version=self._api_version,
        )

    async def complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
    ) -> tuple[str, int, float]:
        client = self._client()
        oai_messages = [{"role": "system", "content": system}, *messages]
        response = await client.chat.completions.create(
            model=model or self._model,
            messages=cast(Any, oai_messages),
            max_tokens=max_tokens,
        )
        content = response.choices[0].message.content or ""
        in_tok = response.usage.prompt_tokens if response.usage else 0
        out_tok = response.usage.completion_tokens if response.usage else 0
        cost = (in_tok / 1000) * self._in_rate + (out_tok / 1000) * self._out_rate
        return content, in_tok + out_tok, cost

    async def complete_with_tools(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        tool_schemas: list[dict[str, Any]],
        tool_fns: dict[str, Any],
    ) -> tuple[str, int, float]:
        return await self.complete(model, messages, system, max_tokens)

    async def stream_complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        agent_id: str,
        step_id: str,
        run_id: str,
    ) -> AsyncIterator[TokenChunk]:
        client = self._client()
        oai_messages = [{"role": "system", "content": system}, *messages]
        stream = await client.chat.completions.create(
            model=model or self._model,
            messages=cast(Any, oai_messages),
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield TokenChunk(
                    text=delta.content, agent_id=agent_id, step_id=step_id, run_id=run_id
                )


# ── Ollama ────────────────────────────────────────────────────────────────────


class OllamaProvider(LLMProvider):
    """Ollama local model provider — free, no API key, runs on your machine.

    Ollama exposes an OpenAI-compatible REST API, so this provider is a thin
    wrapper around OpenAICompatibleProvider pointed at localhost:11434.

    Install Ollama: https://ollama.com/download
    Pull a model:  ollama pull llama3.2

    Usage::

        p = OllamaProvider()                          # llama3.2 at localhost
        p = OllamaProvider(model="mistral")           # different model
        p = OllamaProvider(host="http://10.0.0.1:11434")  # remote Ollama
    """

    _DEFAULT_MODEL = "llama3.2"
    _DEFAULT_HOST  = "http://localhost:11434"

    def __init__(
        self,
        model: str = "",
        host: str = "",
    ) -> None:
        import os
        self._model = (
            model
            or os.environ.get("MESHFLOW_MODEL", "")
            or os.environ.get("OLLAMA_MODEL", "")
            or self._DEFAULT_MODEL
        )
        self._host = host or os.environ.get("OLLAMA_HOST", self._DEFAULT_HOST)
        # Delegate to OpenAICompatibleProvider using Ollama's OpenAI-compat API
        from meshflow.agents.base import OpenAICompatibleProvider
        self._inner = OpenAICompatibleProvider(
            model=self._model,
            api_key="ollama",           # Ollama ignores this but openai SDK requires it
            base_url=self._host.rstrip("/") + "/v1",
            input_rate=0.0,
            output_rate=0.0,            # Ollama is free
        )

    async def complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
    ) -> tuple[str, int, float]:
        return await self._inner.complete(
            model or self._model, messages, system, max_tokens
        )

    async def complete_with_tools(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        tool_schemas: list[dict[str, Any]],
        tool_fns: dict[str, Any],
    ) -> tuple[str, int, float]:
        return await self._inner.complete_with_tools(
            model or self._model, messages, system, max_tokens, tool_schemas, tool_fns
        )

    async def stream_complete(  # type: ignore[override]
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        agent_id: str,
        step_id: str,
        run_id: str,
    ) -> AsyncIterator[Any]:
        async for chunk in self._inner.stream_complete(
            model or self._model, messages, system, max_tokens,
            agent_id, step_id, run_id,
        ):
            yield chunk

    @classmethod
    def is_reachable(cls, host: str = "") -> bool:
        """Return True if the Ollama server can be reached (synchronous ping)."""
        import urllib.request
        import os
        target = host or os.environ.get("OLLAMA_HOST", cls._DEFAULT_HOST)
        try:
            with urllib.request.urlopen(f"{target}/api/tags", timeout=1):
                return True
        except Exception:
            return False


# ── LiteLLM ───────────────────────────────────────────────────────────────────


class LiteLLMProvider(LLMProvider):
    """Universal LLM provider via LiteLLM — covers 100+ models with one interface.

    LiteLLM translates any model string to the right API call.  The model
    format is ``<provider>/<model>``:

        openai/gpt-4o
        anthropic/claude-opus-4-7
        gemini/gemini-2.0-flash
        bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0
        ollama/llama3.2
        groq/llama-3.1-70b-versatile
        cohere/command-r-plus

    Install:  pip install litellm

    Usage::

        p = LiteLLMProvider(model="openai/gpt-4o")
        p = LiteLLMProvider(model="ollama/llama3.2")
        # Or set env var and leave model="":
        # LITELLM_MODEL=gemini/gemini-2.0-flash
    """

    def __init__(self, model: str = "") -> None:
        import os
        self._model = (
            model
            or os.environ.get("MESHFLOW_MODEL", "")
            or os.environ.get("LITELLM_MODEL", "gpt-4o")
        )

    def _litellm(self) -> Any:
        try:
            import litellm
            litellm.drop_params = True   # ignore unknown params silently
            return litellm
        except ImportError as exc:
            raise ImportError(
                "LiteLLMProvider requires litellm: pip install litellm"
            ) from exc

    async def complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
    ) -> tuple[str, int, float]:
        lit = self._litellm()
        m = model or self._model
        oai_msgs = [{"role": "system", "content": system}, *messages]
        response = await lit.acompletion(model=m, messages=oai_msgs, max_tokens=max_tokens)
        content = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        in_tok  = getattr(usage, "prompt_tokens", 0) if usage else 0
        out_tok = getattr(usage, "completion_tokens", 0) if usage else 0
        cost = getattr(response, "_hidden_params", {}).get("response_cost", 0.0) or 0.0
        return content, in_tok + out_tok, float(cost)

    async def complete_with_tools(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        tool_schemas: list[dict[str, Any]],
        tool_fns: dict[str, Any],
    ) -> tuple[str, int, float]:
        # Convert Anthropic-style tool schemas to OpenAI format
        oai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                },
            }
            for t in tool_schemas
        ]
        lit = self._litellm()
        m = model or self._model
        oai_msgs: list[dict[str, Any]] = [{"role": "system", "content": system}, *messages]
        total_in = total_out = 0
        cost_total = 0.0

        for _ in range(10):
            response = await lit.acompletion(
                model=m, messages=oai_msgs, max_tokens=max_tokens, tools=oai_tools
            )
            usage = getattr(response, "usage", None)
            total_in  += getattr(usage, "prompt_tokens", 0) if usage else 0
            total_out += getattr(usage, "completion_tokens", 0) if usage else 0
            cost_total += float(
                getattr(response, "_hidden_params", {}).get("response_cost", 0.0) or 0.0
            )
            msg = response.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None) or []
            if not tool_calls or response.choices[0].finish_reason == "stop":
                return msg.content or "", total_in + total_out, cost_total

            oai_msgs.append({"role": "assistant", "content": msg.content or "", "tool_calls": tool_calls})
            for tc in tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments)
                    fn = tool_fns.get(fn_name)
                    result = (await fn(**fn_args)) if asyncio.iscoroutinefunction(fn) else fn(**fn_args) if fn else f"Unknown tool: {fn_name}"
                    result_text = json.dumps(result) if not isinstance(result, str) else result
                except Exception as exc:
                    result_text = f"Tool error: {exc}"
                oai_msgs.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})

        return "[max tool rounds reached]", total_in + total_out, cost_total

    async def stream_complete(  # type: ignore[override]
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        agent_id: str,
        step_id: str,
        run_id: str,
    ) -> AsyncIterator[Any]:
        from meshflow.core.schemas import TokenChunk
        lit = self._litellm()
        m = model or self._model
        oai_msgs = [{"role": "system", "content": system}, *messages]
        response = await lit.acompletion(model=m, messages=oai_msgs, max_tokens=max_tokens, stream=True)
        async for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None
            text = getattr(delta, "content", "") or ""
            if text:
                yield TokenChunk(text=text, agent_id=agent_id, step_id=step_id, run_id=run_id)


# ── Factory ───────────────────────────────────────────────────────────────────

_PROVIDER_MAP: dict[str, type] = {
    "gemini":   GeminiProvider,
    "bedrock":  BedrockProvider,
    "azure":    AzureOpenAIProvider,
    "ollama":   OllamaProvider,
    "litellm":  LiteLLMProvider,
}

#: All known provider names — shown in error messages and CLI help.
PROVIDER_NAMES = ["anthropic", "openai", "gemini", "bedrock", "azure", "ollama", "litellm", "echo"]


def provider_for(name: str, **kwargs: Any) -> Any:
    """Return a provider instance by short name.  Extra kwargs → constructor.

    Names: anthropic · openai · gemini · bedrock · azure · ollama · litellm · echo

    Examples::

        provider_for("anthropic")
        provider_for("openai", model="gpt-4o")
        provider_for("gemini", model="gemini-2.0-flash")
        provider_for("ollama", model="mistral", host="http://localhost:11434")
        provider_for("litellm", model="groq/llama-3.1-70b-versatile")
        provider_for("bedrock", model="anthropic.claude-3-5-sonnet-20241022-v2:0")
        provider_for("azure", endpoint="https://...", api_key="sk-...")
        provider_for("echo")
    """
    name = name.lower().strip()
    if name in ("echo", "mock"):
        from meshflow.agents.base import EchoProvider
        return EchoProvider(**kwargs)
    if name == "anthropic":
        from meshflow.agents.base import AnthropicProvider
        return AnthropicProvider()
    if name == "openai":
        from meshflow.agents.base import OpenAICompatibleProvider
        return OpenAICompatibleProvider(**kwargs)
    cls = _PROVIDER_MAP.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown provider {name!r}. "
            f"Choose: {', '.join(PROVIDER_NAMES)}"
        )
    return cls(**kwargs)


def auto_detect_provider(verbose: bool = False) -> "LLMProvider":
    """Return the best available provider based on environment, with no configuration needed.

    Detection order (first match wins):

    1. ``MESHFLOW_MOCK=1``          → EchoProvider (no key, no SDK)
    2. ``MESHFLOW_PROVIDER=<name>`` → that provider explicitly
    3. ``ANTHROPIC_API_KEY`` set    → AnthropicProvider
    4. ``OPENAI_API_KEY`` set       → OpenAICompatibleProvider
    5. ``GOOGLE_API_KEY`` /         → GeminiProvider
       ``GEMINI_API_KEY`` set
    6. ``AWS_ACCESS_KEY_ID`` set    → BedrockProvider
    7. ``AZURE_OPENAI_API_KEY``     → AzureOpenAIProvider
    8. Ollama reachable locally     → OllamaProvider (free, no key)
    9. ``LITELLM_MODEL`` set        → LiteLLMProvider
    10. fallback                    → EchoProvider (prints guidance)
    """
    import os

    def _log(msg: str) -> None:
        if verbose:
            print(f"[meshflow] provider: {msg}")

    # 1. Explicit mock
    mock = os.environ.get("MESHFLOW_MOCK", "").strip().lower()
    if mock in ("1", "true", "yes"):
        _log("EchoProvider (MESHFLOW_MOCK=1)")
        from meshflow.agents.base import EchoProvider
        return EchoProvider()

    # 2. Explicit provider name
    prov_name = os.environ.get("MESHFLOW_PROVIDER", "").strip().lower()
    if prov_name:
        _log(f"provider_for({prov_name!r}) via MESHFLOW_PROVIDER")
        return provider_for(prov_name)

    # 3. Anthropic key
    if os.environ.get("ANTHROPIC_API_KEY"):
        _log("AnthropicProvider (ANTHROPIC_API_KEY found)")
        from meshflow.agents.base import AnthropicProvider
        return AnthropicProvider()

    # 4. OpenAI key
    if os.environ.get("OPENAI_API_KEY"):
        model = os.environ.get("MESHFLOW_MODEL", "gpt-4o")
        _log(f"OpenAICompatibleProvider (OPENAI_API_KEY found, model={model!r})")
        from meshflow.agents.base import OpenAICompatibleProvider
        return OpenAICompatibleProvider(model=model)

    # 5. Google / Gemini key
    if os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"):
        model = os.environ.get("MESHFLOW_MODEL", "gemini-2.0-flash")
        _log(f"GeminiProvider (GOOGLE_API_KEY found, model={model!r})")
        return GeminiProvider(model=model)

    # 6. AWS credentials → Bedrock
    if os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE"):
        model = os.environ.get("MESHFLOW_MODEL",
                               "anthropic.claude-3-5-sonnet-20241022-v2:0")
        _log(f"BedrockProvider (AWS credentials found, model={model!r})")
        return BedrockProvider(model=model)

    # 7. Azure OpenAI
    if os.environ.get("AZURE_OPENAI_API_KEY") and os.environ.get("AZURE_OPENAI_ENDPOINT"):
        model = os.environ.get("MESHFLOW_MODEL", "gpt-4o")
        _log(f"AzureOpenAIProvider (Azure env vars found, model={model!r})")
        return AzureOpenAIProvider(
            model=model,
            endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
        )

    # 8. Ollama — try a fast ping
    ollama_host = os.environ.get("OLLAMA_HOST", OllamaProvider._DEFAULT_HOST)
    if OllamaProvider.is_reachable(ollama_host):
        model = os.environ.get("MESHFLOW_MODEL", OllamaProvider._DEFAULT_MODEL)
        _log(f"OllamaProvider (Ollama reachable at {ollama_host}, model={model!r})")
        return OllamaProvider(model=model, host=ollama_host)

    # 9. LiteLLM configured
    if os.environ.get("LITELLM_MODEL"):
        model = os.environ.get("MESHFLOW_MODEL") or os.environ["LITELLM_MODEL"]
        _log(f"LiteLLMProvider (LITELLM_MODEL={model!r})")
        return LiteLLMProvider(model=model)

    # 10. Nothing found — use echo and tell the user
    import warnings
    warnings.warn(
        "\n\nMeshFlow: no LLM provider configured — using EchoProvider (offline mode).\n"
        "To use a real LLM, set one of:\n"
        "  ANTHROPIC_API_KEY=sk-ant-...          (Anthropic Claude)\n"
        "  OPENAI_API_KEY=sk-...                 (OpenAI / any compatible)\n"
        "  GOOGLE_API_KEY=...                    (Google Gemini)\n"
        "  AWS_ACCESS_KEY_ID=... (+ region)      (AWS Bedrock)\n"
        "  ollama pull llama3.2  (no key needed) (local Ollama)\n"
        "Or: MESHFLOW_PROVIDER=<name>  MESHFLOW_MODEL=<model-id>\n"
        "Or: MESHFLOW_MOCK=1           (silence this warning)\n",
        stacklevel=3,
    )
    from meshflow.agents.base import EchoProvider
    return EchoProvider()


# ── Model-name → provider inference (the CrewAI insight) ─────────────────────

# Ordered rules: (prefix_or_substring_list, provider_factory)
# More specific entries must come before more general ones.
_MODEL_RULES: list[tuple[list[str], str]] = [
    # OpenAI
    (["gpt-", "o1-", "o1 ", "o3-", "o4-", "o3 ", "o4 ", "text-embedding", "davinci", "babbage", "ada"],
     "openai"),
    # Anthropic Claude
    (["claude"],
     "anthropic"),
    # Google Gemini
    (["gemini"],
     "gemini"),
    # AWS Bedrock (full model ARN / prefix)
    (["amazon.", "anthropic.", "meta.llama", "cohere.command", "mistral.mistral",
      "ai21.jamba"],
     "bedrock"),
    # Azure (deployed model names often match endpoint, harder to infer — rely on env var)
    # Groq-hosted (OpenAI-compat)
    (["groq/", "llama-3.1-", "llama-3.2-", "llama-3.3-", "mixtral-8x"],
     "openai"),       # via OPENAI_API_KEY + OPENAI_API_BASE=https://api.groq.com/openai/v1
    # Common Ollama / local models
    (["llama", "mistral", "phi-", "phi3", "qwen", "deepseek", "codellama",
      "vicuna", "gemma", "falcon", "orca", "yi-", "solar", "stablelm",
      "command-r", "nous-", "openhermes", "zephyr", "neural-"],
     "ollama"),
]


def model_to_provider(model: str) -> "LLMProvider":
    """Infer the right LLMProvider from a model name string.

    This is the **CrewAI pattern** — the user only needs to know the model name.
    The framework figures out which backend to call.

    Rules (first match wins):

    +--------------------------+---------------------+-------------------+
    | Model name               | Provider            | Requires          |
    +--------------------------+---------------------+-------------------+
    | gpt-4o, o3-mini, …       | OpenAICompatible    | OPENAI_API_KEY    |
    | claude-*                 | Anthropic           | ANTHROPIC_API_KEY |
    | gemini-*                 | Gemini              | GOOGLE_API_KEY    |
    | amazon.*, anthropic.*,   | Bedrock             | AWS credentials   |
    |   meta.llama*, …         |                     |                   |
    | llama*, mistral*, phi*,  | Ollama (local)      | ollama running    |
    |   qwen*, deepseek*, …    |                     |                   |
    | provider/model (any /)   | LiteLLM             | litellm + key     |
    | anything else            | auto_detect()       | (best available)  |
    +--------------------------+---------------------+-------------------+

    Usage::

        # These all just work — no provider import needed:
        agent = Agent(name="a", model="gpt-4o")
        agent = Agent(name="b", model="claude-opus-4-7")
        agent = Agent(name="c", model="gemini-2.0-flash")
        agent = Agent(name="d", model="llama3.2")            # local Ollama
        agent = Agent(name="e", model="groq/llama-3.1-70b")  # LiteLLM
    """
    import os

    # MESHFLOW_MOCK always wins
    if os.environ.get("MESHFLOW_MOCK", "").strip().lower() in ("1", "true", "yes"):
        from meshflow.agents.base import EchoProvider
        return EchoProvider()

    model_lower = model.lower().strip()

    # LiteLLM-style "provider/model" prefix — route directly to LiteLLM
    if "/" in model_lower and not model_lower.startswith("http"):
        return LiteLLMProvider(model=model)

    # Match against known prefix lists
    for prefixes, backend in _MODEL_RULES:
        if any(model_lower.startswith(p) or p in model_lower for p in prefixes):
            if backend == "openai":
                from meshflow.agents.base import OpenAICompatibleProvider
                return OpenAICompatibleProvider(model=model)
            if backend == "anthropic":
                from meshflow.agents.base import AnthropicProvider
                return AnthropicProvider()
            if backend == "gemini":
                return GeminiProvider(model=model)
            if backend == "bedrock":
                return BedrockProvider(model=model)
            if backend == "ollama":
                return OllamaProvider(model=model)

    # Unknown model — fall back to environment-based auto-detection
    return auto_detect_provider()


# ── Unified LLM class (the single-entry-point pattern) ───────────────────────

class LLM:
    """Single entry point for any LLM — the CrewAI-inspired unified interface.

    Pass any model name.  MeshFlow infers the provider automatically.
    Optionally override with explicit kwargs.

    Usage::

        from meshflow import LLM, Agent

        # Auto-infer provider from model name:
        llm = LLM("gpt-4o")                         # → OpenAI
        llm = LLM("claude-opus-4-7")                 # → Anthropic
        llm = LLM("gemini-2.0-flash")                # → Google
        llm = LLM("llama3.2")                        # → local Ollama
        llm = LLM("groq/llama-3.1-70b-versatile")    # → LiteLLM

        # Explicit overrides:
        llm = LLM("llama3.2", host="http://10.0.0.5:11434")
        llm = LLM("gpt-4o", api_key="sk-...", base_url="https://proxy/v1")

        # Use in an agent:
        agent = Agent(name="analyst", role="researcher", llm=llm)

        # Or just pass model= directly — same result:
        agent = Agent(name="analyst", model="gpt-4o")
    """

    def __init__(self, model: str = "", **kwargs: Any) -> None:
        import os
        self.model = (
            model
            or os.environ.get("MESHFLOW_MODEL", "")
            or os.environ.get("LITELLM_MODEL", "")
        )
        self._kwargs = kwargs
        # Build the underlying provider immediately so errors surface early
        if kwargs:
            # Explicit kwargs → route to the inferred provider but with overrides
            backend = self._infer_backend()
            self._provider: LLMProvider = self._build_with_kwargs(backend)
        else:
            self._provider = model_to_provider(self.model) if self.model else auto_detect_provider()

    def _infer_backend(self) -> str:
        m = self.model.lower()
        if any(m.startswith(p) for p in ["gpt-", "o1-", "o3-", "o4-"]):
            return "openai"
        if m.startswith("claude"):
            return "anthropic"
        if m.startswith("gemini"):
            return "gemini"
        if any(m.startswith(p) for p in ["amazon.", "anthropic.", "meta.llama"]):
            return "bedrock"
        if "/" in m:
            return "litellm"
        return "openai"   # sensible default for unknown names with kwargs

    def _build_with_kwargs(self, backend: str) -> LLMProvider:
        if backend == "openai":
            from meshflow.agents.base import OpenAICompatibleProvider
            return OpenAICompatibleProvider(
                model=self.model,
                api_key=self._kwargs.get("api_key", ""),
                base_url=self._kwargs.get("base_url", ""),
            )
        if backend == "anthropic":
            from meshflow.agents.base import AnthropicProvider
            return AnthropicProvider()
        if backend == "gemini":
            return GeminiProvider(model=self.model, **self._kwargs)
        if backend == "bedrock":
            return BedrockProvider(model=self.model)
        if backend == "ollama":
            return OllamaProvider(
                model=self.model,
                host=self._kwargs.get("host", ""),
            )
        return LiteLLMProvider(model=self.model)

    # ── Forward the LLMProvider protocol ─────────────────────────────────────

    async def complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
    ) -> tuple[str, int, float]:
        return await self._provider.complete(model or self.model, messages, system, max_tokens)

    async def complete_with_tools(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        tool_schemas: list[dict[str, Any]],
        tool_fns: dict[str, Any],
    ) -> tuple[str, int, float]:
        return await self._provider.complete_with_tools(
            model or self.model, messages, system, max_tokens, tool_schemas, tool_fns
        )

    async def stream_complete(  # type: ignore[override]
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        agent_id: str,
        step_id: str,
        run_id: str,
    ) -> "AsyncIterator[Any]":
        async for chunk in self._provider.stream_complete(
            model or self.model, messages, system, max_tokens, agent_id, step_id, run_id
        ):
            yield chunk

    def __repr__(self) -> str:
        return f"LLM(model={self.model!r}, provider={type(self._provider).__name__})"


# ── Azure Managed Identity ────────────────────────────────────────────────────


class AzureIdentityProvider(AzureOpenAIProvider):
    """Azure OpenAI provider using Managed Identity (DefaultAzureCredential).

    Eliminates hardcoded API keys.  Works with:
    - System-assigned / user-assigned managed identity (Azure VMs, AKS, App Service)
    - Azure CLI credentials (local dev: ``az login``)
    - Workload identity (AKS with OIDC)
    - Service principal via env vars (AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_CLIENT_SECRET)

    Install: pip install azure-identity openai

    Usage::

        provider = AzureIdentityProvider(
            endpoint="https://my-resource.openai.azure.com/",
            model="gpt-4o",
        )
    """

    _TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"

    def __init__(
        self,
        model: str = "gpt-4o",
        endpoint: str = "",
        api_version: str = "2025-01-01-preview",
        input_rate: float = 0.005,
        output_rate: float = 0.015,
    ) -> None:
        super().__init__(
            model=model,
            endpoint=endpoint,
            api_key="",  # no static key — token fetched at runtime
            api_version=api_version,
            input_rate=input_rate,
            output_rate=output_rate,
        )

    def _get_token(self) -> str:
        try:
            from azure.identity import DefaultAzureCredential  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "AzureIdentityProvider requires azure-identity: pip install azure-identity"
            ) from exc
        cred = DefaultAzureCredential()
        token = cred.get_token(self._TOKEN_SCOPE)
        return token.token

    def _client(self) -> Any:
        try:
            from openai import AsyncAzureOpenAI
        except ImportError as exc:
            raise ImportError("AzureIdentityProvider requires openai: pip install openai") from exc
        import os

        return AsyncAzureOpenAI(
            azure_endpoint=self._endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT", ""),
            azure_ad_token=self._get_token(),
            api_version=self._api_version,
        )


# ── AWS Bedrock with IAM Role ─────────────────────────────────────────────────


class BedrockIAMProvider(BedrockProvider):
    """AWS Bedrock provider with IAM role assumption (no hardcoded credentials).

    Supports:
    - IAM roles via ``sts:AssumeRole``
    - Named AWS profiles (``~/.aws/credentials``)
    - ECS task roles / EC2 instance profiles (automatic via boto3)
    - Cross-account access via role ARN

    Install: pip install boto3

    Usage::

        # Assume an IAM role
        provider = BedrockIAMProvider(role_arn="arn:aws:iam::123456789012:role/MeshFlowRole")

        # Use a named profile
        provider = BedrockIAMProvider(profile_name="prod-readonly")

        # Rely on instance/task role (EC2 / ECS / Lambda)
        provider = BedrockIAMProvider()
    """

    def __init__(
        self,
        model: str = "anthropic.claude-3-5-sonnet-20241022-v2:0",
        region: str = "us-east-1",
        role_arn: str = "",
        profile_name: str = "",
        session_name: str = "meshflow-session",
    ) -> None:
        super().__init__(model=model, region=region)
        self._role_arn = role_arn
        self._profile_name = profile_name
        self._session_name = session_name

    def _client(self) -> Any:
        try:
            import boto3  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError("BedrockIAMProvider requires boto3: pip install boto3") from exc

        if self._profile_name:
            session = boto3.Session(profile_name=self._profile_name)
        elif self._role_arn:
            sts = boto3.client("sts", region_name=self._region)
            assumed = sts.assume_role(
                RoleArn=self._role_arn,
                RoleSessionName=self._session_name,
            )
            creds = assumed["Credentials"]
            session = boto3.Session(
                aws_access_key_id=creds["AccessKeyId"],
                aws_secret_access_key=creds["SecretAccessKey"],
                aws_session_token=creds["SessionToken"],
            )
        else:
            session = boto3.Session()

        return session.client("bedrock-runtime", region_name=self._region)


# ── GCP Vertex AI ─────────────────────────────────────────────────────────────


class VertexAIProvider:
    """Google Cloud Vertex AI provider for Gemini models.

    Supports:
    - Application Default Credentials (``gcloud auth application-default login``)
    - Service account key file (GOOGLE_APPLICATION_CREDENTIALS env var)
    - Workload Identity (GKE)

    Install: pip install google-cloud-aiplatform

    Usage::

        provider = VertexAIProvider(
            project="my-gcp-project",
            location="us-central1",
            model="gemini-2.0-flash",
        )
    """

    def __init__(
        self,
        project: str = "",
        location: str = "us-central1",
        model: str = "gemini-2.0-flash-001",
    ) -> None:
        import os
        self._project = project or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        self._location = location
        self._model = model

    async def complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
    ) -> tuple[str, int, float]:
        try:
            import vertexai  # type: ignore[import]
            from vertexai.generative_models import GenerativeModel, Content, Part  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "VertexAIProvider requires google-cloud-aiplatform: "
                "pip install google-cloud-aiplatform"
            ) from exc

        vertexai.init(project=self._project, location=self._location)
        m = model or self._model
        gemini = GenerativeModel(
            m,
            system_instruction=system if system else None,
        )

        contents = []
        for msg in messages:
            role = "user" if msg.get("role") == "user" else "model"
            contents.append(Content(role=role, parts=[Part.from_text(msg.get("content", ""))]))

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: gemini.generate_content(contents, generation_config={"max_output_tokens": max_tokens}),
        )

        text = response.text if hasattr(response, "text") else ""
        usage = getattr(response, "usage_metadata", None)
        in_tok = getattr(usage, "prompt_token_count", 0) if usage else 0
        out_tok = getattr(usage, "candidates_token_count", 0) if usage else 0
        cost = 0.0  # Vertex AI pricing depends on region + model version
        return text, in_tok + out_tok, cost

    async def complete_with_tools(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        _tool_schemas: list[dict[str, Any]],
        _tool_fns: dict[str, Any],
    ) -> tuple[str, int, float]:
        return await self.complete(model, messages, system, max_tokens)

    async def stream_complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        agent_id: str,
        step_id: str,
        run_id: str,
    ) -> "AsyncIterator[Any]":
        from meshflow.agents.base import TokenChunk
        text, _, _ = await self.complete(model, messages, system, max_tokens)
        if text:
            yield TokenChunk(text=text, agent_id=agent_id, step_id=step_id, run_id=run_id)
