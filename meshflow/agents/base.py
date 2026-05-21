"""Base agent and role-specific agents — AutoGen DNA with HITL support."""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

import anthropic

from meshflow.core.schemas import (
    AgentRole, AgentState, Evidence, Intent, Message,
    Policy, RiskTier, UncertaintyScore,
)


@dataclass
class AgentConfig:
    agent_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    role: AgentRole = AgentRole.EXECUTOR
    model: str = "claude-sonnet-4-6"
    system_prompt: str = ""
    tools: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    max_tokens: int = 4096
    temperature: float = 0.7


class BaseAgent:
    """Base agent — wraps an LLM call with MeshFlow protocol.

    All agents:
    - Emit typed Messages (never raw strings)
    - Declare Intents before executing side-effects
    - Report uncertainty as part of every output
    - Maintain an AgentState that the graph can checkpoint
    """

    def __init__(self, config: AgentConfig, policy: Policy) -> None:
        self.config = config
        self.policy = policy
        self._client = anthropic.AsyncAnthropic()
        self._state = AgentState(
            agent_id=config.agent_id,
            role=config.role,
        )
        self._call_count = 0
        self._total_tokens = 0
        self._total_cost = 0.0

    @property
    def agent_id(self) -> str:
        return self.config.agent_id

    @property
    def role(self) -> AgentRole:
        return self.config.role

    @property
    def state(self) -> AgentState:
        return self._state

    # ── Core LLM call ─────────────────────────────────────────────────────────

    async def think(
        self,
        messages: list[dict[str, str]],
        system: str | None = None,
    ) -> tuple[str, int, float]:
        """Call the LLM — returns (content, tokens, cost_usd)."""
        sys = system or self.config.system_prompt
        response = await self._client.messages.create(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            system=sys,
            messages=messages,
        )
        content = response.content[0].text if response.content else ""
        tokens = response.usage.input_tokens + response.usage.output_tokens

        # Estimate cost (simplified — real impl fetches from Anthropic pricing)
        tier_cost = {"haiku": 0.0005, "sonnet": 0.003, "opus": 0.015}
        rate = next((v for k, v in tier_cost.items() if k in self.config.model.lower()), 0.003)
        cost = (tokens / 1000) * rate

        self._call_count += 1
        self._total_tokens += tokens
        self._total_cost += cost
        self._state.token_count += tokens
        self._state.cost_usd += cost

        return content, tokens, cost

    # ── Protocol helpers ──────────────────────────────────────────────────────

    def make_message(self, content: str, receiver_id: str, trace_id: str = "") -> Message:
        return Message(
            sender_id=self.agent_id,
            receiver_id=receiver_id,
            content=content,
            role=self.config.role.value,
            trace_id=trace_id or str(uuid.uuid4()),
        )

    def make_intent(
        self,
        action: str,
        payload: dict[str, Any],
        evidence: list[Evidence] | None = None,
        risk_tier: RiskTier = RiskTier.READ_ONLY,
    ) -> Intent:
        return Intent(
            action=action,
            payload=payload,
            evidence=evidence or [],
            agent_id=self.agent_id,
            agent_did=self._state.did,
            risk_tier=risk_tier,
        )

    # ── Step — override in subclasses ─────────────────────────────────────────

    async def step(self, task: str, context: dict[str, Any]) -> dict[str, Any]:
        """Execute one agent step. Override in subclasses."""
        raise NotImplementedError


# ── Role-specific agents ──────────────────────────────────────────────────────

class PlannerAgent(BaseAgent):
    """Decomposes tasks into a structured plan for other agents."""

    SYSTEM = (
        "You are a Planner agent. Your job is to decompose the user's task into "
        "clear, ordered steps. Each step must specify: which role executes it, "
        "what inputs it needs, and what it must produce. Output valid JSON only. "
        "Format: {\"steps\": [{\"role\": \"...\", \"input\": \"...\", \"expected_output\": \"...\"}]}"
    )

    async def step(self, task: str, context: dict[str, Any]) -> dict[str, Any]:
        messages = [{"role": "user", "content": f"Task: {task}\nContext: {context}"}]
        content, tokens, cost = await self.think(messages, self.SYSTEM)

        import json
        try:
            plan = json.loads(content)
        except Exception:
            plan = {"steps": [{"role": "executor", "input": task, "expected_output": "result"}]}

        return {
            "plan": plan,
            "planner_id": self.agent_id,
            "tokens": tokens,
            "cost_usd": cost,
            "stated_confidence": 0.85,
        }


