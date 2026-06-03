"""Agent Evals v2 — StructuredJudge, TrajectoryEval, RAGEval, and EvalCI.

Extends the base :class:`~meshflow.eval.judge.LLMJudge` with:

* **StructuredJudge** — multi-criterion rubric with per-metric scores and
  configurable pass thresholds per criterion.
* **TrajectoryEval** — evaluates the *reasoning path* of a multi-step agent,
  not just the final output.
* **RAGEval** — RAGAS-style retrieval-augmented generation evaluation:
  faithfulness, answer relevance, and context recall.
* **EvalCI** — runs an eval suite and raises ``EvalRegressionError`` when
  the mean score drops below a configured baseline. Designed for CI gates.

Usage::

    from meshflow.eval.judge_v2 import StructuredJudge, TrajectoryEval, RAGEval, EvalCI

    # --- StructuredJudge ---
    judge = StructuredJudge(criteria=["correctness", "faithfulness", "helpfulness"])
    result = await judge.score(
        task="Summarise the Q3 earnings call",
        output=agent_output,
        reference=gold_answer,          # optional
        context="Q3 revenue was $12M…", # optional
    )
    print(result.overall)               # 0.0–1.0
    print(result.by_criterion)          # {"correctness": 0.9, …}
    print(result.passed)                # True / False

    # --- TrajectoryEval ---
    teval = TrajectoryEval()
    result = await teval.evaluate(
        task="Find and summarise the top 3 HIPAA violations in 2024",
        trajectory=[                    # list of (thought, action, observation) dicts
            {"thought": "I need to search PubMed", "action": "search('HIPAA violations 2024')", "observation": "…"},
            {"thought": "Found 5 results — picking top 3", "action": "extract(…)", "observation": "…"},
        ],
        final_output=agent_output,
    )
    print(result.step_scores)           # score per trajectory step
    print(result.path_score)            # overall path quality

    # --- RAGEval ---
    rag = RAGEval()
    result = await rag.evaluate(
        question="What is the HIPAA minimum necessary standard?",
        answer=agent_answer,
        retrieved_contexts=["Context chunk 1…", "Context chunk 2…"],
        reference_answer=gold_answer,   # optional
    )
    print(result.faithfulness)          # 0.0–1.0
    print(result.answer_relevance)      # 0.0–1.0
    print(result.context_recall)        # 0.0–1.0 (needs reference_answer)

    # --- EvalCI ---
    ci = EvalCI(baseline_score=0.80, suite_name="regression")
    await ci.run_and_gate(eval_suite, inputs=[…])
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any


# ── StructuredJudge ───────────────────────────────────────────────────────────

@dataclass
class StructuredJudgeResult:
    """Result from :class:`StructuredJudge`."""

    overall: float                   # weighted mean of criterion scores
    by_criterion: dict[str, float]   # per-criterion 0–1 scores
    reasoning: str                   # combined judge explanation
    passed: bool                     # overall >= pass_threshold
    raw: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        crit = ", ".join(f"{k}={v:.2f}" for k, v in self.by_criterion.items())
        return f"StructuredJudgeResult(overall={self.overall:.2f}, {crit}, passed={self.passed})"


_STRUCTURED_SYSTEM = """\
You are an expert evaluation judge. Score the agent output on each requested criterion.
Respond ONLY with valid JSON — no prose before or after.
Schema:
{{
  "overall": <float 0.0-1.0>,
  "criteria": {{{criteria_schema}}},
  "reasoning": "<1-2 sentence overall explanation>"
}}
Each criterion value must be a float 0.0-1.0.
"""

_CRITERION_DEFINITIONS: dict[str, str] = {
    "correctness":  "Is the output factually correct and free of hallucinations?",
    "faithfulness": "Does the output stay faithful to the provided context without fabricating details?",
    "helpfulness":  "Is the output genuinely helpful and actionable for the user's need?",
    "harmlessness": "Does the output avoid harmful, toxic, or unsafe content?",
    "relevance":    "Does the output directly address the task without off-topic content?",
    "completeness": "Does the output fully address all aspects of the task?",
    "conciseness":  "Is the output appropriately concise without unnecessary verbosity?",
    "format":       "Does the output follow the expected format (bullets, JSON, table, etc.)?",
}


class StructuredJudge:
    """Multi-criterion LLM judge with configurable rubrics and pass thresholds.

    Parameters
    ----------
    criteria:
        List of criterion names.  Built-in names (correctness, faithfulness,
        helpfulness, harmlessness, relevance, completeness, conciseness,
        format) come with pre-written definitions; custom names are passed
        as-is to the judge.
    pass_threshold:
        Minimum *overall* score to mark the result as ``passed``.
    weights:
        Optional per-criterion weights for the overall score computation.
        Keys must match *criteria*.
    model:
        LLM model to use for judging.  Defaults to the env-configured model.
    """

    def __init__(
        self,
        criteria: list[str] | None = None,
        pass_threshold: float = 0.8,
        weights: dict[str, float] | None = None,
        model: str = "",
    ) -> None:
        self._criteria      = criteria or ["correctness", "helpfulness", "relevance"]
        self._pass_threshold = pass_threshold
        self._weights       = weights or {}
        self._model         = model

    async def score(
        self,
        task: str,
        output: str,
        *,
        reference: str | None = None,
        context: str | None   = None,
        rubric: str | None    = None,
    ) -> StructuredJudgeResult:
        """Grade *output* against *task* on all configured criteria."""
        criteria_schema = ", ".join(f'"{c}": <float>' for c in self._criteria)
        system = _STRUCTURED_SYSTEM.format(criteria_schema=criteria_schema)

        prompt_parts = [f"Task: {task}", f"Agent output:\n{output}"]
        if reference:
            prompt_parts.append(f"Reference answer:\n{reference}")
        if context:
            prompt_parts.append(f"Context/sources:\n{context}")
        if rubric:
            prompt_parts.append(f"Additional rubric: {rubric}")
        # Append criterion definitions
        defs = []
        for c in self._criteria:
            defn = _CRITERION_DEFINITIONS.get(c, f"Score on: {c}")
            defs.append(f"  {c}: {defn}")
        prompt_parts.append("Criterion definitions:\n" + "\n".join(defs))
        prompt = "\n\n".join(prompt_parts)

        raw = await _llm_judge(prompt, system, self._model)
        return self._parse(raw)

    def score_sync(self, task: str, output: str, **kwargs: Any) -> StructuredJudgeResult:
        """Synchronous wrapper around :meth:`score`."""
        from meshflow.integrations._utils import run_sync
        return run_sync(self.score(task, output, **kwargs))

    async def score_batch(
        self, cases: list[dict[str, Any]]
    ) -> list[StructuredJudgeResult]:
        """Grade a batch of cases concurrently."""
        return list(await asyncio.gather(*[
            self.score(c["task"], c["output"], **{k: v for k, v in c.items() if k not in ("task", "output")})
            for c in cases
        ]))

    def _parse(self, raw: dict[str, Any]) -> StructuredJudgeResult:
        by_criterion = {c: float(raw.get("criteria", {}).get(c, 0.5)) for c in self._criteria}
        if self._weights:
            total_w = sum(self._weights.get(c, 1.0) for c in self._criteria)
            overall = sum(by_criterion[c] * self._weights.get(c, 1.0) for c in self._criteria) / (total_w or 1)
        else:
            overall = float(raw.get("overall", sum(by_criterion.values()) / max(len(by_criterion), 1)))
        return StructuredJudgeResult(
            overall=round(overall, 4),
            by_criterion=by_criterion,
            reasoning=str(raw.get("reasoning", "")),
            passed=overall >= self._pass_threshold,
            raw=raw,
        )


# ── TrajectoryEval ────────────────────────────────────────────────────────────

@dataclass
class TrajectoryStep:
    """One step in an agent's reasoning trajectory."""

    thought: str = ""
    action: str  = ""
    observation: str = ""


