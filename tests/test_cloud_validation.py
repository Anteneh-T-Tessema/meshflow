"""Cloud validation can be reviewed and tested without sending paid requests."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from meshflow import Agent
from meshflow.core.request_budget import report_stream_usage
from meshflow.core.schemas import TokenChunk
from scripts import validate_cloud_provider as validation


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("MESHFLOW_MOCK", "0")
    monkeypatch.setenv("MESHFLOW_LEDGER_DB", str(tmp_path / "runs.db"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


class FakeProvider:
    def __init__(self):
        self.output_limits = []

    def request_cost_bound(self, **kwargs):
        return 0.005, 100

    async def complete(self, **kwargs):
        self.output_limits.append(kwargs["max_tokens"])
        return "READY", 4, 0.001

    async def complete_with_tools(self, **kwargs):
        self.output_limits.append(kwargs["max_tokens"])
        assert await kwargs["tool_fns"]["validation_add"](left=2, right=3) == "5"
        return "5", 4, 0.001

    async def stream_complete(self, **kwargs):
        self.output_limits.append(kwargs["max_tokens"])
        yield TokenChunk(text="READY", agent_id=kwargs["agent_id"], step_id=kwargs["step_id"])
        if "numbers from" in str(kwargs["messages"]):
            await asyncio.Event().wait()
        report_stream_usage(SimpleNamespace(prompt_tokens=3, completion_tokens=2), (0.001, 0.002))


def settings():
    return validation.Settings("openai", "chosen-cloud-model", 0.001, 0.002, max_tokens=64)


@pytest.mark.asyncio
async def test_missing_credential_blocks_execution_before_provider_creation(tmp_path, monkeypatch):
    def unexpected(_):
        raise AssertionError("provider must not be created")

    monkeypatch.setattr(validation, "make_provider", unexpected)
    report = await validation.validate(settings(), tmp_path / "evidence")
    assert report["status"] == "blocked"
    assert report["missing_environment"] == ["OPENAI_API_KEY"]
    assert not (tmp_path / "evidence").exists()


def test_mock_mode_cannot_be_reported_as_cloud_validation(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-test-key")
    monkeypatch.setenv("MESHFLOW_MOCK", "1")
    assert validation.preflight(settings())["status"] == "blocked"


@pytest.mark.asyncio
async def test_runner_exercises_all_cases_with_cost_cap_and_persisted_receipt(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-test-key")
    provider = FakeProvider()
    monkeypatch.setattr(validation, "make_provider", lambda _: provider)
    report = await validation.validate(settings(), tmp_path / "evidence")
    assert report["status"] == "passed", report
    assert report["completed_checks"] == [
        "completion",
        "streaming",
        "cancellation",
        "tool_call",
        "audit",
    ]
    assert len(report["steps"]) == 4
    assert provider.output_limits == [64, 64, 64, 64]
    assert report["accounted_or_reserved_usd"] <= settings().max_usd
    assert report["billing_reconciled"] is False
    assert report["invoice_reconciliation_required"]
    receipt = json.loads((tmp_path / "evidence" / "receipt.json").read_text())
    assert receipt["run_id"] == report["run_id"]
    assert "fake-test-key" not in json.dumps(receipt)


@pytest.mark.asyncio
async def test_provider_constructor_error_is_redacted_and_evidence_retained(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-test-key")

    def failed(_):
        raise RuntimeError("failure containing fake-test-key")

    monkeypatch.setattr(validation, "make_provider", failed)
    report = await validation.validate(settings(), tmp_path / "evidence")
    assert report["status"] == "failed" and report["error_type"] == "RuntimeError"
    assert "fake-test-key" not in json.dumps(report)
    assert (tmp_path / "evidence" / "receipt.json").exists()


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_agent_rejects_invalid_output_limits(limit):
    with pytest.raises(ValueError, match="max_tokens"):
        Agent("bounded", max_tokens=limit)


def test_explicit_model_price_takes_precedence_over_family_rate(monkeypatch):
    from meshflow.agents.base import _PRICING, _cost_usd

    monkeypatch.setitem(_PRICING, "claude-sonnet-specific-validation", (0.001, 0.002))
    assert _cost_usd("claude-sonnet-specific-validation", 1000, 1000) == 0.003


@pytest.mark.asyncio
async def test_modern_openai_output_limit_is_sent_and_reserved(monkeypatch):
    from unittest.mock import AsyncMock
    from meshflow import Policy
    from meshflow.agents.base import OpenAICompatibleProvider
    from meshflow.core.policy import BudgetTracker
    from meshflow.core.request_budget import active_budget

    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="READY", tool_calls=None))],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
    )
    create = AsyncMock(return_value=response)
    provider = OpenAICompatibleProvider(
        model="chosen-model",
        input_rate=0.001,
        output_rate=0.002,
        token_parameter="max_completion_tokens",
    )
    monkeypatch.setattr(
        provider,
        "_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
    )
    tracker = BudgetTracker(Policy(budget_usd=0.01, strict_budget=True))
    token = active_budget.set(tracker)
    try:
        text, tokens, cost = await provider.complete(
            model="chosen-model", messages=[], system="", max_tokens=64
        )
        assert text == "READY" and tokens == 5 and cost == pytest.approx(0.000007)
        assert create.call_args.kwargs["max_completion_tokens"] == 64
        assert "max_tokens" not in create.call_args.kwargs
        assert not tracker._reserved
        assert tracker._provider_usd == pytest.approx(0.000007)
    finally:
        active_budget.reset(token)


def test_local_credential_loader_reads_only_selected_key_without_executing(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.delenv("VALIDATION_UNRELATED_SECRET", raising=False)
    marker = tmp_path / "executed"
    path = tmp_path / ".env"
    path.write_text(f'OPENAI_API_KEY="$(touch {marker})"\nVALIDATION_UNRELATED_SECRET=private\n')
    validation.load_credential_file(path, "openai")
    import os

    assert os.environ["OPENAI_API_KEY"] == f"$(touch {marker})"
    assert "VALIDATION_UNRELATED_SECRET" not in os.environ
    assert not marker.exists()


def test_local_credential_loader_preserves_environment_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    path = tmp_path / ".env"
    path.write_text("OPENAI_API_KEY=file-key\n")
    validation.load_credential_file(path, "openai")
    import os

    assert os.environ["OPENAI_API_KEY"] == "environment-key"


def test_registered_tool_schema_is_json_serializable():
    from meshflow.tools.registry import Tool
    from meshflow.agents.base import _build_tool_schema

    async def add(left: int, right: int) -> str:
        return str(left + right)

    schema = _build_tool_schema(Tool(name="add", description="Add two integers", fn=add))
    json.dumps(schema)
    assert schema["input_schema"]["properties"]["left"]["type"] == "integer"
    assert schema["input_schema"]["required"] == ["left", "right"]


def test_azure_cli_preflight_needs_no_static_key(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(validation.shutil, "which", lambda command: "/usr/bin/az")
    config = validation.Settings(
        "azure",
        "deployment",
        0.001,
        0.002,
        endpoint="https://example.openai.azure.com/",
        auth_mode="azure_cli",
    )
    report = validation.preflight(config)
    assert report["status"] == "ready" and report["authentication"] == "azure_cli"
    assert report["missing_environment"] == []


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.com/",
        "http://example.openai.azure.com/",
        "https://example.openai.azure.com/?key=secret",
        "https://user:secret@example.openai.azure.com/",
    ],
)
def test_azure_endpoint_rejects_credential_redirection(endpoint):
    config = validation.Settings(
        "azure", "deployment", 0.001, 0.002, endpoint=endpoint, auth_mode="azure_cli"
    )
    with pytest.raises(ValueError, match="direct HTTPS"):
        validation.preflight(config)


@pytest.mark.asyncio
async def test_azure_provider_inherits_real_tool_dispatch_and_usage(monkeypatch):
    from unittest.mock import AsyncMock
    from meshflow.agents.providers import AzureOpenAIProvider

    provider = AzureOpenAIProvider(
        model="deployment",
        endpoint="https://example.openai.azure.com/",
        input_rate=0.001,
        output_rate=0.002,
        token_parameter="max_completion_tokens",
    )
    tool_call = SimpleNamespace(
        id="call-1", function=SimpleNamespace(name="add", arguments='{"left": 2, "right": 3}')
    )
    first = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tool_call]))],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
    )
    second = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="5", tool_calls=None))],
        usage=SimpleNamespace(prompt_tokens=6, completion_tokens=1),
    )
    create = AsyncMock(side_effect=[first, second])
    monkeypatch.setattr(
        provider,
        "_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
    )
    calls = []

    async def add(left, right):
        calls.append((left, right))
        return str(left + right)

    result = await provider.complete_with_tools(
        "deployment",
        [],
        "",
        64,
        [{"name": "add", "description": "Add", "input_schema": {"type": "object"}}],
        {"add": add},
    )
    assert result[0] == "5" and result[1] == 12 and calls == [(2, 3)]
    assert create.await_count == 2
    assert all(call.kwargs["max_completion_tokens"] == 64 for call in create.call_args_list)
