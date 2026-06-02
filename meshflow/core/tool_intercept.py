"""ToolCallInterceptor — policy enforcement at the individual tool-call level.

This fills the enforcement gap between node-level governance (StepRuntime) and
the actual tool invocations an LLM generates mid-node.  StepRuntime wraps
``node.run()`` as a whole; this interceptor fires on each tool call *within*
that execution, before the call is dispatched.

The same interceptor handles both paths:
  - OpenAI-style function/tool calls (LLM-generated, dispatched by any framework)
  - MCP tool calls (routed through MCPGateway)

Usage::

    from meshflow.core.tool_intercept import (
        ToolCallEvent,
        ToolCallDecision,
        PolicyToolCallInterceptor,
    )
    from meshflow.policy.engine import PolicyEngine, PolicyStore, PolicyAction

    store = PolicyStore()
    engine = PolicyEngine(store)
    interceptor = PolicyToolCallInterceptor(engine)

    # In a node that wraps LLM calls:
    event = ToolCallEvent(tool_name="write_file", args={"path": "/etc/passwd"}, agent_id="writer")
    decision = await interceptor.before_call(event)
    if not decision.allowed:
        raise RuntimeError(decision.block_reason)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class ToolCallEvent:
    """A tool call that has been requested but not yet executed."""

    tool_name: str
    args: dict[str, Any]
    agent_id: str
    call_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source: str = "llm"        # "llm" | "mcp" | "registry"
    run_id: str = ""
    node_id: str = ""


@dataclass
class ToolCallDecision:
    """Enforcement decision for a single tool call."""

    allowed: bool
    block_reason: str = ""
    modified_args: dict[str, Any] | None = None  # None means use original args


# ── Protocol ──────────────────────────────────────────────────────────────────

@runtime_checkable
class ToolCallInterceptor(Protocol):
    """Evaluate a tool call before execution.

    Implementors return a ``ToolCallDecision``.  If ``allowed=False`` the
    caller must not execute the tool call.  If ``modified_args`` is set the
    caller should use those args instead of the originals (useful for
    PII-scrubbing args before they reach the tool).
    """

    async def before_call(self, event: ToolCallEvent) -> ToolCallDecision: ...


# ── Allow-list interceptor ────────────────────────────────────────────────────

class AllowListInterceptor:
    """Block any tool call whose name is not in the explicit allow-list."""

    def __init__(self, allowed_tools: list[str]) -> None:
        self._allowed = set(allowed_tools)

    async def before_call(self, event: ToolCallEvent) -> ToolCallDecision:
        if event.tool_name in self._allowed:
            return ToolCallDecision(allowed=True)
        return ToolCallDecision(
            allowed=False,
            block_reason=f"tool '{event.tool_name}' not in allow-list",
        )


# ── PII-scanning interceptor ──────────────────────────────────────────────────

class PiiScanInterceptor:
    """Block (or mask) tool calls whose args contain PII/PHI/credentials.

    Requires ``meshflow.security.sensitive_data.SensitiveDataDetector``.
    Set ``mask=True`` to scrub the args and allow the call rather than block.
    """

    def __init__(self, mask: bool = False) -> None:
        self._mask = mask
        self._detector: Any = None

    def _get_detector(self) -> Any:
        if self._detector is None:
            from meshflow.security.sensitive_data import SensitiveDataDetector
            self._detector = SensitiveDataDetector()
        return self._detector

    async def before_call(self, event: ToolCallEvent) -> ToolCallDecision:
        detector = self._get_detector()
        args_str = str(event.args)
        matches = detector.scan(args_str)
        if not matches:
            return ToolCallDecision(allowed=True)
        if self._mask:
            masked_str = detector.mask(args_str)
            return ToolCallDecision(allowed=True, modified_args={"_masked": masked_str})
        kinds = list({m.kind for m in matches})
        return ToolCallDecision(
            allowed=False,
            block_reason=f"tool args contain sensitive data: {kinds}",
        )


# ── Policy-engine interceptor ─────────────────────────────────────────────────

class PolicyToolCallInterceptor:
    """Evaluate tool calls against the PolicyEngine + optional PII scan.

    The PolicyEngine context dict exposed to rules:

    .. code-block:: python

        {
            "tool_name":  event.tool_name,
            "agent_id":   event.agent_id,
            "source":     event.source,   # "llm" | "mcp" | "registry"
            "run_id":     event.run_id,
            "node_id":    event.node_id,
        }

    Example rule that blocks ``write_file`` for any agent::

        store.add_rule(
            name="block-write-file",
            action=PolicyAction.DENY,
            conditions=[("tool_name", "eq", "write_file")],
            framework="tool_calls",
        )
    """

    def __init__(
        self,
        policy_engine: Any,                      # PolicyEngine (avoid hard import)
        pii_scan: bool = False,
        mask_pii: bool = False,
        allow_list: list[str] | None = None,
    ) -> None:
        self._engine = policy_engine
        self._pii = PiiScanInterceptor(mask=mask_pii) if pii_scan else None
        self._allow_list = AllowListInterceptor(allow_list) if allow_list is not None else None
        self._decisions: list[dict[str, Any]] = []

    async def before_call(self, event: ToolCallEvent) -> ToolCallDecision:
        # 1. Allow-list check (fast path)
        if self._allow_list is not None:
            al_decision = await self._allow_list.before_call(event)
            if not al_decision.allowed:
                self._record(event, al_decision)
                return al_decision

        # 2. Policy engine evaluation
        context = {
            "tool_name": event.tool_name,
            "agent_id":  event.agent_id,
            "source":    event.source,
            "run_id":    event.run_id,
            "node_id":   event.node_id,
        }
        decision = self._engine.evaluate(context, framework="tool_calls")
        if not decision.is_allowed:
            result = ToolCallDecision(
                allowed=False,
                block_reason=f"policy:{decision.rule_name}:{decision.reason}",
            )
            self._record(event, result)
            return result

        # 3. PII scan (optional)
        if self._pii is not None:
            pii_decision = await self._pii.before_call(event)
            if not pii_decision.allowed:
                self._record(event, pii_decision)
                return pii_decision
            if pii_decision.modified_args is not None:
                result = ToolCallDecision(allowed=True, modified_args=pii_decision.modified_args)
                self._record(event, result)
                return result

        result = ToolCallDecision(allowed=True)
        self._record(event, result)
        return result

    def _record(self, event: ToolCallEvent, decision: ToolCallDecision) -> None:
        self._decisions.append({
            "call_id":      event.call_id,
            "tool_name":    event.tool_name,
            "agent_id":     event.agent_id,
            "source":       event.source,
            "allowed":      decision.allowed,
            "block_reason": decision.block_reason,
        })

    def audit_log(self) -> list[dict[str, Any]]:
        """Return all decisions recorded since construction."""
        return list(self._decisions)


# ── Composite interceptor ─────────────────────────────────────────────────────

class ChainedInterceptor:
    """Run multiple interceptors in sequence; first DENY wins."""

    def __init__(self, interceptors: list[Any]) -> None:
        self._interceptors = interceptors

    async def before_call(self, event: ToolCallEvent) -> ToolCallDecision:
        for interceptor in self._interceptors:
            decision = await interceptor.before_call(event)
            if not decision.allowed:
                return decision
            if decision.modified_args is not None:
                event = ToolCallEvent(
                    tool_name=event.tool_name,
                    args=decision.modified_args,
                    agent_id=event.agent_id,
                    call_id=event.call_id,
                    source=event.source,
                    run_id=event.run_id,
                    node_id=event.node_id,
                )
        return ToolCallDecision(allowed=True)


__all__ = [
    "ToolCallEvent",
    "ToolCallDecision",
    "ToolCallInterceptor",
    "AllowListInterceptor",
    "PiiScanInterceptor",
    "PolicyToolCallInterceptor",
    "ChainedInterceptor",
]
