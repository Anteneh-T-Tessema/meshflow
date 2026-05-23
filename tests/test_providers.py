"""Tests for extended provider implementations: Gemini, Bedrock, Azure, provider_for."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestGeminiProvider:
    def test_import(self) -> None:
        from meshflow.agents.providers import GeminiProvider
        assert GeminiProvider is not None

    def test_client_raises_without_sdk(self) -> None:
        import sys
        from meshflow.agents.providers import GeminiProvider
        p = GeminiProvider(model="gemini-2.0-flash", api_key="fake")
        with patch.dict(sys.modules, {"google.generativeai": None, "google": None}):
            with pytest.raises((ImportError, TypeError, AttributeError)):
                p._client()

    @pytest.mark.asyncio
    async def test_complete_returns_tuple(self) -> None:
        from meshflow.agents.providers import GeminiProvider
        p = GeminiProvider(model="gemini-2.0-flash", api_key="fake-key")

        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Gemini says hello"
        mock_response.usage_metadata = MagicMock(prompt_token_count=30, candidates_token_count=20)
        mock_model.generate_content.return_value = mock_response

        mock_genai = MagicMock()
        mock_genai.GenerativeModel.return_value = mock_model
        mock_genai.configure = MagicMock()

        with patch.dict(
            __import__("sys").modules,
            {"google.generativeai": mock_genai, "google": MagicMock(generativeai=mock_genai)},
        ):
            text, tokens, cost = await p.complete(
                model="gemini-2.0-flash",
                messages=[{"role": "user", "content": "hi"}],
                system="You are helpful",
                max_tokens=100,
            )
        assert "Gemini" in text
        assert isinstance(tokens, int)
        assert isinstance(cost, float)


class TestBedrockProvider:
    def test_import(self) -> None:
        from meshflow.agents.providers import BedrockProvider
        assert BedrockProvider is not None

    def test_init_default_region(self) -> None:
        from meshflow.agents.providers import BedrockProvider
        p = BedrockProvider()
        assert p._region == "us-east-1"

    def test_client_raises_without_boto3(self) -> None:
        import sys
        from meshflow.agents.providers import BedrockProvider
        p = BedrockProvider()
        with patch.dict(sys.modules, {"boto3": None}):
            with pytest.raises((ImportError, TypeError)):
                p._client()

    @pytest.mark.asyncio
    async def test_complete_parses_response(self) -> None:
        from meshflow.agents.providers import BedrockProvider
        import json

        p = BedrockProvider(model="anthropic.claude-3-5-sonnet-20241022-v2:0")

        mock_body = MagicMock()
        mock_body.read.return_value = json.dumps({
            "content": [{"type": "text", "text": "Bedrock says hi"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }).encode()

        mock_response = {"body": mock_body}
        mock_client = MagicMock()
        mock_client.invoke_model.return_value = mock_response

        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_client

        with patch.dict(__import__("sys").modules, {"boto3": mock_boto3}):
            text, tokens, cost = await p.complete(
                model="anthropic.claude-3-5-sonnet-20241022-v2:0",
                messages=[{"role": "user", "content": "hello"}],
                system="Be helpful",
                max_tokens=50,
            )
        assert "Bedrock" in text
        assert tokens == 15


class TestAzureOpenAIProvider:
    def test_import(self) -> None:
        from meshflow.agents.providers import AzureOpenAIProvider
        assert AzureOpenAIProvider is not None

    def test_init_stores_config(self) -> None:
        from meshflow.agents.providers import AzureOpenAIProvider
        p = AzureOpenAIProvider(
            endpoint="https://myorg.openai.azure.com",
            api_key="az-key",
            api_version="2024-02-01",
            model="gpt-4o",
        )
        assert p._endpoint == "https://myorg.openai.azure.com"
        assert p._model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_complete_delegates_to_azure_client(self) -> None:
        from meshflow.agents.providers import AzureOpenAIProvider

        p = AzureOpenAIProvider(
            endpoint="https://myorg.openai.azure.com",
            api_key="az-key",
            model="gpt-4o",
        )

        mock_choice = MagicMock()
        mock_choice.message.content = "Azure says hello"
        mock_completion = MagicMock()
        mock_completion.choices = [mock_choice]
        mock_completion.usage = MagicMock(prompt_tokens=20, completion_tokens=10)

        mock_chat = MagicMock()
        mock_chat.completions = MagicMock()
        mock_chat.completions.create = AsyncMock(return_value=mock_completion)

        mock_client = MagicMock()
        mock_client.chat = mock_chat

        with patch.object(p, "_client", return_value=mock_client):
            text, tokens, cost = await p.complete(
                model="gpt-4o",
                messages=[{"role": "user", "content": "hi"}],
                system="Be helpful",
                max_tokens=100,
            )
        assert "Azure" in text
        assert tokens == 30


class TestProviderFor:
    def test_anthropic(self) -> None:
        from meshflow.agents.providers import provider_for
        from meshflow.agents.base import AnthropicProvider
        p = provider_for("anthropic")
        assert isinstance(p, AnthropicProvider)

    def test_gemini(self) -> None:
        from meshflow.agents.providers import provider_for, GeminiProvider
        p = provider_for("gemini", model="gemini-2.0-flash")
        assert isinstance(p, GeminiProvider)

    def test_bedrock(self) -> None:
        from meshflow.agents.providers import provider_for, BedrockProvider
        p = provider_for("bedrock")
        assert isinstance(p, BedrockProvider)

    def test_azure(self) -> None:
        from meshflow.agents.providers import provider_for, AzureOpenAIProvider
        p = provider_for("azure", endpoint="https://x.openai.azure.com", api_key="k")
        assert isinstance(p, AzureOpenAIProvider)

    def test_unknown_raises(self) -> None:
        from meshflow.agents.providers import provider_for
        with pytest.raises(ValueError, match="Unknown provider"):
            provider_for("nonexistent")

    def test_case_insensitive(self) -> None:
        from meshflow.agents.providers import provider_for, GeminiProvider
        p = provider_for("GEMINI")
        assert isinstance(p, GeminiProvider)
