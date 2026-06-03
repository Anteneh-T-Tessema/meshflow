"""WorkflowDefinition — portable, YAML-declarative, graph-topological workflow.

A workflow is a directed acyclic graph of MeshNodes with a single policy
applied to all edges. Any DAG topology is supported, including fan-out
(parallel branches) and fan-in (joins).

Fan-out / fan-in example::

    name: research_pipeline
    version: "1"

    policy:
      budget_usd: 2.00
      max_steps: 30
      enable_guardian: true

    nodes:
      planner:   {kind: native, role: planner}
      branch_a:  {kind: python, ref: agents.research_a}
      branch_b:  {kind: python, ref: agents.research_b}
      branch_c:  {kind: python, ref: agents.research_c}
      synthesizer: {kind: native, role: executor}

    edges:
      - planner -> branch_a
      - planner -> branch_b
      - planner -> branch_c
      - branch_a -> synthesizer
      - branch_b -> synthesizer
      - branch_c -> synthesizer

    terminal:
      - synthesizer

Execution order: planner runs first. branch_a, branch_b, branch_c have no
dependency between them so they run concurrently via asyncio.gather().
synthesizer runs after all three complete with their merged outputs in context.

Conditional edge routing example::

    nodes:
      validator:  {kind: langgraph, ref: graphs.fact_check}
      approval:   {kind: human}
      publisher:  {kind: native, role: executor}

    edges:
      - from: validator
        to: approval
        condition: "confidence < 0.8"      # route to human review if uncertain
      - from: validator
        to: publisher
        condition: "confidence >= 0.8"     # fast path when confident

Conditions are Python expressions evaluated against the shared context plus
``output``, ``content``, ``confidence``, and ``structured`` from the source
node's output. Empty condition = always fire. If no incoming edge fires for a
node, it is skipped (recorded in WorkflowResult.skipped_nodes).

Every node, including parallel branches, passes through the full StepRuntime
governance kernel: guardian scan, budget gate, HITL, OTEL span, uncertainty
scoring, collusion detection, and ledger write. Parallelism is transparent to
the control plane — each branch gets its own audit record.

Linear (sequential) example::

    nodes:
      planner: {kind: native, role: planner}
      researcher: {kind: crewai, ref: crews.market_research}
      validator: {kind: langgraph, ref: graphs.fact_check}
      approval: {kind: human}
      writer: {kind: native, role: executor}

    edges:
      - planner -> researcher
      - researcher -> validator
      - validator -> approval
      - approval -> writer

The YAML is the artifact you commit to git. It is reproducible and inspectable
without running any code.
"""

from __future__ import annotations

import asyncio
import datetime
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml

from meshflow.core.events import EventKind, WorkflowEvent, WorkflowEventBus, global_event_bus
from meshflow.core.node import MeshNode, NodeInput, NodeKind, NodeOutput
from meshflow.core.policy import BudgetExceededError
from meshflow.core.runtime import RuntimeOutcome, StepRecord, StepRuntime
from meshflow.core.schemas import HumanInLoopConfig, Policy, RiskTier, policy_for_mode

if TYPE_CHECKING:
    from meshflow.core.ledger import ReplayLedger


@dataclass
class HumanDecision:
    """A human's response to a HITL approval gate.

    Pass to ``WorkflowDefinition.resume()`` or ``Mesh.resume_workflow()``
    to continue a workflow that paused waiting for human approval.

    ``approved=True`` routes the workflow forward; ``approved=False`` sets
    ``confidence=0.0`` in context so conditional edges can route to a
    rejection branch.

    The optional ``rating``, ``feedback``, and ``corrections`` fields carry
    qualitative signal that :class:`~meshflow.eval.feedback.FeedbackCollector`
    can aggregate for fine-tuning preparation.
    """

    approved: bool
    comment: str = ""
    decided_by: str = "human"
    rating: int = 0                          # 1–5 quality score; 0 = not rated
    feedback: str = ""                       # free-text notes on output quality
    corrections: dict[str, str] = field(default_factory=dict)  # field-level corrections


class MaxIterationsError(Exception):
    """Raised when a loop edge exceeds its ``max_iterations`` limit."""


@dataclass
class WorkflowEdge:
    from_node: str
    to_node: str
    condition: str = ""  # Python expression; empty = always fire


@dataclass
class _LoopEdge:
    """A back-edge that creates a cycle — excluded from topological sort."""

    src: str
    dst: str
    condition: str = ""  # Python expression; empty = always loop
    max_iterations: int = 10
    _count: int = 0


@dataclass
class _ConditionalBranch:
    condition: Any
    branches: dict[str, Any]


@dataclass
class WorkflowResult:
    """Final outcome of running a WorkflowDefinition."""

    run_id: str
    workflow_name: str
    completed: bool
    output: str
    steps: list[RuntimeOutcome]
    total_cost_usd: float
    total_tokens: int
    total_carbon_gco2: float
    duration_s: float
    blocked_nodes: list[str]
    paused_nodes: list[str]
    skipped_nodes: list[str]
    ledger_db: str
    state: Any = None

    @property
    def cost_usd(self) -> float:
        """Alias for total_cost_usd — ergonomic shorthand."""
        return self.total_cost_usd

    @property
    def tokens(self) -> int:
        """Alias for total_tokens — ergonomic shorthand."""
        return self.total_tokens

    def __str__(self) -> str:
        return self.output

    def summary(self) -> str:
        """One-line human-readable summary of the run."""
        status = "✅ completed" if self.completed else "❌ blocked"
        return (
            f"{status}  steps={len(self.steps)}  "
            f"cost=${self.total_cost_usd:.4f}  tokens={self.total_tokens}  "
            f"duration={self.duration_s:.1f}s"
        )


