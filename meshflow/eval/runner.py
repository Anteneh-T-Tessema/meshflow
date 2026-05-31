"""Agent evaluation runner — test agents against defined scenarios.

Eval YAML schema
----------------
version: "1.0"
name: "Research Agent Evals"
policy: dev           # governance mode for eval runs

scenarios:
  - name: basic_qa
    input: "What is the capital of France?"
    expected_contains: ["Paris"]
    expected_not_contains: ["Berlin", "London"]
    min_confidence: 0.8
    max_tokens: 200
    tags: [smoke, factual]

  - name: code_generation
    input: "Write a Python function that reverses a string"
    expected_contains: ["def", "return"]
    eval_fn: check_runnable_python     # built-in checker
    max_tokens: 500
    tags: [code]

  - name: json_output
    input: "Return JSON with keys: name, age, city"
    eval_fn: valid_json
    tags: [structured]

  - name: custom_eval
    input: "Summarize the French Revolution in one sentence"
    eval_fn: |
      # Inline Python — receives (output: str) -> bool
      words = output.split()
      return 5 <= len(words) <= 50

Built-in eval_fn values: valid_json, check_runnable_python, non_empty, no_hallucination_markers
"""

from __future__ import annotations

import ast
import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml


# ── Built-in checkers ─────────────────────────────────────────────────────────

def _valid_json(output: str) -> bool:
    try:
        json.loads(output.strip())
        return True
    except Exception:
        # Try to extract JSON from mixed output
        import re
        m = re.search(r"\{[\s\S]+\}", output)
        if m:
            try:
                json.loads(m.group())
                return True
            except Exception:
                pass
    return False


def _check_runnable_python(output: str) -> bool:
    import re
    code_blocks = re.findall(r"```(?:python)?\n?([\s\S]+?)```", output)
    if code_blocks:
        src = code_blocks[0]
    else:
        # Assume the whole output is code
        src = output
    try:
        ast.parse(src)
        return True
    except SyntaxError:
        return False


def _non_empty(output: str) -> bool:
    return len(output.strip()) > 10


def _no_hallucination_markers(output: str) -> bool:
    markers = [
        "as an AI", "I cannot", "I don't have access",
        "I'm not able to", "I apologize",
    ]
    lower = output.lower()
    return not any(m.lower() in lower for m in markers)


_BUILTIN_CHECKERS: dict[str, Callable[[str], bool]] = {
    "valid_json": _valid_json,
    "check_runnable_python": _check_runnable_python,
    "non_empty": _non_empty,
    "no_hallucination_markers": _no_hallucination_markers,
}


# ── Scenario ──────────────────────────────────────────────────────────────────