class ResearcherAgent(BaseAgent):
    """Gathers and synthesises information for a given query."""

    SYSTEM = (
        "You are a Researcher agent. Given a research question, provide a "
        "thorough, factual answer with source attribution. Flag any uncertainty. "
        "Be explicit about what you do not know. Output as structured text."
    )

    async def step(self, task: str, context: dict[str, Any]) -> dict[str, Any]:
        plan_step = context.get("current_step", {})
        query = plan_step.get("input", task)
        messages = [{"role": "user", "content": f"Research question: {query}"}]
        content, tokens, cost = await self.think(messages, self.SYSTEM)

        return {
            "research": content,
            "researcher_id": self.agent_id,
            "tokens": tokens,
            "cost_usd": cost,
            "stated_confidence": 0.75,
            "evidence": [Evidence(content=content, source="llm_synthesis", trust_level="internal")],
        }


class ExecutorAgent(BaseAgent):
    """Executes concrete actions based on plan and research."""

    SYSTEM = (
        "You are an Executor agent. You receive a plan step and research context. "
        "Execute the step precisely. If you need to write code, write complete, "
        "runnable code. If you need to take an action, describe it precisely and "
        "declare it as an Intent before proceeding. Do not take irreversible actions "
        "without explicit instruction."
    )

    async def step(self, task: str, context: dict[str, Any]) -> dict[str, Any]:
        research = context.get("research", "")
        plan_step = context.get("current_step", {})
        messages = [{"role": "user", "content": (
            f"Plan step: {plan_step}\n"
            f"Research context: {research[:2000]}\n"
            f"Task: {task}"
        )}]
        content, tokens, cost = await self.think(messages, self.SYSTEM)
        return {
            "execution_result": content,
            "executor_id": self.agent_id,
            "tokens": tokens,
            "cost_usd": cost,
            "stated_confidence": 0.80,
        }


class CriticAgent(BaseAgent):
    """Independent critic — evaluates outputs before handoff.

    Dual-judge pattern: one Critic looks for failures, one for successes.
    A meta-arbitrator (the orchestrator) settles disagreements.
    """

    SYSTEM_FAILURE = (
        "You are a Critic agent looking for FAILURES. Given an output, "
        "identify all errors, omissions, hallucinations, and weak reasoning. "
        "Be adversarial. Score from 0–10 (10 = completely wrong). "
        "Output JSON: {\"failure_score\": N, \"issues\": [...]}"
    )

    SYSTEM_SUCCESS = (
        "You are a Critic agent looking for SUCCESSES. Given an output, "
        "identify what is correct, well-reasoned, and complete. "
        "Score from 0–10 (10 = perfect). "
        "Output JSON: {\"success_score\": N, \"strengths\": [...]}"
    )

    async def step(self, task: str, context: dict[str, Any]) -> dict[str, Any]:
        output_to_review = context.get("execution_result", context.get("research", ""))
        import json

        # Dual-judge: run both evaluations
        fail_msgs = [{"role": "user", "content": f"Output to review:\n{output_to_review}"}]
        succ_msgs = [{"role": "user", "content": f"Output to review:\n{output_to_review}"}]

        fail_content, ftokens, fcost = await self.think(fail_msgs, self.SYSTEM_FAILURE)
        succ_content, stokens, scost = await self.think(succ_msgs, self.SYSTEM_SUCCESS)

        try:
            fail_result = json.loads(fail_content)
        except Exception:
            fail_result = {"failure_score": 5, "issues": []}
        try:
            succ_result = json.loads(succ_content)
        except Exception:
            succ_result = {"success_score": 5, "strengths": []}

        failure_score = fail_result.get("failure_score", 5)
        success_score = succ_result.get("success_score", 5)
        # Arbitration: combine scores (higher success + lower failure = pass)
        composite = (success_score - failure_score + 10) / 20.0  # normalise 0–1
        passed = composite >= 0.5

        return {
            "critic_passed": passed,
            "composite_score": composite,
            "failure_score": failure_score,
            "success_score": success_score,
            "issues": fail_result.get("issues", []),
            "strengths": succ_result.get("strengths", []),
            "critic_id": self.agent_id,
            "tokens": ftokens + stokens,
            "cost_usd": fcost + scost,
            "stated_confidence": composite,
        }
