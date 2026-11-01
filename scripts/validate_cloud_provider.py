"""Step 1: explicit, cost-bounded cloud validation with locally retained evidence."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
import shlex
import shutil
import subprocess
from urllib.parse import urlsplit
from pathlib import Path
import tempfile
from typing import Any

from meshflow import Agent, Policy
from meshflow.agents.base import AnthropicProvider, OpenAICompatibleProvider, update_pricing
from meshflow.core.session import GovernedSession
from meshflow.tools.registry import Tool


@dataclass(frozen=True)
class Settings:
    provider: str
    model: str
    input_per_1k: float
    output_per_1k: float
    max_usd: float = 0.05
    max_tokens: int = 128
    timeout_s: float = 90
    endpoint: str = ""
    azure_subscription: str = ""
    auth_mode: str = "api_key"

    def __post_init__(self) -> None:
        if self.provider not in {"openai", "anthropic", "azure"} or not self.model.strip():
            raise ValueError("Choose openai, anthropic or azure and an explicit model")
        if self.auth_mode not in {"api_key", "azure_cli"}:
            raise ValueError("Unsupported authentication mode")
        if self.auth_mode == "azure_cli" and self.provider != "azure":
            raise ValueError("Azure CLI authentication requires the azure provider")
        for number in (self.input_per_1k, self.output_per_1k, self.max_usd, self.timeout_s):
            if not math.isfinite(number) or number <= 0:
                raise ValueError("Prices, budget and timeout must be finite and positive")
        if (
            isinstance(self.max_tokens, bool)
            or not isinstance(self.max_tokens, int)
            or self.max_tokens <= 0
        ):
            raise ValueError("max_tokens must be a positive integer")


def credential_name(provider: str) -> str:
    return {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "azure": "AZURE_OPENAI_API_KEY",
    }[provider]


def azure_endpoint(settings: Settings) -> str:
    endpoint = settings.endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith((".openai.azure.com", ".services.ai.azure.com"))
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.port not in (None, 443)
        or parsed.path.rstrip("/") not in ("", "/openai/v1")
    ):
        raise ValueError(
            "Use a direct HTTPS Azure OpenAI endpoint without credentials or query parameters"
        )
    return endpoint.rstrip("/")


def azure_cli_token(subscription: str) -> str:
    command = [
        "az",
        "account",
        "get-access-token",
        "--resource",
        "https://cognitiveservices.azure.com/",
        "-o",
        "json",
    ]
    if subscription:
        command += ["--subscription", subscription]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError("Azure CLI authentication unavailable")
    token = json.loads(result.stdout).get("accessToken", "")
    if not isinstance(token, str) or not token:
        raise RuntimeError("Azure CLI did not return an access token")
    return token


def load_credential_file(path: Path, provider: str) -> None:
    """Read only the selected credential as data; environment values take precedence."""
    key = credential_name(provider)
    if os.environ.get(key, "").strip() or not path.is_file():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip().removeprefix("export ")
        name, separator, raw_value = line.partition("=")
        if not separator or name.strip() != key:
            continue
        values = shlex.split(raw_value, comments=True)
        if len(values) > 1:
            raise ValueError(f"Malformed credential entry for {key}")
        if values:
            os.environ[key] = values[0]
            return


def preflight(settings: Settings) -> dict[str, Any]:
    key_name = credential_name(settings.provider)
    missing = [] if os.environ.get(key_name) else [key_name]
    if settings.provider == "azure":
        if settings.auth_mode == "azure_cli":
            missing = [] if shutil.which("az") else ["Azure CLI (az)"]
        if not (settings.endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT")):
            missing.append("AZURE_OPENAI_ENDPOINT")
        else:
            azure_endpoint(settings)
    mocked = os.environ.get("MESHFLOW_MOCK") == "1"
    return {
        "status": "ready" if not missing and not mocked else "blocked",
        "provider": settings.provider,
        "model": settings.model,
        "authentication": settings.auth_mode,
        "missing_environment": missing,
        "mock_mode_enabled": mocked,
        "max_usd": settings.max_usd,
        "max_output_tokens": settings.max_tokens,
        "pricing": {
            "input_usd_per_1k": settings.input_per_1k,
            "output_usd_per_1k": settings.output_per_1k,
            "source": "operator-supplied; verify against provider pricing",
        },
        "checks": ["completion", "streaming", "cancellation", "tool_call", "audit"],
        "billing_reconciled": False,
    }


def make_provider(settings: Settings) -> Any:
    if settings.provider == "openai":
        return OpenAICompatibleProvider(
            model=settings.model,
            base_url="https://api.openai.com/v1",
            input_rate=settings.input_per_1k,
            output_rate=settings.output_per_1k,
            token_parameter="max_completion_tokens",
        )
    if settings.provider == "azure":
        from meshflow.agents.providers import AzureOpenAIProvider

        token = (
            azure_cli_token(settings.azure_subscription)
            if settings.auth_mode == "azure_cli"
            else ""
        )
        return AzureOpenAIProvider(
            model=settings.model,
            endpoint=azure_endpoint(settings),
            api_version="v1",
            input_rate=settings.input_per_1k,
            output_rate=settings.output_per_1k,
            token_parameter="max_completion_tokens",
            azure_ad_token=token,
        )
    update_pricing(settings.model, settings.input_per_1k, settings.output_per_1k)
    return AnthropicProvider()


async def validate(settings: Settings, output_dir: Path) -> dict[str, Any]:
    report = preflight(settings)
    if report["status"] != "ready":
        return report
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = output_dir / "audit.db"
    report.update(
        status="running",
        ledger_db=str(ledger_path),
        completed_checks=[],
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    provider: Any = None
    policy = Policy(
        strict_budget=True,
        budget_usd=settings.max_usd,
        budget_tokens=50000,
        enable_uncertainty=False,
        deterministic_gate=False,
    )
    tool_calls: list[tuple[int, int]] = []

    async def add(left: int, right: int) -> str:
        left, right = int(left), int(right)
        tool_calls.append((left, right))
        return str(left + right)

    arithmetic = Tool(
        name="validation_add", description="Add two integers for this validation.", fn=add
    )

    def agent(name: str, **kwargs: Any) -> Agent:
        return Agent(
            name,
            model=settings.model,
            provider=provider,
            max_tokens=settings.max_tokens,
            system_prompt="Follow instructions exactly. Keep responses brief.",
            **kwargs,
        )

    session = GovernedSession(policy, str(ledger_path))
    try:
        async with asyncio.timeout(settings.timeout_s), session:
            provider = make_provider(settings)
            result = await agent("completion").run("Reply with the word READY.")
            if result.get("blocked") or not str(result.get("result", "")).strip():
                raise RuntimeError("Completion failed")
            report["completed_checks"].append("completion")
            pieces = [
                text async for text in agent("streaming").stream("Reply with the word READY.")
            ]
            if not "".join(pieces).strip():
                raise RuntimeError("Stream produced no output")
            report["completed_checks"].append("streaming")
            iterator = agent("cancellation").stream(
                "Write the numbers from 1 to 100, separated by spaces."
            )
            try:
                await anext(iterator)
            finally:
                await iterator.aclose()
            cancelled = session.outcomes[-1].record
            if not cancelled.blocked or cancelled.block_reason != "stream:cancelled":
                raise RuntimeError("Cancellation did not finalize a partial audit record")
            report["completed_checks"].append("cancellation")
            result = await agent("tool_call", tools=[arithmetic]).run(
                "You must call validation_add with left=2 and right=3, then reply with the result."
            )
            if result.get("blocked") or (2, 3) not in tool_calls:
                raise RuntimeError("Provider did not execute the validation tool")
            report["completed_checks"].append("tool_call")
            if not (await session.ledger.verify_chain(session.run_id))["valid"]:
                raise RuntimeError("Audit chain verification failed")
            if session.runtime._budget._reserved:
                raise RuntimeError("Request reservations were not finalized")
            report["completed_checks"].append("audit")
            report["status"] = "passed"
    except Exception as exc:
        # Provider errors can include credential fragments; keep raw errors out of receipts.
        report.update(status="failed", error_type=type(exc).__name__)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["run_id"] = session.run_id
    report["steps"] = [
        {
            "name": o.node_id,
            "blocked": o.record.blocked,
            "reason": o.record.block_reason.split(":", 1)[0] if o.record.blocked else "",
            "tokens": o.record.tokens_used,
            "accounted_usd": o.record.cost_usd,
            "entry_hash": o.record.entry_hash,
        }
        for o in session.outcomes
    ]
    report["accounted_or_reserved_usd"] = sum(o.record.cost_usd for o in session.outcomes)
    report["invoice_reconciliation_required"] = True
    receipt = output_dir / "receipt.json"
    report["receipt"] = str(receipt)
    receipt.write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["openai", "anthropic", "azure"], required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--azure-subscription", default="")
    parser.add_argument("--auth-mode", choices=["api_key", "azure_cli"], default="api_key")
    parser.add_argument("--input-per-1k", type=float, required=True)
    parser.add_argument("--output-per-1k", type=float, required=True)
    parser.add_argument("--max-usd", type=float, default=0.05)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Read only the selected provider key from this local file",
    )
    parser.add_argument(
        "--execute", action="store_true", help="Make bounded real provider calls after preflight"
    )
    args = parser.parse_args()
    load_credential_file(args.env_file, args.provider)
    settings = Settings(
        args.provider,
        args.model,
        args.input_per_1k,
        args.output_per_1k,
        args.max_usd,
        args.max_tokens,
        args.timeout,
        args.endpoint,
        args.azure_subscription,
        args.auth_mode,
    )
    report = preflight(settings)
    if args.execute and report["status"] == "ready":
        directory = args.output_dir or Path(tempfile.mkdtemp(prefix="meshflow-cloud-validation-"))
        report = asyncio.run(validate(settings, directory))
    print(json.dumps(report, indent=2))
    return 0 if report["status"] in {"ready", "passed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
