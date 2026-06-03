"""AdvisorAgent + AdvisorRouter — Anthropic advisor-tool pattern.

Implements Anthropic's advisor-tool pattern: a high-intelligence *advisor*
model (Claude Opus) reviews the task and provides structured guidance; a
cost-efficient *executor* model (Claude Sonnet/Haiku) carries out the work
using that guidance.

Early benchmarks show:
- Sonnet + Opus advisor: 74.8% on SWE-bench Multilingual (+2.7pp vs Sonnet alone)
- Cost per agentic task: −11.9% vs always-Opus

Design
------
AdvisorConfig        — pairing config: advisor model, executor model, thresholds
AdvisorGuidance      — structured output from the advisor step
AdvisorAgent         — wraps any Agent with an advisor pre-flight step
AdvisorRouter        — routing decision: use advisor for complex tasks only
AdvisorResult        — extends WorkflowResult with advisor metadata

Usage::

    from meshflow.agents.advisor import AdvisorAgent, AdvisorConfig

    agent = AdvisorAgent(
        name="smart_coder",
        config=AdvisorConfig(
            advisor_model="claude-opus-4-8",
            executor_model="claude-sonnet-4-6",
            complexity_threshold=0.6,     # only advise on complex tasks
        ),
        mode="sandbox",
    )
    result = agent.run("Refactor this Python module to use async/await.")
    print(result.advisor_guidance)   # what Opus recommended
    print(result.output)             # what Sonnet produced

    # Or use AdvisorRouter to add advising to an existing workflow
    from meshflow import Workflow, Agent
    from meshflow.agents.advisor import AdvisorRouter

    router = AdvisorRouter(
        advisor_model="claude-opus-4-8",
        executor_model="claude-sonnet-4-6",
        complexity_threshold=0.5,
    )
    wf = Workflow()
    wf.add(Agent("coder", model_router=router))
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


# ── AdvisorConfig ─────────────────────────────────────────────────────────────

@dataclass
class AdvisorConfig:
    """Configuration for the advisor-tool pattern.

    Attributes
    ----------
    advisor_model:
        High-intelligence model used as the advisor.
        Default: ``"claude-opus-4-8"`` (or ``"claude-opus-4-7"``).
    executor_model:
        Cost-efficient model that does the actual work.
        Default: ``"claude-sonnet-4-6"``.
    complexity_threshold:
        Tasks with a complexity score (0–1) above this value trigger the
        advisor step.  Scores below it go directly to the executor.
        Default: 0.5.
    advisor_system_prompt:
        System prompt for the advisor.  If empty, a sensible default is used.
    max_advisor_tokens:
        Maximum tokens the advisor may use.  Tighter budget → cheaper advice.
    include_guidance_in_executor:
        When True (default), the advisor's guidance is prepended to the
        executor's context as a ``[Advisor guidance]`` block.
    guidance_format:
        ``"text"`` (default) returns plain advice; ``"json"`` requests
        structured JSON with keys ``approach``, ``pitfalls``, ``checklist``.
    """
    advisor_model: str = "claude-opus-4-8"
    executor_model: str = "claude-sonnet-4-6"
    complexity_threshold: float = 0.5
    advisor_system_prompt: str = ""
    max_advisor_tokens: int = 512
    include_guidance_in_executor: bool = True
    guidance_format: str = "text"   # "text" | "json"


# ── AdvisorGuidance ───────────────────────────────────────────────────────────

@dataclass
class AdvisorGuidance:
    """Structured output from the advisor step.

    Attributes
    ----------
    raw:
        Raw text output from the advisor LLM call.
    approach:
        Recommended approach (extracted from JSON guidance when available).
    pitfalls:
        List of pitfalls to avoid.
    checklist:
        Step-by-step checklist (when guidance_format="json").
    advisor_model:
        Model that produced this guidance.
    advisor_tokens_used:
        Token count for the advisor call.
    advisor_cost_usd:
        Estimated cost of the advisor call.
    skipped:
        True when the task complexity was below the threshold and no advisor
        call was made.
    """
    raw: str = ""
    approach: str = ""
    pitfalls: list[str] = field(default_factory=list)
    checklist: list[str] = field(default_factory=list)
    advisor_model: str = ""
    advisor_tokens_used: int = 0
    advisor_cost_usd: float = 0.0
    skipped: bool = False

    def as_context_block(self) -> str:
        """Return a formatted context block to inject into the executor prompt."""
        if self.skipped or not self.raw:
            return ""
        lines = ["[Advisor guidance]", self.raw.strip()]
        if self.checklist:
            lines.append("\nChecklist:")
            for step in self.checklist:
                lines.append(f"  • {step}")
        return "\n".join(lines)


# ── AdvisorResult ─────────────────────────────────────────────────────────────

@dataclass
class AdvisorResult:
    """Result returned by :class:`AdvisorAgent.run`.

    Extends the executor output with advisor metadata.
    """
    output: str
    advisor_guidance: AdvisorGuidance
    executor_model: str
    total_cost_usd: float
    advisor_cost_usd: float
    executor_cost_usd: float
    total_tokens: int
    advisor_used: bool
    completed: bool = True

    def __str__(self) -> str:
        return self.output

    @property
    def cost_savings_vs_full_opus(self) -> float:
        """Estimated USD saved vs running the task entirely on the advisor model."""
        if not self.advisor_used:
            return 0.0
        full_opus_cost_estimate = self.executor_cost_usd * 15.0  # rough Opus/Sonnet ratio
        return max(0.0, full_opus_cost_estimate - self.total_cost_usd)


# ── AdvisorAgent ──────────────────────────────────────────────────────────────

class AdvisorAgent:
    """Agent that pairs a high-intelligence advisor with a cost-efficient executor.

    For every task:
    1. Scores task complexity (0–1) using MeshFlow's 5-factor scorer.
    2. If complexity ≥ ``config.complexity_threshold``:
       a. Calls the *advisor* model for structured guidance.
       b. Injects that guidance into the *executor*'s context.
    3. The *executor* model carries out the task with (or without) guidance.

    Parameters
    ----------
    name:
        Identifier for this agent.
    config:
        :class:`AdvisorConfig` with model names, thresholds, and formatting.
    tools:
        Tools available to the executor.
    mode:
        ``"sandbox"`` for offline testing.
    provider:
        Override LLMProvider (useful in tests with EchoProvider).
    """

    def __init__(
        self,
        name: str = "advisor_agent",
        config: AdvisorConfig | None = None,
        tools: list[Any] | None = None,
        mode: str = "production",
        provider: Any = None,
    ) -> None:
        self.name = name
        self.config = config or AdvisorConfig()
        self.tools = tools or []
        self.mode = mode
        self.provider = provider

    # ── Public sync entry point ───────────────────────────────────────────────

    def run(self, task: str, context: dict[str, Any] | None = None) -> AdvisorResult:
        """Run the advisor → executor pipeline synchronously."""
        from meshflow.integrations._utils import run_sync
        return run_sync(self.arun(task, context=context))

    # ── Async core ────────────────────────────────────────────────────────────

    async def arun(
        self,
        task: str,
        context: dict[str, Any] | None = None,
    ) -> AdvisorResult:
        ctx = context or {}

        # 1. Score complexity
        complexity = self._score_complexity(task)
        needs_advisor = complexity >= self.config.complexity_threshold

        # 2. Advisor step
        if needs_advisor:
            guidance = await self._call_advisor(task)
        else:
            guidance = AdvisorGuidance(skipped=True)

        # 3. Build executor task
        exec_task = task
        if needs_advisor and self.config.include_guidance_in_executor:
            guidance_block = guidance.as_context_block()
            if guidance_block:
                exec_task = f"{task}\n\n{guidance_block}"

        if ctx:
            ctx_str = "\n".join(f"{k}: {v}" for k, v in ctx.items())
            exec_task = f"{exec_task}\n\nContext:\n{ctx_str}"

        # 4. Executor step
        exec_output, exec_tokens, exec_cost = await self._call_executor(exec_task)

        total_cost = guidance.advisor_cost_usd + exec_cost
        total_tokens = guidance.advisor_tokens_used + exec_tokens

        return AdvisorResult(
            output=exec_output,
            advisor_guidance=guidance,
            executor_model=self.config.executor_model,
            total_cost_usd=total_cost,
            advisor_cost_usd=guidance.advisor_cost_usd,
            executor_cost_usd=exec_cost,
            total_tokens=total_tokens,
            advisor_used=needs_advisor,
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _score_complexity(self, task: str) -> float:
        """Score task complexity 0–1 using the 5-factor scorer if available."""
        try:
            from meshflow.agents.scoring import score_task
            score_obj = score_task(task)
            return float(getattr(score_obj, "composite", 0.5))
        except Exception:
            # Fallback: length-based heuristic
            return min(1.0, len(task) / 500.0)

    async def _call_advisor(self, task: str) -> AdvisorGuidance:
        """Call the advisor model and parse its guidance."""
        advisor_prompt = self.config.advisor_system_prompt or (
            "You are an expert technical advisor. Review the following task and provide:\n"
            "1. The recommended approach (2-3 sentences)\n"
            "2. Key pitfalls to avoid\n"
            "3. A concise checklist of steps\n\n"
            "Be brief and practical. The executor agent will read your advice before acting."
        )

        if self.config.guidance_format == "json":
            advisor_prompt += (
                '\n\nRespond ONLY with valid JSON: '
                '{"approach": "...", "pitfalls": ["..."], "checklist": ["..."]}'
            )

        from meshflow.agents.builder import Agent
        from meshflow.core.workflow import Workflow

        advisor = Agent(
            name=f"{self.name}/advisor",
            system_prompt=advisor_prompt,
            model=self.config.advisor_model,
            mode=self.mode,
        )
        if self.provider is not None:
            advisor.provider = self.provider

        wf = Workflow(mode=self.mode)
        wf.add(advisor)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, wf.run, task)
        raw = result.output or ""

        guidance = AdvisorGuidance(
            raw=raw,
            advisor_model=self.config.advisor_model,
            advisor_tokens_used=result.total_tokens,
            advisor_cost_usd=result.total_cost_usd,
        )

        # Attempt JSON parse when format="json"
        if self.config.guidance_format == "json" and raw.strip().startswith("{"):
            try:
                import json
                parsed = json.loads(raw)
                guidance.approach = parsed.get("approach", "")
                guidance.pitfalls = parsed.get("pitfalls", [])
                guidance.checklist = parsed.get("checklist", [])
            except Exception:
                pass

        return guidance

    async def _call_executor(
        self, task: str
    ) -> tuple[str, int, float]:
        """Call the executor model and return (output, tokens, cost_usd)."""
        from meshflow.agents.builder import Agent
        from meshflow.core.workflow import Workflow

        executor = Agent(
            name=f"{self.name}/executor",
            tools=self.tools,
            model=self.config.executor_model,
            mode=self.mode,
        )
        if self.provider is not None:
            executor.provider = self.provider

        wf = Workflow(mode=self.mode)
        wf.add(executor)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, wf.run, task)
        return result.output or "", result.total_tokens, result.total_cost_usd


# ── AdvisorRouter ─────────────────────────────────────────────────────────────

class AdvisorRouter:
    """Model router that applies the advisor pattern per-task.

    Can be passed as ``model_router=`` to a standard :class:`~meshflow.Agent`.
    For tasks above ``complexity_threshold``, it adds an advisor consultation
    before the main model call.  For simple tasks it routes directly.

    Implements the ``route(task, **kwargs) → RoutingDecision`` interface used
    by MeshFlow's existing router chain.

    Parameters
    ----------
    advisor_model:
        High-intelligence model for advice (default: ``"claude-opus-4-8"``).
    executor_model:
        Fast model for execution (default: ``"claude-sonnet-4-6"``).
    complexity_threshold:
        Tasks above this score get advisor consultation (default: 0.5).
    """

    def __init__(
        self,
        advisor_model: str = "claude-opus-4-8",
        executor_model: str = "claude-sonnet-4-6",
        complexity_threshold: float = 0.5,
    ) -> None:
        self.advisor_model = advisor_model
        self.executor_model = executor_model
        self.complexity_threshold = complexity_threshold
        self._outcomes: list[dict[str, Any]] = []

    def route(self, task: str, **kwargs: Any) -> Any:
        """Return a routing decision for *task*.

        Returns an object with ``.model``, ``.tier``, and ``.use_advisor``
        attributes compatible with MeshFlow's router chain.
        """
        complexity = self._score(task)
        use_advisor = complexity >= self.complexity_threshold
        model = self.executor_model

        @dataclass
        class _Decision:
            model: str
            tier: str
            use_advisor: bool
            complexity: float

        return _Decision(
            model=model,
            tier="advisor" if use_advisor else "executor",
            use_advisor=use_advisor,
            complexity=complexity,
        )

    def record_outcome(self, routing_id: str, **kwargs: Any) -> None:
        """Record outcome for analytics (mirrors ModelTierRouter interface)."""
        self._outcomes.append({"routing_id": routing_id, **kwargs})

    def report(self) -> dict[str, Any]:
        """Return advisor usage statistics."""
        total = len(self._outcomes)
        advised = sum(1 for o in self._outcomes if o.get("use_advisor"))
        return {
            "total_routes": total,
            "advisor_used": advised,
            "direct_routes": total - advised,
            "advisor_rate": advised / total if total > 0 else 0.0,
            "advisor_model": self.advisor_model,
            "executor_model": self.executor_model,
        }

    def _score(self, task: str) -> float:
        try:
            from meshflow.agents.scoring import score_task
            return float(getattr(score_task(task), "composite", 0.5))
        except Exception:
            return min(1.0, len(task) / 500.0)
