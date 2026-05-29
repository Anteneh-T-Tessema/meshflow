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
        ctx: dict[str, Any],
        step_outcomes: dict[str, RuntimeOutcome],
    ) -> tuple[list[str], list[str]]:
        """Return (nodes_ready_to_run, nodes_that_can_be_skipped).

        A node is ready when:
          - all its predecessors are done (completed | skipped), AND
          - at least one incoming edge from a *completed* predecessor fires.

        A node is skipped when all predecessors are done but no incoming edge
        from a completed predecessor fires (all conditions evaluated False).
        """
        done = completed | skipped
        ready: list[str] = []
        newly_skipped: list[str] = []

        for node_id in self._nodes:
            if node_id in done:
                continue
            preds = self._predecessors(node_id)
            if not preds:
                ready.append(node_id)
                continue
            if not all(p in done for p in preds):
                continue  # still waiting on an upstream node
            any_fires = any(
                self._condition_fires(e, ctx, step_outcomes)
                for e in self._edges_to(node_id)
                if e.from_node in completed  # skipped predecessors don't route forward
            )
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

    async def run(
        self,
        task: str,
        runtime: StepRuntime,
        context: dict[str, Any] | None = None,
        event_bus: WorkflowEventBus | None = None,
    ) -> WorkflowResult:
        """Execute the workflow with full StepRuntime governance on every node.

        Uses a dynamic ready-queue so conditional edges are respected at
        runtime. Nodes with no dependency between them run concurrently via
        asyncio.gather(). All governance (guardian, budget, HITL, ledger)
        fires per node regardless of parallelism or routing.

        ``event_bus`` receives structured WorkflowEvents for every state
        transition. Defaults to the process-wide ``global_event_bus``.
        """
        bus = event_bus if event_bus is not None else global_event_bus
        run_id = runtime._run_id
        start = time.monotonic()
        ctx = dict(context or {})
        ctx["task"] = task
        # Propagate workflow version pin so it lands in ledger step records
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
        step_outcomes: dict[str, RuntimeOutcome] = {}

        ready, _ = self._compute_ready(completed, skipped, ctx, step_outcomes)

        while ready:
            level_nodes = [self._nodes[nid] for nid in ready if nid in self._nodes]
            if not level_nodes:
                break

            # Emit step_start for all nodes in this level
            for nd in level_nodes:
                await bus.emit(WorkflowEvent(
                    kind=EventKind.STEP_START,
                    run_id=run_id,
                    node_id=nd.id,
                    data={"kind": nd.kind.value},
                ))

            # Snapshot context: all parallel nodes see the same input state.
            # Each gets its own copy so runtime can write back internal keys
            # (_upstream_confidence etc.) without cross-node interference.
            ctx_snapshot = ctx.copy()

            # When 2+ nodes run in parallel, deduplicate large repeated context
            # values to avoid paying for the same tokens N times.
            _dedup = None
            if len(level_nodes) > 1:
                from meshflow.agents.context_dedup import ContextDeduplicator
                _dedup = ContextDeduplicator()

            async def _run_node(nd: MeshNode) -> RuntimeOutcome:
                node_ctx = ctx_snapshot.copy()
                if _dedup is not None:
                    node_ctx = _dedup.deduplicate(node_ctx, agent_name=nd.id)

                # Build output validator from node metadata
                from meshflow.core.output_validation import OutputValidator, validator_from_yaml
                schema_cfg = nd.metadata.get("output_schema")
                validator = (
                    OutputValidator(schema=schema_cfg) if schema_cfg else None
                )
                retry_on_fail  = nd.metadata.get("retry_on_fail", False)
                max_retries    = int(nd.metadata.get("max_retries", 1))

                current_task = task
                for attempt in range(max(1, max_retries + 1 if retry_on_fail else 1)):
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
                        break  # governance block — no retry
                    if validator is None:
                        break  # no schema — accept as-is
                    vresult = validator.validate(outcome.output.content)
                    if vresult.valid:
                        break  # valid output
                    if attempt < max_retries:
                        # Build retry prompt and re-run
                        current_task = validator.retry_prompt(outcome.output.content, vresult.error)
                    else:
                        # Max retries reached — mark as blocked
                        from meshflow.core.runtime import RuntimeOutcome as RO, StepRecord
                        import datetime, uuid as _uuid
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
                        outcome = RO(
                            ok=False,
                            node_id=nd.id,
                            node_kind=outcome.record.node_kind,
                            output=outcome.output,
                            record=blk_rec,
                            blocked_by=f"output_schema:{vresult.error[:80]}",
                            paused_for_human=False,
                            human_context={},
                        )
                return outcome

            level_outcomes: list[RuntimeOutcome] = list(
                await asyncio.gather(*[_run_node(nd) for nd in level_nodes])
            )

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
                    # Persist durable checkpoint so the workflow survives restarts
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
                    if outcome.output.structured:
                        ctx.update(
                            {
                                k: v
                                for k, v in outcome.output.structured.items()
                                if not k.startswith("_")
                            }
                        )

            if blocked_nodes or paused_nodes:
                break

            # Check loop edges: if a completed src triggers a loop back to dst,
            # remove dst from completed so it re-enters the ready queue.
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

            # Propagate skips transitively until stable: if B is skipped, C
            # (which depends only on B) also becomes skipped without running.
            ready, newly_skipped = self._compute_ready(completed, skipped, ctx, step_outcomes)
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
                ready, newly_skipped = self._compute_ready(completed, skipped, ctx, step_outcomes)

        final_output = ""
        for outcome in reversed(steps):
            if outcome.ok and outcome.output.content:
                final_output = outcome.output.content
                break

        total_cost = sum(s.record.cost_usd for s in steps)
        total_tokens = sum(s.record.tokens_used for s in steps)
        total_carbon = sum(s.record.carbon_gco2 for s in steps)
        duration = round(time.monotonic() - start, 2)

        terminal_kind = (
            EventKind.WORKFLOW_FAILED
            if (blocked_nodes and not paused_nodes)
            else EventKind.WORKFLOW_COMPLETE
        )
        await bus.emit(WorkflowEvent(
            kind=terminal_kind,
            run_id=run_id,
            data={
                "completed": not blocked_nodes and not paused_nodes,
                "total_cost_usd": round(total_cost, 6),
                "total_tokens": total_tokens,
                "duration_s": duration,
                "blocked_nodes": blocked_nodes,
                "paused_nodes": paused_nodes,
            },
        ))

        ledger_db = getattr(runtime._ledger, "_db_path", ":memory:")

        return WorkflowResult(
            run_id=run_id,
            workflow_name=self.name,
            completed=not blocked_nodes and not paused_nodes,
            output=final_output,
            steps=steps,
            total_cost_usd=round(total_cost, 6),
            total_tokens=total_tokens,
            total_carbon_gco2=round(total_carbon, 4),
            duration_s=duration,
            blocked_nodes=blocked_nodes,
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
        """Async generator that yields :class:`~meshflow.core.streaming.StreamChunk` objects.

        Combines step-lifecycle events (``node_start`` / ``node_end``) with
        token-level streaming from native agent nodes.  Non-native nodes emit
        lifecycle chunks only (no per-token granularity).

        Usage::

            async for chunk in wf.stream(task="Analyse Q3", runtime=runtime):
                if chunk.kind == "token":
                    print(chunk.content, end="", flush=True)
                elif chunk.kind == "node_end":
                    print(f"\\n[{chunk.node_name}] cost=${chunk.metadata.get('cost_usd',0):.5f}")
        """
        from meshflow.core.streaming import StreamChunk

        ctx = dict(context or {})
        ctx["task"] = task
        if self.yaml_sha256:
            ctx["_workflow_sha256"] = self.yaml_sha256
            ctx["_workflow_version"] = self.version

        run_id = runtime._run_id
        completed: set[str] = set()
        skipped: set[str] = set()
        step_outcomes: dict[str, Any] = {}

        yield StreamChunk(
            kind="task_start",
            node_name=self.name,
            metadata={"run_id": run_id, "task": task[:120]},
        )

        ready, _ = self._compute_ready(completed, skipped, ctx, step_outcomes)

        while ready:
            level_nodes = [self._nodes[nid] for nid in ready if nid in self._nodes]
            if not level_nodes:
                break
            ctx_snapshot = ctx.copy()

            for nd in level_nodes:
                yield StreamChunk(kind="node_start", node_name=nd.id,
                                  metadata={"kind": nd.kind.value, "run_id": run_id})
                try:
                    tokens_streamed = False

                    # Attempt token-level streaming for native nodes
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
                                pass  # governance run below will still execute

                    # Governance kernel run (audit/ledger/HITL — always executed)
                    from meshflow.core.node import NodeInput
                    outcome = await runtime.run(
                        nd,
                        NodeInput(
                            task=task,
                            context=ctx_snapshot.copy(),
                            attachments=nd.metadata.get("attachments", []),
                        ),
                        ctx_snapshot.copy(),
                    )
                    step_outcomes[nd.id] = outcome

                    if outcome.ok:
                        completed.add(nd.id)
                        if outcome.output.content:
                            ctx[f"{nd.id}_output"] = outcome.output.content
                        if outcome.output.structured:
                            ctx.update({
                                k: v for k, v in outcome.output.structured.items()
                                if not k.startswith("_")
                            })
                        # Emit final output as token if nothing was streamed live
                        if not tokens_streamed and outcome.output.content:
                            yield StreamChunk(
                                kind="token",
                                content=outcome.output.content,
                                node_name=nd.id,
                            )

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
                except Exception as exc:
                    yield StreamChunk(kind="error", content=str(exc), node_name=nd.id)
                    break

            ready, newly_skipped = self._compute_ready(completed, skipped, ctx, step_outcomes)
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

        ready, newly_skipped = self._compute_ready(completed, skipped, ctx, step_outcomes)
        while newly_skipped:
            skipped.update(newly_skipped)
            skipped_nodes.extend(newly_skipped)
            ready, newly_skipped = self._compute_ready(completed, skipped, ctx, step_outcomes)

        while ready:
            level_nodes = [self._nodes[nid] for nid in ready if nid in self._nodes]
            if not level_nodes:
                break

            ctx_snapshot = ctx.copy()

            async def _run_node(nd: MeshNode) -> RuntimeOutcome:
                return await runtime.run(
                    nd,
                    NodeInput(
                        task=task,
                        context=ctx_snapshot.copy(),
                        attachments=nd.metadata.get("attachments", []),
                    ),
                    ctx_snapshot.copy(),
                )

            level_outcomes: list[RuntimeOutcome] = list(
                await asyncio.gather(*[_run_node(nd) for nd in level_nodes])
            )

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
                else:
                    completed.add(node_id)
                    if outcome.output.content:
                        ctx[f"{node_id}_output"] = outcome.output.content
                    if outcome.output.structured:
                        ctx.update(
                            {
                                k: v
                                for k, v in outcome.output.structured.items()
                                if not k.startswith("_")
                            }
                        )

            if blocked_nodes or paused_nodes:
                break

            ready, newly_skipped = self._compute_ready(completed, skipped, ctx, step_outcomes)
            while newly_skipped:
                skipped.update(newly_skipped)
                skipped_nodes.extend(newly_skipped)
                ready, newly_skipped = self._compute_ready(completed, skipped, ctx, step_outcomes)

        # Delete checkpoint on clean completion
        if not blocked_nodes and not paused_nodes:
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
            completed=not blocked_nodes and not paused_nodes,
            output=final_output,
            steps=steps,
            total_cost_usd=round(total_cost, 6),
            total_tokens=total_tokens,
            total_carbon_gco2=round(total_carbon, 4),
            duration_s=round(time.monotonic() - start, 2),
            blocked_nodes=blocked_nodes,
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

        wf = cls(
            name=data.get("name", "unnamed"),
            version=str(data.get("version", "1")),
            policy=pol,
        )

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
        wf.yaml_sha256 = _yaml_sha256    # SHA-256 of the YAML for exact-replay version pinning
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
