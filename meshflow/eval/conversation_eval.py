"""Multi-turn conversation evals — test agents across a full session.

Unlike single-turn evals, conversation evals verify that agents:
- Maintain context across turns
- Correctly reference earlier messages
- Handle follow-up clarifications
- Stay within policy across the full session

Usage::

    from meshflow.eval.conversation_eval import ConversationEval, ConversationCase, Turn

    case = ConversationCase(
        name="hipaa-followup",
        turns=[
            Turn(user="What is the HIPAA minimum necessary standard?",
                 must_contain=["minimum necessary"],
                 judge_rubric="Score for accuracy and completeness."),
            Turn(user="Give me a concrete example of how to apply it.",
                 must_contain=["example"],
                 judge_rubric="Score for relevance to the previous answer."),
            Turn(user="What are the penalties for violations?",
                 must_contain=["penalty", "fine", "civil", "criminal"],
                 judge_rubric="Score for regulatory accuracy."),
        ],
    )

    eval_runner = ConversationEval()
    result = await eval_runner.run(case, agent=my_agent)
    print(result.avg_score, result.turns_passed, result.summary())
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Turn:
    """One turn in a multi-turn conversation eval case."""

    user: str
    must_contain: list[str] = field(default_factory=list)
    must_not_contain: list[str] = field(default_factory=list)
    judge_rubric: str = ""
    min_score: float = 0.0


@dataclass
class TurnResult:
    """Result of a single evaluated turn."""

    turn_idx: int
    user_message: str
    agent_response: str
    contains_passed: bool
    not_contains_passed: bool
    judge_score: float
    judge_reasoning: str
    passed: bool
    duration_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_idx": self.turn_idx,
            "user": self.user_message[:200],
            "response_preview": self.agent_response[:300],
            "contains_passed": self.contains_passed,
            "not_contains_passed": self.not_contains_passed,
            "judge_score": self.judge_score,
            "judge_reasoning": self.judge_reasoning,
            "passed": self.passed,
            "duration_ms": round(self.duration_ms, 1),
        }


@dataclass
class ConversationCase:
    """A multi-turn eval scenario."""

    name: str
    turns: list[Turn]
    system_prompt: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConversationResult:
    """Aggregated result of a full conversation eval."""

    case_name: str
    turn_results: list[TurnResult]
    total_duration_ms: float

    @property
    def turns_passed(self) -> int:
        return sum(1 for t in self.turn_results if t.passed)

    @property
    def turns_failed(self) -> int:
        return len(self.turn_results) - self.turns_passed

    @property
    def avg_score(self) -> float:
        if not self.turn_results:
            return 0.0
        return round(sum(t.judge_score for t in self.turn_results) / len(self.turn_results), 4)

    @property
    def passed(self) -> bool:
        return all(t.passed for t in self.turn_results)

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return (
            f"[{status}] {self.case_name} — "
            f"{self.turns_passed}/{len(self.turn_results)} turns passed, "
            f"avg score {self.avg_score:.2f}, "
            f"{self.total_duration_ms:.0f}ms"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_name": self.case_name,
            "passed": self.passed,
            "turns_passed": self.turns_passed,
            "turns_failed": self.turns_failed,
            "avg_score": self.avg_score,
            "total_duration_ms": round(self.total_duration_ms, 1),
            "turns": [t.to_dict() for t in self.turn_results],
        }


class ConversationEval:
    """Run a :class:`ConversationCase` against an agent and return a
    :class:`ConversationResult`.

    Parameters
    ----------
    judge:
        Optional :class:`~meshflow.eval.judge.LLMJudge` instance.  If not
        provided, a default judge is created (uses EchoProvider in tests).
    pass_threshold:
        Minimum judge score for a turn to be considered passing (0–1).
    """

    def __init__(
        self,
        judge: Any = None,
        pass_threshold: float = 0.6,
    ) -> None:
        self._judge = judge
        self._threshold = pass_threshold

    def _get_judge(self) -> Any:
        if self._judge is not None:
            return self._judge
        from meshflow.eval.judge import LLMJudge
        return LLMJudge()

    async def run(
        self,
        case: ConversationCase,
        *,
        agent: Any = None,
        provider: Any = None,
        model: str = "claude-haiku-4-5",
    ) -> ConversationResult:
        """Run *case* through *agent* and evaluate each turn.

        Either pass a MeshFlow :class:`~meshflow.agents.builder.Agent` via
        ``agent``, or pass a raw ``provider`` + ``model`` for direct provider
        calls (useful in tests).
        """
        judge = self._get_judge()
        history: list[dict[str, Any]] = []
        turn_results: list[TurnResult] = []
        t0 = time.monotonic()

        for idx, turn in enumerate(case.turns, 1):
            turn_start = time.monotonic()

            # Add user message to history
            history.append({"role": "user", "content": turn.user})

            # Get agent response
            response = await self._call(
                history=history,
                agent=agent,
                provider=provider,
                model=model,
                system=case.system_prompt,
            )

            # Add assistant message to history for next turn
            history.append({"role": "assistant", "content": response})

            turn_ms = (time.monotonic() - turn_start) * 1000

            # Check must_contain / must_not_contain
            resp_lower = response.lower()
            contains_ok = all(kw.lower() in resp_lower for kw in turn.must_contain)
            not_contains_ok = all(kw.lower() not in resp_lower for kw in turn.must_not_contain)

            # Judge score
            js = await judge.score(
                task=turn.user,
                output=response,
                rubric=turn.judge_rubric,
            )

            # Turn passes if contains checks pass AND judge score meets threshold AND min_score
            effective_min = max(self._threshold, turn.min_score)
            turn_passed = contains_ok and not_contains_ok and js.score >= effective_min

            turn_results.append(TurnResult(
                turn_idx=idx,
                user_message=turn.user,
                agent_response=response,
                contains_passed=contains_ok,
                not_contains_passed=not_contains_ok,
                judge_score=js.score,
                judge_reasoning=js.reasoning,
                passed=turn_passed,
                duration_ms=turn_ms,
            ))

        total_ms = (time.monotonic() - t0) * 1000
        return ConversationResult(
            case_name=case.name,
            turn_results=turn_results,
            total_duration_ms=total_ms,
        )

    async def run_suite(
        self,
        cases: list[ConversationCase],
        **kwargs: Any,
    ) -> list[ConversationResult]:
        """Run multiple cases and return all results."""
        import asyncio
        return list(await asyncio.gather(*[self.run(c, **kwargs) for c in cases]))

    async def _call(
        self,
        history: list[dict[str, Any]],
        agent: Any,
        provider: Any,
        model: str,
        system: str,
    ) -> str:
        if agent is not None:
            # Use MeshFlow Agent — build a task string from the last user message
            last = history[-1]["content"]
            ctx = {"history": history[:-1]} if len(history) > 1 else None
            result = await agent.run(last, ctx)
            return result.get("result", "") if isinstance(result, dict) else str(result)

        if provider is not None:
            content, _, _ = await provider.complete(
                model=model,
                messages=history,
                system=system or "You are a helpful assistant.",
                max_tokens=1024,
            )
            return content

        # No agent or provider — use auto-detected provider
        from meshflow.agents.providers import auto_detect_provider
        prov = auto_detect_provider()
        content, _, _ = await prov.complete(
            model=model,
            messages=history,
            system=system or "You are a helpful assistant.",
            max_tokens=1024,
        )
        return content