@dataclass
class TrajectoryEvalResult:
    """Result from :class:`TrajectoryEval`."""

    path_score: float             # 0–1 overall trajectory quality
    step_scores: list[float]      # per-step quality scores
    efficiency: float             # 1 - redundant_steps / total_steps
    final_score: float            # final output quality (0–1)
    reasoning: str                # judge explanation
    passed: bool

    def __str__(self) -> str:
        return (f"TrajectoryEvalResult(path={self.path_score:.2f}, "
                f"efficiency={self.efficiency:.2f}, final={self.final_score:.2f}, "
                f"passed={self.passed})")


_TRAJECTORY_SYSTEM = """\
You are an expert AI evaluator. Analyse an agent's step-by-step reasoning trajectory.
Respond ONLY with valid JSON. Schema:
{
  "path_score": <float 0.0-1.0>,
  "step_scores": [<float>, ...],
  "redundant_steps": <int>,
  "final_score": <float 0.0-1.0>,
  "reasoning": "<1-2 sentences>"
}
path_score: overall quality of the reasoning path
step_scores: one score per trajectory step (same length as steps)
redundant_steps: number of steps that were unnecessary or circular
final_score: quality of the final output relative to the task
"""


class TrajectoryEval:
    """Evaluate the *reasoning path* of a multi-step agent run.

    Unlike output-only evals, TrajectoryEval scores each intermediate step
    for logical soundness, necessity, and progress toward the goal.

    Parameters
    ----------
    pass_threshold:
        Minimum *path_score* to mark the result as ``passed``.
    """

    def __init__(self, pass_threshold: float = 0.75, model: str = "") -> None:
        self._pass_threshold = pass_threshold
        self._model          = model

    async def evaluate(
        self,
        task: str,
        trajectory: list[Any],
        final_output: str,
    ) -> TrajectoryEvalResult:
        """Evaluate a trajectory.

        Parameters
        ----------
        task:
            The original task the agent was solving.
        trajectory:
            List of ``{"thought": …, "action": …, "observation": …}`` dicts
            OR :class:`TrajectoryStep` objects.
        final_output:
            The agent's final answer / output.
        """
        steps = [
            s if isinstance(s, TrajectoryStep)
            else TrajectoryStep(**{k: str(v) for k, v in s.items()})
            for s in trajectory
        ]

        steps_text = "\n".join(
            f"Step {i+1}: thought={s.thought!r}  action={s.action!r}  observation={s.observation!r}"
            for i, s in enumerate(steps)
        )
        prompt = (
            f"Task: {task}\n\nTrajectory:\n{steps_text}\n\n"
            f"Final output:\n{final_output}\n\n"
            f"Number of steps: {len(steps)}"
        )
        raw = await _llm_judge(prompt, _TRAJECTORY_SYSTEM, self._model)
        return self._parse(raw, len(steps))

    def evaluate_sync(
        self,
        task: str,
        trajectory: list[Any],
        final_output: str,
    ) -> TrajectoryEvalResult:
        from meshflow.integrations._utils import run_sync
        return run_sync(self.evaluate(task, trajectory, final_output))

    def _parse(self, raw: dict[str, Any], n_steps: int) -> TrajectoryEvalResult:
        path_score  = float(raw.get("path_score",  0.5))
        step_scores = [float(s) for s in raw.get("step_scores", [0.5] * n_steps)]
        # Pad / truncate to match actual step count
        while len(step_scores) < n_steps:
            step_scores.append(0.5)
        step_scores = step_scores[:n_steps]
        redundant   = int(raw.get("redundant_steps", 0))
        efficiency  = round(1 - redundant / max(n_steps, 1), 4)
        final_score = float(raw.get("final_score", 0.5))
        return TrajectoryEvalResult(
            path_score=round(path_score, 4),
            step_scores=[round(s, 4) for s in step_scores],
            efficiency=efficiency,
            final_score=round(final_score, 4),
            reasoning=str(raw.get("reasoning", "")),
            passed=path_score >= self._pass_threshold,
        )