class WorkflowDefinition:
    """A governed, graph-topological workflow.

    Build from YAML with ``WorkflowDefinition.from_yaml(path)`` or
    programmatically with ``WorkflowDefinition(name=...).add_node(...).add_edge(...)``.
    """

    def __init__(
        self,
        name: str,
        version: str = "1",
        policy: Policy | None = None,
    ) -> None:
        self.name = name
        self.version = version
        self.policy = policy or Policy()
        self._nodes: dict[str, MeshNode] = {}
        self._edges: list[WorkflowEdge] = []
        self._loop_edges: list[_LoopEdge] = []
        self._entry: str = ""
        self._terminal: list[str] = []
        self.compliance_guard: Any = None  # set by from_yaml when compliance: section present
        self.metadata: dict[str, Any] = {}  # free-form workflow metadata from YAML
        self.yaml_sha256: str = ""          # SHA-256 of source YAML (set by from_yaml)
        self.yaml_path: str = ""            # filesystem path to source YAML (set by from_yaml)
        self.context_bus: dict[str, Any] = {}
        self._replans_count: int = 0

    # ── Builder API ───────────────────────────────────────────────────────────

    def add_node(self, node: MeshNode) -> "WorkflowDefinition":
        self._nodes[node.id] = node
        if not self._entry:
            self._entry = node.id
        return self

    def add_edge(self, from_node: str, to_node: str, condition: str = "") -> "WorkflowDefinition":
        self._edges.append(WorkflowEdge(from_node, to_node, condition))
        return self

    def set_entry(self, node_id: str) -> "WorkflowDefinition":
        self._entry = node_id
        return self

    def set_terminal(self, *node_ids: str) -> "WorkflowDefinition":
        self._terminal = list(node_ids)
        return self

    def add_loop_edge(
        self,
        src: str,
        dst: str,
        condition: str = "",
        max_iterations: int = 10,
    ) -> "WorkflowDefinition":
        """Add a back-edge that creates a cycle between src and dst.

        After ``src`` completes, if ``condition`` evaluates True (or is empty),
        ``dst`` is re-queued for execution up to ``max_iterations`` times.
        The loop terminates when the condition is False or the limit is reached.

        Example — generate → critique → refine loop::

            wf.add_node(generator).add_node(critic)
            wf.add_edge("generator", "critic")
            wf.add_loop_edge("critic", "generator",
                             condition="confidence < 0.9",
                             max_iterations=5)
            wf.set_terminal("critic")
        """
        self._loop_edges.append(
            _LoopEdge(src=src, dst=dst, condition=condition, max_iterations=max_iterations)
        )
        return self

    # ── Graph helpers ─────────────────────────────────────────────────────────

    def _successors(self, node_id: str) -> list[str]:
        return [e.to_node for e in self._edges if e.from_node == node_id]

    def _predecessors(self, node_id: str) -> list[str]:
        return [e.from_node for e in self._edges if e.to_node == node_id]

    def _edges_to(self, node_id: str) -> list[WorkflowEdge]:
        return [e for e in self._edges if e.to_node == node_id]

    def _is_terminal(self, node_id: str) -> bool:
        return node_id in self._terminal or not self._successors(node_id)

    def _condition_fires(
        self,
        edge: WorkflowEdge,
        ctx: dict[str, Any],
        step_outcomes: dict[str, RuntimeOutcome],
    ) -> bool:
        """Evaluate an edge condition expression. Empty condition always fires.

        The expression runs in a restricted namespace. Available names:
          - Any key in the shared context (set by prior nodes)
          - ``output``     — NodeOutput object from the source node
          - ``content``    — output.content (str)
          - ``confidence`` — output.confidence (float 0–1)
          - ``structured`` — output.structured (dict)
        """
        if not edge.condition:
            return True
        prior = step_outcomes.get(edge.from_node)
        node_out = prior.output if prior else None
        namespace: dict[str, Any] = {
            **{k: v for k, v in ctx.items() if not k.startswith("_")},
            "output": node_out,
            "content": node_out.content if node_out else "",
            "confidence": node_out.confidence if node_out else 0.0,
            "structured": node_out.structured if node_out else {},
            "__builtins__": {
                "True": True,
                "False": False,
                "None": None,
                "bool": bool,
                "int": int,
                "float": float,
                "str": str,
                "len": len,
                "abs": abs,
                "min": min,
                "max": max,
            },
        }
        try:
            return bool(eval(edge.condition, {"__builtins__": {}}, namespace))  # noqa: S307
        except Exception:
            return False

    def _compute_ready(
        self,
        completed: set[str],
        skipped: set[str],
        failed: set[str],
        dynamic_next_nodes: set[str],
        nodes_with_handoff: set[str],
        ctx: dict[str, Any],
        step_outcomes: dict[str, RuntimeOutcome],
    ) -> tuple[list[str], list[str]]:
        """Return (nodes_ready_to_run, nodes_that_can_be_skipped).

        A node is ready when:
          - it is dynamically queued by a handoff, OR
          - all its predecessors are done (completed | skipped | failed), AND
          - its fan-in rule is met, AND
          - at least one incoming edge from a *completed* predecessor fires (and is not bypassed by handoff).
        """
        done = completed | skipped | failed
        ready: list[str] = []
        newly_skipped: list[str] = []

        # 1. Schedule dynamic handoff nodes immediately if not already done
        for node_id in list(dynamic_next_nodes):
            if node_id not in done:
                ready.append(node_id)
                dynamic_next_nodes.discard(node_id)

        # 2. Evaluate remaining nodes
        for node_id in self._nodes:
            if node_id in done or node_id in ready:
                continue
            preds = self._predecessors(node_id)
            if not preds:
                ready.append(node_id)
                continue
            if not all(p in done for p in preds):
                continue  # still waiting on an upstream node

            # Evaluate Fan-In rule
            node_obj = self._nodes[node_id]
            fan_in_rule = node_obj.metadata.get("fan_in_rule", "all")
            completed_preds = [p for p in preds if p in completed]

            rule_met = False
            if fan_in_rule == "all":
                rule_met = (len(completed_preds) == len(preds))
            elif fan_in_rule == "any":
                rule_met = (len(completed_preds) > 0)
            elif fan_in_rule == "majority":
                rule_met = (len(completed_preds) > len(preds) / 2)
            else:
                rule_met = (len(completed_preds) == len(preds))

            if not rule_met:
                newly_skipped.append(node_id)
                continue

            any_fires = False
            for e in self._edges_to(node_id):
                if e.from_node in nodes_with_handoff:
                    continue
                if e.from_node not in completed:
                    continue
                if self._condition_fires(e, ctx, step_outcomes):
                    any_fires = True
                    break

            if any_fires:
                ready.append(node_id)
            else:
                newly_skipped.append(node_id)

        return sorted(ready), newly_skipped

    def _topological_levels(self) -> list[list[str]]:
        """Kahn's algorithm — groups nodes into parallel-safe execution levels.

        All nodes within a level have no dependency between them and can run
        concurrently. Nodes in level N+1 depend only on nodes in level ≤ N.
        Useful for static analysis; ``run()`` uses the dynamic ready-queue
        which respects conditional edges at runtime.
        """
        in_degree: dict[str, int] = {n: 0 for n in self._nodes}
        for edge in self._edges:
            if edge.to_node in in_degree:
                in_degree[edge.to_node] += 1

        current: list[str] = sorted(n for n, d in in_degree.items() if d == 0)
        levels: list[list[str]] = []

        while current:
            levels.append(current)
            next_level: list[str] = []
            for node_id in current:
                for succ in self._successors(node_id):
                    in_degree[succ] -= 1
                    if in_degree[succ] == 0:
                        next_level.append(succ)
            current = sorted(next_level)

        return levels

    def _topological_order(self) -> list[str]:
        return [n for level in self._topological_levels() for n in level]

    # ── Execution ─────────────────────────────────────────────────────────────

    async def _execute_workflow_node(
        self,
        nd: MeshNode,
        task: str,
        runtime: StepRuntime,
        node_ctx: dict[str, Any],
        run_id: str,
    ) -> RuntimeOutcome:
        from meshflow.core.output_validation import OutputValidator
        schema_cfg = nd.metadata.get("output_schema")
        validator = (
            OutputValidator(schema=schema_cfg) if schema_cfg else None
        )
        retry_on_fail  = nd.metadata.get("retry_on_fail", False)
        max_retries    = int(nd.metadata.get("max_retries", 1))
        timeout_s = nd.metadata.get("timeout_s") or nd.metadata.get("timeout")
        attempt_limit = max_retries + 1 if retry_on_fail else 1

        current_task = task
        for attempt in range(attempt_limit):
            try:
                if timeout_s is not None:
                    outcome = await asyncio.wait_for(
                        runtime.run(
                            nd,
                            NodeInput(
                                task=current_task,
                                context=node_ctx,
                                attachments=nd.metadata.get("attachments", []),
                            ),
                            node_ctx,
                        ),
                        timeout=float(timeout_s),
                    )
                else:
                    outcome = await runtime.run(
                        nd,
                        NodeInput(
                            task=current_task,
                            context=node_ctx,
                            attachments=nd.metadata.get("attachments", []),
                        ),
                        node_ctx,
                    )

                if not outcome.ok:
                    return outcome
                if validator is None:
                    return outcome
                vresult = validator.validate(outcome.output.content)
                if vresult.valid:
                    return outcome

                if attempt < attempt_limit - 1:
                    current_task = validator.retry_prompt(outcome.output.content, vresult.error)
                    await asyncio.sleep(1.0)
                else:
                    # Construct a validation block outcome
                    from meshflow.core.runtime import RuntimeOutcome as RO, StepRecord
                    import datetime
                    import uuid as _uuid
                    blk_rec = StepRecord(
                        run_id=outcome.record.run_id,
                        step_id=_uuid.uuid4().hex[:8],
                        node_id=nd.id,
                        node_kind=outcome.record.node_kind,
                        input_task=task,
                        output_content=outcome.output.content,
                        verdict="block",
                        blocked=True,
                        block_reason=f"output_schema validation failed: {vresult.error}",
                        uncertainty=outcome.record.uncertainty,
                        cost_usd=outcome.record.cost_usd,
                        tokens_used=outcome.record.tokens_used,
                        carbon_gco2=outcome.record.carbon_gco2,
                        duration_ms=outcome.record.duration_ms,
                        timestamp=datetime.datetime.now().isoformat(),
                    )
                    return RO(
                        ok=False,
                        node_id=nd.id,
                        node_kind=outcome.record.node_kind,
                        output=outcome.output,
                        record=blk_rec,
                        blocked_by=f"output_schema:{vresult.error[:80]}",
                        paused_for_human=False,
                        human_context={},
                    )
            except (asyncio.TimeoutError, Exception) as exc:
                if attempt < attempt_limit - 1:
                    await asyncio.sleep(1.0)
                    continue
                else:
                    from meshflow.core.runtime import RuntimeOutcome as RO, StepRecord
                    import datetime
                    import uuid as _uuid

                    # Create a dummy failed StepRecord
                    blk_rec = StepRecord(
                        run_id=run_id,
                        step_id=_uuid.uuid4().hex[:8],
                        node_id=nd.id,
                        node_kind=nd.kind.value,
                        input_task=task,
                        output_content="",
                        verdict="block",
                        blocked=True,
                        block_reason=f"Execution failed: {exc}",
                        uncertainty=0.0,
                        cost_usd=0.0,
                        tokens_used=0,
                        carbon_gco2=0.0,
                        duration_ms=0.0,
                        timestamp=datetime.datetime.now().isoformat(),
                    )
                    return RO(
                        ok=False,
                        node_id=nd.id,
                        node_kind=nd.kind.value,
                        output=NodeOutput(content="", structured={}),
                        record=blk_rec,
                        blocked_by=f"execution_error:{str(exc)[:80]}",
                        paused_for_human=False,
                        human_context={},
                    )
        return RuntimeOutcome(
            ok=False,
            node_id=nd.id,
            node_kind=nd.kind.value,
            output=NodeOutput(content="", structured={}),
            record=StepRecord(
                run_id=run_id,
                step_id=uuid.uuid4().hex[:8],
                node_id=nd.id,
                node_kind=nd.kind.value,
                input_task=task,
                output_content="",
                verdict="block",
                blocked=True,
                block_reason="Retry loop exited without outcome",
                uncertainty=0.0,
                cost_usd=0.0,
                tokens_used=0,
                carbon_gco2=0.0,
                duration_ms=0.0,
                timestamp=datetime.datetime.now().isoformat(),
            ),
            blocked_by="error",
            paused_for_human=False,
            human_context={},
        )

    async def run(
        self,
        task: str,
        runtime: StepRuntime,
        context: dict[str, Any] | None = None,
        event_bus: WorkflowEventBus | None = None,
    ) -> WorkflowResult:
        """Execute the workflow with full StepRuntime governance on every node."""
        bus = event_bus if event_bus is not None else global_event_bus
        run_id = runtime._run_id
        start = time.monotonic()
        ctx = dict(context or {})
        ctx["task"] = task
        if self.yaml_sha256:
            ctx["_workflow_sha256"] = self.yaml_sha256
            ctx["_workflow_version"] = self.version

        await bus.emit(WorkflowEvent(
            kind=EventKind.WORKFLOW_START,
            run_id=run_id,
            data={"workflow": self.name, "task": task[:200]},
        ))

        # ── Pre-run cost forecast gate ─────────────────────────────────────────
        max_forecast = getattr(self.policy, "max_forecast_usd", 0.0)
        if max_forecast > 0:
            try:
                from meshflow.optimization.planner import CostForecaster
                fc = CostForecaster()
                all_models: list[str] = []
                for nd in self._nodes.values():
                    if nd.kind.value == "native" and nd._runner is not None:
                        from meshflow.core.workflow import _extract_agent_from_closure
                        ag = _extract_agent_from_closure(nd._runner)
                        if ag is not None:
                            m = getattr(getattr(ag, "config", None), "model", "")
                            if m:
                                all_models.append(m)
                if all_models:
                    representative_model = all_models[0]
                    forecast = fc.forecast(
                        model=representative_model,
                        messages=[{"role": "user", "content": task}],
                        max_budget_usd=max_forecast,
                    )
                    if not forecast["within_budget"]:
                        raise BudgetExceededError(
                            f"Pre-run cost forecast ${forecast['total_usd_est']:.5f} "
                            f"exceeds max_forecast_usd=${max_forecast:.5f} — run aborted."
                        )
            except BudgetExceededError:
                raise
            except Exception:
                pass  # forecast errors are non-fatal; proceed with execution

        steps: list[RuntimeOutcome] = []
        blocked_nodes: list[str] = []
        paused_nodes: list[str] = []
        skipped_nodes: list[str] = []
        completed: set[str] = set()
        skipped: set[str] = set()
        failed: set[str] = set()
        dynamic_next_nodes: set[str] = set()
        nodes_with_handoff: set[str] = set()
        step_outcomes: dict[str, RuntimeOutcome] = {}

        ready, _ = self._compute_ready(completed, skipped, failed, dynamic_next_nodes, nodes_with_handoff, ctx, step_outcomes)

        while ready:
            level_nodes = [self._nodes[nid] for nid in ready if nid in self._nodes]
            if not level_nodes:
                break

            for nd in level_nodes:
                await bus.emit(WorkflowEvent(
                    kind=EventKind.STEP_START,
                    run_id=run_id,
                    node_id=nd.id,
                    data={"kind": nd.kind.value},
                ))

            ctx_snapshot = ctx.copy()

            _dedup = None
            if len(level_nodes) > 1:
                from meshflow.agents.context_dedup import ContextDeduplicator
                _dedup = ContextDeduplicator()

            async def _run_node(nd: MeshNode) -> RuntimeOutcome:
                node_ctx = ctx_snapshot.copy()
                if _dedup is not None:
                    node_ctx = _dedup.deduplicate(node_ctx, agent_name=nd.id)
                return await self._execute_workflow_node(nd, task, runtime, node_ctx, run_id)

            level_outcomes: list[RuntimeOutcome] = list(
                await asyncio.gather(*[_run_node(nd) for nd in level_nodes])
            )

            level_updates: dict[str, list[tuple[str, Any, float]]] = {}

            for node_id, outcome in zip(ready, level_outcomes):
                step_outcomes[node_id] = outcome
                steps.append(outcome)
                if outcome.paused_for_human:
                    paused_nodes.append(node_id)
                    await bus.emit(WorkflowEvent(
                        kind=EventKind.STEP_PAUSED,
                        run_id=run_id,
                        node_id=node_id,
                        data={
                            "human_context": outcome.human_context or {},
                            "uncertainty": outcome.record.uncertainty,
                        },
                    ))
                    await bus.emit(WorkflowEvent(
                        kind=EventKind.HITL_REQUIRED,
                        run_id=run_id,
                        node_id=node_id,
                        data={"human_context": outcome.human_context or {}},
                    ))
                    if hasattr(runtime, "_ledger") and runtime._ledger is not None:
                        await _save_checkpoint(
                            ledger=runtime._ledger,
                            run_id=run_id,
                            workflow_name=self.name,
                            task=task,
                            paused_at_node=node_id,
                            context=ctx,
                            completed=completed,
                            skipped=skipped_nodes[:],
                            step_outcomes=step_outcomes,
                        )
                        await bus.emit(WorkflowEvent(
                            kind=EventKind.CHECKPOINT_SAVED,
                            run_id=run_id,
                            node_id=node_id,
                            data={"paused_at": node_id},
                        ))
                elif not outcome.ok:
                    blocked_nodes.append(node_id)
                    failed.add(node_id)
                    await bus.emit(WorkflowEvent(
                        kind=EventKind.STEP_BLOCKED,
                        run_id=run_id,
                        node_id=node_id,
                        data={
                            "blocked_by": outcome.blocked_by,
                            "uncertainty": outcome.record.uncertainty,
                        },
                    ))
                else:
                    completed.add(node_id)
                    await bus.emit(WorkflowEvent(
                        kind=EventKind.STEP_COMPLETE,
                        run_id=run_id,
                        node_id=node_id,
                        data={
                            "tokens": outcome.record.tokens_used,
                            "cost_usd": outcome.record.cost_usd,
                            "uncertainty": outcome.record.uncertainty,
                            "content_preview": outcome.output.content[:120],
                        },
                    ))
                    if outcome.output.content:
                        ctx[f"{node_id}_output"] = outcome.output.content

                    if outcome.output.structured and "next_node" in outcome.output.structured:
                        next_node_id = outcome.output.structured["next_node"]
                        if next_node_id in self._nodes:
                            dynamic_next_nodes.add(next_node_id)
                            nodes_with_handoff.add(node_id)

                    if outcome.output.structured:
                        confidence = outcome.output.confidence if hasattr(outcome.output, "confidence") else 0.8
                        for k, v in outcome.output.structured.items():
                            if k.startswith("_"):
                                continue
                            if k not in level_updates:
                                level_updates[k] = []
                            level_updates[k].append((node_id, v, confidence))

            if paused_nodes:
                break

            # Apply merge strategies for Context Bus
            for key, updates in level_updates.items():
                strategy = self.context_bus.get("merge_strategies", {}).get(key, "overwrite")

                if strategy == "overwrite":
                    for node_id, val, conf in updates:
                        ctx[key] = val
                elif strategy == "append":
                    merged = ctx.get(key)
                    for node_id, val, conf in updates:
                        if merged is None:
                            merged = val
                        else:
                            if isinstance(merged, list) and isinstance(val, list):
                                merged = merged + val
                            elif isinstance(merged, dict) and isinstance(val, dict):
                                merged = {**merged, **val}
                            elif isinstance(merged, str) and isinstance(val, str):
                                merged = merged + "\n" + val
                            else:
                                if not isinstance(merged, list):
                                    merged = [merged]
                                if isinstance(val, list):
                                    merged = merged + val
                                else:
                                    merged.append(val)
                    ctx[key] = merged
                elif strategy == "select_highest_confidence":
                    best_val = None
                    best_conf = -1.0
                    for node_id, val, conf in updates:
                        if conf > best_conf:
                            best_conf = conf
                            best_val = val
                    if best_val is not None:
                        ctx[key] = best_val
                elif strategy == "logical_and":
                    vals = [ctx[key]] if key in ctx else []
                    for node_id, val, conf in updates:
                        vals.append(val)
                    ctx[key] = all(bool(v) for v in vals)
                elif strategy == "logical_or":
                    vals = [ctx[key]] if key in ctx else []
                    for node_id, val, conf in updates:
                        vals.append(val)
                    ctx[key] = any(bool(v) for v in vals)

            for le in self._loop_edges:
                if le.src in ready and le.src in completed:
                    src_outcome = step_outcomes.get(le.src)
                    fires = (
                        self._condition_fires(
                            WorkflowEdge(le.src, le.dst, le.condition),
                            ctx,
                            step_outcomes,
                        )
                        if le.condition
                        else src_outcome is not None and src_outcome.ok
                    )
                    if fires:
                        if le._count >= le.max_iterations:
                            raise MaxIterationsError(
                                f"Loop {le.src}→{le.dst} exceeded {le.max_iterations} iterations"
                            )
                        le._count += 1
                        completed.discard(le.dst)

            # Check for Dynamic Replanning
            replanned = False
            for node_id, outcome in zip(ready, level_outcomes):
                if outcome.ok and outcome.output.structured:
                    new_yaml = outcome.output.structured.get("replanned_workflow_yaml")
                    new_dict = outcome.output.structured.get("replanned_workflow")

                    if new_yaml or new_dict:
                        max_replans = int(self.policy.max_replans) if hasattr(self.policy, "max_replans") else 3
                        if self._replans_count >= max_replans:
                            raise MaxIterationsError(
                                f"Workflow replanning exceeded limit of {max_replans} replans"
                            )

                        self._replans_count += 1

                        if new_yaml:
                            new_wf = WorkflowDefinition.from_yaml_string(new_yaml, getattr(self, "_node_registry", None))
                        else:
                            import yaml as _yaml
                            yaml_str = _yaml.dump(new_dict)
                            new_wf = WorkflowDefinition.from_yaml_string(yaml_str, getattr(self, "_node_registry", None))

                        # Copy runners from the current workflow for any matching node IDs
                        for nid, node in new_wf._nodes.items():
                            if nid in self._nodes and node._runner is None:
                                node._runner = self._nodes[nid]._runner
                                node.capabilities = self._nodes[nid].capabilities
                                node.risk_profile = self._nodes[nid].risk_profile
                                if not node.metadata:
                                    node.metadata = {}
                                node.metadata.update(self._nodes[nid].metadata)

                        from meshflow.core.diff import workflow_diff_objects
                        diff_res = workflow_diff_objects(self, new_wf)

                        if diff_res.has_changes:
                            print(diff_res.summary())

                            await bus.emit(WorkflowEvent(
                                kind=EventKind.STEP_COMPLETE,
                                run_id=run_id,
                                node_id=node_id,
                                data={"message": f"Workflow replanned: {len(diff_res.changes)} changes", "diff": diff_res.to_dict()},
                            ))

                            self._nodes = new_wf._nodes
                            self._edges = new_wf._edges
                            self._loop_edges = new_wf._loop_edges
                            self._entry = new_wf._entry
                            self._terminal = new_wf._terminal
                            self.policy = new_wf.policy
                            self.compliance_guard = new_wf.compliance_guard
                            self.metadata = new_wf.metadata
                            replanned = True
                            break

            if replanned:
                ready, newly_skipped = self._compute_ready(completed, skipped, failed, dynamic_next_nodes, nodes_with_handoff, ctx, step_outcomes)
                while newly_skipped:
                    for skipped_id in newly_skipped:
                        await bus.emit(WorkflowEvent(
                            kind=EventKind.STEP_SKIPPED,
                            run_id=run_id,
                            node_id=skipped_id,
                            data={},
                        ))
                    skipped.update(newly_skipped)
                    skipped_nodes.extend(newly_skipped)
                    ready, newly_skipped = self._compute_ready(completed, skipped, failed, dynamic_next_nodes, nodes_with_handoff, ctx, step_outcomes)
                continue

            ready, newly_skipped = self._compute_ready(completed, skipped, failed, dynamic_next_nodes, nodes_with_handoff, ctx, step_outcomes)
            while newly_skipped:
                for skipped_id in newly_skipped:
                    await bus.emit(WorkflowEvent(
                        kind=EventKind.STEP_SKIPPED,
                        run_id=run_id,
                        node_id=skipped_id,
                        data={},
                    ))
                skipped.update(newly_skipped)
                skipped_nodes.extend(newly_skipped)
                ready, newly_skipped = self._compute_ready(completed, skipped, failed, dynamic_next_nodes, nodes_with_handoff, ctx, step_outcomes)

        final_output = ""
        for outcome in reversed(steps):
            if outcome.ok and outcome.output.content:
                final_output = outcome.output.content
                break

        total_cost = sum(s.record.cost_usd for s in steps)
        total_tokens = sum(s.record.tokens_used for s in steps)
        total_carbon = sum(s.record.carbon_gco2 for s in steps)
        duration = round(time.monotonic() - start, 2)

        is_completed = True
        if paused_nodes:
            is_completed = False
        elif failed:
            if self._terminal:
                is_completed = all(t in completed or t in skipped for t in self._terminal) and any(t in completed for t in self._terminal)
            else:
                is_completed = len(failed) == 0

        budget = getattr(runtime, "_budget", None)
        if is_completed and budget:
            from meshflow.core.policy import BudgetExceededError
            try:
                budget.pre_check()
            except BudgetExceededError:
                is_completed = False

        terminal_kind = (
            EventKind.WORKFLOW_COMPLETE
            if is_completed
            else EventKind.WORKFLOW_FAILED
        )
        await bus.emit(WorkflowEvent(
            kind=terminal_kind,
            run_id=run_id,
            data={
                "completed": is_completed,
                "total_cost_usd": round(total_cost, 6),
                "total_tokens": total_tokens,
                "duration_s": duration,
                "blocked_nodes": list(failed),
                "paused_nodes": paused_nodes,
            },
        ))

        ledger_db = getattr(runtime._ledger, "_db_path", ":memory:")

        return WorkflowResult(
            run_id=run_id,
            workflow_name=self.name,
            completed=is_completed,
            output=final_output,
            steps=steps,
            total_cost_usd=round(total_cost, 6),
            total_tokens=total_tokens,
            total_carbon_gco2=round(total_carbon, 4),
            duration_s=duration,
            blocked_nodes=list(failed),
            paused_nodes=paused_nodes,
            skipped_nodes=skipped_nodes,
            ledger_db=ledger_db,
        )

    # ── Token-level streaming ─────────────────────────────────────────────────

    async def stream(
        self,
        task: str,
        runtime: StepRuntime,
        *,
        context: dict[str, Any] | None = None,
    ):
        """Async generator that yields :class:`~meshflow.core.streaming.StreamChunk` objects."""
        from meshflow.core.streaming import StreamChunk

        ctx = dict(context or {})
        ctx["task"] = task
        if self.yaml_sha256:
            ctx["_workflow_sha256"] = self.yaml_sha256
            ctx["_workflow_version"] = self.version

        run_id = runtime._run_id
        completed: set[str] = set()
        skipped: set[str] = set()
        failed: set[str] = set()
        dynamic_next_nodes: set[str] = set()
        nodes_with_handoff: set[str] = set()
        step_outcomes: dict[str, Any] = {}

        yield StreamChunk(
            kind="task_start",
            node_name=self.name,
            metadata={"run_id": run_id, "task": task[:120]},
        )

        ready, _ = self._compute_ready(completed, skipped, failed, dynamic_next_nodes, nodes_with_handoff, ctx, step_outcomes)

        while ready:
            level_nodes = [self._nodes[nid] for nid in ready if nid in self._nodes]
            if not level_nodes:
                break
            ctx_snapshot = ctx.copy()

            replanned = False
            for nd in level_nodes:
                yield StreamChunk(kind="node_start", node_name=nd.id,
                                  metadata={"kind": nd.kind.value, "run_id": run_id})
                try:
                    tokens_streamed = False

                    if nd.kind.value == "native" and nd._runner is not None:
                        agent_obj = _extract_agent_from_closure(nd._runner)
                        if agent_obj is not None and hasattr(agent_obj, "_provider"):
                            try:
                                async for tok in _stream_agent_tokens(
                                    agent_obj, task, ctx_snapshot
                                ):
                                    yield StreamChunk(kind="token", content=tok, node_name=nd.id)
                                    tokens_streamed = True
                            except Exception:
                                pass

                    node_ctx = ctx_snapshot.copy()
                    outcome = await self._execute_workflow_node(nd, task, runtime, node_ctx, run_id)
                    step_outcomes[nd.id] = outcome

                    if outcome.ok:
                        completed.add(nd.id)
                        if outcome.output.content:
                            ctx[f"{nd.id}_output"] = outcome.output.content

                        if outcome.output.structured and "next_node" in outcome.output.structured:
                            next_node_id = outcome.output.structured["next_node"]
                            if next_node_id in self._nodes:
                                dynamic_next_nodes.add(next_node_id)
                                nodes_with_handoff.add(nd.id)

                        if outcome.output.structured:
                            confidence = outcome.output.confidence if hasattr(outcome.output, "confidence") else 0.8
                            for k, v in outcome.output.structured.items():
                                if k.startswith("_"):
                                    continue
                                strategy = self.context_bus.get("merge_strategies", {}).get(k, "overwrite")
                                if strategy == "overwrite":
                                    ctx[k] = v
                                elif strategy == "append":
                                    merged = ctx.get(k)
                                    if merged is None:
                                        merged = v
                                    else:
                                        if isinstance(merged, list) and isinstance(v, list):
                                            merged = merged + v
                                        elif isinstance(merged, dict) and isinstance(v, dict):
                                            merged = {**merged, **v}
                                        elif isinstance(merged, str) and isinstance(v, str):
                                            merged = merged + "\n" + v
                                        else:
                                            if not isinstance(merged, list):
                                                merged = [merged]
                                            if isinstance(v, list):
                                                merged = merged + v
                                            else:
                                                merged.append(v)
                                    ctx[k] = merged
                                elif strategy == "select_highest_confidence":
                                    ctx[k] = v
                                elif strategy == "logical_and":
                                    ctx[k] = bool(ctx[k]) and bool(v) if k in ctx else bool(v)
                                elif strategy == "logical_or":
                                    ctx[k] = bool(ctx[k]) or bool(v) if k in ctx else bool(v)

                        if not tokens_streamed and outcome.output.content:
                            yield StreamChunk(
                                kind="token",
                                content=outcome.output.content,
                                node_name=nd.id,
                            )
                    else:
                        failed.add(nd.id)

                    yield StreamChunk(
                        kind="node_end",
                        node_name=nd.id,
                        metadata={
                            "ok": outcome.ok,
                            "cost_usd": outcome.record.cost_usd,
                            "tokens": outcome.record.tokens_used,
                            "blocked": outcome.blocked_by or "",
                            "run_id": run_id,
                        },
                    )

                    # Check for Dynamic Replanning
                    if outcome.ok and outcome.output.structured:
                        new_yaml = outcome.output.structured.get("replanned_workflow_yaml")
                        new_dict = outcome.output.structured.get("replanned_workflow")

                        if new_yaml or new_dict:
                            max_replans = int(self.policy.max_replans) if hasattr(self.policy, "max_replans") else 3
                            if self._replans_count >= max_replans:
                                raise MaxIterationsError(
                                    f"Workflow replanning exceeded limit of {max_replans} replans"
                                )

                            self._replans_count += 1

                            if new_yaml:
                                new_wf = WorkflowDefinition.from_yaml_string(new_yaml, getattr(self, "_node_registry", None))
                            else:
                                import yaml as _yaml
                                yaml_str = _yaml.dump(new_dict)
                                new_wf = WorkflowDefinition.from_yaml_string(yaml_str, getattr(self, "_node_registry", None))

                            # Copy runners from the current workflow for any matching node IDs
                            for nid, node in new_wf._nodes.items():
                                if nid in self._nodes and node._runner is None:
                                    node._runner = self._nodes[nid]._runner
                                    node.capabilities = self._nodes[nid].capabilities
                                    node.risk_profile = self._nodes[nid].risk_profile
                                    if not node.metadata:
                                        node.metadata = {}
                                    node.metadata.update(self._nodes[nid].metadata)

                            self._nodes = new_wf._nodes
                            self._edges = new_wf._edges
                            self._loop_edges = new_wf._loop_edges
                            self._entry = new_wf._entry
                            self._terminal = new_wf._terminal
                            self.policy = new_wf.policy
                            self.compliance_guard = new_wf.compliance_guard
                            self.metadata = new_wf.metadata
                            replanned = True
                            break
                except Exception as exc:
                    yield StreamChunk(kind="error", content=str(exc), node_name=nd.id)
                    break

            if replanned:
                ready, newly_skipped = self._compute_ready(completed, skipped, failed, dynamic_next_nodes, nodes_with_handoff, ctx, step_outcomes)
                while newly_skipped:
                    for skipped_id in newly_skipped:
                        yield StreamChunk(kind="error", content=f"Node {skipped_id} skipped", node_name=skipped_id)
                    skipped.update(newly_skipped)
                continue

            ready, newly_skipped = self._compute_ready(completed, skipped, failed, dynamic_next_nodes, nodes_with_handoff, ctx, step_outcomes)
            skipped.update(newly_skipped)

        yield StreamChunk(kind="done", node_name=self.name, metadata={"run_id": run_id})

    # ── Durable HITL resume ───────────────────────────────────────────────────

    async def resume(
        self,
        run_id: str,
        decision: HumanDecision,
        ledger: "ReplayLedger",
        runtime: StepRuntime,
    ) -> WorkflowResult:
        """Continue a workflow that paused waiting for human approval.

        Loads the checkpoint saved by ``run()`` when a HITL gate fired,
        injects the human's decision, and continues execution from the next
        ready nodes. The checkpoint is deleted on successful completion.

        Usage::

            # First run — pauses at approval node
            result = await mesh.run_workflow(wf, task="...", ledger_db="runs.db")
            assert result.paused_nodes == ["approval"]

            # Human reviews and decides
            decision = HumanDecision(approved=True, comment="LGTM")

            # Resume — picks up exactly where it left off
            result = await workflow.resume(
                run_id=result.run_id,
                decision=decision,
                ledger=ReplayLedger("runs.db"),
                runtime=<new StepRuntime with same run_id>,
            )
            assert result.completed is True
        """

        checkpoint = await ledger.load_checkpoint_data(run_id)
        if checkpoint is None:
            raise ValueError(f"No checkpoint found for run_id={run_id!r}")

        # Restore state from checkpoint
        task = checkpoint["task"]
        ctx: dict[str, Any] = checkpoint["context"]
        paused_at = checkpoint["paused_at_node"]
        completed: set[str] = set(checkpoint["completed_nodes"])
        skipped: set[str] = set(checkpoint["skipped_nodes"])
        skipped_nodes: list[str] = checkpoint["skipped_nodes"][:]

        # Reconstruct minimal step_outcomes so _condition_fires can work
        step_outcomes: dict[str, RuntimeOutcome] = _rebuild_step_outcomes(
            run_id, task, checkpoint.get("node_outputs", {})
        )

        # Record the human's decision as a proper step
        decision_content = "approved" if decision.approved else "rejected"
        decision_structured = {
            "approved": decision.approved,
            "comment": decision.comment,
            "decided_by": decision.decided_by,
        }
        decision_output = NodeOutput(
            content=decision_content,
            confidence=1.0 if decision.approved else 0.0,
            structured=decision_structured,
        )
        human_record = StepRecord(
            run_id=run_id,
            step_id=uuid.uuid4().hex[:8],
            node_id=paused_at,
            node_kind="human",
            input_task=task,
            output_content=decision_content,
            verdict="commit",
            blocked=False,
            block_reason="",
            uncertainty=0.0,
            cost_usd=0.0,
            tokens_used=0,
            carbon_gco2=0.0,
            duration_ms=0.0,
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            metadata={"human_decision": decision.approved, "comment": decision.comment},
        )
        await ledger.write(human_record)

        human_outcome = RuntimeOutcome(
            ok=True,
            node_id=paused_at,
            node_kind="human",
            output=decision_output,
            record=human_record,
            blocked_by="",
            paused_for_human=False,
            human_context={},
        )
        step_outcomes[paused_at] = human_outcome
        completed.add(paused_at)

        # Inject decision into shared context for downstream conditions
        ctx[f"{paused_at}_output"] = decision_content
        ctx["human_decision"] = decision.approved
        ctx["human_comment"] = decision.comment
        ctx.update(decision_structured)

        # Run the rest of the workflow using the same ready-queue logic
        start = time.monotonic()
        steps: list[RuntimeOutcome] = [human_outcome]
        blocked_nodes: list[str] = []
        paused_nodes: list[str] = []
        failed: set[str] = set()
        dynamic_next_nodes: set[str] = set()
        nodes_with_handoff: set[str] = set()

        ready, newly_skipped = self._compute_ready(completed, skipped, failed, dynamic_next_nodes, nodes_with_handoff, ctx, step_outcomes)
        while newly_skipped:
            skipped.update(newly_skipped)
            skipped_nodes.extend(newly_skipped)
            ready, newly_skipped = self._compute_ready(completed, skipped, failed, dynamic_next_nodes, nodes_with_handoff, ctx, step_outcomes)

        while ready:
            level_nodes = [self._nodes[nid] for nid in ready if nid in self._nodes]
            if not level_nodes:
                break

            ctx_snapshot = ctx.copy()

            _dedup = None
            if len(level_nodes) > 1:
                from meshflow.agents.context_dedup import ContextDeduplicator
                _dedup = ContextDeduplicator()

            async def _run_node(nd: MeshNode) -> RuntimeOutcome:
                node_ctx = ctx_snapshot.copy()
                if _dedup is not None:
                    node_ctx = _dedup.deduplicate(node_ctx, agent_name=nd.id)
                return await self._execute_workflow_node(nd, task, runtime, node_ctx, run_id)

            level_outcomes: list[RuntimeOutcome] = list(
                await asyncio.gather(*[_run_node(nd) for nd in level_nodes])
            )

            level_updates: dict[str, list[tuple[str, Any, float]]] = {}

            for node_id, outcome in zip(ready, level_outcomes):
                step_outcomes[node_id] = outcome
                steps.append(outcome)
                if outcome.paused_for_human:
                    paused_nodes.append(node_id)
                    await _save_checkpoint(
                        ledger=ledger,
                        run_id=run_id,
                        workflow_name=self.name,
                        task=task,
                        paused_at_node=node_id,
                        context=ctx,
                        completed=completed,
                        skipped=skipped_nodes[:],
                        step_outcomes=step_outcomes,
                    )
                elif not outcome.ok:
                    blocked_nodes.append(node_id)
                    failed.add(node_id)
                else:
                    completed.add(node_id)
                    if outcome.output.content:
                        ctx[f"{node_id}_output"] = outcome.output.content

                    if outcome.output.structured and "next_node" in outcome.output.structured:
                        next_node_id = outcome.output.structured["next_node"]
                        if next_node_id in self._nodes:
                            dynamic_next_nodes.add(next_node_id)
                            nodes_with_handoff.add(node_id)

                    if outcome.output.structured:
                        confidence = outcome.output.confidence if hasattr(outcome.output, "confidence") else 0.8
                        for k, v in outcome.output.structured.items():
                            if k.startswith("_"):
                                continue
                            if k not in level_updates:
                                level_updates[k] = []
                            level_updates[k].append((node_id, v, confidence))

            if paused_nodes:
                break

            # Apply merge strategies for Context Bus
            for key, updates in level_updates.items():
                strategy = self.context_bus.get("merge_strategies", {}).get(key, "overwrite")

                if strategy == "overwrite":
                    for node_id, val, conf in updates:
                        ctx[key] = val
                elif strategy == "append":
                    merged = ctx.get(key)
                    for node_id, val, conf in updates:
                        if merged is None:
                            merged = val
                        else:
                            if isinstance(merged, list) and isinstance(val, list):
                                merged = merged + val
                            elif isinstance(merged, dict) and isinstance(val, dict):
                                merged = {**merged, **val}
                            elif isinstance(merged, str) and isinstance(val, str):
                                merged = merged + "\n" + val
                            else:
                                if not isinstance(merged, list):
                                    merged = [merged]
                                if isinstance(val, list):
                                    merged = merged + val
                                else:
                                    merged.append(val)
                    ctx[key] = merged
                elif strategy == "select_highest_confidence":
                    best_val = None
                    best_conf = -1.0
                    for node_id, val, conf in updates:
                        if conf > best_conf:
                            best_conf = conf
                            best_val = val
                    if best_val is not None:
                        ctx[key] = best_val
                elif strategy == "logical_and":
                    vals = [ctx[key]] if key in ctx else []
                    for node_id, val, conf in updates:
                        vals.append(val)
                    ctx[key] = all(bool(v) for v in vals)
                elif strategy == "logical_or":
                    vals = [ctx[key]] if key in ctx else []
                    for node_id, val, conf in updates:
                        vals.append(val)
                    ctx[key] = any(bool(v) for v in vals)

            # Check for Dynamic Replanning
            replanned = False
            for node_id, outcome in zip(ready, level_outcomes):
                if outcome.ok and outcome.output.structured:
                    new_yaml = outcome.output.structured.get("replanned_workflow_yaml")
                    new_dict = outcome.output.structured.get("replanned_workflow")

                    if new_yaml or new_dict:
                        max_replans = int(self.policy.max_replans) if hasattr(self.policy, "max_replans") else 3
                        if self._replans_count >= max_replans:
                            raise MaxIterationsError(
                                f"Workflow replanning exceeded limit of {max_replans} replans"
                            )

                        self._replans_count += 1

                        if new_yaml:
                            new_wf = WorkflowDefinition.from_yaml_string(new_yaml, getattr(self, "_node_registry", None))
                        else:
                            import yaml as _yaml
                            yaml_str = _yaml.dump(new_dict)
                            new_wf = WorkflowDefinition.from_yaml_string(yaml_str, getattr(self, "_node_registry", None))

                        # Copy runners from the current workflow for any matching node IDs
                        for nid, node in new_wf._nodes.items():
                            if nid in self._nodes and node._runner is None:
                                node._runner = self._nodes[nid]._runner
                                node.capabilities = self._nodes[nid].capabilities
                                node.risk_profile = self._nodes[nid].risk_profile
                                if not node.metadata:
                                    node.metadata = {}
                                node.metadata.update(self._nodes[nid].metadata)

                        from meshflow.core.diff import workflow_diff_objects
                        diff_res = workflow_diff_objects(self, new_wf)

                        if diff_res.has_changes:
                            print(diff_res.summary())

                            self._nodes = new_wf._nodes
                            self._edges = new_wf._edges
                            self._loop_edges = new_wf._loop_edges
                            self._entry = new_wf._entry
                            self._terminal = new_wf._terminal
                            self.policy = new_wf.policy
                            self.compliance_guard = new_wf.compliance_guard
                            self.metadata = new_wf.metadata
                            replanned = True
                            break

            if replanned:
                ready, newly_skipped = self._compute_ready(completed, skipped, failed, dynamic_next_nodes, nodes_with_handoff, ctx, step_outcomes)
                while newly_skipped:
                    skipped.update(newly_skipped)
                    skipped_nodes.extend(newly_skipped)
                    ready, newly_skipped = self._compute_ready(completed, skipped, failed, dynamic_next_nodes, nodes_with_handoff, ctx, step_outcomes)
                continue

            ready, newly_skipped = self._compute_ready(completed, skipped, failed, dynamic_next_nodes, nodes_with_handoff, ctx, step_outcomes)
            while newly_skipped:
                skipped.update(newly_skipped)
                skipped_nodes.extend(newly_skipped)
                ready, newly_skipped = self._compute_ready(completed, skipped, failed, dynamic_next_nodes, nodes_with_handoff, ctx, step_outcomes)

        # Delete checkpoint on clean completion
        is_completed = True
        if paused_nodes:
            is_completed = False
        elif failed:
            if self._terminal:
                is_completed = all(t in completed or t in skipped for t in self._terminal) and any(t in completed for t in self._terminal)
            else:
                is_completed = len(failed) == 0

        if is_completed and not paused_nodes:
            await ledger.delete_checkpoint(run_id)

        final_output = ""
        for outcome in reversed(steps):
            if outcome.ok and outcome.output.content:
                final_output = outcome.output.content
                break

        total_cost = sum(s.record.cost_usd for s in steps)
        total_tokens = sum(s.record.tokens_used for s in steps)
        total_carbon = sum(s.record.carbon_gco2 for s in steps)

        return WorkflowResult(
            run_id=run_id,
            workflow_name=self.name,
            completed=is_completed,
            output=final_output,
            steps=steps,
            total_cost_usd=round(total_cost, 6),
            total_tokens=total_tokens,
            total_carbon_gco2=round(total_carbon, 4),
            duration_s=round(time.monotonic() - start, 2),
            blocked_nodes=list(failed),
            paused_nodes=paused_nodes,
            skipped_nodes=skipped_nodes,
            ledger_db=getattr(ledger, "_db_path", ":memory:"),
        )

    # ── YAML loader ───────────────────────────────────────────────────────────

    @classmethod
    def from_yaml(
        cls,
        path: str,
        node_registry: dict[str, Any] | None = None,
    ) -> "WorkflowDefinition":
        """Load a WorkflowDefinition from a YAML file.

        ``node_registry`` maps ref strings in YAML to live Python objects::

            registry = {
                "crews.market_research": my_crewai_crew,
                "graphs.fact_check":     my_langgraph_graph,
            }
            wf = WorkflowDefinition.from_yaml("mesh.yaml", registry)
        """
        import hashlib as _hashlib
        with open(path, "rb") as _fh:
            _raw_bytes = _fh.read()
        _yaml_sha256 = _hashlib.sha256(_raw_bytes).hexdigest()
        with open(path) as fh:
            data = yaml.safe_load(fh)
        return cls._from_dict(data, _yaml_sha256, path, node_registry)

    @classmethod
    def from_yaml_string(
        cls,
        yaml_str: str,
        node_registry: dict[str, Any] | None = None,
    ) -> "WorkflowDefinition":
        """Load a WorkflowDefinition from a YAML string."""
        import hashlib as _hashlib
        _yaml_sha256 = _hashlib.sha256(yaml_str.encode("utf-8")).hexdigest()
        data = yaml.safe_load(yaml_str)
        return cls._from_dict(data, _yaml_sha256, "", node_registry)

    @classmethod
    def _from_dict(
        cls,
        data: dict[str, Any],
        yaml_sha256: str,
        path: str,
        node_registry: dict[str, Any] | None = None,
    ) -> "WorkflowDefinition":
        # Policy
        pol_cfg = data.get("policy", {})
        mode = pol_cfg.get("mode", "standard")
        hitl_tier_str = pol_cfg.get("human_approval_tier", "irreversible").upper()
        hitl_tier = (
            RiskTier[hitl_tier_str]
            if hitl_tier_str in RiskTier.__members__
            else RiskTier.IRREVERSIBLE
        )
        hitl_enabled = hitl_tier_str != "NONE"

        pol = policy_for_mode(
            mode,
            budget_usd=pol_cfg.get("budget_usd", 1.0),
            budget_tokens=pol_cfg.get("budget_tokens", 500_000),
            timeout_s=pol_cfg.get("timeout_s", 300.0),
            max_steps=pol_cfg.get("max_steps", 50),
            enable_guardian=pol_cfg.get("enable_guardian", True),
            enable_collusion_audit=pol_cfg.get("enable_collusion_audit", True),
            enable_uncertainty=pol_cfg.get("enable_uncertainty", True),
            enable_environmental=pol_cfg.get("enable_environmental", False),
            enable_cross_run_learning=pol_cfg.get("enable_cross_run_learning", False),
            human_in_loop=HumanInLoopConfig(
                enabled=hitl_enabled,
                tier_threshold=hitl_tier,
            ),
        )
        if pol_cfg.get("max_forecast_usd", 0.0):
            pol.max_forecast_usd = float(pol_cfg["max_forecast_usd"])
        if pol_cfg.get("max_replans") is not None:
            pol.max_replans = int(pol_cfg["max_replans"])

        wf = cls(
            name=data.get("name", "unnamed"),
            version=str(data.get("version", "1")),
            policy=pol,
        )
        wf.context_bus = dict(data.get("context_bus", {}))
        wf._node_registry = node_registry

        # Nodes
        for node_id, node_cfg in data.get("nodes", {}).items():
            kind_str = node_cfg.get("kind", "native").lower()
            kind = NodeKind(kind_str)
            risk_str = node_cfg.get("risk", "READ_ONLY").upper()
            risk = RiskTier[risk_str] if risk_str in RiskTier.__members__ else RiskTier.READ_ONLY
            ref = node_cfg.get("ref", "")

            if kind == NodeKind.NATIVE:
                node = _build_native_node(node_id, node_cfg, pol)
            elif kind == NodeKind.HUMAN:
                node = MeshNode.human_approval(node_id)
            elif kind == NodeKind.PYTHON:
                fn = (node_registry or {}).get(ref)
                if fn:
                    node = MeshNode.from_callable(node_id, fn, risk)
                else:
                    node = MeshNode(id=node_id, kind=kind, risk_profile=risk)
            elif kind == NodeKind.CREWAI:
                crew = (node_registry or {}).get(ref)
                node = (
                    MeshNode.from_crewai(node_id, crew)
                    if crew
                    else MeshNode(id=node_id, kind=kind, risk_profile=risk)
                )
            elif kind == NodeKind.LANGGRAPH:
                graph = (node_registry or {}).get(ref)
                node = (
                    MeshNode.from_langgraph(node_id, graph)
                    if graph
                    else MeshNode(id=node_id, kind=kind, risk_profile=risk)
                )
            elif kind == NodeKind.AUTOGEN:
                agent = (node_registry or {}).get(ref)
                node = (
                    MeshNode.from_autogen(node_id, agent)
                    if agent
                    else MeshNode(id=node_id, kind=kind, risk_profile=risk)
                )
            elif kind == NodeKind.SUBGRAPH:
                if ref:
                    from meshflow.core.subgraph import subgraph_from_yaml
                    node = subgraph_from_yaml(node_id, ref, node_registry)
                else:
                    inner_wf = (node_registry or {}).get(node_cfg.get("workflow", node_id))
                    if inner_wf is not None:
                        from meshflow.core.subgraph import SubgraphNode
                        node = SubgraphNode.create(node_id, inner_wf)
                    else:
                        node = MeshNode(id=node_id, kind=kind, risk_profile=risk)
            elif kind == NodeKind.HTTP:
                url = node_cfg.get("url", "")
                node = MeshNode.from_http(node_id, url, risk=risk)
            else:
                node = MeshNode(id=node_id, kind=kind, risk_profile=risk)

            # Store per-node static attachments (multi-modal YAML spec)
            attachments = node_cfg.get("attachments", [])
            if attachments:
                node.metadata["attachments"] = list(attachments)

            # Store structured output schema + retry config
            output_schema = node_cfg.get("output_schema")
            if output_schema:
                node.metadata["output_schema"] = output_schema
            node.metadata["retry_on_fail"] = bool(node_cfg.get("retry_on_fail", False))
            node.metadata["max_retries"] = int(node_cfg.get("max_retries", 1))

            wf.add_node(node)

        # Edges
        for edge_data in data.get("edges", []):
            if isinstance(edge_data, str):
                # "nodeA -> nodeB" shorthand
                parts = [p.strip() for p in edge_data.split("->")]
                if len(parts) == 2:
                    wf.add_edge(parts[0], parts[1])
            elif isinstance(edge_data, dict):
                wf.add_edge(
                    edge_data.get("from", ""),
                    edge_data.get("to", ""),
                    edge_data.get("condition", ""),
                )

        # Loop edges
        for le_data in data.get("loop_edges", []):
            if isinstance(le_data, dict):
                wf.add_loop_edge(
                    src=le_data.get("from", ""),
                    dst=le_data.get("to", ""),
                    condition=le_data.get("condition", ""),
                    max_iterations=int(le_data.get("max_iterations", 10)),
                )

        # Entry + terminal
        entry = data.get("entry", "")
        if entry:
            wf.set_entry(entry)

        terminal = data.get("terminal", [])
        if isinstance(terminal, str):
            terminal = [terminal]
        if terminal:
            wf.set_terminal(*terminal)

        # Metadata — user-defined only; fingerprint lives on separate attributes
        wf.metadata = dict(data.get("metadata", {}))
        wf.yaml_sha256 = yaml_sha256    # SHA-256 of the YAML for exact-replay version pinning
        wf.yaml_path = path

        # Compliance guard — optional section activates real-time rule enforcement
        compliance_cfg = data.get("compliance", {})
        if compliance_cfg:
            try:
                from meshflow.compliance.guard import ComplianceGuard
                frameworks: list[str] = compliance_cfg.get("frameworks", [])
                if isinstance(frameworks, str):
                    frameworks = [frameworks]
                block_on_violation: bool = bool(compliance_cfg.get("block_on_violation", True))
                wf.compliance_guard = ComplianceGuard(
                    frameworks=frameworks,
                    block_on_violation=block_on_violation,
                )
            except Exception:
                pass  # guard unavailable; workflow continues without it

        # model_router: section — auto-route model tiers for native nodes
        router_cfg = data.get("model_router", {})
        if router_cfg:
            try:
                from meshflow.agents.model_router import ModelRouter, RouterConfig
                router = ModelRouter(config=RouterConfig.from_dict({"model_router": router_cfg}))
                wf.metadata["_model_router"] = router
                # Apply model routing to nodes that have a task_description in metadata
                for node in wf._nodes.values():
                    task_desc = node.metadata.get("task_description", "")
                    if task_desc:
                        decision = router.route(task_desc)
                        node.metadata["_routed_model"] = decision.model
                        node.metadata["_routed_tier"] = decision.tier
            except Exception:
                pass  # router unavailable; workflow continues without it

        return wf

    def describe(self) -> dict[str, Any]:
        """Return a human-readable description of the workflow topology."""
        return {
            "name": self.name,
            "version": self.version,
            "metadata": self.metadata,
            "nodes": [
                {"id": n.id, "kind": n.kind.value, "risk": int(n.risk_profile)}
                for n in self._nodes.values()
            ],
            "edges": [
                {
                    "from": e.from_node,
                    "to": e.to_node,
                    **({"condition": e.condition} if e.condition else {}),
                }
                for e in self._edges
            ],
            "loop_edges": [
                {
                    "from": le.src,
                    "to": le.dst,
                    "condition": le.condition,
                    "max_iterations": le.max_iterations,
                }
                for le in self._loop_edges
            ],
            "entry": self._entry,
            "terminal": self._terminal,
            "compliance_guard": self.compliance_guard is not None,
            "policy": {
                "budget_usd": self.policy.budget_usd,
                "max_steps": self.policy.max_steps,
                "enable_guardian": self.policy.enable_guardian,
            },
        }

    def to_yaml(self, path: str | None = None) -> str:
        """Serialise this WorkflowDefinition back to a YAML string.

        Makes pipelines portable, versionable, and diffable in CI —
        closing the Haystack pipeline-serialization parity gap.

        Parameters
        ----------
        path:
            If provided, write the YAML to this file path as well as
            returning it as a string.

        Returns
        -------
        YAML string representation of the workflow.

        Example
        -------
        ::

            wf = WorkflowDefinition.from_yaml("pipeline.yaml")
            # … modify nodes …
            wf.to_yaml("pipeline_v2.yaml")   # round-trip export
        """
        import yaml as _yaml

        doc: dict[str, Any] = {
            "name": self.name,
            "version": self.version,
        }

        if self.metadata:
            doc["metadata"] = self.metadata

        if self.context_bus:
            doc["context_bus"] = self.context_bus

        # Policy section
        p = self.policy
        doc["policy"] = {
            "budget_usd": p.budget_usd,
            "max_steps": p.max_steps,
            "enable_guardian": p.enable_guardian,
            "max_replans": p.max_replans,
        }

        # Nodes — reconstruct from MeshNode metadata where possible
        nodes_out: dict[str, Any] = {}
        for node in self._nodes.values():
            nd: dict[str, Any] = {"kind": node.kind.value}
            # Preserve agent config stored in metadata by from_yaml
            agent_cfg = node.metadata.get("agent", {})
            if agent_cfg:
                nd["agent"] = agent_cfg
            elif node.metadata:
                nd.update({k: v for k, v in node.metadata.items()
                           if k not in ("_from_checkpoint", "run_id", "kind")})
            nodes_out[node.id] = nd
        doc["nodes"] = nodes_out

        # Edges
        edges_out = []
        for e in self._edges:
            if e.condition:
                edges_out.append(f"{e.from_node} -> {e.to_node} [{e.condition}]")
            else:
                edges_out.append(f"{e.from_node} -> {e.to_node}")
        if edges_out:
            doc["edges"] = edges_out

        # Loop edges
        if self._loop_edges:
            doc["loop_edges"] = [
                {
                    "from": le.src,
                    "to": le.dst,
                    "condition": le.condition,
                    "max_iterations": le.max_iterations,
                }
                for le in self._loop_edges
            ]

        if self._entry:
            doc["entry"] = self._entry

        if self._terminal:
            doc["terminal"] = self._terminal

        yaml_str = _yaml.dump(doc, default_flow_style=False, sort_keys=False, allow_unicode=True)

        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(yaml_str)

        return yaml_str