@dataclass
class EvalScenario:
    """A single evaluation scenario."""

    name: str
    input: str
    expected_contains: list[str] = field(default_factory=list)
    expected_not_contains: list[str] = field(default_factory=list)
    min_confidence: float = 0.0
    max_tokens: int = 0
    eval_fn: str | Callable | None = None
    tags: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    weight: float = 1.0  # weight for scoring

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EvalScenario":
        return cls(
            name=d["name"],
            input=d["input"],
            expected_contains=d.get("expected_contains", []),
            expected_not_contains=d.get("expected_not_contains", []),
            min_confidence=float(d.get("min_confidence", 0.0)),
            max_tokens=int(d.get("max_tokens", 0)),
            eval_fn=d.get("eval_fn"),
            tags=d.get("tags", []),
            context=d.get("context", {}),
            weight=float(d.get("weight", 1.0)),
        )

    def _resolve_fn(self) -> Callable[[str], bool] | None:
        if self.eval_fn is None:
            return None
        if callable(self.eval_fn):
            return self.eval_fn
        fn_name = str(self.eval_fn).strip()
        if fn_name in _BUILTIN_CHECKERS:
            return _BUILTIN_CHECKERS[fn_name]
        # Inline Python expression / block
        if "\n" in fn_name or "return " in fn_name or "output" in fn_name:
            local_ns: dict[str, Any] = {}
            exec("def _eval_fn(output):\n" + "\n".join("    " + ln for ln in fn_name.splitlines()), local_ns)
            return local_ns["_eval_fn"]
        return None

    def evaluate(self, output: str, tokens: int, confidence: float) -> "ScenarioResult":
        checks: dict[str, bool] = {}

        # Contains checks
        for phrase in self.expected_contains:
            checks[f"contains:{phrase[:30]}"] = phrase.lower() in output.lower()

        # Not-contains checks
        for phrase in self.expected_not_contains:
            checks[f"not_contains:{phrase[:30]}"] = phrase.lower() not in output.lower()

        # Token budget
        if self.max_tokens > 0:
            checks["within_token_budget"] = tokens <= self.max_tokens

        # Confidence floor
        if self.min_confidence > 0:
            checks["min_confidence"] = confidence >= self.min_confidence

        # Custom eval function
        fn = self._resolve_fn()
        if fn is not None:
            try:
                checks["custom_eval"] = bool(fn(output))
            except Exception as e:
                checks["custom_eval"] = False
                checks[f"custom_eval_error:{e}"] = False

        passed = all(checks.values())
        score = sum(checks.values()) / max(len(checks), 1)

        return ScenarioResult(
            scenario_name=self.name,
            passed=passed,
            score=score,
            checks=checks,
            output=output,
            tokens=tokens,
            confidence=confidence,
        )


# ── Results ───────────────────────────────────────────────────────────────────

@dataclass
class ScenarioResult:
    scenario_name: str
    passed: bool
    score: float  # 0.0 – 1.0
    checks: dict[str, bool]
    output: str
    tokens: int
    confidence: float
    duration_ms: float = 0.0
    error: str = ""


@dataclass
class EvalResult:
    """Aggregated result of running a full EvalSuite."""

    suite_name: str
    total: int
    passed: int
    failed: int
    errors: int
    pass_rate: float
    weighted_score: float
    total_tokens: int
    total_cost_usd: float
    duration_s: float
    scenarios: list[ScenarioResult]

    def report(self, verbose: bool = False) -> str:
        lines = [
            f"\n{'='*60}",
            f"  MeshFlow Eval Report: {self.suite_name}",
            f"{'='*60}",
            f"  Pass rate   : {self.pass_rate:.1%}  ({self.passed}/{self.total})",
            f"  Score       : {self.weighted_score:.3f}",
            f"  Tokens used : {self.total_tokens:,}",
            f"  Cost        : ${self.total_cost_usd:.4f}",
            f"  Duration    : {self.duration_s:.2f}s",
            f"{'='*60}",
        ]
        for sr in self.scenarios:
            status = "PASS" if sr.passed else "FAIL"
            lines.append(f"  [{status}] {sr.scenario_name}  (score={sr.score:.2f}, tokens={sr.tokens})")
            if verbose or not sr.passed:
                for check, ok in sr.checks.items():
                    mark = "✓" if ok else "✗"
                    lines.append(f"         {mark} {check}")
                if sr.error:
                    lines.append(f"         ERROR: {sr.error}")
        lines.append(f"{'='*60}\n")
        return "\n".join(lines)


# ── EvalSuite ─────────────────────────────────────────────────────────────────