# ── RAGEval ───────────────────────────────────────────────────────────────────

@dataclass
class RAGEvalResult:
    """RAGAS-style RAG evaluation result."""

    faithfulness: float       # is the answer grounded in the retrieved context?
    answer_relevance: float   # does the answer address the question?
    context_recall: float     # does the context contain the information needed?
    overall: float            # mean of the three metrics
    reasoning: str
    passed: bool

    def __str__(self) -> str:
        return (f"RAGEvalResult(faithfulness={self.faithfulness:.2f}, "
                f"relevance={self.answer_relevance:.2f}, "
                f"recall={self.context_recall:.2f}, "
                f"overall={self.overall:.2f}, passed={self.passed})")


_RAG_SYSTEM = """\
You are an expert RAG evaluation judge. Evaluate on three RAGAS metrics.
Respond ONLY with valid JSON. Schema:
{
  "faithfulness": <float 0.0-1.0>,
  "answer_relevance": <float 0.0-1.0>,
  "context_recall": <float 0.0-1.0>,
  "reasoning": "<1-2 sentences>"
}
faithfulness: fraction of answer claims supported by the retrieved contexts (1.0 = all claims supported)
answer_relevance: how well the answer addresses the question (1.0 = perfectly on-topic)
context_recall: fraction of reference-answer content present in the retrieved contexts (0.5 if no reference)
"""


