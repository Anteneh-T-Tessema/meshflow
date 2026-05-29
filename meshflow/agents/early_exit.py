"""EarlyExitAgent — stop generation when confidence threshold is reached.

Closes the token-optimization gap: once the agent's output reaches a
user-configured confidence threshold, the loop is terminated rather than
continuing to refine.  This saves tokens on tasks where an early, good-enough
answer is acceptable.

Two modes:
1. **Per-call gate** — wraps ``Agent.run()`` with a confidence check on the result.
   If confidence < threshold, retries with a lower-cost fallback model.
2. **Multi-turn refinement** — runs the agent up to *max_turns* times, exiting
   early if confidence crosses the threshold between turns.

Usage::

    from meshflow import Agent
    from meshflow.agents.early_exit import EarlyExitAgent

    base = Agent(name="analyst", role="researcher")
    agent = EarlyExitAgent(base, confidence_threshold=0.85, max_turns=3)

    result = await agent.run("Summarise the latest AI governance news.")
    # Exits after turn 1 if confidence >= 0.85
    print(result["result"])
    print(result["_confidence"])
    print(result["_turns"])
"""

from __future__ import annotations

import re
from typing import Any


def _extract_confidence(text: str) -> float:
    m = re.search(r"CONFIDENCE:\s*(0?\.\d+|1\.0+)", text, re.IGNORECASE)
    if m:
        try:
            return min(1.0, max(0.0, float(m.group(1))))
        except ValueError:
            pass
    return 0.7  # neutral default — not high enough to be overconfident


class EarlyExitAgent:
    """Wraps an Agent to exit as soon as confidence reaches *threshold*.

    Parameters
    ----------
    base_agent:           The base Agent to call.
    confidence_threshold: Stop when output confidence >= this value.
    max_turns:            Maximum calls before returning best result.
    fallback_model:       If set, use this model on the final turn when
                          confidence never reached the threshold (quality upgrade).
    """

    def __init__(
        self,
        base_agent: Any,
        *,
        confidence_threshold: float = 0.85,
        max_turns: int = 3,
        fallback_model: str = "",
    ) -> None:
        self._base = base_agent
        self._threshold = confidence_threshold
        self._max_turns = max_turns
        self._fallback = fallback_model

    async def run(self, task: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run with per-turn confidence gate.

        Metadata added to the returned dict:
        - ``_confidence``:     Final confidence score.
        - ``_turns``:          Number of turns executed.
        - ``_early_exit``:     True when threshold was met before max_turns.
        - ``_exit_reason``:    Human-readable explanation.
        """
        ctx = context or {}
        best_result: dict[str, Any] = {}
        best_conf = 0.0

        for turn in range(1, self._max_turns + 1):
            # On last turn, optionally swap to the fallback model for quality boost
            if turn == self._max_turns and self._fallback and best_conf < self._threshold:
                original_model = getattr(self._base, "model", "")
                try:
                    object.__setattr__(self._base, "model", self._fallback)
                    result = await self._base.run(task, ctx)
                finally:
                    object.__setattr__(self._base, "model", original_model)
            else:
                result = await self._base.run(task, ctx)

            conf = _extract_confidence(result.get("result", ""))
            result["_confidence"] = conf
            result["_turns"] = turn

            if conf > best_conf:
                best_conf = conf
                best_result = dict(result)
            # Always keep turn count current in best_result
            best_result["_turns"] = turn

            if conf >= self._threshold:
                best_result["_early_exit"] = True
                best_result["_exit_reason"] = f"confidence {conf:.2f} >= threshold {self._threshold}"
                return best_result

            # Use the previous result as additional context for the next turn
            ctx = {**ctx, "_prior_output": result.get("result", ""), "_prior_confidence": conf}

        best_result["_early_exit"] = False
        best_result["_exit_reason"] = f"max_turns={self._max_turns} reached; best confidence={best_conf:.2f}"
        return best_result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


__all__ = ["EarlyExitAgent"]