# ── Internal helpers ──────────────────────────────────────────────────────────


async def _save_checkpoint(
    ledger: Any,
    run_id: str,
    workflow_name: str,
    task: str,
    paused_at_node: str,
    context: dict[str, Any],
    completed: set[str],
    skipped: list[str],
    step_outcomes: dict[str, RuntimeOutcome],
) -> None:
    """Serialize paused workflow state to the ledger."""
    node_outputs = {
        nid: {
            "content": o.output.content,
            "confidence": o.output.confidence,
            "structured": o.output.structured,
            "kind": o.node_kind,
        }
        for nid, o in step_outcomes.items()
        if o.ok
    }
    await ledger.save_checkpoint(
        run_id,
        {
            "run_id": run_id,
            "workflow_name": workflow_name,
            "task": task,
            "workflow_yaml": context.get("workflow_yaml", ""),
            "paused_at_node": paused_at_node,
            "context": context,
            "completed_nodes": list(completed),
            "skipped_nodes": skipped,
            "node_outputs": node_outputs,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
    )


def _rebuild_step_outcomes(
    run_id: str,
    task: str,
    node_outputs: dict[str, Any],
) -> dict[str, RuntimeOutcome]:
    """Reconstruct minimal RuntimeOutcome objects from checkpoint data."""
    outcomes: dict[str, RuntimeOutcome] = {}
    for nid, out_data in node_outputs.items():
        output = NodeOutput(
            content=out_data.get("content", ""),
            confidence=out_data.get("confidence", 0.0),
            structured=out_data.get("structured", {}),
        )
        record = StepRecord(
            run_id=run_id,
            step_id="checkpoint",
            node_id=nid,
            node_kind=out_data.get("kind", "python"),
            input_task=task,
            output_content=output.content,
            verdict="commit",
            blocked=False,
            block_reason="",
            uncertainty=0.0,
            cost_usd=0.0,
            tokens_used=0,
            carbon_gco2=0.0,
            duration_ms=0.0,
            timestamp="",
            metadata={},
        )
        outcomes[nid] = RuntimeOutcome(
            ok=True,
            node_id=nid,
            node_kind=out_data.get("kind", "python"),
            output=output,
            record=record,
            blocked_by="",
            paused_for_human=False,
            human_context={},
        )
    return outcomes


def _build_native_node(node_id: str, node_cfg: dict[str, Any], pol: Policy) -> MeshNode:
    """Construct a MeshFlow native agent node from YAML config."""
    from meshflow.agents.base import (
        AgentConfig,
        CriticAgent,
        ExecutorAgent,
        PlannerAgent,
        ResearcherAgent,
    )
    from meshflow.core.schemas import AgentRole

    role_str = node_cfg.get("role", "executor").lower()
    model = node_cfg.get("model", pol.model_tier_map.get(AgentRole(role_str), "claude-sonnet-4-6"))

    role_map = {
        "planner": (PlannerAgent, AgentRole.PLANNER),
        "researcher": (ResearcherAgent, AgentRole.RESEARCHER),
        "executor": (ExecutorAgent, AgentRole.EXECUTOR),
        "critic": (CriticAgent, AgentRole.CRITIC),
    }
    AgentCls, role = role_map.get(role_str, (ExecutorAgent, AgentRole.EXECUTOR))
    cfg = AgentConfig(role=role, model=model)
    agent = AgentCls(cfg, pol)

    return MeshNode.from_native(node_id, agent)


# ── Streaming helpers (used by WorkflowDefinition.stream()) ──────────────────

def _extract_agent_from_closure(runner: Any) -> Any:
    """Try to pull the underlying agent object out of a closure created by
    ``MeshNode.from_native``.  Returns ``None`` if the closure doesn't hold
    an agent-like object.
    """
    closure = getattr(runner, "__closure__", None)
    if closure is None:
        return None
    for cell in closure:
        try:
            val = cell.cell_contents
            # A _BuiltAgent / BaseAgent carries both .config and .step()
            if hasattr(val, "config") and hasattr(val, "step") and hasattr(val, "_provider"):
                return val
        except ValueError:
            pass
    return None


async def _stream_agent_tokens(agent: Any, task: str, context: dict[str, Any]):
    """Async generator: yield token strings from *agent*'s LLM provider.

    Uses the provider's ``stream_complete()`` if available, otherwise falls
    back to a single ``complete()`` call and yields the full text as one chunk.
    """
    import uuid as _uuid

    model  = getattr(agent.config, "model", "")
    system = getattr(agent.config, "system_prompt", "")
    max_tok = getattr(agent.config, "max_tokens", 4096)
    provider = getattr(agent, "_provider", None)

    if provider is None:
        return

    messages = [{"role": "user", "content": f"Task: {task}\nContext: {context}"}]
    run_id  = str(_uuid.uuid4())[:8]
    step_id = str(_uuid.uuid4())[:8]

    if hasattr(provider, "stream_complete"):
        try:
            async for chunk in provider.stream_complete(
                model=model,
                messages=messages,
                system=system,
                max_tokens=max_tok,
                agent_id=getattr(agent.config, "agent_id", "agent"),
                step_id=step_id,
                run_id=run_id,
            ):
                text = getattr(chunk, "text", str(chunk))
                if text:
                    yield text
            return
        except Exception:
            pass

    # Fallback: full completion in one chunk
    try:
        text, _, _ = await provider.complete(
            model=model,
            messages=messages,
            system=system,
            max_tokens=max_tok,
            agent_id=getattr(agent.config, "agent_id", "agent"),
            step_id=step_id,
            run_id=run_id,
        )
        if text:
            yield text
    except Exception:
        pass


@dataclass
class CostCap:
    """Configures the cost limit (USD) for a Workflow run."""

    usd: float = 1.0


@dataclass
class _AgentCostLine:
    agent: str
    model: str
    cost_usd: float
    is_local: bool


@dataclass
class CostEstimate:
    """Per-agent cost estimate returned by :meth:`Workflow.estimate_cost`.

    Attributes
    ----------
    lines:        One entry per agent with model name and estimated cost.
    task_preview: First 80 chars of the task used for the estimate.
    """

    lines: list[_AgentCostLine]
    task_preview: str = ""

    @property
    def total_usd(self) -> float:
        return sum(ln.cost_usd for ln in self.lines)

    @property
    def cloud_agents(self) -> list[str]:
        return [ln.agent for ln in self.lines if not ln.is_local]

    @property
    def local_agents(self) -> list[str]:
        return [ln.agent for ln in self.lines if ln.is_local]

    def __str__(self) -> str:
        col_a = max((len(ln.agent) for ln in self.lines), default=6)
        col_m = max((len(ln.model) for ln in self.lines), default=12)
        rows = []
        for ln in self.lines:
            tag = "(local)" if ln.is_local else "(cloud)"
            rows.append(
                f"  {ln.agent:{col_a}}  {ln.model:{col_m}}  ${ln.cost_usd:.4f}  {tag}"
            )
        sep = "  " + "─" * (col_a + col_m + 18)
        rows.append(sep)
        rows.append(f"  {'Total':{col_a}}  {'':{ col_m}}  ${self.total_usd:.4f}")
        return "\n".join(rows)


class Workflow:
    """High-level synchronous wrapper executing agents sequentially.

    Combines progressive governance, durability, and cost cap constraints by
    compiling the agent list into a Team running sequentially.
    """

    def __init__(
        self,
        cost_cap: CostCap | None = None,
        mode: str = "production",
        state_schema: Any | None = None,
        initial_state: Any | None = None,
    ) -> None:
        self.cost_cap = cost_cap
        self.mode = mode
        self.agents: list[Any] = []
        self.state_schema = state_schema
        self.initial_state = initial_state
        self._input_maps: dict[Any, Any] = {}
        self._output_maps: dict[Any, Any] = {}
        self.state: Any = None
        if state_schema is not None:
            from pydantic import BaseModel
            if isinstance(initial_state, BaseModel):
                self.state = initial_state
            elif isinstance(initial_state, dict):
                self.state = state_schema(**initial_state)
            elif initial_state is None:
                self.state = state_schema()
            else:
                raise TypeError("initial_state must be a dict or a Pydantic model instance.")
        elif initial_state is not None:
            self.state = initial_state

    def add(
        self,
        *agents: Any,
        input_map: Any = None,
        output_map: Any = None,
    ) -> "Workflow":
        """Add one or more agents to the sequential workflow. Supports chaining."""
        self.agents.extend(agents)
        for agent in agents:
            if input_map:
                self._input_maps[agent.name] = input_map
            if output_map:
                self._output_maps[agent.name] = output_map
        return self

    def add_parallel(
        self,
        *agents: Any,
        input_map: Any = None,
        output_map: Any = None,
    ) -> "Workflow":
        """Add parallel execution step with multiple agents. Supports chaining."""
        if not agents:
            raise ValueError("Must provide at least one agent for parallel execution.")
        self.agents.append(list(agents))
        block_key = tuple(a.name for a in agents)
        if input_map:
            self._input_maps[block_key] = input_map
        if output_map:
            self._output_maps[block_key] = output_map
        return self

    def add_conditional(self, condition: Any, branches: dict[str, Any]) -> "Workflow":
        """Add a conditional branch step based on a state condition. Supports chaining."""
        if not callable(condition):
            raise TypeError("condition must be a callable.")
        if not isinstance(branches, dict):
            raise TypeError("branches must be a dictionary.")
        self.agents.append(_ConditionalBranch(condition, branches))
        return self

    def estimate_cost(self, task: str = "") -> "CostEstimate":
        """Estimate cost per agent before making any LLM calls.

        Uses token-count heuristics (chars / 4 ≈ tokens) and the model's
        per-token pricing. Local models (Ollama, llama, mistral…) always
        return $0.00. Cloud models use the pricing registry in
        ``meshflow.agents.base._PRICING``.

        Returns a :class:`CostEstimate` with per-agent breakdown and totals.

        Usage::

            wf = Workflow(cost_cap=CostCap(usd=0.50))
            wf.add(
                Agent("planner",    model="llama3.2"),
                Agent("researcher", model="mistral"),
                Agent("writer",     model="meta.llama3-70b-instruct-v1:0"),
            )
            est = wf.estimate_cost("analyse our competitive landscape")
            print(est)
            # planner    llama3.2                              $0.0000  (local)
            # researcher mistral                               $0.0000  (local)
            # writer     meta.llama3-70b-instruct-v1:0        $0.0032  (cloud)
            # ────────────────────────────────────────────────────────────
            # Total                                            $0.0032
        """
        from meshflow.agents.base import _cost_usd, model_is_local
        from meshflow.agents.registry import DEFAULT_REGISTRY

        # Rough token estimate: chars / 4 input, 25% output ratio
        input_tokens = max(len(task) // 4, 50)
        output_tokens = max(input_tokens // 4, 25)

        flat_agents = []
        for item in self.agents:
            if isinstance(item, (list, tuple)):
                flat_agents.extend(item)
            else:
                flat_agents.append(item)

        lines: list[_AgentCostLine] = []
        for agent in flat_agents:
            name = getattr(agent, "name", str(agent))
            model = ""
            if hasattr(agent, "_resolve_model"):
                try:
                    model = agent._resolve_model()
                except Exception:
                    model = getattr(agent, "model", "") or ""
            # If a model_router is attached, ask it to route the task.
            # _TierResult.is_local carries any explicit user override from ModelTier.
            is_local_override: bool | None = None
            router = getattr(agent, "model_router", None)
            if router is not None:
                try:
                    route_result = router.route(task)
                    model = getattr(route_result, "model", model) or model
                    # Respect explicit is_local on the tier (None = fall back to detection)
                    raw = getattr(route_result, "is_local", None)
                    if raw is not None:
                        is_local_override = bool(raw)
                except Exception:
                    pass
            # Priority: explicit tier override → registry → pattern detection
            if is_local_override is not None:
                is_local = is_local_override
                force_cloud = not is_local
                cost = 0.0 if is_local else _cost_usd(model, input_tokens, output_tokens, force_cloud=force_cloud)
            elif model in DEFAULT_REGISTRY:
                is_local = DEFAULT_REGISTRY.is_local(model)
                cost = DEFAULT_REGISTRY.cost_usd(model, input_tokens, output_tokens)
            else:
                is_local = model_is_local(model)
                cost = 0.0 if is_local else _cost_usd(model, input_tokens, output_tokens)
            lines.append(_AgentCostLine(agent=name, model=model, cost_usd=cost, is_local=is_local))

        return CostEstimate(lines=lines, task_preview=task[:80])

    # ── Structured output ─────────────────────────────────────────────────────

    def run_structured(self, task: str, schema: Any) -> Any:
        """Run the workflow and return a fully validated structured object.

        Uses the agent's structured output pipeline internally, streaming tokens
        into a partial JSON parser and returning the final validated Pydantic model
        (or raw dict if *schema* is None).

        Parameters
        ----------
        task:   Text task for the pipeline.
        schema: A Pydantic ``BaseModel`` class, or ``None`` to return raw dict.

        Example::

            from pydantic import BaseModel
            from meshflow import Workflow, Agent

            class Report(BaseModel):
                title: str
                summary: str
                key_points: list[str]

            wf = Workflow()
            wf.add(Agent("analyst"))
            report = wf.run_structured("Write a market analysis.", Report)
            print(report.title)
        """
        from meshflow.integrations._utils import run_sync
        return run_sync(self._run_structured_async(task, schema))

    async def _run_structured_async(self, task: str, schema: Any) -> Any:
        """Async backbone for :meth:`run_structured`."""
        async for chunk in self.astream_structured(task, schema):
            if chunk.complete:
                return chunk.validated if schema is not None else chunk.partial
        return None

    async def astream_structured(self, task: str, schema: Any) -> "Any":
        """Native async generator — yields :class:`~meshflow.streaming.partial_output.PartialOutputChunk` objects.

        Streams the pipeline's token output through a partial JSON parser.
        Yields one chunk per token (with the growing partial parse) and a final
        chunk with ``complete=True`` and the fully validated object in
        ``chunk.validated``.

        Parameters
        ----------
        task:   Text task for the pipeline.
        schema: A Pydantic ``BaseModel`` class, or ``None`` to return raw dict.

        Usage in FastAPI::

            @app.get("/stream-structured")
            async def endpoint(task: str):
                async def _gen():
                    async for chunk in wf.astream_structured(task, Report):
                        yield stream_to_ndjson(PartialOutputChunk(...))
                return StreamingResponse(_gen(), media_type="application/x-ndjson")

        Or collect synchronously::

            report = wf.run_structured("Write a market report.", Report)
        """
        from meshflow.streaming.partial_output import stream_structured

        async def _token_gen():
            async for chunk in self.astream(task):
                if chunk.is_token and chunk.content:
                    yield chunk.content

        async for partial_chunk in stream_structured(_token_gen(), schema):
            yield partial_chunk

    def stream_structured(self, task: str, schema: Any) -> "Any":
        """Synchronous structured streaming generator.

        Like :meth:`stream` but wraps the output through a partial JSON parser,
        yielding :class:`~meshflow.streaming.partial_output.PartialOutputChunk`
        objects as tokens accumulate.

        Use :meth:`run_structured` to block until the final object is ready.
        Use this method when you want to update a progress UI as the JSON fills in.

        Example::

            for chunk in wf.stream_structured("Analyse this.", Report):
                print(chunk.partial)      # dict with fields parsed so far
                if chunk.complete:
                    report = chunk.validated  # fully validated Report instance
        """
        import asyncio
        import queue as _queue
        import threading as _threading

        q: "_queue.Queue[Any]" = _queue.Queue(maxsize=256)

        async def _produce() -> None:
            try:
                async for chunk in self.astream_structured(task, schema):
                    q.put(chunk)
            except Exception:
                from meshflow.streaming.partial_output import PartialOutputChunk
                q.put(PartialOutputChunk(partial={}, complete=False, raw_so_far=""))
            finally:
                q.put(None)

        def _run() -> None:
            asyncio.run(_produce())

        t = _threading.Thread(target=_run, daemon=True)
        t.start()
        while True:
            chunk = q.get()
            if chunk is None:
                break
            yield chunk

    def stream(self, task: str) -> "Any":
        """Synchronous streaming generator — yields :class:`~meshflow.core.streaming.StreamChunk` objects.

        Streams token-by-token output from each agent in the pipeline while
        emitting ``"routing"`` events when a model tier is selected.  All
        chunks are instances of :class:`~meshflow.core.streaming.StreamChunk`
        so the same ``is_token``, ``is_routing``, ``is_done`` helpers work.

        Use :func:`~meshflow.core.streaming.stream_collect` to get the final
        text without handling individual chunks::

            from meshflow.core.streaming import stream_collect
            text = stream_collect(wf.stream("Explain GDPR §17"))

        Or handle chunks directly for real-time UI updates::

            for chunk in wf.stream("Write a haiku"):
                if chunk.is_token:
                    print(chunk.content, end="", flush=True)
                elif chunk.is_routing:
                    print(f"\\n→ tier={chunk.metadata['tier']} model={chunk.metadata['model']}")
        """
        import asyncio
        import queue as _queue
        import threading as _threading
        from meshflow.core.streaming import StreamChunk

        q: _queue.Queue[StreamChunk | None] = _queue.Queue(maxsize=256)

        async def _produce() -> None:
            try:
                async for chunk in self._stream_async(task):
                    q.put(chunk)
            except Exception as exc:
                q.put(StreamChunk(kind="error", content=str(exc)))
            finally:
                q.put(None)  # sentinel

        def _run_loop() -> None:
            asyncio.run(_produce())

        t = _threading.Thread(target=_run_loop, daemon=True)
        t.start()

        while True:
            chunk = q.get()
            if chunk is None:
                break
            yield chunk

    async def astream(self, task: str) -> "Any":
        """Native async streaming generator — no thread overhead.

        Prefer this in async contexts (FastAPI, Starlette, asyncio apps).
        Use :meth:`stream` for synchronous code.

        Usage in FastAPI::

            from fastapi import FastAPI
            from fastapi.responses import StreamingResponse
            from meshflow.core.streaming import chunks_to_sse, stream_to_sse

            app = FastAPI()

            @app.get("/stream")
            async def stream_endpoint(task: str):
                async def _sse():
                    async for chunk in wf.astream(task):
                        yield stream_to_sse(chunk)
                return StreamingResponse(_sse(), media_type="text/event-stream")

        Or collect the full output::

            from meshflow.core.streaming import async_stream_collect
            text = await async_stream_collect(wf.astream("Summarise Q3"))
        """
        async for chunk in self._stream_async(task):
            yield chunk

    async def astream_multimodal(self, task: str, inputs: list[Any]) -> "Any":
        """Native async streaming generator for multi-modal pipelines.

        Like :meth:`astream` but passes *inputs* (images, documents, audio)
        to the first agent.  No thread overhead — preferred in async contexts.

        Usage::

            from meshflow import ImageInput
            from meshflow.core.streaming import async_stream_collect

            img = ImageInput.from_bytes(chart_bytes, "image/png")
            text = await async_stream_collect(
                wf.astream_multimodal("Extract figures.", [img])
            )
        """
        async for chunk in self._stream_multimodal_async(task, inputs):
            yield chunk

    async def _stream_async(self, task: str) -> "Any":
        """Async generator powering :meth:`stream` and :meth:`stream_multimodal`."""
        from meshflow.core.streaming import StreamChunk
        import asyncio

        current_task = task

        for i, agent_item in enumerate(self.agents):
            if isinstance(agent_item, (list, tuple)):
                # Parallel streaming
                q: asyncio.Queue[StreamChunk | None] = asyncio.Queue()
                active = len(agent_item)
                collected_outputs = {}

                async def _run_agent(agent: Any) -> None:
                    # Emit routing event if model_router is attached
                    if agent.model_router is not None:
                        try:
                            routing = agent.model_router.route(current_task)
                            is_local = getattr(routing, "is_local", False)
                            await q.put(StreamChunk(
                                kind="routing",
                                node_name=agent.name,
                                metadata={
                                    "model": getattr(routing, "model", ""),
                                    "tier": getattr(routing, "tier", ""),
                                    "is_local": is_local,
                                    "cascade_escalation": False,
                                    "reason": f"composite score routed to tier '{getattr(routing, 'tier', '')}'"
                                              f" ({'local' if is_local else 'cloud'})",
                                },
                            ))
                        except Exception:
                            pass

                    await q.put(StreamChunk(kind="node_start", node_name=agent.name))
                    collected: list[str] = []
                    try:
                        async for token in agent.stream(current_task):
                            await q.put(StreamChunk(kind="token", content=token, node_name=agent.name))
                            collected.append(token)
                    except Exception as exc:
                        await q.put(StreamChunk(kind="error", content=str(exc), node_name=agent.name))

                    full_out = "".join(collected)
                    collected_outputs[agent.name] = full_out
                    await q.put(StreamChunk(kind="node_end", content=full_out, node_name=agent.name))
                    await q.put(None)

                tasks = [asyncio.create_task(_run_agent(a)) for a in agent_item]
                finished = 0
                while finished < active:
                    chunk = await q.get()
                    if chunk is None:
                        finished += 1
                    else:
                        yield chunk

                await asyncio.gather(*tasks, return_exceptions=True)

                sorted_agents = [a.name for a in agent_item]
                merged_output = "\n\n".join(
                    f"[Agent {name}]:\n{collected_outputs.get(name, '')}" for name in sorted_agents
                )
                if i < len(self.agents) - 1:
                    current_task = f"{task}\n\nPrior output:\n{merged_output}"
            else:
                agent = agent_item
                # ── Emit routing event if model_router is attached ────────────────
                if agent.model_router is not None:
                    try:
                        routing = agent.model_router.route(current_task)
                        is_local = getattr(routing, "is_local", False)
                        yield StreamChunk(
                            kind="routing",
                            node_name=agent.name,
                            metadata={
                                "model": getattr(routing, "model", ""),
                                "tier": getattr(routing, "tier", ""),
                                "is_local": is_local,
                                "cascade_escalation": False,
                                "reason": f"composite score routed to tier '{getattr(routing, 'tier', '')}'"
                                          f" ({'local' if is_local else 'cloud'})",
                            },
                        )
                    except Exception:
                        pass

                yield StreamChunk(kind="node_start", node_name=agent.name)

                # ── Stream tokens from the agent ──────────────────────────────────
                accumulated: list[str] = []
                try:
                    async for token in agent.stream(current_task):
                        accumulated.append(token)
                        yield StreamChunk(kind="token", content=token, node_name=agent.name)
                except Exception as exc:
                    yield StreamChunk(kind="error", content=str(exc), node_name=agent.name)
                    break

                full_output = "".join(accumulated)
                yield StreamChunk(kind="node_end", content=full_output, node_name=agent.name)

                # ── Chain: pass output to next agent ──────────────────────────────
                if i < len(self.agents) - 1:
                    current_task = f"{task}\n\nPrior output:\n{full_output}"

        yield StreamChunk(kind="done", node_name="workflow")

    def stream_multimodal(self, task: str, inputs: list[Any]) -> "Any":
        """Synchronous streaming generator for multi-modal pipelines.

        Like :meth:`stream` but passes *inputs* (images, documents, audio)
        to the first agent, then chains remaining agents on the text output.

        Example::

            from meshflow.multimodal.inputs import ImageInput
            from meshflow.core.streaming import stream_collect

            img = ImageInput.from_bytes(image_bytes, "image/png")
            text = stream_collect(wf.stream_multimodal("Describe this chart.", [img]))
        """
        import asyncio
        import queue as _queue
        import threading as _threading
        from meshflow.core.streaming import StreamChunk

        q: _queue.Queue[StreamChunk | None] = _queue.Queue(maxsize=256)

        async def _produce() -> None:
            try:
                async for chunk in self._stream_multimodal_async(task, inputs):
                    q.put(chunk)
            except Exception as exc:
                q.put(StreamChunk(kind="error", content=str(exc)))
            finally:
                q.put(None)

        def _run_loop() -> None:
            asyncio.run(_produce())

        t = _threading.Thread(target=_run_loop, daemon=True)
        t.start()
        while True:
            chunk = q.get()
            if chunk is None:
                break
            yield chunk

    async def _stream_multimodal_async(self, task: str, inputs: list[Any]) -> "Any":
        """Async generator for multimodal streaming."""
        from meshflow.core.streaming import StreamChunk

        if not self.agents:
            yield StreamChunk(kind="done", node_name="workflow")
            return

        # First agent: multimodal — accumulate its output, yield tokens
        first_item = self.agents[0]
        if isinstance(first_item, (list, tuple)):
            # Fallback: if first step is parallel, just run them (but typically multimodal has a single first agent)
            # We can stream them concurrently:
            q: asyncio.Queue[StreamChunk | None] = asyncio.Queue()
            active = len(first_item)
            collected_outputs = {}

            async def _run_agent(agent: Any) -> None:
                yield StreamChunk(kind="node_start", node_name=agent.name)
                res = await agent.run_multimodal(task, inputs, {})
                out = res.get("result", "") if isinstance(res, dict) else str(res)
                collected_outputs[agent.name] = out
                if out:
                    await q.put(StreamChunk(kind="token", content=out, node_name=agent.name))
                await q.put(StreamChunk(kind="node_end", content=out, node_name=agent.name))
                await q.put(None)

            tasks = [asyncio.create_task(_run_agent(a)) for a in first_item]
            finished = 0
            while finished < active:
                chunk = await q.get()
                if chunk is None:
                    finished += 1
                else:
                    yield chunk
            await asyncio.gather(*tasks, return_exceptions=True)
            sorted_agents = [a.name for a in first_item]
            first_output = "\n\n".join(
                f"[Agent {name}]:\n{collected_outputs.get(name, '')}" for name in sorted_agents
            )
        else:
            first = first_item
            yield StreamChunk(kind="node_start", node_name=first.name)
            first_result = await first.run_multimodal(task, inputs, {})
            first_output = first_result.get("result", "") if isinstance(first_result, dict) else str(first_result)
            if first_output:
                yield StreamChunk(kind="token", content=first_output, node_name=first.name)
            yield StreamChunk(kind="node_end", content=first_output, node_name=first.name)

        # Remaining agents: stream text
        if len(self.agents) > 1:
            chained_task = f"{task}\n\nPrior output:\n{first_output}"
            rest_wf = Workflow(cost_cap=self.cost_cap, mode=self.mode)
            rest_wf.agents = self.agents[1:]
            async for chunk in rest_wf._stream_async(chained_task):
                yield chunk
        else:
            yield StreamChunk(kind="done", node_name="workflow")

    def run(self, task: str) -> WorkflowResult:
        """Run the workflow sequentially and synchronously."""
        from meshflow.agents.base import sandbox_mode_var
        from meshflow.agents.team import Team
        from meshflow.core.schemas import policy_for_mode
        from meshflow.integrations._utils import run_sync

        is_sandbox = (self.mode == "sandbox")
        token = sandbox_mode_var.set(is_sandbox)
        try:
            if is_sandbox:
                for item in self.agents:
                    if isinstance(item, (list, tuple)):
                        for agent in item:
                            agent.mode = "sandbox"
                    else:
                        item.mode = "sandbox"

            has_parallel = any(isinstance(a, (list, tuple)) for a in self.agents)
            has_conditional = any(isinstance(a, _ConditionalBranch) for a in self.agents)
            if has_parallel or has_conditional or self.state is not None:
                return run_sync(self._run_async(task))

            budget_usd = self.cost_cap.usd if self.cost_cap else 5.0
            policy = policy_for_mode("standard", budget_usd=budget_usd)

            team = Team(
                name="simple_workflow_team",
                agents=self.agents,
                pattern="sequential",
                policy=policy,
                budget_usd=budget_usd,
            )
            return run_sync(team.run(task))
        finally:
            sandbox_mode_var.reset(token)

    async def _run_async(self, task: str) -> WorkflowResult:
        from meshflow.core.workflow import WorkflowResult
        import time as _time
        import asyncio
        import inspect
        import json

        start_time = _time.monotonic()
        total_cost = 0.0
        total_tokens = 0
        current_task = task
        accumulated = ""
        limit = self.cost_cap.usd if self.cost_cap else None
        blocked_nodes = []

        for i, agent_item in enumerate(self.agents):
            if limit is not None and total_cost > limit:
                if isinstance(agent_item, (list, tuple)):
                    blocked_nodes.extend(a.name for a in agent_item)
                else:
                    blocked_nodes.append(agent_item.name)
                continue

            if isinstance(agent_item, _ConditionalBranch):
                block_key = "__conditional__"
            elif isinstance(agent_item, (list, tuple)):
                block_key = tuple(a.name for a in agent_item)
            else:
                block_key = agent_item.name

            # Map inputs if state exists
            context = {"state": self.state} if self.state is not None else {}
            task_for_step = current_task
            if self.state is not None:
                input_map = self._input_maps.get(block_key)
                if input_map:
                    task_for_step = input_map(self.state)
                else:
                    state_data = self.state.model_dump() if hasattr(self.state, "model_dump") else self.state.dict()
                    state_str = json.dumps(state_data, indent=2)
                    task_for_step = f"{current_task}\n\nCurrent State:\n{state_str}"

            if isinstance(agent_item, _ConditionalBranch):
                if self.state is None:
                    try:
                        branch_key = agent_item.condition(current_task)
                    except Exception:
                        branch_key = agent_item.condition(None)
                else:
                    branch_key = agent_item.condition(self.state)

                chosen_item = agent_item.branches.get(branch_key)
                if chosen_item is None:
                    continue

                if isinstance(chosen_item, Workflow):
                    sub_res = await chosen_item._run_async(current_task)
                    accumulated = sub_res.output
                    total_tokens += sub_res.total_tokens
                    total_cost += sub_res.total_cost_usd
                    if self.state is not None and sub_res.state is not None:
                        sub_data = sub_res.state.model_dump() if hasattr(sub_res.state, "model_dump") else sub_res.state.dict()
                        for k, v in sub_data.items():
                            if hasattr(self.state, k):
                                try:
                                    setattr(self.state, k, v)
                                except Exception:
                                    pass
                elif isinstance(chosen_item, (list, tuple)):
                    async def _run_one(agent):
                        sig = inspect.signature(agent.run)
                        if "context" in sig.parameters:
                            res = await agent.run(task_for_step, context=context)
                        else:
                            res = await agent.run(task_for_step)
                        output = res.get("result", "") if isinstance(res, dict) else str(res)
                        tokens = res.get("tokens", 0) if isinstance(res, dict) else 0
                        cost = res.get("cost_usd", 0.0) if isinstance(res, dict) else 0.0
                        return agent.name, output, tokens, cost, res

                    results = await asyncio.gather(*[_run_one(a) for a in chosen_item])
                    merged_parts = []
                    for name, output, tokens, cost, res in results:
                        merged_parts.append(f"[Agent {name}]:\n{output}")
                        total_tokens += tokens
                        total_cost += cost
                        if self.state is not None:
                            agent_output_map = self._output_maps.get(name)
                            if agent_output_map:
                                agent_output_map(self.state, res)
                            else:
                                self._auto_merge_state(self.state, res)
                    accumulated = "\n\n".join(merged_parts)
                else:
                    agent = chosen_item
                    sig = inspect.signature(agent.run)
                    if "context" in sig.parameters:
                        res = await agent.run(task_for_step, context=context)
                    else:
                        res = await agent.run(task_for_step)
                    accumulated = res.get("result", "") if isinstance(res, dict) else str(res)
                    if isinstance(res, dict):
                        total_tokens += res.get("tokens", 0)
                        total_cost += res.get("cost_usd", 0.0)
                    if self.state is not None:
                        agent_output_map = self._output_maps.get(agent.name)
                        if agent_output_map:
                            agent_output_map(self.state, res)
                        else:
                            self._auto_merge_state(self.state, res)
            elif isinstance(agent_item, (list, tuple)):
                # Run parallel agents concurrently
                async def _run_one(agent):
                    sig = inspect.signature(agent.run)
                    if "context" in sig.parameters:
                        res = await agent.run(task_for_step, context=context)
                    else:
                        res = await agent.run(task_for_step)

                    output = res.get("result", "") if isinstance(res, dict) else str(res)
                    tokens = res.get("tokens", 0) if isinstance(res, dict) else 0
                    cost = res.get("cost_usd", 0.0) if isinstance(res, dict) else 0.0
                    return agent.name, output, tokens, cost, res

                results = await asyncio.gather(*[_run_one(a) for a in agent_item])

                # Combine outputs and usage
                merged_parts = []
                for name, output, tokens, cost, res in results:
                    merged_parts.append(f"[Agent {name}]:\n{output}")
                    total_tokens += tokens
                    total_cost += cost
                    
                    if self.state is not None:
                        agent_output_map = self._output_maps.get(name)
                        if agent_output_map:
                            agent_output_map(self.state, res)
                        else:
                            self._auto_merge_state(self.state, res)

                accumulated = "\n\n".join(merged_parts)

                if self.state is not None:
                    block_output_map = self._output_maps.get(block_key)
                    if block_output_map:
                        block_res = {name: res for name, _, _, _, res in results}
                        block_output_map(self.state, block_res)
            else:
                agent = agent_item
                sig = inspect.signature(agent.run)
                if "context" in sig.parameters:
                    res = await agent.run(task_for_step, context=context)
                else:
                    res = await agent.run(task_for_step)

                accumulated = res.get("result", "") if isinstance(res, dict) else str(res)
                if isinstance(res, dict):
                    total_tokens += res.get("tokens", 0)
                    total_cost += res.get("cost_usd", 0.0)

                # Map outputs if state exists
                if self.state is not None:
                    output_map = self._output_maps.get(block_key)
                    if output_map:
                        output_map(self.state, res)
                    else:
                        self._auto_merge_state(self.state, res)

            if i < len(self.agents) - 1:
                current_task = f"{task}\n\nPrior output:\n{accumulated}"

            if limit is not None and total_cost > limit:
                if isinstance(agent_item, (list, tuple)):
                    blocked_nodes.extend(a.name for a in agent_item)
                else:
                    blocked_nodes.append(agent_item.name)

        is_completed = True
        if limit is not None and total_cost > limit:
            is_completed = False
        if blocked_nodes:
            is_completed = False

        return WorkflowResult(
            run_id="",
            workflow_name="simple_workflow_team",
            completed=is_completed,
            output=accumulated,
            steps=[],
            total_cost_usd=round(total_cost, 6),
            total_tokens=total_tokens,
            total_carbon_gco2=0.0,
            duration_s=round(_time.monotonic() - start_time, 2),
            blocked_nodes=blocked_nodes,
            paused_nodes=[],
            skipped_nodes=[],
            ledger_db="",
            state=self.state,
        )

    def _auto_merge_state(self, state: Any, res: Any) -> None:
        """Automatically merge agent outcome back to state fields."""
        out_dict = {}
        raw_text = ""
        if isinstance(res, dict):
            # Check if dict directly has keys of interest
            for key in res:
                if hasattr(state, key) and key not in ["result", "tokens", "cost_usd"]:
                    out_dict[key] = res[key]
            raw_text = res.get("result", "")
        else:
            raw_text = str(res)

        # If direct fields weren't found, try parsing raw_text (or res['result']) as JSON
        if not out_dict and raw_text:
            from meshflow.core.output_validation import OutputValidator
            import json
            extracted = OutputValidator._extract_json(raw_text)
            if extracted:
                try:
                    out_dict = json.loads(extracted)
                except Exception:
                    pass

        merged_any = False
        if isinstance(out_dict, dict):
            for key, val in out_dict.items():
                if hasattr(state, key):
                    try:
                        setattr(state, key, val)
                        merged_any = True
                    except Exception:
                        pass

        if not merged_any and raw_text:
            for field in ["output", "latest_output", "result", "summary", "text"]:
                if hasattr(state, field):
                    try:
                        setattr(state, field, raw_text)
                        break
                    except Exception:
                        pass

    def run_multimodal(self, task: str, inputs: list[Any]) -> WorkflowResult:
        """Run the workflow with multi-modal inputs (images, documents, audio).

        Passes *inputs* to the first agent in the pipeline as multi-modal
        content blocks.  Subsequent agents receive the text output of the
        preceding step as their task (standard sequential chaining).

        Parameters
        ----------
        task:   Text prompt describing what to do with the inputs.
        inputs: List of :class:`~meshflow.multimodal.inputs.ImageInput`,
                :class:`~meshflow.multimodal.inputs.DocumentInput`, or
                :class:`~meshflow.multimodal.inputs.AudioInput` objects.

        Example::

            from meshflow import Workflow, Agent
            from meshflow.multimodal.inputs import ImageInput

            wf = Workflow()
            wf.add(Agent("analyst", model="claude-sonnet-4-6"))
            result = wf.run_multimodal(
                "Extract all text and tables from this chart.",
                [ImageInput("quarterly_chart.png")],
            )

        Returns the same :class:`~meshflow.agents.team.WorkflowResult` as
        :meth:`run`.
        """
        from meshflow.agents.base import sandbox_mode_var
        from meshflow.integrations._utils import run_sync

        is_sandbox = (self.mode == "sandbox")
        token = sandbox_mode_var.set(is_sandbox)
        try:
            return run_sync(self._run_multimodal_async(task, inputs))
        finally:
            sandbox_mode_var.reset(token)

    async def _run_multimodal_async(self, task: str, inputs: list[Any]) -> WorkflowResult:
        """Run the first agent with multimodal inputs, then chain as text."""
        from meshflow.agents.team import Team
        from meshflow.core.schemas import policy_for_mode
        from meshflow.core.policy import BudgetExceededError
        import asyncio

        budget_usd = self.cost_cap.usd if self.cost_cap else 5.0
        policy = policy_for_mode("standard", budget_usd=budget_usd)
        limit = self.cost_cap.usd if self.cost_cap else None

        # First agent: pass multimodal inputs directly
        first_result: dict[str, Any] = {}
        if self.agents:
            first_item = self.agents[0]
            if isinstance(first_item, (list, tuple)):
                # If first item is parallel, run them concurrently:
                async def _run_one(agent):
                    res = await agent.run_multimodal(task, inputs, {})
                    out = res.get("result", "") if isinstance(res, dict) else str(res)
                    tokens = res.get("tokens", 0) if isinstance(res, dict) else 0
                    cost = res.get("cost_usd", 0.0) if isinstance(res, dict) else 0.0
                    return agent.name, out, tokens, cost
                results = await asyncio.gather(*[_run_one(a) for a in first_item])
                merged_parts = []
                total_t = 0
                total_c = 0.0
                for name, out, tokens, cost in results:
                    merged_parts.append(f"[Agent {name}]:\n{out}")
                    total_t += tokens
                    total_c += cost
                first_result = {
                    "result": "\n\n".join(merged_parts),
                    "tokens": total_t,
                    "cost_usd": total_c,
                }
            else:
                first_result = await first_item.run_multimodal(task, inputs, {})

        first_cost = first_result.get("cost_usd", 0.0)
        if limit is not None and first_cost > limit:
            remaining = self.agents[1:]
            blocked = []
            for item in remaining:
                if isinstance(item, (list, tuple)):
                    blocked.extend(a.name for a in item)
                else:
                    blocked.append(item.name)
            return WorkflowResult(
                run_id="",
                workflow_name="multimodal_workflow",
                completed=False,
                output=first_result.get("result", ""),
                steps=[],
                total_cost_usd=first_cost,
                total_tokens=first_result.get("tokens", 0),
                total_carbon_gco2=0.0,
                duration_s=0.0,
                blocked_nodes=blocked,
                paused_nodes=[],
                skipped_nodes=[],
                ledger_db="",
            )

        # Remaining agents: chain as text (standard sequential / parallel)
        if len(self.agents) <= 1:
            return WorkflowResult(
                run_id="",
                workflow_name="multimodal_workflow",
                completed=True,
                output=first_result.get("result", ""),
                steps=[],
                total_cost_usd=first_result.get("cost_usd", 0.0),
                total_tokens=first_result.get("tokens", 0),
                total_carbon_gco2=0.0,
                duration_s=0.0,
                blocked_nodes=[],
                paused_nodes=[],
                skipped_nodes=[],
                ledger_db="",
            )

        chained_task = first_result.get("result", task)
        has_parallel = any(isinstance(a, (list, tuple)) for a in self.agents[1:])
        if not has_parallel:
            team = Team(
                name="multimodal_workflow_team",
                agents=self.agents[1:],
                pattern="sequential",
                policy=policy,
                budget_usd=budget_usd,
            )
            result = await team.run(chained_task)
            # Fold first-agent cost into result
            result.total_cost_usd += first_result.get("cost_usd", 0.0)
            result.total_tokens += first_result.get("tokens", 0)
            return result
        else:
            rest_wf = Workflow(cost_cap=self.cost_cap, mode=self.mode)
            rest_wf.agents = self.agents[1:]
            result = await rest_wf._run_async(chained_task)
            result.total_cost_usd += first_result.get("cost_usd", 0.0)
            result.total_tokens += first_result.get("tokens", 0)
            return result

    def batch_run(
        self,
        tasks: list[str],
        *,
        max_concurrency: int = 4,
    ) -> list[WorkflowResult]:
        """Run the workflow on multiple tasks in parallel.

        Executes up to *max_concurrency* tasks simultaneously.  Results are
        returned in the same order as *tasks* — a task that fails returns a
        :class:`~meshflow.agents.team.WorkflowResult` with
        ``status="failed"`` and the error message in ``output``.

        Parameters
        ----------
        tasks:           List of task strings to run.
        max_concurrency: Maximum simultaneous workflow executions (default 4).

        Example::

            results = wf.batch_run([
                "Summarise Q1 results",
                "Summarise Q2 results",
                "Summarise Q3 results",
                "Summarise Q4 results",
            ], max_concurrency=4)
            for r in results:
                print(r.output, r.total_cost_usd)
        """
        from meshflow.integrations._utils import run_sync
        return run_sync(self._batch_run_async(tasks, max_concurrency))

    async def _batch_run_async(
        self,
        tasks: list[str],
        max_concurrency: int,
    ) -> list[WorkflowResult]:
        import asyncio
        from meshflow.agents.team import Team
        from meshflow.core.schemas import policy_for_mode

        budget_usd = self.cost_cap.usd if self.cost_cap else 5.0
        policy = policy_for_mode("standard", budget_usd=budget_usd)
        sem = asyncio.Semaphore(max_concurrency)

        async def _one(task: str) -> WorkflowResult:
            async with sem:
                team = Team(
                    name="batch_team",
                    agents=self.agents,
                    pattern="sequential",
                    policy=policy,
                    budget_usd=budget_usd,
                )
                try:
                    return await team.run(task)
                except Exception as exc:
                    return WorkflowResult(
                        run_id="",
                        workflow_name="batch_team",
                        completed=False,
                        output=str(exc),
                        steps=[],
                        total_cost_usd=0.0,
                        total_tokens=0,
                        total_carbon_gco2=0.0,
                        duration_s=0.0,
                        blocked_nodes=[],
                        paused_nodes=[],
                        skipped_nodes=[],
                        ledger_db="",
                    )

        return list(await asyncio.gather(*[_one(t) for t in tasks]))

    def run_until(self, task: str, condition: Any, max_steps: int = 5) -> WorkflowResult:
        """Run the workflow repeatedly on *task* until *condition* is met.

        On subsequent iterations, the task is updated to include the prior output
        and a refinement directive.

        Parameters
        ----------
        task:       The original task.
        condition:  A quality condition. Can be:
                    - a float (threshold for confidence, e.g. 0.85)
                    - a string condition expression (e.g. "confidence >= 0.85")
                    - a callable/custom function (e.g. lambda result: ...)
                    - an assertion (which is also a callable or string)
        max_steps:  Maximum number of refinement iterations (defaults to 5).
        """
        from meshflow.integrations._utils import run_sync
        return run_sync(self._run_until_async(task, condition, max_steps))

    async def _run_until_async(self, task: str, condition: Any, max_steps: int) -> WorkflowResult:
        from meshflow.core.workflow import WorkflowResult
        import time as _time

        start_time = _time.monotonic()
        current_task = task
        last_result = None
        total_cost = 0.0
        total_tokens = 0
        limit = self.cost_cap.usd if self.cost_cap else None
        exceeded = False

        for step in range(1, max_steps + 1):
            if limit is not None and total_cost > limit:
                exceeded = True
                break

            # Run the entire workflow on current_task
            result = await self._run_workflow_once(current_task)
            last_result = result
            total_cost += result.total_cost_usd
            total_tokens += result.total_tokens

            # Evaluate condition
            if self._eval_loop_condition(result, condition):
                break

            # If not met, update current_task for the next refinement iteration
            current_task = (
                f"{task}\n\n"
                f"Previous attempt:\n{result.output}\n\n"
                f"Please refine and improve the previous output to meet the quality criteria."
            )

        if limit is not None and total_cost > limit:
            exceeded = True

        # Return the final result with accumulated usage stats
        return WorkflowResult(
            run_id=last_result.run_id if last_result else "",
            workflow_name=last_result.workflow_name if last_result else "run_until_workflow",
            completed=not exceeded,
            output=last_result.output if last_result else "",
            steps=last_result.steps if last_result else [],
            total_cost_usd=round(total_cost, 6),
            total_tokens=total_tokens,
            total_carbon_gco2=last_result.total_carbon_gco2 if last_result else 0.0,
            duration_s=round(_time.monotonic() - start_time, 2),
            blocked_nodes=last_result.blocked_nodes if last_result else [],
            paused_nodes=last_result.paused_nodes if last_result else [],
            skipped_nodes=last_result.skipped_nodes if last_result else [],
            ledger_db=last_result.ledger_db if last_result else "",
            state=self.state,
        )

    async def _run_workflow_once(self, task: str) -> WorkflowResult:
        has_parallel = any(isinstance(a, (list, tuple)) for a in self.agents)
        has_conditional = any(isinstance(a, _ConditionalBranch) for a in self.agents)
        if has_parallel or has_conditional or self.state is not None:
            return await self._run_async(task)
        else:
            from meshflow.agents.base import sandbox_mode_var
            from meshflow.agents.team import Team
            from meshflow.core.schemas import policy_for_mode

            is_sandbox = (self.mode == "sandbox")
            token = sandbox_mode_var.set(is_sandbox)
            try:
                if is_sandbox:
                    for agent in self.agents:
                        agent.mode = "sandbox"

                budget_usd = self.cost_cap.usd if self.cost_cap else 5.0
                policy = policy_for_mode("standard", budget_usd=budget_usd)

                team = Team(
                    name="simple_workflow_team",
                    agents=self.agents,
                    pattern="sequential",
                    policy=policy,
                    budget_usd=budget_usd,
                )
                return await team.run(task)
            finally:
                sandbox_mode_var.reset(token)

    def _eval_loop_condition(self, result: WorkflowResult, condition: Any) -> bool:
        if condition is None:
            return True

        output_content = result.output or ""

        # 1. If condition is a callable
        if callable(condition):
            try:
                return bool(condition(result))
            except Exception:
                try:
                    return bool(condition(output_content))
                except Exception:
                    return False

        # Parse confidence score from output_content to use in float or string conditions
        from meshflow.agents.team import _parse_confidence
        confidence = _parse_confidence(output_content)

        # Also try to extract stated confidence from the last step record
        if result.steps:
            last_step = result.steps[-1]
            if hasattr(last_step, "output") and hasattr(last_step.output, "confidence"):
                confidence = max(confidence, last_step.output.confidence)
            elif hasattr(last_step, "record") and hasattr(last_step.record, "uncertainty"):
                # in case uncertainty composite score is present (it represents confidence/uncertainty)
                pass

        # 2. If condition is a float
        if isinstance(condition, (int, float)):
            return confidence >= float(condition)

        # 3. If condition is a string (e.g. "confidence >= 0.85" or "CONFIDENCE score")
        if isinstance(condition, str):
            cond_str = condition.strip().lower()
            # If the string contains a relational operator (e.g., >=, >, ==, etc.)
            import re
            m = re.match(r"(?:confidence\s*)?(>=|<=|>|<|==)\s*([0-9]*\.?[0-9]+)", cond_str)
            if m:
                op, val_str = m.groups()
                val = float(val_str)
                if op == ">=":
                    return confidence >= val
                elif op == "<=":
                    return confidence <= val
                elif op == ">":
                    return confidence > val
                elif op == "<":
                    return confidence < val
                elif op == "==":
                    return confidence == val

            # Simple substring checking in the output content as an assertion
            return condition in output_content

        return False
