"""RewindEngine — interactive time-travel debugger for MeshFlow runs.

Closes the LangGraph "time-travel debugging" gap: rewind to any checkpoint
(by step index or step_id), optionally override model or system prompt, and
re-run the workflow from that point forward.

Usage::

    from meshflow.core.time_travel import RewindEngine

    engine = RewindEngine(ledger_db="meshflow_runs.db")
    steps = await engine.list_steps("run-abc123")

    # Inspect
    for s in steps:
        print(s["idx"], s["node_id"], s["ok"], s["output_preview"])

    # Rewind to step 3, swap model, re-run
    result = await engine.rewind(
        run_id="run-abc123",
        to_step=3,
        model_override="claude-haiku-4-5-20251001",
        prompt_override="Be more concise.",
    )
    print(result.output)

CLI (added to meshflow replay)::

    meshflow replay <run_id> --rewind 3 --model claude-haiku-4-5-20251001
    meshflow replay <run_id> --rewind step_id_abc123 --prompt "Be concise"
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StepSnapshot:
    """Lightweight summary of one ledger step — used by the rewind UI."""

    idx: int
    step_id: str
    node_id: str
    node_kind: str
    ok: bool
    blocked: bool
    cost_usd: float
    tokens_used: int
    duration_ms: float
    uncertainty: float
    output_preview: str
    timestamp: str


@dataclass
class RewindResult:
    """Outcome of a rewind-and-re-run operation."""

    original_run_id: str
    rewind_run_id: str
    rewound_to_step: int
    model_override: str
    prompt_override: str
    output: str
    completed: bool
    steps_replayed: int
    total_cost_usd: float
    total_tokens: int
    forked_context: dict[str, Any] = field(default_factory=dict)


class RewindEngine:
    """Interactive time-travel debugger.

    Loads the step history for a past run, lets you pick any step as the
    rewind point, and re-runs the workflow from there — optionally with a
    different model or system prompt.

    Parameters
    ----------
    ledger_db:  Path to the SQLite ledger (same ``--db`` used by ``meshflow run``).
    """

    def __init__(self, ledger_db: str = "meshflow_runs.db") -> None:
        self._db = ledger_db

    # ── Inspection ─────────────────────────────────────────────────────────────

    async def list_steps(self, run_id: str) -> list[StepSnapshot]:
        """Return an ordered list of step snapshots for *run_id*."""
        from meshflow.core.ledger import ReplayLedger

        ledger = ReplayLedger(self._db)
        raw = await ledger.get_run(run_id)
        snapshots = []
        for idx, step in enumerate(raw, start=1):
            out = step.get("output", "") or ""
            snapshots.append(StepSnapshot(
                idx=idx,
                step_id=step.get("step_id", ""),
                node_id=step.get("node_id", ""),
                node_kind=step.get("node_kind", ""),
                ok=not step.get("blocked", False),
                blocked=bool(step.get("blocked", False)),
                cost_usd=float(step.get("cost_usd", 0.0)),
                tokens_used=int(step.get("tokens_used", 0)),
                duration_ms=float(step.get("duration_ms", 0.0)),
                uncertainty=float(step.get("uncertainty", 0.0)),
                output_preview=out[:120].replace("\n", " "),
                timestamp=step.get("timestamp", ""),
            ))
        return snapshots

    async def get_checkpoint_context(self, run_id: str) -> dict[str, Any] | None:
        """Return the serialised context from the HITL checkpoint (if any)."""
        from meshflow.core.ledger import ReplayLedger

        ledger = ReplayLedger(self._db)
        return await ledger.load_checkpoint_data(run_id)

    # ── Rewind ─────────────────────────────────────────────────────────────────

    async def rewind(
        self,
        run_id: str,
        to_step: int,
        *,
        workflow_yaml: str = "",
        node_registry: dict[str, Any] | None = None,
        model_override: str = "",
        prompt_override: str = "",
        context_patch: dict[str, Any] | None = None,
    ) -> RewindResult:
        """Rewind *run_id* to *to_step* (1-based) and re-run from there.

        Parameters
        ----------
        run_id:          Original run to rewind.
        to_step:         1-based index to rewind to.  Steps before this index
                         are replayed from the ledger (their outputs injected
                         into context); from to_step onward the workflow executes
                         live with the overrides.
        workflow_yaml:   Path to the workflow YAML.  Required if node_registry is None.
        node_registry:   Optional registry for resolving node refs.
        model_override:  Swap every agent's model to this value (e.g. "claude-haiku-4-5-20251001").
        prompt_override: Prepend this text to every agent's system prompt.
        context_patch:   Extra key/value pairs injected into the shared context.
        """
        from meshflow.core.ledger import ReplayLedger
        from meshflow.core.runtime import StepRuntime

        ledger = ReplayLedger(self._db)
        steps = await self.list_steps(run_id)

        if not steps:
            raise ValueError(f"No steps found for run_id={run_id!r}")

        to_step = max(1, min(to_step, len(steps)))
        rewind_run_id = f"{run_id}/rewind_{uuid.uuid4().hex[:6]}"

        # ── Reconstruct context from steps BEFORE the rewind point ────────────
        prior_steps = steps[:to_step - 1]
        ctx: dict[str, Any] = {}
        if context_patch:
            ctx.update(context_patch)

        # Replay the ledger outputs for all prior steps into context
        raw_steps = await ledger.get_run(run_id)
        for i, raw in enumerate(raw_steps[: to_step - 1], start=1):
            node_id = raw.get("node_id", f"step_{i}")
            out = raw.get("output", "") or ""
            if out:
                ctx[f"{node_id}_output"] = out

        ctx["run_id"] = rewind_run_id
        ctx["_rewound_from"] = run_id
        ctx["_rewound_at_step"] = to_step

        # ── Load workflow from checkpoint or YAML ─────────────────────────────
        if workflow_yaml:
            from meshflow.core.workflow import WorkflowDefinition
            wf = WorkflowDefinition.from_yaml(workflow_yaml, node_registry)
        else:
            # Best-effort: try to load checkpoint YAML path
            checkpoint = await ledger.load_checkpoint_data(run_id)
            wf_yaml = (checkpoint or {}).get("workflow_yaml", "")
            if wf_yaml:
                from meshflow.core.workflow import WorkflowDefinition
                wf = WorkflowDefinition.from_yaml(wf_yaml, node_registry)
            else:
                raise ValueError(
                    "Cannot rewind without a workflow_yaml path. "
                    "Pass workflow_yaml= to RewindEngine.rewind()."
                )

        # ── Apply model / prompt overrides ────────────────────────────────────
        if model_override or prompt_override:
            _patch_workflow_nodes(wf, model_override=model_override, prompt_override=prompt_override)

        # ── Re-execute from to_step onward ────────────────────────────────────
        # Restrict the workflow to only the nodes from to_step onward
        remaining_node_ids = {s.node_id for s in steps[to_step - 1:]}
        _restrict_workflow(wf, remaining_node_ids)

        policy = wf.policy
        runtime = StepRuntime(policy=policy, run_id=rewind_run_id, ledger=ledger)

        task = (await ledger.run_summary(run_id) or {}).get("task", "")
        result = await wf.run(task=task, runtime=runtime, context=ctx)

        return RewindResult(
            original_run_id=run_id,
            rewind_run_id=rewind_run_id,
            rewound_to_step=to_step,
            model_override=model_override,
            prompt_override=prompt_override,
            output=result.output,
            completed=result.completed,
            steps_replayed=len(result.steps),
            total_cost_usd=result.total_cost_usd,
            total_tokens=result.total_tokens,
            forked_context=ctx,
        )

    # ── Branching ──────────────────────────────────────────────────────────────

    async def branch(
        self,
        run_id: str,
        at_step: int,
        variants: list[dict[str, Any]],
        workflow_yaml: str,
        node_registry: dict[str, Any] | None = None,
    ) -> list[RewindResult]:
        """Fork the run at *at_step* and run N parallel variants.

        Parameters
        ----------
        variants:  Each entry is a dict with optional keys
                   ``model_override``, ``prompt_override``, ``context_patch``.

        Returns one :class:`RewindResult` per variant.
        """
        tasks = [
            self.rewind(
                run_id,
                to_step=at_step,
                workflow_yaml=workflow_yaml,
                node_registry=node_registry,
                model_override=v.get("model_override", ""),
                prompt_override=v.get("prompt_override", ""),
                context_patch=v.get("context_patch"),
            )
            for v in variants
        ]
        return list(await asyncio.gather(*tasks))


# ── Internal helpers ──────────────────────────────────────────────────────────

def _patch_workflow_nodes(
    wf: Any,
    *,
    model_override: str = "",
    prompt_override: str = "",
) -> None:
    """Patch every native node in *wf* with model/prompt overrides."""
    for node in wf._nodes.values():
        if node.kind.value == "native":
            runner = node._runner
            if runner is None:
                continue
            # Patch the underlying agent inside the closure if accessible
            agent = getattr(runner, "__self__", None) or _extract_agent_from_closure(runner)
            if agent is None:
                continue
            if model_override and hasattr(agent, "config"):
                agent.config.model = model_override
            if prompt_override and hasattr(agent, "config"):
                existing = agent.config.system_prompt or ""
                agent.config.system_prompt = f"{prompt_override}\n\n{existing}" if existing else prompt_override


def _extract_agent_from_closure(fn: Any) -> Any:
    """Try to extract an agent object from a closure's free variables."""
    closure = getattr(fn, "__closure__", None)
    if closure is None:
        return None
    for cell in closure:
        try:
            val = cell.cell_contents
            if hasattr(val, "config") and hasattr(val, "step"):
                return val
        except ValueError:
            pass
    return None


def _restrict_workflow(wf: Any, node_ids: set[str]) -> None:
    """Remove nodes NOT in *node_ids* from the workflow (rewind to partial DAG)."""
    to_remove = [nid for nid in list(wf._nodes.keys()) if nid not in node_ids]
    for nid in to_remove:
        wf._nodes.pop(nid, None)
    wf._edges = [e for e in wf._edges if e.from_node in node_ids and e.to_node in node_ids]
    if wf._entry not in node_ids:
        remaining = list(wf._nodes.keys())
        wf._entry = remaining[0] if remaining else ""
    wf._terminal = [t for t in wf._terminal if t in node_ids]


__all__ = ["RewindEngine", "RewindResult", "StepSnapshot"]
