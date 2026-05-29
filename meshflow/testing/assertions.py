"""Assertion helpers for WorkflowResult testing.

Usage::

    from meshflow.testing import (
        assert_completed,
        assert_node_executed,
        assert_cost_within,
        WorkflowAssertion,
    )

    result = await wf.run(task="...", runtime=runtime)

    # Standalone assertions (raise AssertionError on failure)
    assert_completed(result)
    assert_node_executed(result, "researcher")
    assert_cost_within(result, max_usd=0.05)

    # Fluent builder
    WorkflowAssertion(result).completed().node_ran("researcher").cost_within(0.05)
"""

from __future__ import annotations

from typing import Any


# ── Standalone assertion functions ────────────────────────────────────────────

def assert_completed(result: Any, msg: str = "") -> None:
    """Assert that the workflow completed without blocks or pauses."""
    if not result.completed:
        detail = f"blocked={result.blocked_nodes}, paused={result.paused_nodes}"
        raise AssertionError(
            f"Workflow did not complete. {detail}" + (f" — {msg}" if msg else "")
        )


def assert_node_executed(result: Any, node_id: str, msg: str = "") -> None:
    """Assert that *node_id* appears in the run's step outcomes."""
    executed = {s.node_id for s in result.steps}
    if node_id not in executed:
        raise AssertionError(
            f"Node {node_id!r} was not executed. Executed: {sorted(executed)}"
            + (f" — {msg}" if msg else "")
        )


def assert_node_not_executed(result: Any, node_id: str, msg: str = "") -> None:
    """Assert that *node_id* was NOT executed (e.g. skipped by condition)."""
    executed = {s.node_id for s in result.steps}
    if node_id in executed:
        raise AssertionError(
            f"Node {node_id!r} was executed but should not have been."
            + (f" — {msg}" if msg else "")
        )


def assert_node_blocked(result: Any, node_id: str, msg: str = "") -> None:
    """Assert that *node_id* was blocked by governance."""
    if node_id not in result.blocked_nodes:
        raise AssertionError(
            f"Node {node_id!r} was not blocked. Blocked nodes: {result.blocked_nodes}"
            + (f" — {msg}" if msg else "")
        )


def assert_cost_within(result: Any, max_usd: float, msg: str = "") -> None:
    """Assert that total cost ≤ *max_usd*."""
    if result.total_cost_usd > max_usd:
        raise AssertionError(
            f"Cost ${result.total_cost_usd:.5f} exceeds limit ${max_usd:.5f}"
            + (f" — {msg}" if msg else "")
        )


def assert_tokens_within(result: Any, max_tokens: int, msg: str = "") -> None:
    """Assert that total token usage ≤ *max_tokens*."""
    if result.total_tokens > max_tokens:
        raise AssertionError(
            f"Tokens {result.total_tokens:,} exceeds limit {max_tokens:,}"
            + (f" — {msg}" if msg else "")
        )


def assert_output_contains(result: Any, substring: str, msg: str = "") -> None:
    """Assert that the workflow's final output contains *substring*."""
    if substring not in result.output:
        preview = result.output[:120].replace("\n", " ")
        raise AssertionError(
            f"Output does not contain {substring!r}. Got: {preview!r}"
            + (f" — {msg}" if msg else "")
        )


def assert_output_matches(result: Any, pattern: str, msg: str = "") -> None:
    """Assert that the workflow output matches a regex *pattern*."""
    import re
    if not re.search(pattern, result.output):
        raise AssertionError(
            f"Output does not match pattern {pattern!r}. Got: {result.output[:120]!r}"
            + (f" — {msg}" if msg else "")
        )


def assert_step_count(result: Any, expected: int, msg: str = "") -> None:
    """Assert exact number of steps executed."""
    actual = len(result.steps)
    if actual != expected:
        raise AssertionError(
            f"Expected {expected} steps, got {actual}"
            + (f" — {msg}" if msg else "")
        )


def assert_confidence_above(result: Any, min_confidence: float, msg: str = "") -> None:
    """Assert that every step's output confidence ≥ *min_confidence*."""
    low = [
        (s.node_id, s.output.confidence)
        for s in result.steps
        if s.output.confidence < min_confidence
    ]
    if low:
        detail = ", ".join(f"{nid}={c:.2f}" for nid, c in low)
        raise AssertionError(
            f"Nodes with confidence below {min_confidence}: {detail}"
            + (f" — {msg}" if msg else "")
        )


# ── Fluent assertion builder ──────────────────────────────────────────────────

class WorkflowAssertion:
    """Fluent assertion builder for :class:`~meshflow.core.workflow.WorkflowResult`.

    Usage::

        WorkflowAssertion(result)\\
            .completed()\\
            .node_ran("researcher")\\
            .node_skipped("reviewer")\\
            .cost_within(0.05)\\
            .output_contains("HIPAA")
    """

    def __init__(self, result: Any) -> None:
        self._result = result

    def completed(self, msg: str = "") -> "WorkflowAssertion":
        assert_completed(self._result, msg)
        return self

    def node_ran(self, node_id: str, msg: str = "") -> "WorkflowAssertion":
        assert_node_executed(self._result, node_id, msg)
        return self

    def node_skipped(self, node_id: str, msg: str = "") -> "WorkflowAssertion":
        assert_node_not_executed(self._result, node_id, msg)
        return self

    def node_blocked(self, node_id: str, msg: str = "") -> "WorkflowAssertion":
        assert_node_blocked(self._result, node_id, msg)
        return self

    def cost_within(self, max_usd: float, msg: str = "") -> "WorkflowAssertion":
        assert_cost_within(self._result, max_usd, msg)
        return self

    def tokens_within(self, max_tokens: int, msg: str = "") -> "WorkflowAssertion":
        assert_tokens_within(self._result, max_tokens, msg)
        return self

    def output_contains(self, substring: str, msg: str = "") -> "WorkflowAssertion":
        assert_output_contains(self._result, substring, msg)
        return self

    def output_matches(self, pattern: str, msg: str = "") -> "WorkflowAssertion":
        assert_output_matches(self._result, pattern, msg)
        return self

    def step_count(self, expected: int, msg: str = "") -> "WorkflowAssertion":
        assert_step_count(self._result, expected, msg)
        return self

    def confidence_above(self, threshold: float, msg: str = "") -> "WorkflowAssertion":
        assert_confidence_above(self._result, threshold, msg)
        return self


__all__ = [
    "WorkflowAssertion",
    "assert_completed",
    "assert_node_executed",
    "assert_node_not_executed",
    "assert_node_blocked",
    "assert_cost_within",
    "assert_tokens_within",
    "assert_output_contains",
    "assert_output_matches",
    "assert_step_count",
    "assert_confidence_above",
]
