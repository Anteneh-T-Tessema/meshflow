"""MeshFlow Agent Evaluation Framework.

Run evaluations from CLI:
    meshflow eval evals.yaml --agent my_agent.py

Or programmatically:
    from meshflow.eval import EvalSuite, run_eval, EvalBaseline

    suite = EvalSuite.from_yaml("evals.yaml")
    result = await suite.run(agent)
    print(result.report())

    # Save golden baseline for CI regression:
    baseline = EvalBaseline.from_result(result)
    baseline.save("evals/baseline.json")

    # Next CI run — compare:
    old = EvalBaseline.load("evals/baseline.json")
    new_result = await suite.run(agent)
    diff = old.diff(EvalBaseline.from_result(new_result))
    if diff.has_regressions:
        sys.exit(1)
"""

from meshflow.eval.runner import (
    EvalResult,
    EvalScenario,
    EvalSuite,
    ScenarioResult,
    run_eval,
)
from meshflow.eval.baseline import BaselineDiff, EvalBaseline, ScenarioBaseline
from meshflow.eval.feedback import FeedbackRecord, FeedbackStore
from meshflow.eval.shadow import ShadowResult, shadow_run, RegressionAlert, RegressionDetector

__all__ = [
    "EvalSuite",
    "EvalScenario",
    "EvalResult",
    "ScenarioResult",
    "run_eval",
    "EvalBaseline",
    "ScenarioBaseline",
    "BaselineDiff",
    "FeedbackRecord",
    "FeedbackStore",
    "ShadowResult",
    "shadow_run",
    "RegressionAlert",
    "RegressionDetector",
]
