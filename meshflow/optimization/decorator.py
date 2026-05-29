"""Decorator for enforcing token/cost constraints on workflows and agent executions."""

from __future__ import annotations

import functools
from typing import Any, Callable

from meshflow.optimization.tracker import OptimizationTracker


def token_budget(
    max_tokens: int = 0,
    max_cost_usd: float = 0.0,
    action: str = "fail",
    fallback_model: str = "claude-haiku-3-5",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to enforce token and cost budgets on Agent runs or Workflow runs.

    Injects an OptimizationTracker into the `context` dictionary under the
    `_optimization_tracker` key, which propagates downstream to all base agent think calls.
    """
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            context: dict[str, Any] | None = None

            # Look up in kwargs first
            if "context" in kwargs:
                if kwargs["context"] is None:
                    kwargs["context"] = {}
                context = kwargs["context"]
            else:
                # Look up in positional args (find the first dictionary)
                args_list = list(args)
                for idx, arg in enumerate(args_list):
                    if isinstance(arg, dict):
                        context = arg
                        break
                else:
                    # No dictionary found in positional args; create and inject one
                    context = {}
                    kwargs["context"] = context

            # Ensure tracker is initialized in the shared context
            if "_optimization_tracker" not in context:
                context["_optimization_tracker"] = OptimizationTracker(
                    max_tokens=max_tokens,
                    max_cost_usd=max_cost_usd,
                    action=action,
                    fallback_model=fallback_model,
                )

            return await fn(*args, **kwargs)

        return wrapper

    return decorator
