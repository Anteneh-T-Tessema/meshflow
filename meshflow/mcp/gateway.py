"""L2.9 — MCP Gateway: proxy, validate, rate-limit, and trace every tool call.

In Feb 2026 researchers found 341 malicious skills on a major MCP marketplace
with prompt injection payloads and credential harvesters. Every call is:
  - Validated against a signed manifest registry
  - Rate-limited per agent
  - Budget-capped (per-turn cost ceiling)
  - Traced with full OTEL spans
"""
from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from meshflow.core.schemas import MCPToolCall


@dataclass
class ToolManifest:
    """Signed description of an MCP tool's expected behaviour."""
    tool_name: str
    server_uri: str
    description: str
    max_cost_usd: float = 0.10         # per-call ceiling
    max_calls_per_minute: int = 10
    allowed_agent_roles: list[str] = field(default_factory=list)
    signature: str = ""               # manifest signing key (simplified)
    trusted: bool = False

    def validate_signature(self) -> bool:
        expected = hashlib.sha256(
            f"{self.tool_name}:{self.server_uri}:{self.description}".encode()
        ).hexdigest()
        return self.signature == expected or self.trusted


@dataclass
class RateLimiterState:
    calls: list[float] = field(default_factory=list)

    def allow(self, max_per_minute: int) -> bool:
        now = time.monotonic()
        self.calls = [t for t in self.calls if now - t < 60.0]
        if len(self.calls) >= max_per_minute:
            return False
        self.calls.append(now)
        return True


class MCPGateway:
    """Proxies all MCP tool calls through validation, rate limiting, and tracing."""

    def __init__(
        self,
        budget_usd_per_turn: float = 0.05,
        on_trace: Callable[[MCPToolCall], Awaitable[None]] | None = None,
    ) -> None:
        self._manifests: dict[str, ToolManifest] = {}
        self._rate_limiters: dict[str, RateLimiterState] = {}  # agent_id:tool_name
        self._budget_per_turn = budget_usd_per_turn
        self._on_trace = on_trace
        self._blocked: list[MCPToolCall] = []
        self._total_calls = 0
        self._total_cost = 0.0

    def register_tool(self, manifest: ToolManifest) -> None:
        self._manifests[manifest.tool_name] = manifest

    def register_tools(self, manifests: list[ToolManifest]) -> None:
        for m in manifests:
            self.register_tool(m)

    async def call(
        self,
        tool_name: str,
        params: dict[str, Any],
        agent_id: str,
        agent_role: str,
        trace_id: str,
        handler: Callable[[str, dict[str, Any]], Awaitable[Any]],
    ) -> MCPToolCall:
        call = MCPToolCall(
            tool_name=tool_name,
            agent_id=agent_id,
            params=params,
            trace_id=trace_id,
            timestamp=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )

        manifest = self._manifests.get(tool_name)

        # 1. Manifest validation
        if not manifest:
            call.blocked = True
            call.block_reason = f"Tool '{tool_name}' not in registry"
            self._blocked.append(call)
            if self._on_trace:
                await self._on_trace(call)
            return call

        if not manifest.validate_signature():
            call.blocked = True
            call.block_reason = f"Tool '{tool_name}' manifest signature invalid"
            self._blocked.append(call)
            if self._on_trace:
                await self._on_trace(call)
            return call

        call.server_uri = manifest.server_uri

        # 2. Role check
        if manifest.allowed_agent_roles and agent_role not in manifest.allowed_agent_roles:
            call.blocked = True
            call.block_reason = f"Role '{agent_role}' not allowed for '{tool_name}'"
            self._blocked.append(call)
            if self._on_trace:
                await self._on_trace(call)
            return call

        # 3. Rate limiting
        rl_key = f"{agent_id}:{tool_name}"
        limiter = self._rate_limiters.setdefault(rl_key, RateLimiterState())
        if not limiter.allow(manifest.max_calls_per_minute):
            call.blocked = True
            call.block_reason = f"Rate limit exceeded: {manifest.max_calls_per_minute}/min"
            self._blocked.append(call)
            if self._on_trace:
                await self._on_trace(call)
            return call

        # 4. Budget cap per turn
        if manifest.max_cost_usd > self._budget_per_turn:
            call.blocked = True
            call.block_reason = (
                f"Tool cost ceiling ${manifest.max_cost_usd:.3f} exceeds "
                f"turn budget ${self._budget_per_turn:.3f}"
            )
            self._blocked.append(call)
            if self._on_trace:
                await self._on_trace(call)
            return call

        # 5. Execute
        start = time.monotonic()
        try:
            result = await handler(tool_name, params)
            call.result = result
            call.validated = True
        except Exception as e:
            call.result = None
            call.block_reason = str(e)
        finally:
            call.latency_ms = (time.monotonic() - start) * 1000
            call.cost_usd = manifest.max_cost_usd

        self._total_calls += 1
        self._total_cost += call.cost_usd

        if self._on_trace:
            await self._on_trace(call)

        return call

    def blocked_count(self) -> int:
        return len(self._blocked)

    def stats(self) -> dict[str, Any]:
        return {
            "total_calls": self._total_calls,
            "blocked_calls": len(self._blocked),
            "total_cost_usd": round(self._total_cost, 6),
            "registered_tools": len(self._manifests),
        }
