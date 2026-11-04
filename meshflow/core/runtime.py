"""StepRuntime — the governed execution kernel.

Every MeshNode — regardless of kind (native, LangGraph, CrewAI, AutoGen, MCP,
human, HTTP, Python) — passes through this identical lifecycle:

  pre_step:
    1.  identity provisioning + capability check
    2.  circuit breaker allow/deny
    3.  guardian injection scan on input
    4.  risk classification (AutoRiskClassifier overrides self-declared tier)
    5.  policy budget pre-check
    6.  HITL escalation if tier >= threshold

  execute:
    7.  node.run()  ← the actual LLM/crew/graph/service call
    8.  OTEL span + checkpoint

  post_step:
    9.  uncertainty scoring + adaptive response (warn/verify/escalate/abort)
    10. cost + token + carbon accounting
    11. audit ledger write (hash-chained)
    12. memory update (MEM1)
    13. collusion / drift recording
    14. CAEP identity risk check (revoke DID if risk spikes)
    15. behavioural monitoring baseline update

This is the architectural heart of MeshFlow's differentiation.
One path. Every node. No exceptions.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from meshflow.core.node import MeshNode, NodeInput, NodeOutput
from meshflow.core.schemas import ActionVerdict, Intent, Policy


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_record(
    run_id: str,
    step_id: str,
    node_id: str,
    input_task: str,
    output_content: str,
    verdict: str,
    blocked: bool,
    timestamp: str,
    prev_hash: str,
) -> str:
    """SHA-256 of the canonical record fields for tamper-evidence chain."""
    payload = json.dumps(
        {
            "run_id": run_id,
            "step_id": step_id,
            "node_id": node_id,
            "input_task": input_task[:200],
            "output_content": output_content[:200],
            "verdict": verdict,
            "blocked": blocked,
            "timestamp": timestamp,
            "prev_hash": prev_hash,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def record_hash(record: Any, *, leaf: bool = False) -> str:
    """Hash the complete persisted v2 record, or verify a legacy v1 record."""
    from dataclasses import asdict, is_dataclass
    data = asdict(record) if is_dataclass(record) else dict(record)
    metadata = dict(data.get("metadata") or {})
    if metadata.get("hash_version") != 2:
        data["blocked"] = bool(data.get("blocked", False))
        return _hash_record(**{key: data.get(key, "") for key in (
            "run_id", "step_id", "node_id", "input_task", "output_content",
            "verdict", "blocked", "timestamp", "prev_hash",
        )} | ({"prev_hash": ""} if leaf else {}))
    if leaf:
        metadata.pop("merkle_proof", None)
    fields = (
        "run_id", "step_id", "node_id", "node_kind", "input_task", "output_content",
        "verdict", "blocked", "block_reason", "uncertainty", "cost_usd",
        "tokens_used", "carbon_gco2", "duration_ms", "timestamp", "prev_hash",
    )
    payload = {key: data[key] for key in fields}
    payload["blocked"] = bool(payload["blocked"])
    for key in ("uncertainty", "cost_usd", "carbon_gco2", "duration_ms"):
        payload[key] = float(payload[key])
    payload["metadata"] = metadata
    if leaf:
        payload["prev_hash"] = ""
    return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False,
                                     separators=(",", ":")).encode()).hexdigest()


@dataclass
class StepRecord:
    """Immutable governed-step record — written to the ReplayLedger.

    Each record carries ``prev_hash`` (the hash of the previous record in this
    run) and ``entry_hash`` (the SHA-256 of this record's canonical fields).
    Together they form a tamper-evident hash chain that ``ReplayLedger.verify_chain``
    can audit without trusting the database.
    """

    run_id: str
    step_id: str
    node_id: str
    node_kind: str
    input_task: str  # first 500 chars
    output_content: str  # first 2 000 chars
    verdict: str  # "commit" | "reject" | "escalate"
    blocked: bool
    block_reason: str
    uncertainty: float
    cost_usd: float
    tokens_used: int
    carbon_gco2: float
    duration_ms: float
    timestamp: str
    prev_hash: str = ""  # SHA-256 of the previous record (empty for first)
    metadata: dict[str, Any] = field(default_factory=dict)
    entry_hash: str = field(default="", init=False, repr=False)

    def __post_init__(self) -> None:
        self.metadata = dict(self.metadata)
        self.metadata.setdefault("hash_version", 2)
        self.entry_hash = record_hash(self)


@dataclass
class RuntimeOutcome:
    """Result returned from StepRuntime.run()."""

    ok: bool
    node_id: str
    node_kind: str
    output: NodeOutput
    record: StepRecord
    blocked_by: str = ""
    paused_for_human: bool = False
    human_context: dict[str, Any] = field(default_factory=dict)


class StepRuntime:
    """Governed execution kernel — wraps every MeshNode execution.

    Instantiate once per Mesh run and reuse across all nodes.
    All per-run state is in the governance layers passed in.

    Usage::

        runtime = StepRuntime(policy=pol, run_id=run_id,
                              guardian=guardian, dasc_gate=dasc,
                              identity=identity, uncertainty=uncertainty,
                              collusion=collusion, telemetry=tracer,
                              ledger=ledger, budget=budget_tracker)

        output, record = await runtime.run(node, NodeInput(task="..."), ctx)
    """

    def __init__(
        self,
        policy: Policy,
        run_id: str,
        *,
        guardian: Any = None,
        dasc_gate: Any = None,
        identity: Any = None,
        uncertainty: Any = None,
        collusion: Any = None,
        telemetry: Any = None,
        mem1: Any = None,
        eco: Any = None,
        ledger: Any = None,
        budget: Any = None,
        circuit_breakers: Any = None,
        compliance_guard: Any = None,
        zero_trust: Any = None,
        tool_call_interceptor: Any = None,
    ) -> None:
        self._policy = policy
        self._run_id = run_id
        self._guardian = guardian
        self._dasc = dasc_gate
        self._identity = identity
        self._uncertainty = uncertainty
        self._collusion = collusion
        self._telemetry = telemetry
        self._mem1 = mem1
        self._eco = eco
        self._ledger = ledger
        self._budget = budget
        self._cbs = circuit_breakers  # dict[str, CircuitBreaker]
        self._compliance_guard = compliance_guard
        self._zero_trust = zero_trust  # ZeroTrustOrchestrator | None
        self.tool_call_interceptor = tool_call_interceptor  # ToolCallInterceptor | None
        import asyncio
        self._record_lock = asyncio.Lock()
        self._prev_hash = ""  # grows with each committed record
        self._cache_read_tokens: int = 0       # accumulated across all steps in the run
        self._cache_creation_tokens: int = 0   # accumulated across all steps in the run
        # SIEM streamer — lazy-loaded when ZT Advanced siem_streaming is active
        self._siem: Any = None
        if zero_trust is not None:
            try:
                zt_policy = getattr(zero_trust, "_policy", None)
                if zt_policy and getattr(zt_policy, "siem_streaming", False):
                    from meshflow.observability.siem import get_siem_streamer
                    self._siem = get_siem_streamer()
            except Exception:
                pass

    async def close(self) -> None:
        """Clean up resources associated with this run runtime."""
        if self._identity is not None:
            self._identity.revoke_all(reason="run_complete")
        if self._telemetry is not None:
            try:
                self._telemetry.shutdown()
            except Exception:
                pass
        if self._ledger is not None:
            try:
                if hasattr(self._ledger, "aclose"):
                    await self._ledger.aclose()
                elif hasattr(self._ledger, "close"):
                    self._ledger.close()
            except Exception:
                pass

    async def run(
        self,
        node: MeshNode,
        node_input: NodeInput,
        context: dict[str, Any],
    ) -> RuntimeOutcome:
        step_id = str(uuid.uuid4())[:8]
        start = time.monotonic()

        _guardian_alert_cursor = (
            len(self._guardian.alerts())
            if self._guardian is not None and hasattr(self._guardian, "alerts")
            else 0
        )

        # W3C Trace Context — propagate or create
        try:
            from meshflow.observability.trace_context import TraceContext
            _tp_header = context.get("_traceparent", "")
            _tc = (
                TraceContext.from_header(_tp_header)
                if _tp_header
                else TraceContext.new()
            )
            if _tc is None:
                _tc = TraceContext.new()
            context["_trace_id"] = _tc.trace_id
            context["_traceparent"] = f"00-{_tc.trace_id}-{_tc.child_span_id()}-01"
        except Exception:
            pass

        blocked = False
        block_reason = ""
        verdict = "commit"
        paused = False
        human_ctx: dict[str, Any] = {}
        output = NodeOutput(content="")

        # ── PRE_STEP ──────────────────────────────────────────────────────────

        # 1. Identity: provision DID if not yet done, check still active
        if self._identity:
            if not self._identity.is_provisioned(node.id):
                self._identity.provision(node.id, node.capabilities)
            if not self._identity.is_active(node.id):
                blocked = True
                block_reason = "identity:did_revoked"

        # 1b. Zero Trust — continuous authorization check
        if not blocked and self._zero_trust:
            try:
                zt_policy = getattr(self._zero_trust, "_policy", None)
                if zt_policy and getattr(zt_policy, "continuous_auth", False):
                    cont_auth = getattr(self._zero_trust, "_cont_auth", None)
                    if cont_auth is None:
                        raise RuntimeError("Continuous authorization is enabled but unavailable")
                    if cont_auth:
                        decision = cont_auth.authorize(node.id, action="run:step")
                        if not decision.allowed:
                            blocked = True
                            block_reason = f"zero_trust:auth_denied:{decision.reason}"
            except Exception as exc:
                blocked = True
                block_reason = f"zero_trust:control_error:{type(exc).__name__}"

        # 1c. Zero Trust — apply spotlighting to input
        if not blocked and self._zero_trust:
            try:
                zt_policy = getattr(self._zero_trust, "_policy", None)
                if zt_policy and getattr(zt_policy, "spotlighting", False):
                    from meshflow.zero_trust.spotlight import SpotlightContext
                    _spotlight_ctx = SpotlightContext(strategy="xml_tags")
                    node_input = type(node_input)(
                        task=_spotlight_ctx.wrap(node_input.task),
                        **{k: v for k, v in node_input.__dict__.items() if k != "task"},
                    )
            except Exception as exc:
                blocked = True
                block_reason = f"zero_trust:spotlight_error:{type(exc).__name__}"

        # 2. Circuit breaker
        if not blocked and self._cbs:
            cb = self._cbs.get(node.id)
            if cb and not cb.allow(node.id):
                blocked = True
                block_reason = "circuit_breaker:open"

        # 3. Guardian injection scan on input
        if not blocked and self._guardian and self._policy.enable_guardian:
            from meshflow.core.schemas import Message

            msg = Message(
                sender_id="runtime",
                receiver_id=node.id,
                content=f"{node_input.task} {str(node_input.context)[:300]}",
            )
            allowed, reason = self._guardian.evaluate_message(msg)
            if not allowed:
                blocked = True
                block_reason = f"guardian:{reason}"

        # 4. Risk classification + DascGate evaluate
        if not blocked and self._dasc and self._policy.deterministic_gate:
            intent = Intent(
                action=f"node:{node.kind}",
                payload={"task": node_input.task[:200]},
                evidence=[],
                agent_id=node.id,
                agent_did=self._identity.get_did(node.id) if self._identity else "",
                risk_tier=node.risk_profile,
            )
            gate_result = await self._dasc.evaluate(intent)
            verdict = gate_result.value
            if gate_result == ActionVerdict.REJECT:
                blocked = True
                block_reason = "dasc:rejected"
            elif gate_result == ActionVerdict.ESCALATE:
                paused = True
                human_ctx = {
                    "reason": "dasc_escalation",
                    "node": node.id,
                    "tier": int(node.risk_profile),
                }

        # 5. Budget pre-check
        if not blocked and self._budget:
            from meshflow.core.policy import BudgetExceededError

            try:
                self._budget.pre_check()
            except BudgetExceededError:
                blocked = True
                block_reason = "budget:exceeded"

        # 5b. Real-time compliance guard (pre-check)
        if not blocked and not paused and self._compliance_guard:
            try:
                self._compliance_guard.pre_check(
                    node_id=node.id,
                    input_task=node_input.task,
                    policy=self._policy,
                    context=context,
                )
            except Exception as _cg_exc:
                # ComplianceViolation or unexpected error — block the step
                blocked = True
                block_reason = f"compliance:{_cg_exc}"

        # 6. HITL escalation if node tier >= threshold
        if (
            not blocked
            and not paused
            and self._policy.human_in_loop
            and self._policy.human_in_loop.enabled
            and node.risk_profile >= self._policy.human_in_loop.tier_threshold
        ):
            paused = True
            human_ctx = {
                "reason": "hitl_tier_threshold",
                "node": node.id,
                "tier": int(node.risk_profile),
            }

        # ── EXECUTE ───────────────────────────────────────────────────────────

        # Inject interceptor into node so intra-node tool calls can be governed
        if self.tool_call_interceptor is not None and hasattr(node, "set_tool_call_interceptor"):
            node.set_tool_call_interceptor(self.tool_call_interceptor)

        # Cursor into the interceptor's audit log — lets us slice calls made during THIS step
        _tool_log_cursor = (
            len(self.tool_call_interceptor.audit_log())
            if self.tool_call_interceptor is not None and hasattr(self.tool_call_interceptor, "audit_log")
            else 0
        )

        if self._guardian is not None:
            node_input.context["_guardian"] = self._guardian
        if self._ledger is not None:
            node_input.context["_ledger"] = self._ledger

        _step_reservation = None
        if not blocked and not paused and self._budget and self._policy.strict_budget and not node.metadata.get("request_budget_managed"):
            try:
                if "max_cost_usd" not in node.metadata or "max_tokens" not in node.metadata:
                    raise BudgetExceededError("External nodes require max_cost_usd and max_tokens bounds")
                _step_reservation = self._budget.reserve(float(node.metadata["max_cost_usd"]), int(node.metadata["max_tokens"]))
            except BudgetExceededError as exc:
                blocked = True
                block_reason = f"budget:unbounded_or_exhausted:{exc}"

        from meshflow.core.request_budget import request_usage
        _parent_request_stats = request_usage.get()
        _request_stats: dict[str, Any] = {"cost_usd": 0.0, "tokens": 0}
        if not blocked and not paused:
            from meshflow.core.request_budget import active_budget, request_usage
            _budget_token = active_budget.set(self._budget)
            _usage_token = request_usage.set(_request_stats)
            try:
                _step_timeout = getattr(self._policy, "step_timeout_s", 0.0)
                if _step_timeout and _step_timeout > 0:
                    import asyncio as _ato_step
                    try:
                        output = await _ato_step.wait_for(
                            node.run(node_input), timeout=_step_timeout
                        )
                    except _ato_step.TimeoutError:
                        _action = getattr(self._policy, "step_timeout_action", "fail")
                        if _action == "skip":
                            output = NodeOutput(content="[step_timeout: skipped]", confidence=0.0)
                        elif _action == "retry":
                            try:
                                output = await _ato_step.wait_for(
                                    node.run(node_input), timeout=_step_timeout
                                )
                            except _ato_step.TimeoutError:
                                output = NodeOutput(content="[step_timeout: retry_exhausted]", confidence=0.0)
                                blocked = True
                                block_reason = f"step_timeout:{_step_timeout}s"
                        else:  # "fail"
                            blocked = True
                            block_reason = f"step_timeout:{_step_timeout}s"
                            output = NodeOutput(content=f"[step_timeout: {_step_timeout}s exceeded]", confidence=0.0)
                else:
                    output = await node.run(node_input)
                if self._cbs and node.id in self._cbs:
                    self._cbs[node.id].record_success(node.id)
                if self._telemetry:
                    with self._telemetry.span(
                        f"meshflow.node.{node.kind.value}",
                        run_id=self._run_id,
                        **{"node.id": node.id, "node.kind": node.kind.value, "step_id": step_id},
                    ):
                        pass  # span wraps the completed execution for trace record
            except Exception as exc:
                output = NodeOutput(content=f"[node_error: {exc}]", confidence=0.0)
                block_reason = f"node_exception:{exc}"
                blocked = True
                if self._cbs and node.id in self._cbs:
                    self._cbs[node.id].record_failure(node.id)

            finally:
                active_budget.reset(_budget_token)
                request_usage.reset(_usage_token)

        # ── POST_STEP ─────────────────────────────────────────────────────────

        if output.structured.get("blocked"):
            blocked = True
            block_reason = str(output.structured.get("blocked_by") or output.structured.get("guardrail") or "node:blocked")
        tokens_used = output.tokens_used
        uncertainty_val = 0.0
        cost_usd = 0.0
        if output.structured and isinstance(output.structured, dict):
            cost_usd = output.structured.get("cost_usd", 0.0)
        cost_usd = max(cost_usd - _request_stats.get("nested_cost_usd", 0.0), _request_stats["cost_usd"], 0.0)
        tokens_used = max(tokens_used - _request_stats.get("nested_tokens", 0), _request_stats["tokens"], 0)
        carbon_gco2 = 0.0

        if _step_reservation is not None:
            try:
                if block_reason.startswith(("node_exception:", "step_timeout:")):
                    reserved_cost, reserved_tokens = self._budget._reserved[_step_reservation]
                    self._budget.settle(_step_reservation)
                    cost_usd = max(cost_usd, reserved_cost)
                    tokens_used = max(tokens_used, reserved_tokens)
                else:
                    self._budget.settle(_step_reservation, cost_usd, tokens_used)
            except BudgetExceededError:
                blocked = True
                block_reason = "budget:provider_bound_exceeded"

        # 9. Uncertainty scoring + adaptive response
        if not blocked and not paused and self._uncertainty and self._policy.enable_uncertainty:
            u_score = self._uncertainty.evaluate(
                agent_id=node.id,
                outputs=[output.content] if output.content else [""],
                stated_confidence=output.confidence,
                upstream_calibrated=context.get("_upstream_confidence"),
            )
            uncertainty_val = u_score.composite
            context["_upstream_confidence"] = u_score.calibrated

            adaptive = self._uncertainty.adaptive_response(u_score)
            if adaptive["action"] == "abort":
                blocked = True
                block_reason = f"uncertainty:abort:{uncertainty_val:.2f}"
            elif adaptive["action"] == "escalate_human" and not paused:
                paused = True
                human_ctx = {
                    "reason": f"uncertainty:{uncertainty_val:.2f}",
                    "node": node.id,
                }

        # 10. Cost + token + carbon accounting
        if self._budget:
            from meshflow.core.policy import BudgetExceededError

            try:
                self._budget.charge(usd=cost_usd, tokens=tokens_used)
            except BudgetExceededError:
                blocked = True
                block_reason = "budget:exceeded"

        if self._eco and tokens_used:
            eco_result = self._eco.estimate_and_charge(tokens_used, node.id)
            carbon_gco2 = eco_result.carbon_g

        # 11. Collusion / drift recording
        if (
            self._collusion
            and self._policy.enable_collusion_audit
            and not paused
            and output.content
        ):
            node_role = node.metadata.get("role", getattr(node, "role", "natural_language"))
            self._collusion.record_output(node.id, output.content[:500], role=node_role)
            # Collusion alert webhook — fire if risk score is high
            try:
                import asyncio as _ca_asyncio
                _risk = getattr(self._collusion, "last_risk_score", None)
                if _risk is None:
                    _risk = getattr(self._collusion, "_last_risk", None)
                if _risk is not None and float(_risk) > 0.7:
                    from meshflow.observability.webhooks import get_webhook_manager as _gwh
                    _cwh = _gwh()
                    if _cwh.list():
                        _ca_asyncio.create_task(_cwh.deliver("collusion_alert", {
                            "run_id": self._run_id,
                            "node_id": node.id,
                            "step_id": step_id,
                            "collusion_risk": float(_risk),
                        }))
            except Exception:
                pass

        # 14. CAEP: revoke DID if risk spikes post-step
        if self._identity and not paused and uncertainty_val > 0.7:
            self._identity.caep_check(node.id, 1.0 - uncertainty_val)

        # 15. Behavioural monitoring
        if self._guardian and self._policy.enable_guardian and not paused:
            self._guardian.observe_behaviour(
                agent_id=node.id,
                tokens=float(tokens_used),
                tools=0.0,
                output_len=len(output.content),
            )

        # 12. Memory update
        if self._mem1 and not paused and output.content:
            self._mem1.add(node.id, output.content, {"step_id": step_id})

        # Build step record with tamper-evident hash chain
        duration_ms = (time.monotonic() - start) * 1000

        raw_output = output.content
        # PHI scrubbing before ledger write (HIPAA mode)
        if getattr(self._policy, "scrub_phi", False):
            try:
                from meshflow.security.phi_scrubber import PHIScrubber

                raw_output = PHIScrubber().scrub(raw_output)
            except ImportError:
                pass

        # Respect max_output_chars policy (0 = unlimited)
        max_chars = getattr(self._policy, "max_output_chars", 0)
        ledger_output = raw_output[:max_chars] if max_chars > 0 else raw_output

        # Collect tool calls made during this step from the interceptor audit log
        _step_tool_calls: list[dict[str, Any]] = []
        if (
            self.tool_call_interceptor is not None
            and hasattr(self.tool_call_interceptor, "audit_log")
        ):
            _step_tool_calls = self.tool_call_interceptor.audit_log()[_tool_log_cursor:]

        # Collect cache stats from the Anthropic provider (no-op for other providers)
        try:
            from meshflow.agents.base import get_cache_stats, reset_cache_stats
            _cache = get_cache_stats()
            reset_cache_stats()
        except Exception:
            _cache = {}

        _meta: dict[str, Any] = {}
        if self._guardian is not None and hasattr(self._guardian, "alerts"):
            _step_alerts = self._guardian.alerts()[_guardian_alert_cursor:]
            if _step_alerts:
                _meta["guardian_alerts"] = _step_alerts
        if _step_tool_calls:
            _meta["tool_calls"] = _step_tool_calls
        _cr = _cache.get("cache_read_tokens", 0)
        _cc = _cache.get("cache_creation_tokens", 0)
        if _cc:
            _meta["cache_creation_tokens"] = _cc
            self._cache_creation_tokens += _cc
        if _cr:
            _meta["cache_read_tokens"] = _cr
            self._cache_read_tokens += _cr
        # Thinking tokens forwarded from NodeOutput.metadata
        _think_tok = output.metadata.get("thinking_tokens", 0)
        if _think_tok:
            _meta["thinking_tokens"] = _think_tok

        async with self._record_lock:
            record = StepRecord(
                run_id=self._run_id,
                step_id=step_id,
                node_id=node.id,
                node_kind=node.kind.value,
                input_task=node_input.task[:500],
                output_content=ledger_output,
                verdict=verdict,
                blocked=blocked,
                block_reason=block_reason,
                uncertainty=uncertainty_val,
                cost_usd=cost_usd,
                tokens_used=tokens_used,
                carbon_gco2=carbon_gco2,
                duration_ms=duration_ms,
                timestamp=_now_iso(),
                prev_hash=self._prev_hash,
                metadata=_meta,
            )
            # Calibration feedback: record actual outcome so the tracker corrects bias
            if self._uncertainty and self._policy.enable_uncertainty:
                self._uncertainty._calibration.record(
                    node.id,
                    stated=output.confidence,
                    actual=1.0 if not blocked else 0.0,
                )

            # 11. Audit ledger write
            if self._ledger:
                await self._ledger.write(record)
            self._prev_hash = record.entry_hash
            if _parent_request_stats is not None:
                _parent_request_stats["nested_cost_usd"] = _parent_request_stats.get("nested_cost_usd", 0.0) + record.cost_usd + _request_stats.get("nested_cost_usd", 0.0)
                _parent_request_stats["nested_tokens"] = _parent_request_stats.get("nested_tokens", 0) + record.tokens_used + _request_stats.get("nested_tokens", 0)

        # SLA latency recording (best-effort, never raises)
        try:
            from meshflow.observability.sla import get_sla_tracker
            get_sla_tracker().record(node.id, duration_ms)
        except Exception:
            pass

        # Post-step compliance guard update
        if self._compliance_guard:
            try:
                self._compliance_guard.post_step(node.id, blocked=blocked)
            except Exception:
                pass

        # 15b. OTEL span export — fire-and-forget; never blocks the step
        try:
            from meshflow.observability.otel_exporter import get_global_exporter as _get_otel
            _otel = _get_otel()
            if _otel._enabled:
                import asyncio as _ao
                _t0_ns = int(record.timestamp.replace("Z", "+00:00") and
                             (__import__("datetime").datetime.fromisoformat(
                                 record.timestamp.replace("Z", "+00:00")
                             ).timestamp() * 1_000_000_000))
                _otel_attrs = {
                    "node.id": node.id,
                    "node.kind": node.kind.value,
                    "step_id": step_id,
                    "run_id": self._run_id,
                    "blocked": blocked,
                    "cost_usd": cost_usd,
                    "tokens_used": tokens_used,
                }
                if block_reason:
                    _otel_attrs["block_reason"] = block_reason
                _ao.get_event_loop().run_in_executor(
                    None,
                    lambda: _otel.export_span(
                        trace_id=self._run_id.replace("-", "").ljust(32, "0")[:32],
                        span_id=step_id.replace("-", "").ljust(16, "0")[:16],
                        name=f"step:{node.id}",
                        start_ns=_t0_ns - int(duration_ms * 1_000_000),
                        end_ns=_t0_ns,
                        attributes=_otel_attrs,
                        status="error" if blocked else "ok",
                    )
                )
        except Exception:
            pass

        # 16. Webhook alerts — fire-and-forget; never blocks the step
        try:
            import asyncio as _asyncio
            from meshflow.observability.webhooks import get_webhook_manager as _get_wh
            _wh = _get_wh()
            if _wh.list():
                _wp: dict[str, Any] = {
                    "run_id": self._run_id,
                    "step_id": step_id,
                    "node_id": node.id,
                    "block_reason": block_reason,
                    "cost_usd": cost_usd,
                    "tokens_used": tokens_used,
                    "timestamp": record.timestamp,
                }
                if blocked and block_reason.startswith("budget:"):
                    _asyncio.create_task(_wh.deliver("budget_exceeded", _wp))
                elif blocked:
                    _asyncio.create_task(_wh.deliver("policy_violation", _wp))
                if paused:
                    _asyncio.create_task(_wh.deliver("hitl_pending", {**_wp, "human_context": human_ctx}))
        except Exception:
            pass

        # SIEM streaming — emit to all configured backends
        if self._siem:
            try:
                _siem_event = "step_blocked" if blocked else ("hitl_pending" if paused else "step_complete")
                self._siem.emit(_siem_event, {
                    "node_id":      node.id,
                    "node_kind":    node.kind.value,
                    "verdict":      verdict,
                    "blocked":      blocked,
                    "block_reason": block_reason,
                    "cost_usd":     getattr(output, "cost_usd", 0.0),
                    "tokens_used":  getattr(output, "tokens_used", 0),
                    "duration_ms":  round((time.monotonic() - start) * 1000, 1),
                    "run_id":       self._run_id,
                }, run_id=self._run_id)
                if blocked and block_reason:
                    self._siem.emit("policy_violation", {
                        "node_id":      node.id,
                        "block_reason": block_reason,
                        "run_id":       self._run_id,
                    }, run_id=self._run_id)
            except Exception:
                pass

        return RuntimeOutcome(
            ok=not blocked,
            node_id=node.id,
            node_kind=node.kind.value,
            output=output,
            record=record,
            blocked_by=block_reason,
            paused_for_human=paused,
            human_context=human_ctx,
        )
