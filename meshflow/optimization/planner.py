"""Static cost planning, model recommendation, and pre-run cost forecasting."""

from __future__ import annotations

import re
from typing import Any


# ── Per-model pricing (USD per 1M tokens, as of 2025-05) ─────────────────────
# Format: {model_id: (input_per_1M, output_per_1M)}
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-8":            (15.00, 75.00),
    "claude-opus-4-7":            (15.00, 75.00),
    "claude-sonnet-4-6":          (3.00,  15.00),
    "claude-haiku-4-5-20251001":  (0.80,   4.00),
    "claude-haiku-3-5":           (0.80,   4.00),
    "gpt-4o":                     (5.00,  15.00),
    "gpt-4o-mini":                (0.15,   0.60),
    "gemini-2.0-flash":           (0.075,  0.30),
    "llama3.2":                   (0.00,   0.00),  # local
}

# Average output/input ratio from empirical data (model-dependent but ~0.4 is typical)
_DEFAULT_OUTPUT_RATIO = 0.4


class TokenBudgetPlanner:
    """Estimates the input token footprint of prompts, messages, and tool definitions."""

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Estimate the token count of a given text block using a standard 1.3x word multiplier.
        
        Handles empty or whitespace-only inputs gracefully.
        """
        if not text:
            return 0
        # Use basic space tokenization but count punctuation as potential tokens
        words = re.findall(r"\w+|[^\w\s]", text, re.UNICODE)
        return int(len(words) * 1.35)

    def plan_budget(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        tools: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Estimate the total input token requirements for a proposed agent turn."""
        sys_tokens = self.estimate_tokens(system_prompt)
        
        msg_tokens = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                msg_tokens += self.estimate_tokens(content)
            elif isinstance(content, list):
                # Handle structured content blocks
                for block in content:
                    if isinstance(block, dict):
                        msg_tokens += self.estimate_tokens(block.get("text", ""))

        tool_tokens = 0
        if tools:
            for t in tools:
                # Convert tool schema to string to estimate its size
                desc = getattr(t, "description", "")
                name = getattr(t, "name", "")
                tool_tokens += self.estimate_tokens(f"{name} {desc}")

        total_in = sys_tokens + msg_tokens + tool_tokens

        return {
            "system_tokens": sys_tokens,
            "message_tokens": msg_tokens,
            "tool_tokens": tool_tokens,
            "total_estimated_in": total_in,
        }


class ModelSizingAdvisor:
    """Recommends optimal model tier based on task complexity heuristics."""

    HIGH_TIER = "claude-sonnet-4-6"
    LOW_TIER = "claude-haiku-3-5"

    # Keywords associated with complex reasoning/coding/arbitration
    COMPLEXITY_KEYWORDS = {
        "debug", "compile", "optimize", "analyze", "evaluate",
        "critique", "arbitrate", "verify", "refactor", "architect",
        "synthesize", "resolve", "diagnose", "audit"
    }

    def recommend_model(self, task: str, tools: list[Any] | None = None) -> str:
        """Evaluate task and tools to recommend either a high-tier or low-tier model."""
        task_lower = task.lower()
        
        # Heuristic 1: If there are tools, it requires tool calling capability
        if tools and len(tools) > 1:
            return self.HIGH_TIER

        # Heuristic 2: If the task contains complexity keywords
        words = set(re.findall(r"\b\w+\b", task_lower))
        if words & self.COMPLEXITY_KEYWORDS:
            return self.HIGH_TIER

        # Heuristic 3: Long task descriptions indicate reasoning depth
        if len(task) > 350:
            return self.HIGH_TIER

        # Default fallback: lightweight model is suitable for simple tasks
        return self.LOW_TIER


# ── CostForecaster (Gap 7) ────────────────────────────────────────────────────

class CostForecaster:
    """Pre-run USD cost estimate: input tokens + projected output tokens.

    Usage::

        from meshflow.optimization.planner import CostForecaster

        fc = CostForecaster()
        forecast = fc.forecast(
            model="claude-sonnet-4-6",
            system_prompt=system,
            messages=[{"role": "user", "content": task}],
            tools=tools,
        )
        print(forecast)
        # {'model': 'claude-sonnet-4-6', 'input_tokens': 342,
        #  'output_tokens_est': 136, 'input_usd': 0.001026,
        #  'output_usd_est': 0.00204, 'total_usd_est': 0.003066,
        #  'within_budget': True}

        # Gate on budget
        if not forecast["within_budget"]:
            raise BudgetExceeded(...)
    """

    def __init__(
        self,
        output_ratio: float = _DEFAULT_OUTPUT_RATIO,
        pricing: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        self._output_ratio = output_ratio
        self._pricing = pricing or _MODEL_PRICING

    def _price_for(self, model: str) -> tuple[float, float]:
        """Return (input_per_1M, output_per_1M) for *model*, fuzzy-matched."""
        if model in self._pricing:
            return self._pricing[model]
        model_lower = model.lower()
        for key, price in self._pricing.items():
            if key in model_lower or model_lower.startswith(key.split("-")[0]):
                return price
        return (3.00, 15.00)  # safe sonnet default

    def forecast(
        self,
        model: str,
        system_prompt: str = "",
        messages: list[dict[str, Any]] | None = None,
        tools: list[Any] | None = None,
        max_budget_usd: float = 0.0,
    ) -> dict[str, Any]:
        """Estimate the cost of one agent turn before executing it.

        Parameters
        ----------
        model:           Model string (e.g. ``"claude-sonnet-4-6"``).
        system_prompt:   System prompt text.
        messages:        List of message dicts (role/content).
        tools:           Tool list (for schema token estimation).
        max_budget_usd:  Optional budget gate; 0 = no gate.

        Returns a dict with input/output token estimates and USD costs.
        """
        planner = TokenBudgetPlanner()
        budget = planner.plan_budget(system_prompt, messages or [], tools)
        input_tokens = budget["total_estimated_in"]

        # Output estimation: typical output is output_ratio × input tokens
        output_tokens_est = max(10, int(input_tokens * self._output_ratio))

        in_price, out_price = self._price_for(model)
        input_usd = input_tokens * in_price / 1_000_000
        output_usd_est = output_tokens_est * out_price / 1_000_000
        total_usd_est = input_usd + output_usd_est

        within_budget = (
            True if max_budget_usd <= 0 else total_usd_est <= max_budget_usd
        )

        return {
            "model":            model,
            "input_tokens":     input_tokens,
            "output_tokens_est": output_tokens_est,
            "input_usd":        round(input_usd, 7),
            "output_usd_est":   round(output_usd_est, 7),
            "total_usd_est":    round(total_usd_est, 7),
            "within_budget":    within_budget,
            "max_budget_usd":   max_budget_usd,
            **{k: v for k, v in budget.items()},
        }

    def compare_models(
        self,
        models: list[str],
        system_prompt: str = "",
        messages: list[dict[str, Any]] | None = None,
        tools: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return cost forecasts for multiple models, sorted cheapest first."""
        results = [
            self.forecast(m, system_prompt, messages, tools)
            for m in models
        ]
        results.sort(key=lambda x: x["total_usd_est"])
        return results
