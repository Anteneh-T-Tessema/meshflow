"""LLM-as-judge scoring — use Claude to grade agent output quality.

Produces a structured 0–1 score with per-criterion breakdown and reasoning.
Uses EchoProvider when ANTHROPIC_API_KEY is absent so unit tests never stall.

Usage::

    from meshflow.eval.judge import LLMJudge

    judge = LLMJudge()
    score = await judge.score(
        task="Summarise the HIPAA privacy rule in three bullet points",
        output=agent_output,
        rubric="Award full marks for accuracy, conciseness, and bullet format.",
    )
    print(score.score, score.reasoning)

    # Grade against a reference answer
    score = await judge.score(task, output, reference=gold_answer)

    # Grade a batch
    scores = await judge.score_batch([
        {"task": t1, "output": o1},
        {"task": t2, "output": o2, "reference": r2},
    ])
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


_DEFAULT_RUBRIC = (
    "Grade the output on a scale from 0.0 to 1.0. "
    "Consider: accuracy (is it factually correct?), completeness (does it fully address the task?), "
    "clarity (is it clear and well-structured?), and relevance (does it stay on topic?). "
)

_JUDGE_SYSTEM = """\
You are an expert evaluator grading AI agent outputs.
Respond ONLY with a JSON object — no prose before or after.
Schema:
{
  "score": <float 0.0–1.0>,
  "reasoning": "<one sentence explanation>",
  "criteria": {
    "accuracy": <float 0.0–1.0>,
    "completeness": <float 0.0–1.0>,
    "clarity": <float 0.0–1.0>,
    "relevance": <float 0.0–1.0>
  }
}"""


@dataclass
class JudgeScore:
    """Result of a single LLM-as-judge evaluation."""

    score: float
    reasoning: str
    criteria: dict[str, float] = field(default_factory=dict)
    task: str = ""
    output_preview: str = ""
    model: str = ""

    def passed(self, threshold: float = 0.7) -> bool:
        return self.score >= threshold

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "reasoning": self.reasoning,
            "criteria": self.criteria,
            "task": self.task,
            "output_preview": self.output_preview[:200],
            "model": self.model,
        }


class LLMJudge:
    """Grade agent outputs using an LLM as an impartial evaluator.

    Parameters
    ----------
    model:
        Claude model used for judging. Defaults to ``claude-haiku-4-5`` (fast
        and cheap). Override with ``claude-sonnet-4-6`` for higher-stakes evals.
    rubric:
        Default scoring rubric appended to every judge prompt.  Override
        per-call via the ``rubric`` argument of :meth:`score`.
    provider:
        Optional LLM provider instance.  Auto-detected from env if not given.
    """

    def __init__(
        self,
        model: str = "claude-haiku-4-5",
        rubric: str = "",
        provider: Any = None,
    ) -> None:
        self._model = model
        self._rubric = rubric or _DEFAULT_RUBRIC
        self._provider = provider

    def _get_provider(self) -> Any:
        if self._provider is not None:
            return self._provider
        try:
            from meshflow.agents.providers import auto_detect_provider
            return auto_detect_provider()
        except Exception:
            from meshflow.agents.base import EchoProvider
            return EchoProvider(response=json.dumps({
                "score": 0.75,
                "reasoning": "EchoProvider fallback — no API key available.",
                "criteria": {"accuracy": 0.75, "completeness": 0.75, "clarity": 0.75, "relevance": 0.75},
            }))

    def _build_prompt(
        self,
        task: str,
        output: str,
        reference: str,
        rubric: str,
    ) -> str:
        parts = [f"TASK:\n{task}\n\nOUTPUT TO GRADE:\n{output}"]
        if reference:
            parts.append(f"\nREFERENCE ANSWER (for comparison):\n{reference}")
        parts.append(f"\nRUBRIC:\n{rubric or self._rubric}")
        return "\n".join(parts)

    def _parse_response(self, raw: str, task: str, output: str) -> JudgeScore:
        # Extract JSON block from the response
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                score = max(0.0, min(1.0, float(data.get("score", 0.5))))
                reasoning = str(data.get("reasoning", ""))
                criteria = {
                    k: max(0.0, min(1.0, float(v)))
                    for k, v in data.get("criteria", {}).items()
                }
                return JudgeScore(
                    score=round(score, 4),
                    reasoning=reasoning,
                    criteria=criteria,
                    task=task[:200],
                    output_preview=output[:200],
                    model=self._model,
                )
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
        # Fallback: try to extract a bare float
        float_match = re.search(r"\b(0\.\d+|1\.0)\b", raw)
        score = float(float_match.group()) if float_match else 0.5
        return JudgeScore(
            score=round(score, 4),
            reasoning=raw[:300],
            criteria={},
            task=task[:200],
            output_preview=output[:200],
            model=self._model,
        )

    async def score(
        self,
        task: str,
        output: str,
        *,
        reference: str = "",
        rubric: str = "",
    ) -> JudgeScore:
        """Grade *output* for *task* and return a structured :class:`JudgeScore`.

        Parameters
        ----------
        task:      The original task/prompt given to the agent.
        output:    The agent's response to grade.
        reference: Optional gold-standard answer for comparison grading.
        rubric:    Override the default rubric for this call only.
        """
        provider = self._get_provider()
        prompt = self._build_prompt(task, output, reference, rubric)
        try:
            content, _, _ = await provider.complete(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                system=_JUDGE_SYSTEM,
                max_tokens=512,
                response_format="json",
            )
        except TypeError:
            # Provider doesn't support response_format kwarg
            content, _, _ = await provider.complete(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                system=_JUDGE_SYSTEM,
                max_tokens=512,
            )
        return self._parse_response(content, task, output)

    async def score_batch(
        self,
        items: list[dict[str, Any]],
        *,
        rubric: str = "",
    ) -> list[JudgeScore]:
        """Grade multiple outputs concurrently.

        Each item is a dict with keys: ``task``, ``output``, and optionally
        ``reference`` and ``rubric``.
        """
        import asyncio
        tasks = [
            self.score(
                item["task"],
                item["output"],
                reference=item.get("reference", ""),
                rubric=item.get("rubric", rubric),
            )
            for item in items
        ]
        return list(await asyncio.gather(*tasks))

    async def score_suite(
        self,
        results: list[dict[str, Any]],
        *,
        task_key: str = "task",
        output_key: str = "output",
        rubric: str = "",
    ) -> "JudgeSuiteResult":
        """Grade an entire eval suite result list and return summary stats."""
        items = [{"task": r[task_key], "output": r[output_key]} for r in results]
        scores = await self.score_batch(items, rubric=rubric)
        return JudgeSuiteResult(scores=scores)


@dataclass
class JudgeSuiteResult:
    """Aggregated result of judging an entire eval suite."""

    scores: list[JudgeScore]

    @property
    def avg_score(self) -> float:
        if not self.scores:
            return 0.0
        return round(sum(s.score for s in self.scores) / len(self.scores), 4)

    @property
    def pass_rate(self, threshold: float = 0.7) -> float:
        if not self.scores:
            return 0.0
        passed = sum(1 for s in self.scores if s.passed(threshold))
        return round(passed / len(self.scores), 4)

    @property
    def min_score(self) -> float:
        return min((s.score for s in self.scores), default=0.0)

    @property
    def max_score(self) -> float:
        return max((s.score for s in self.scores), default=0.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "avg_score": self.avg_score,
            "pass_rate": self.pass_rate,
            "min_score": self.min_score,
            "max_score": self.max_score,
            "n": len(self.scores),
            "scores": [s.to_dict() for s in self.scores],
        }
