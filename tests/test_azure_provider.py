"""Unit tests for the Azure OpenAI Provider."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from meshflow.agents.providers import AzureOpenAIProvider, provider_for


@pytest.mark.asyncio
async def test_azure_provider_instantiates_and_configures():
    provider = AzureOpenAIProvider(
        model="gpt-4o",
        endpoint="https://test-endpoint.openai.azure.com/",
        api_key="test-api-key",
    )
    assert provider._model == "gpt-4o"
    assert provider._endpoint == "https://test-endpoint.openai.azure.com/"
    assert provider._api_key == "test-api-key"


@pytest.mark.asyncio
async def test_azure_provider_factory():
    provider = provider_for("azure", endpoint="https://test-endpoint.openai.azure.com/", api_key="test-api-key")
    assert isinstance(provider, AzureOpenAIProvider)
    assert provider._endpoint == "https://test-endpoint.openai.azure.com/"


@pytest.mark.asyncio
@patch("openai.AsyncAzureOpenAI")
async def test_azure_provider_complete(mock_azure_client):
    # Set up mock response
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Azure response"
    mock_response.usage.prompt_tokens = 10
    mock_response.usage.completion_tokens = 5
    
    mock_client_instance = MagicMock()
    mock_client_instance.chat.completions.create = AsyncMock(return_value=mock_response)
    mock_azure_client.return_value = mock_client_instance

    provider = AzureOpenAIProvider(
        model="gpt-4o",
        endpoint="https://test.openai.azure.com/",
        api_key="test-key",
    )
    
    content, tokens, cost = await provider.complete("gpt-4o", [{"role": "user", "content": "hello"}], "system", 100)
    
    assert content == "Azure response"
    assert tokens == 15
    assert cost > 0
    mock_client_instance.chat.completions.create.assert_called_once()
