"""Supervisor pattern — orchestrator plans, workers execute.

The orchestrator (an Agent with role=orchestrator) creates a DelegationPlan via
run_typed() then dispatches subtasks to named workers.  After each round it
synthesises the results, marks the task DONE, or delegates another round.

Usage::

    from meshflow.agents.supervisor import Supervisor
    from meshflow import Agent

    planner = Agent(name="planner", role="orchestrator")
    writer  = Agent(name="writer",  role="executor")
    critic  = Agent(name="critic",  role="critic")

    sv = Supervisor(planner, [writer, critic])
    result = await sv.run("Write a HIPAA breach notification letter")
    print(result.final_answer)
    print(f"{result.rounds} rounds, {result.total_tokens} tokens")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from meshflow.agents.builder import Agent


# ── Pydantic schemas (lazy import so pydantic is truly optional) ──────────────

def _make_plan_model() -> Any:
    try:
        from pydantic import BaseModel

        class _Step(BaseModel):
            worker_name: str
            subtask: str

        class _Plan(BaseModel):
            steps: list[_Step]

        return _Plan
    except ImportError:
        return None


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class SupervisorResult:
    final_answer: str
    worker_outputs: dict[str, str]
    rounds: int
    total_tokens: int
    total_cost_usd: float


# ── Supervisor ────────────────────────────────────────────────────────────────

class Supervisor:
    """Orchestrator → workers delegation pattern.

    Parameters
    ----------
    orchestrator:
        Agent with role=orchestrator.  Plans and synthesises.
    workers:
        Worker agents.  The orchestrator addresses them by name.
    max_rounds:
        Maximum plan → execute → review cycles (default 3).
    skill_registry:
        Optional :class:`~meshflow.agents.skill_registry.AgentSkillRegistry`.
        When provided, the supervisor uses BM25 or LLM-driven selection to
        pick the best worker for each subtask instead of hard-coding names.
    use_llm_delegation:
        When True (and *skill_registry* is set), uses the orchestrator LLM to
        make the final worker selection.  Default False (BM25 only).
    """

    def __init__(
        self,
        orchestrator: Agent,
        workers: list[Agent],
        max_rounds: int = 3,
        skill_registry: Any = None,
        use_llm_delegation: bool = False,
    ) -> None:
        self._orchestrator = orchestrator
        self._workers: dict[str, Agent] = {w.name: w for w in workers}
        self._max_rounds = max_rounds
        self._skill_registry = skill_registry
        self._use_llm_delegation = use_llm_delegation

    async def run(self, task: str, context: dict[str, Any] | None = None) -> SupervisorResult:
        ctx = context or {}
        worker_outputs: dict[str, str] = {}
        total_tokens = 0
        total_cost = 0.0
        answer = ""
        worker_list = ", ".join(self._workers)

        PlanModel = _make_plan_model()

        for round_n in range(self._max_rounds):
            # ── Plan ──────────────────────────────────────────────────────────
            plan_task = (
                f"Original task: {task}\n"
                f"Available workers: {worker_list}\n"
                f"Results so far: {worker_outputs}\n\n"
                "Create a delegation plan to complete the task."
            )

            steps_to_run: list[tuple[str, str]] = []

            if PlanModel is not None:
                try:
                    plan = await self._orchestrator.run_typed(plan_task, PlanModel, ctx)
                    steps_to_run = [(s.worker_name, s.subtask) for s in plan.steps]
                except Exception:
                    steps_to_run = []

            if not steps_to_run:
                # Fallback: ask orchestrator for plain instructions, delegate to all workers
                raw = await self._orchestrator.run(plan_task, ctx)
                total_tokens += raw.get("tokens", 0)
                total_cost += raw.get("cost_usd", 0.0)
                steps_to_run = [(name, task) for name in self._workers]

            # ── Execute ───────────────────────────────────────────────────────
            for worker_name, subtask in steps_to_run:
                # Skill-registry delegation: override the worker_name if registry available
                if self._skill_registry is not None:
                    if self._use_llm_delegation:
                        profile = await self._skill_registry.select_llm(
                            subtask, self._orchestrator
                        )
                    else:
                        profile = self._skill_registry.select_best(subtask)
                    if profile and profile.agent_name in self._workers:
                        worker_name = profile.agent_name

                worker = self._workers.get(worker_name)
                if worker is None:
                    worker_outputs[worker_name] = f"[worker '{worker_name}' not found]"
                    continue
                step_ctx = {**ctx, "previous_results": worker_outputs}
                out = await worker.run(subtask, step_ctx)
                total_tokens += out.get("tokens", 0)
                total_cost += out.get("cost_usd", 0.0)
                worker_outputs[worker_name] = out.get("result", "")

            # ── Synthesise ────────────────────────────────────────────────────
            synth_task = (
                f"Original task: {task}\n"
                f"Worker outputs:\n"
                + "\n".join(f"  [{k}]: {v}" for k, v in worker_outputs.items())
                + "\n\nSynthesise a final answer. "
                "If the task is fully complete, begin your response with 'DONE: '. "
                "Otherwise describe what remains."
            )
            synth = await self._orchestrator.run(synth_task, ctx)
            total_tokens += synth.get("tokens", 0)
            total_cost += synth.get("cost_usd", 0.0)
            answer = synth.get("result", "")

            if answer.lstrip().upper().startswith("DONE:"):
                colon_pos = answer.index(":")
                final = answer[colon_pos + 1 :].strip()
                return SupervisorResult(
                    final_answer=final,
                    worker_outputs=worker_outputs,
                    rounds=round_n + 1,
                    total_tokens=total_tokens,
                    total_cost_usd=total_cost,
                )

        return SupervisorResult(
            final_answer=answer or "[max rounds reached without a final answer]",
            worker_outputs=worker_outputs,
            rounds=self._max_rounds,
            total_tokens=total_tokens,
            total_cost_usd=total_cost,
        )
