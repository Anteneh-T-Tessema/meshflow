"""SpawnableAgent — dynamic sub-agent spawning at runtime.

Implements the "agent system" pattern (Google Gemini-style): a top-level agent
analyses the incoming task, matches it against a set of :class:`SpawnRule`
specifications, and creates specialised child agents on the fly.  Child agents
run under the same governance kernel (StepRuntime / policy) as any other
MeshFlow agent.

Usage::

    from meshflow.agents.spawnable import SpawnableAgent, SpawnRule, SpawnConfig

    config = SpawnConfig(
        rules=[
            SpawnRule("code",    keywords=["code", "python", "function"],  role="executor"),
            SpawnRule("research",keywords=["research", "find", "analyse"],  role="researcher"),
            SpawnRule("review",  keywords=["review", "audit", "check"],     role="critic"),
        ],
        fallback_role="executor",
        parallel=True,           # run matched rules in parallel when True
    )

    agent = SpawnableAgent("orchestrator", spawn_config=config)
    result = agent.run("Write a Python function and review it for security flaws.")
    print(result.output)         # aggregated output from spawned agents

Governance
----------
Each spawned child agent inherits the parent's policy and mode so governance
invariants (cost caps, PII blocking, audit ledger) apply transparently.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any


# ── SpawnRule ─────────────────────────────────────────────────────────────────


@dataclass
class SpawnRule:
    """Describes when to spawn a sub-agent and how to configure it.

    Attributes
    ----------
    name:
        A human-readable label for this rule (used in logs / tracing).
    keywords:
        If *any* of these words appear (case-insensitive) in the task, the
        rule is considered a match.
    pattern:
        A regex pattern string.  If provided, it is tested against the task
        in addition to (or instead of) *keywords*.  A rule matches if either
        *keywords* **or** *pattern* fires.
    role:
        Role string for the spawned agent — passed directly to
        ``Agent(role=...)``.
    tools:
        Optional list of Tool objects to give the spawned agent.
    skills:
        Optional list of built-in skill names (e.g. ``["python", "security"]``).
    system_prompt:
        Override the default role system prompt for this sub-agent.
    model:
        Model string for the spawned agent.  Defaults to the parent's model.
    """

    name: str
    keywords: list[str] = field(default_factory=list)
    pattern: str = ""
    role: str = "executor"
    tools: list[Any] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    system_prompt: str = ""
    model: str = ""

    def matches(self, task: str) -> bool:
        task_lower = task.lower()
        if any(kw.lower() in task_lower for kw in self.keywords):
            return True
        if self.pattern:
            return bool(re.search(self.pattern, task, re.IGNORECASE))
        return False


# ── SpawnConfig ───────────────────────────────────────────────────────────────


@dataclass
class SpawnConfig:
    """Collection of spawn rules plus orchestration settings.

    Attributes
    ----------
    rules:
        Ordered list of :class:`SpawnRule` objects.  All matching rules fire
        (not just the first).
    fallback_role:
        Role to use when *no* rule matches.  If empty, the parent agent
        handles the task directly without spawning.
    parallel:
        When *True* (default), matched sub-agents run concurrently via
        ``asyncio.gather``.  When *False*, they run sequentially.
    max_spawns:
        Upper bound on spawned sub-agents per call.  Excess matches are
        silently dropped.
    aggregate:
        How to combine sub-agent outputs.  ``"concat"`` (default) joins with
        newlines.  ``"first"`` returns only the first successful output.
        ``"last"`` returns only the last.
    """

    rules: list[SpawnRule] = field(default_factory=list)
    fallback_role: str = "executor"
    parallel: bool = True
    max_spawns: int = 8
    aggregate: str = "concat"   # "concat" | "first" | "last"


# ── SpawnResult ───────────────────────────────────────────────────────────────


@dataclass
class SpawnResult:
    """Result returned by :class:`SpawnableAgent.run`.

    Attributes
    ----------
    output:
        The aggregated output from all spawned sub-agents (or the direct
        output if no sub-agent was spawned).
    spawn_count:
        Number of sub-agents that were actually spawned.
    agents_used:
        Names of the spawned agents (matches the rule names).
    sub_outputs:
        Individual outputs keyed by rule name.
    completed:
        True when no sub-agent raised an unhandled exception.
    """

    output: str
    spawn_count: int
    agents_used: list[str]
    sub_outputs: dict[str, str]
    completed: bool = True


# ── SpawnableAgent ────────────────────────────────────────────────────────────


class SpawnableAgent:
    """Top-level agent that spawns specialised child agents at runtime.

    Parameters
    ----------
    name:
        Identifier for this orchestrator agent.
    spawn_config:
        A :class:`SpawnConfig` that defines the spawning rules.
    role:
        Role for the orchestrator itself (used as fallback when no rule
        matches and *fallback_role* is empty).
    model:
        Default model string inherited by spawned children (unless each
        :class:`SpawnRule` overrides ``model``).
    policy:
        Governance policy forwarded to all spawned agents.
    provider:
        Low-level LLMProvider used when running in test / sandbox mode.
    mode:
        ``"sandbox"`` skips real LLM calls (uses EchoProvider).
    """

    def __init__(
        self,
        name: str,
        spawn_config: SpawnConfig | None = None,
        role: str = "orchestrator",
        model: str = "",
        policy: Any = None,
        provider: Any = None,
        mode: str = "production",
    ) -> None:
        self.name = name
        self.spawn_config = spawn_config or SpawnConfig()
        self.role = role
        self.model = model
        self.policy = policy
        self.provider = provider
        self.mode = mode

    # ── Public sync entry-point ───────────────────────────────────────────────

    def run(self, task: str, context: dict[str, Any] | None = None) -> SpawnResult:
        """Run the task, spawning sub-agents as needed.  Synchronous wrapper."""
        from meshflow.integrations._utils import run_sync
        return run_sync(self.arun(task, context=context))

    # ── Async core ────────────────────────────────────────────────────────────

    async def arun(
        self,
        task: str,
        context: dict[str, Any] | None = None,
    ) -> SpawnResult:
        """Async version of :meth:`run`."""
        ctx = context or {}

        matched: list[SpawnRule] = [
            r for r in self.spawn_config.rules if r.matches(task)
        ][: self.spawn_config.max_spawns]

        if not matched:
            # No rule matched — run with fallback or directly
            fallback_role = self.spawn_config.fallback_role or self.role
            output = await self._run_single(task, fallback_role, "", [], [], ctx)
            return SpawnResult(
                output=output,
                spawn_count=0,
                agents_used=[],
                sub_outputs={},
            )

        if self.spawn_config.parallel:
            outputs = await asyncio.gather(
                *[self._run_rule(rule, task, ctx) for rule in matched],
                return_exceptions=True,
            )
        else:
            outputs = []
            for rule in matched:
                out = await self._run_rule(rule, task, ctx)
                outputs.append(out)

        sub_outputs: dict[str, str] = {}
        completed = True
        for rule, out in zip(matched, outputs):
            if isinstance(out, BaseException):
                sub_outputs[rule.name] = f"[ERROR: {out}]"
                completed = False
            else:
                sub_outputs[rule.name] = str(out)

        aggregated = self._aggregate(list(sub_outputs.values()))

        return SpawnResult(
            output=aggregated,
            spawn_count=len(matched),
            agents_used=[r.name for r in matched],
            sub_outputs=sub_outputs,
            completed=completed,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _run_rule(
        self,
        rule: SpawnRule,
        task: str,
        context: dict[str, Any],
    ) -> str:
        return await self._run_single(
            task,
            rule.role,
            rule.model or self.model,
            rule.tools,
            rule.skills,
            context,
            system_prompt=rule.system_prompt,
            agent_name=f"{self.name}/{rule.name}",
        )

    async def _run_single(
        self,
        task: str,
        role: str,
        model: str,
        tools: list[Any],
        skills: list[str],
        context: dict[str, Any],
        system_prompt: str = "",
        agent_name: str = "",
    ) -> str:
        from meshflow.agents.builder import Agent
        from meshflow.core.workflow import Workflow

        name = agent_name or f"{self.name}/worker"
        build_kwargs: dict[str, Any] = {
            "name": name,
            "role": role,
            "tools": tools,
            "skills": skills,
            "mode": self.mode,
        }
        if model:
            build_kwargs["model"] = model
        if self.provider is not None:
            build_kwargs["provider"] = self.provider
        if self.policy is not None:
            build_kwargs["policy"] = self.policy
        if system_prompt:
            build_kwargs["system_prompt"] = system_prompt

        child = Agent(**build_kwargs)

        wf = Workflow(mode=self.mode)
        wf.add(child)

        full_task = task
        if context:
            ctx_str = "\n".join(f"{k}: {v}" for k, v in context.items())
            full_task = f"{task}\n\nContext:\n{ctx_str}"

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, wf.run, full_task)
        return result.output or ""

    def _aggregate(self, outputs: list[str]) -> str:
        mode = self.spawn_config.aggregate
        if mode == "first":
            return outputs[0] if outputs else ""
        if mode == "last":
            return outputs[-1] if outputs else ""
        # default: concat
        return "\n\n".join(o for o in outputs if o)