class RAGEval:
    """RAGAS-style evaluation for retrieval-augmented generation agents.

    Measures three metrics:
    - **Faithfulness** — are all answer claims grounded in the retrieved context?
    - **Answer relevance** — does the answer address the question?
    - **Context recall** — does the retrieved context contain the needed info?

    Parameters
    ----------
    pass_threshold:
        Minimum *overall* (mean of three metrics) to mark as ``passed``.
    """

    def __init__(self, pass_threshold: float = 0.75, model: str = "") -> None:
        self._pass_threshold = pass_threshold
        self._model          = model

    async def evaluate(
        self,
        question: str,
        answer: str,
        retrieved_contexts: list[str],
        reference_answer: str | None = None,
    ) -> RAGEvalResult:
        """Evaluate a RAG answer.

        Parameters
        ----------
        question:
            The user's question.
        answer:
            The agent's generated answer.
        retrieved_contexts:
            List of retrieved document chunks used to generate the answer.
        reference_answer:
            Gold-standard answer (optional; improves context_recall scoring).
        """
        ctx_text = "\n\n".join(f"[{i+1}] {c}" for i, c in enumerate(retrieved_contexts))
        prompt_parts = [
            f"Question: {question}",
            f"Generated answer:\n{answer}",
            f"Retrieved contexts:\n{ctx_text}",
        ]
        if reference_answer:
            prompt_parts.append(f"Reference answer:\n{reference_answer}")
        else:
            prompt_parts.append("(No reference answer provided — set context_recall to 0.5)")
        prompt = "\n\n".join(prompt_parts)
        raw    = await _llm_judge(prompt, _RAG_SYSTEM, self._model)
        return self._parse(raw)

    def evaluate_sync(
        self,
        question: str,
        answer: str,
        retrieved_contexts: list[str],
        reference_answer: str | None = None,
    ) -> RAGEvalResult:
        from meshflow.integrations._utils import run_sync
        return run_sync(self.evaluate(question, answer, retrieved_contexts, reference_answer))

    def _parse(self, raw: dict[str, Any]) -> RAGEvalResult:
        f = round(float(raw.get("faithfulness",    0.5)), 4)
        a = round(float(raw.get("answer_relevance",0.5)), 4)
        c = round(float(raw.get("context_recall",  0.5)), 4)
        overall = round((f + a + c) / 3, 4)
        return RAGEvalResult(
            faithfulness=f,
            answer_relevance=a,
            context_recall=c,
            overall=overall,
            reasoning=str(raw.get("reasoning", "")),
            passed=overall >= self._pass_threshold,
        )


# ── EvalCI ────────────────────────────────────────────────────────────────────

class EvalRegressionError(Exception):
    """Raised by :class:`EvalCI` when score regresses below baseline."""


@dataclass
class EvalCIReport:
    """Result of a CI eval gate run."""

    suite_name: str
    mean_score: float
    baseline_score: float
    passed: bool
    regression: bool
    delta: float
    n_cases: int
    n_passed: int
    results: list[Any] = field(default_factory=list)

    def __str__(self) -> str:
        status = "✅ PASS" if self.passed else "❌ FAIL"
        return (
            f"{status}  suite={self.suite_name}  "
            f"score={self.mean_score:.3f}  baseline={self.baseline_score:.3f}  "
            f"delta={self.delta:+.3f}  cases={self.n_cases}/{self.n_passed} passed"
        )


