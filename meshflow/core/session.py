"""A shared governed run for ergonomic APIs and arbitrary agent implementations."""

from __future__ import annotations

import inspect
import os
import uuid
from contextvars import ContextVar, Token
from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any, Awaitable, Callable

from meshflow.core.node import MeshNode, NodeInput, NodeKind, NodeOutput
from meshflow.core.policy import BudgetTracker
from meshflow.core.runtime import RuntimeOutcome, StepRuntime
from meshflow.core.schemas import Policy, policy_for_mode

active_session: ContextVar[GovernedSession | None] = ContextVar("governed_session", default=None)
active_ledger_scope: ContextVar[tuple[str, str] | None] = ContextVar("ledger_scope", default=None)


@contextmanager
def ledger_scope(path: str, tenant_id: str) -> Iterator[None]:
    """Bind audit ownership at an authenticated application boundary."""
    token = active_ledger_scope.set((path, tenant_id or "default"))
    try:
        yield
    finally:
        active_ledger_scope.reset(token)


def default_ledger_path() -> str:
    scope = active_ledger_scope.get()
    if scope is not None:
        return scope[0]
    return os.environ.get("MESHFLOW_LEDGER_DB", "meshflow_runs.db")


class GovernedSession:
    """Own the kernel, accounting and durable audit trail for one run."""

    def __init__(
        self,
        policy: Policy | str | None = None,
        ledger_db: str | None = None,
        run_id: str | None = None,
    ) -> None:
        from meshflow.core.ledger import ReplayLedger
        from meshflow.core.mesh import _default_tool_interceptor, _zt_from_env
        from meshflow.security.guardian import Guardian
        from meshflow.security.dasc_gate import DascGate
        from meshflow.security.identity import AgentIdentityProvider
        from meshflow.intelligence.uncertainty import UncertaintyEngine
        from meshflow.intelligence.collusion import CollusionAuditor
        from meshflow.efficiency.environmental import EnvironmentalOptimizer
        from meshflow.observability.telemetry import MeshFlowTracer

        self.policy = (
            policy_for_mode(policy or "standard") if not isinstance(policy, Policy) else policy
        )
        self.run_id = run_id or str(uuid.uuid4())
        self.ledger_db = ledger_db if ledger_db is not None else default_ledger_path()
        scope = active_ledger_scope.get()
        self.ledger = ReplayLedger(
            self.ledger_db, tenant_id=scope[1] if scope else "default", enable_batching=False
        )
        self.outcomes: list[RuntimeOutcome] = []
        self.runtime = StepRuntime(
            policy=self.policy,
            run_id=self.run_id,
            ledger=self.ledger,
            budget=BudgetTracker(self.policy),
            guardian=Guardian(budget_usd=self.policy.budget_usd)
            if self.policy.enable_guardian
            else None,
            dasc_gate=DascGate(self.policy, self.run_id)
            if self.policy.deterministic_gate
            else None,
            identity=AgentIdentityProvider(self.run_id),
            uncertainty=UncertaintyEngine() if self.policy.enable_uncertainty else None,
            collusion=CollusionAuditor() if self.policy.enable_collusion_audit else None,
            eco=EnvironmentalOptimizer(self.policy.carbon_budget_g)
            if self.policy.enable_environmental
            else None,
            telemetry=MeshFlowTracer(),
            zero_trust=_zt_from_env(),
            tool_call_interceptor=_default_tool_interceptor(),
        )
        self._token: Token[GovernedSession | None] | None = None

    async def __aenter__(self) -> GovernedSession:
        try:
            return await self._enter()
        except BaseException:
            await self.runtime.close()
            raise

    async def _enter(self) -> GovernedSession:
        records = await self.ledger.get_run(self.run_id)
        if records:
            verified = await self.ledger.verify_chain(self.run_id)
            if not verified["valid"]:
                raise ValueError("Cannot resume a run with an invalid audit chain")
            from meshflow.core.runtime import StepRecord
            from dataclasses import fields

            names = {f.name for f in fields(StepRecord) if f.init}
            for data in records:
                data = dict(data)
                data["metadata"] = dict(data.get("metadata") or {})
                data["metadata"].setdefault("hash_version", 1)
                record = StepRecord(**{key: data[key] for key in names if key in data})
                output = NodeOutput(content=record.output_content, tokens_used=record.tokens_used)
                self.outcomes.append(
                    RuntimeOutcome(
                        ok=not record.blocked,
                        node_id=record.node_id,
                        node_kind=record.node_kind,
                        output=output,
                        record=record,
                        blocked_by=record.block_reason,
                    )
                )
            self.runtime._prev_hash = records[-1]["entry_hash"]
            self.runtime._budget._usd = sum(r["cost_usd"] for r in records)
            self.runtime._budget._tokens = sum(r["tokens_used"] for r in records)
            self.runtime._budget._carbon_g = sum(r["carbon_gco2"] for r in records)
        self._token = active_session.set(self)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        try:
            await self.runtime.close()
        finally:
            if self._token is not None:
                active_session.reset(self._token)

    async def invoke(
        self,
        name: str,
        task: str,
        callback: Callable[[NodeInput], Awaitable[Any]],
        context: dict[str, Any] | None = None,
        risk: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[Any, RuntimeOutcome]:
        value: Any = None

        async def runner(inp: NodeInput) -> NodeOutput:
            nonlocal value
            value = await callback(inp)
            data = value if isinstance(value, dict) else {}
            return NodeOutput(
                content=str(
                    data.get("result", data.get("output", value if value is not None else ""))
                ),
                structured=data,
                tokens_used=int(data.get("tokens", 0)),
                confidence=float(data.get("stated_confidence", 0.8)),
            )

        node = MeshNode(
            id=name, kind=NodeKind.PYTHON, _runner=runner, metadata=dict(metadata or {})
        )
        if risk is not None:
            node.risk_profile = risk
        ctx = dict(context or {})
        outcome = await self.runtime.run(node, NodeInput(task=task, context=ctx), ctx)
        self.outcomes.append(outcome)
        return value, outcome

    async def agent(self, agent: Any, task: str, context: dict[str, Any] | None = None) -> Any:
        from meshflow.agents.builder import Agent

        method = agent._run_unguarded if isinstance(agent, Agent) else agent.run
        ctx = dict(context or {})

        async def call(inp: NodeInput) -> Any:
            if "context" in inspect.signature(method).parameters:
                return await method(inp.task, context=inp.context)
            return await method(inp.task)

        metadata: dict[str, Any] = {}
        if isinstance(agent, Agent) and agent._prebuilt_node is None:
            metadata["request_budget_managed"] = True
        elif isinstance(agent, Agent):
            metadata.update(agent._prebuilt_node.metadata)
        elif self.policy.strict_budget:
            bound = getattr(agent, "request_cost_bound", None)
            if callable(bound):
                usd, tokens = bound(task=task, context=ctx)
                metadata.update(max_cost_usd=usd, max_tokens=tokens)
        value, outcome = await self.invoke(
            agent.name, task, call, ctx, getattr(agent, "risk", None), metadata
        )
        if outcome.ok and not outcome.paused_for_human:
            if isinstance(value, dict):
                return {**value, "run_id": self.run_id, "ledger_db": self.ledger_db}
            return value
        return {
            "result": outcome.output.content,
            "tokens": outcome.record.tokens_used,
            "cost_usd": outcome.record.cost_usd,
            "blocked": not outcome.ok,
            "paused_for_human": outcome.paused_for_human,
            "blocked_by": outcome.blocked_by,
            "run_id": self.run_id,
            "stated_confidence": 0.0,
        }

    def finish(self, result: Any) -> Any:
        result.run_id = self.run_id
        result.steps = list(self.outcomes)
        result.ledger_db = self.ledger_db
        result.total_cost_usd = sum(o.record.cost_usd for o in self.outcomes)
        result.total_tokens = sum(o.record.tokens_used for o in self.outcomes)
        result.total_carbon_gco2 = sum(o.record.carbon_gco2 for o in self.outcomes)
        result.blocked_nodes = list(
            dict.fromkeys(result.blocked_nodes + [o.node_id for o in self.outcomes if not o.ok])
        )
        result.paused_nodes = list(
            dict.fromkeys(
                result.paused_nodes + [o.node_id for o in self.outcomes if o.paused_for_human]
            )
        )
        result.completed = result.completed and not result.blocked_nodes and not result.paused_nodes
        return result


async def run_agent(agent: Any, task: str, context: dict[str, Any] | None = None) -> Any:
    session = active_session.get()
    if session is not None:
        return await session.agent(agent, task, context)
    async with GovernedSession(getattr(agent, "policy", None)) as session:
        return await session.agent(agent, task, context)


class GovernanceBlockedError(RuntimeError):
    """An operation was denied or paused by the governance kernel."""

    def __init__(self, outcome: RuntimeOutcome) -> None:
        self.outcome = outcome
        super().__init__(outcome.blocked_by or "human_approval_required")


async def governed_operation(
    agent: Any,
    task: str,
    callback: Callable[[NodeInput], Awaitable[tuple[Any, int, float, str]]],
    context: dict[str, Any] | None = None,
) -> Any:
    """Govern a typed operation while preserving its return type and exceptions."""
    session = active_session.get()
    if session is None:
        async with GovernedSession(getattr(agent, "policy", None)):
            return await governed_operation(agent, task, callback, context)
    result: Any = None
    original_error: Exception | None = None

    async def call(inp: NodeInput) -> dict[str, Any]:
        nonlocal result, original_error
        try:
            result, tokens, cost, content = await callback(inp)
            return {"result": content, "tokens": tokens, "cost_usd": cost, "stated_confidence": 1.0}
        except Exception as exc:
            original_error = exc
            raise

    _, outcome = await session.invoke(
        agent.name,
        task,
        call,
        context,
        getattr(agent, "risk", None),
        {"request_budget_managed": True},
    )
    if original_error is not None:
        raise original_error
    if not outcome.ok or outcome.paused_for_human:
        raise GovernanceBlockedError(outcome)
    return result


async def stream_agent(agent: Any, task: str, context: dict[str, Any] | None = None) -> Any:
    """Stream through the kernel and finalize the audit even when the consumer closes."""
    import asyncio
    from contextlib import aclosing

    session = active_session.get()
    owns_session = session is None
    if session is None:
        session = GovernedSession(getattr(agent, "policy", None))
    queue: asyncio.Queue[Any] = asyncio.Queue()

    from meshflow.agents.builder import Agent

    method = agent._stream_unguarded if isinstance(agent, Agent) else agent.stream
    metadata: dict[str, Any] = {"request_budget_managed": isinstance(agent, Agent)}
    if not isinstance(agent, Agent) and session.policy.strict_budget:
        bound = getattr(agent, "request_cost_bound", None)
        if callable(bound):
            usd, tokens = bound(task=task, context=context or {})
            metadata.update(max_cost_usd=usd, max_tokens=tokens)

    async def call(inp: NodeInput) -> dict[str, Any]:
        pieces: list[str] = []
        blocked_by = ""
        try:
            stream = (
                method(inp.task, context=inp.context)
                if "context" in inspect.signature(method).parameters
                else method(inp.task)
            )
            async with aclosing(stream) as iterator:
                async for text in iterator:
                    pieces.append(text)
                    queue.put_nowait(text)
        except asyncio.CancelledError:
            blocked_by = "stream:cancelled"
        return {
            "result": "".join(pieces),
            "blocked": bool(blocked_by),
            "blocked_by": blocked_by,
            "stated_confidence": 0.8,
        }

    async def invoke() -> None:
        _, outcome = await session.invoke(
            agent.name,
            task,
            call,
            context,
            getattr(agent, "risk", None),
            metadata,
        )
        if not outcome.ok or outcome.paused_for_human:
            queue.put_nowait(GovernanceBlockedError(outcome))

    async def produce() -> None:
        try:
            if owns_session:
                async with session:
                    await invoke()
            else:
                await invoke()
        except Exception as exc:
            queue.put_nowait(exc)
        finally:
            queue.put_nowait(None)

    producer = asyncio.create_task(produce())
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        if not producer.done():
            producer.cancel()
        await producer