class EvalSuite:
    """A collection of evaluation scenarios for testing an agent."""

    def __init__(
        self,
        name: str,
        scenarios: list[EvalScenario],
        policy: Any = None,
    ) -> None:
        self.name = name
        self.scenarios = scenarios
        self.policy = policy

    @classmethod
    def from_yaml(cls, path: str | Path) -> "EvalSuite":
        """Load an EvalSuite from a YAML file."""
        p = Path(path)
        raw: dict[str, Any] = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

        from meshflow.core.schemas import policy_for_mode

        name = raw.get("name", p.stem)
        policy = policy_for_mode(raw.get("policy", "dev"))
        scenarios = [EvalScenario.from_dict(s) for s in raw.get("scenarios", [])]
        return cls(name=name, scenarios=scenarios, policy=policy)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvalSuite":
        """Load an EvalSuite from a dict (for inline test definitions)."""
        from meshflow.core.schemas import policy_for_mode

        name = data.get("name", "eval")
        policy = policy_for_mode(data.get("policy", "dev"))
        scenarios = [EvalScenario.from_dict(s) for s in data.get("scenarios", [])]
        return cls(name=name, scenarios=scenarios, policy=policy)

    def filter(self, tags: list[str]) -> "EvalSuite":
        """Return a new suite with only scenarios matching any of the given tags."""
        filtered = [s for s in self.scenarios if any(t in s.tags for t in tags)]
        return EvalSuite(self.name, filtered, self.policy)

    async def run(
        self,
        agent: Any,
        concurrency: int = 4,
        context: dict[str, Any] | None = None,
    ) -> EvalResult:
        """Run all scenarios against the given agent.

        Parameters
        ----------
        agent:       Any object with an async ``run(task, context) -> dict`` method.
        concurrency: Max number of scenarios to run simultaneously.
        context:     Extra context dict passed to every agent.run() call.
        """
        start = time.monotonic()
        sem = asyncio.Semaphore(concurrency)
        tasks = [self._run_one(s, agent, sem, context or {}) for s in self.scenarios]
        scenario_results: list[ScenarioResult] = await asyncio.gather(*tasks)

        passed = sum(1 for r in scenario_results if r.passed)
        errors = sum(1 for r in scenario_results if r.error)
        total_tokens = sum(r.tokens for r in scenario_results)

        total_weight = sum(s.weight for s in self.scenarios)
        weighted_score = (
            sum(r.score * s.weight for r, s in zip(scenario_results, self.scenarios))
            / total_weight
            if total_weight > 0
            else 0.0
        )

        return EvalResult(
            suite_name=self.name,
            total=len(self.scenarios),
            passed=passed,
            failed=len(self.scenarios) - passed - errors,
            errors=errors,
            pass_rate=passed / max(len(self.scenarios), 1),
            weighted_score=weighted_score,
            total_tokens=total_tokens,
            total_cost_usd=0.0,
            duration_s=time.monotonic() - start,
            scenarios=scenario_results,
        )

    async def _run_one(
        self,
        scenario: EvalScenario,
        agent: Any,
        sem: asyncio.Semaphore,
        context: dict[str, Any],
    ) -> ScenarioResult:
        async with sem:
            t0 = time.monotonic()
            try:
                ctx = {**context, **scenario.context}
                result = await agent.run(scenario.input, ctx)
                output = result.get("result", "")
                tokens = result.get("tokens", 0)
                confidence = result.get("stated_confidence", 0.8)
                sr = scenario.evaluate(output, tokens, confidence)
                sr.duration_ms = (time.monotonic() - t0) * 1000
                return sr
            except Exception as e:
                return ScenarioResult(
                    scenario_name=scenario.name,
                    passed=False,
                    score=0.0,
                    checks={},
                    output="",
                    tokens=0,
                    confidence=0.0,
                    duration_ms=(time.monotonic() - t0) * 1000,
                    error=str(e),
                )


# ── Top-level helper ──────────────────────────────────────────────────────────

async def run_eval(
    agent: Any,
    eval_path: str | Path | None = None,
    eval_dict: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    concurrency: int = 4,
    verbose: bool = True,
) -> EvalResult:
    """Convenience function: load a suite, run it, print the report.

    Usage:
        result = await run_eval(my_agent, "evals.yaml")
        assert result.pass_rate >= 0.9
    """
    if eval_path:
        suite = EvalSuite.from_yaml(eval_path)
    elif eval_dict:
        suite = EvalSuite.from_dict(eval_dict)
    else:
        raise ValueError("Provide eval_path or eval_dict.")

    if tags:
        suite = suite.filter(tags)

    result = await suite.run(agent, concurrency=concurrency)

    if verbose:
        print(result.report())

    return result