class EvalCI:
    """CI regression gate for eval suites.

    Runs an eval suite and raises :class:`EvalRegressionError` when the mean
    judge score drops below the configured *baseline_score*.  Designed to be
    dropped into a ``pytest`` session or a ``meshflow eval ci`` invocation.

    Parameters
    ----------
    baseline_score:
        Minimum acceptable mean score (0.0–1.0).  Typical: 0.80.
    suite_name:
        Human-readable name for the suite (used in reports / alerts).
    judge:
        :class:`StructuredJudge` or :class:`~meshflow.eval.judge.LLMJudge`
        instance.  Defaults to a new :class:`StructuredJudge`.
    fail_on_regression:
        If True (default), raise :class:`EvalRegressionError` when the gate
        fails.  Set to False to only return the report without raising.
    cloud:
        Optional :class:`~meshflow.cloud.MeshFlowCloud` instance for reporting
        eval results to the dashboard.
    """

    def __init__(
        self,
        baseline_score: float = 0.80,
        suite_name: str = "default",
        judge: Any = None,
        fail_on_regression: bool = True,
        cloud: Any = None,
    ) -> None:
        self._baseline = baseline_score
        self._suite    = suite_name
        self._judge    = judge or StructuredJudge(pass_threshold=baseline_score)
        self._fail     = fail_on_regression
        self._cloud    = cloud

    async def run(
        self,
        cases: list[dict[str, Any]],
    ) -> EvalCIReport:
        """Run all *cases* and return a :class:`EvalCIReport`.

        Each case dict requires ``task`` and ``output`` keys; optional keys:
        ``reference``, ``context``, ``scenario`` (name for reporting).
        """
        results = await self._judge.score_batch(cases)
        scores  = [r.overall for r in results]
        mean    = sum(scores) / len(scores) if scores else 0.0
        n_pass  = sum(1 for r in results if r.passed)
        delta   = mean - self._baseline
        regressed = mean < self._baseline

        # Report to cloud dashboard
        if self._cloud is not None:
            for i, (case, result) in enumerate(zip(cases, results)):
                scenario = case.get("scenario", f"case_{i}")
                try:
                    self._cloud.report_eval(
                        run_id=case.get("run_id", ""),
                        suite=self._suite,
                        scenario=scenario,
                        metric="overall",
                        score=result.overall,
                        passed=result.passed,
                        reasoning=result.reasoning,
                    )
                except Exception:
                    pass

        report = EvalCIReport(
            suite_name=self._suite,
            mean_score=round(mean, 4),
            baseline_score=self._baseline,
            passed=not regressed,
            regression=regressed,
            delta=round(delta, 4),
            n_cases=len(cases),
            n_passed=n_pass,
            results=results,
        )

        if regressed and self._fail:
            raise EvalRegressionError(
                f"Eval suite '{self._suite}' regressed: "
                f"mean={mean:.3f} < baseline={self._baseline:.3f} (Δ={delta:+.3f})"
            )
        return report

    def run_sync(self, cases: list[dict[str, Any]]) -> EvalCIReport:
        from meshflow.integrations._utils import run_sync
        return run_sync(self.run(cases))


# ── Shared LLM judge helper ───────────────────────────────────────────────────

async def _llm_judge(prompt: str, system: str, model: str = "") -> dict[str, Any]:
    """Call the LLM with a judge prompt and parse the JSON response."""
    try:
        from meshflow.agents.base import auto_detect_provider
        provider = auto_detect_provider()
        m = model or ""
        content, _, _ = await provider.complete(m, [{"role": "user", "content": prompt}], system, 1024)
    except Exception:
        # Fallback when no API key — return neutral 0.5 scores
        return {
            "overall": 0.5,
            "criteria": {},
            "path_score": 0.5,
            "step_scores": [0.5] * 3,
            "redundant_steps": 0,
            "final_score": 0.5,
            "faithfulness": 0.5,
            "answer_relevance": 0.5,
            "context_recall": 0.5,
            "reasoning": "[offline — no API key]",
        }
    # Extract JSON from the response
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        return {"overall": 0.5, "reasoning": content}
    try:
        return json.loads(m.group())
    except Exception:
        return {"overall": 0.5, "reasoning": content}
