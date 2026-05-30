"""BranchCompare — parallel fork execution with output diffing.

Closes the LangGraph time-travel 'Branch & Compare' mode gap. Runs two or
more workflow forks in parallel from the same checkpoint, each with different
configuration (model, prompt, context patch), then diffs their outputs.

Critical for:
- Prompt A/B testing without re-running full pipelines
- Model comparison on real production checkpoints
- Debugging divergent behaviour between prompt versions

LangGraph equivalent: fork a thread_id with modified state → re-execute →
compare outputs. MeshFlow implementation runs N forks concurrently using
asyncio.gather() and produces a structured diff report.

Usage::

    from meshflow.core.branch_compare import BranchCompare, ForkConfig

    bc = BranchCompare(ledger_db="meshflow_runs.db")

    result = await bc.compare(
        run_id="prod-run-abc123",
        to_step=3,
        forks=[
            ForkConfig(
                label="baseline",
                model_override="claude-sonnet-4-6",
            ),
            ForkConfig(
                label="haiku-downgrade",
                model_override="claude-haiku-4-5-20251001",
                prompt_override="Be more concise.",
            ),
            ForkConfig(
                label="injected-context",
                context_patch={"user_tier": "enterprise", "region": "eu-west-1"},
            ),
        ],
    )

    print(result.winner)           # fork label with highest confidence
    print(result.diff_summary)     # human-readable diff table
    for fork in result.forks:
        print(fork.label, fork.output[:80], fork.confidence)

CLI::

    meshflow replay <run_id> --branch-compare --at-step 3 \\
        --fork baseline:model=claude-sonnet-4-6 \\
        --fork haiku:model=claude-haiku-4-5-20251001,prompt="be concise"
"""

from __future__ import annotations

import asyncio
import difflib
import time
from dataclasses import dataclass, field
from typing import Any


# ── ForkConfig ────────────────────────────────────────────────────────────────


@dataclass
class ForkConfig:
    """Configuration for one branch in a BranchCompare run.

    Parameters
    ----------
    label:          Human-readable name for this fork (e.g. ``"baseline"``).
    model_override: Swap every agent in the fork to this model string.
    prompt_override: Prepend this text to every agent's system prompt.
    context_patch:  Extra key/value pairs injected into the shared context
                    before re-execution (State Injection mode).
    workflow_yaml:  Path to the workflow YAML for this fork. Defaults to the
                    same YAML as the original run if not provided.
    """

    label: str
    model_override: str = ""
    prompt_override: str = ""
    context_patch: dict[str, Any] = field(default_factory=dict)
    workflow_yaml: str = ""


# ── ForkResult ────────────────────────────────────────────────────────────────


@dataclass
class ForkResult:
    """Output of a single fork execution.

    Attributes
    ----------
    label:          Matches ForkConfig.label.
    output:         Final text output from the fork.
    completed:      Whether the fork completed without error.
    confidence:     Confidence score extracted from the output (0–1).
    steps_replayed: Number of steps re-executed live.
    total_cost_usd: Total LLM cost for this fork.
    total_tokens:   Total tokens used.
    latency_ms:     Wall-clock time for this fork in milliseconds.
    error:          Error message if the fork failed.
    """

    label: str
    output: str
    completed: bool
    confidence: float = 0.7
    steps_replayed: int = 0
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    latency_ms: float = 0.0
    error: str = ""
    model_used: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "output_preview": self.output[:300],
            "completed": self.completed,
            "confidence": round(self.confidence, 3),
            "steps_replayed": self.steps_replayed,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "total_tokens": self.total_tokens,
            "latency_ms": round(self.latency_ms, 1),
            "error": self.error,
            "model_used": self.model_used,
        }


# ── CompareResult ─────────────────────────────────────────────────────────────


@dataclass
class CompareResult:
    """Aggregate result of a BranchCompare run.

    Attributes
    ----------
    run_id:         Original run ID that was forked.
    fork_point:     Step index at which the forks diverged (1-based).
    forks:          Per-fork execution results.
    winner:         Label of the fork with the highest confidence score.
                    ``""`` if all forks failed.
    diff_summary:   Human-readable line-diff between the two best forks
                    (empty string if fewer than 2 forks succeeded).
    total_time_ms:  Wall-clock time for the entire compare run.
    """

    run_id: str
    fork_point: int
    forks: list[ForkResult]
    winner: str = ""
    diff_summary: str = ""
    total_time_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "fork_point": self.fork_point,
            "winner": self.winner,
            "diff_summary": self.diff_summary,
            "total_time_ms": round(self.total_time_ms, 1),
            "forks": [f.to_dict() for f in self.forks],
        }

    def cost_comparison(self) -> list[dict[str, Any]]:
        """Return per-fork cost sorted cheapest first."""
        return sorted(
            [{"label": f.label, "cost_usd": f.total_cost_usd} for f in self.forks],
            key=lambda x: x["cost_usd"],
        )

    def quality_comparison(self) -> list[dict[str, Any]]:
        """Return per-fork confidence sorted highest first."""
        return sorted(
            [{"label": f.label, "confidence": f.confidence} for f in self.forks],
            key=lambda x: x["confidence"],
            reverse=True,
        )


