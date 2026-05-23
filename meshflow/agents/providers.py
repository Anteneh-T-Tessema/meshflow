"""Extended LLM provider implementations for MeshFlow.

Beyond the built-in AnthropicProvider and OpenAICompatibleProvider in base.py,
this module adds:
  - GeminiProvider    (Google Gemini via google-generativeai)
  - BedrockProvider   (AWS Bedrock Claude via boto3)
  - AzureOpenAIProvider (Azure OpenAI via openai SDK)

Factory function::

    from meshflow.agents.providers import provider_for
    p = provider_for("gemini", model="gemini-2.0-flash")
    p = provider_for("bedrock", model="anthropic.claude-3-5-sonnet-20241022-v2:0")
    p = provider_for("azure", model="gpt-4o", endpoint="https://...", api_key="...")
"""

from __future__ import annotations

import asyncio
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


# ── Factory ───────────────────────────────────────────────────────────────────

_PROVIDER_MAP: dict[str, type[LLMProvider] | None] = {
    "anthropic": None,  # resolved lazily to avoid circular import
    "openai": None,
    "gemini": GeminiProvider,
    "bedrock": BedrockProvider,
    "azure": AzureOpenAIProvider,
}


def provider_for(name: str, **kwargs: Any) -> Any:
    """Return a provider instance by short name.

    Names: "anthropic", "openai", "gemini", "bedrock", "azure".
    Extra kwargs are passed to the provider constructor.

    Example::

        p = provider_for("gemini", model="gemini-2.0-flash")
        p = provider_for("bedrock", model="anthropic.claude-3-5-sonnet-20241022-v2:0")
        p = provider_for("azure", endpoint="https://...", api_key="sk-...")
    """
    name = name.lower()
    if name == "anthropic":
        from meshflow.agents.base import AnthropicProvider

        return AnthropicProvider()
    if name == "openai":
        from meshflow.agents.base import OpenAICompatibleProvider

        return OpenAICompatibleProvider(**kwargs)
    cls = _PROVIDER_MAP.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown provider {name!r}. Choose: anthropic, openai, gemini, bedrock, azure"
        )
    return cls(**kwargs)