# ── Diff helpers ──────────────────────────────────────────────────────────────


def _word_diff(a: str, b: str, context: int = 3) -> str:
    """Produce a unified-style diff between two text outputs."""
    a_lines = a.splitlines(keepends=True)
    b_lines = b.splitlines(keepends=True)
    diff = list(difflib.unified_diff(a_lines, b_lines, lineterm="", n=context))
    if not diff:
        return "(outputs identical)"
    return "".join(diff[:100])  # cap at 100 diff lines


def _extract_confidence(text: str) -> float:
    import re
    patterns = [
        r"confidence[:\s=]+([0-9]\.[0-9]+)",
        r"\[([0-9]\.[0-9]+)\]",
        r"([0-9]{1,3})\s*%\s*confident",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = float(m.group(1))
            return min(max(val / 100.0 if val > 1.0 else val, 0.0), 1.0)
    return 0.7


# ── BranchCompare ─────────────────────────────────────────────────────────────


class BranchCompare:
    """Run N workflow forks in parallel from a checkpoint and diff their outputs.

    Implements LangGraph's 'Branch & Compare' mode — critical for prompt A/B
    testing without re-running full pipelines.

    Parameters
    ----------
    ledger_db:
        Path to the SQLite ledger (same ``--db`` used by ``meshflow run``).
    max_parallel:
        Maximum number of forks to run concurrently (default: 4).
    """

    def __init__(
        self,
        ledger_db: str = "meshflow_runs.db",
        *,
        max_parallel: int = 4,
    ) -> None:
        self._db = ledger_db
        self._max_parallel = max_parallel

    async def compare(
        self,
        run_id: str,
        to_step: int,
        forks: list[ForkConfig],
        *,
        timeout_per_fork: float = 300.0,
    ) -> CompareResult:
        """Execute all forks in parallel and return a CompareResult.

        Parameters
        ----------
        run_id:           Original run to fork from.
        to_step:          Step index at which to diverge (1-based).
        forks:            List of ForkConfig describing each branch.
        timeout_per_fork: Per-fork wall-clock timeout in seconds.

        Returns
        -------
        CompareResult with all fork outputs, winner, and diff summary.
        """
        if not forks:
            raise ValueError("At least one ForkConfig is required")

        t_start = time.monotonic()

        # Run all forks concurrently (bounded by max_parallel)
        semaphore = asyncio.Semaphore(self._max_parallel)

        async def _run_one(cfg: ForkConfig) -> ForkResult:
            async with semaphore:
                return await self._execute_fork(run_id, to_step, cfg, timeout_per_fork)

        fork_results: list[ForkResult] = list(
            await asyncio.gather(*[_run_one(cfg) for cfg in forks])
        )

        total_ms = (time.monotonic() - t_start) * 1000.0

        # Determine winner by confidence
        successful = [f for f in fork_results if f.completed]
        winner = max(successful, key=lambda f: f.confidence).label if successful else ""

        # Diff the top-2 successful forks
        diff_summary = ""
        if len(successful) >= 2:
            best = sorted(successful, key=lambda f: f.confidence, reverse=True)
            diff_summary = _word_diff(best[0].output, best[1].output)

        return CompareResult(
            run_id=run_id,
            fork_point=to_step,
            forks=fork_results,
            winner=winner,
            diff_summary=diff_summary,
            total_time_ms=total_ms,
        )

    async def _execute_fork(
        self,
        run_id: str,
        to_step: int,
        cfg: ForkConfig,
        timeout_s: float,
    ) -> ForkResult:
        """Execute a single fork via RewindEngine."""
        from meshflow.core.time_travel import RewindEngine

        t0 = time.monotonic()
        engine = RewindEngine(self._db)

        try:
            result = await asyncio.wait_for(
                engine.rewind(
                    run_id=run_id,
                    to_step=to_step,
                    workflow_yaml=cfg.workflow_yaml,
                    model_override=cfg.model_override,
                    prompt_override=cfg.prompt_override,
                    context_patch=cfg.context_patch or None,
                ),
                timeout=timeout_s,
            )
            latency_ms = (time.monotonic() - t0) * 1000.0
            return ForkResult(
                label=cfg.label,
                output=result.output,
                completed=result.completed,
                confidence=_extract_confidence(result.output),
                steps_replayed=result.steps_replayed,
                total_cost_usd=result.total_cost_usd,
                total_tokens=result.total_tokens,
                latency_ms=latency_ms,
                model_used=cfg.model_override or "default",
            )
        except asyncio.TimeoutError:
            return ForkResult(
                label=cfg.label,
                output="",
                completed=False,
                latency_ms=(time.monotonic() - t0) * 1000.0,
                error=f"Fork timed out after {timeout_s}s",
            )
        except Exception as exc:
            return ForkResult(
                label=cfg.label,
                output="",
                completed=False,
                latency_ms=(time.monotonic() - t0) * 1000.0,
                error=str(exc),
            )


__all__ = ["BranchCompare", "ForkConfig", "ForkResult", "CompareResult"]
