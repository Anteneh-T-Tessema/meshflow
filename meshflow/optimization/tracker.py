"""Runtime token and cost budget tracker with context compression and degradation triggers."""

from __future__ import annotations

import contextvars
import logging
import threading
from typing import Any

logger = logging.getLogger("meshflow.optimization")

# ContextVar storing the current active OptimizationTracker
active_tracker: contextvars.ContextVar[OptimizationTracker | None] = contextvars.ContextVar("active_tracker", default=None)


class BudgetExceededError(RuntimeError):
    """Raised when token or cost budget constraints are breached under 'fail' policy."""
    pass


class OptimizationTracker:
    """Tracks token and cost expenditures at runtime and triggers budget mitigations."""

    def __init__(
        self,
        max_tokens: int = 0,
        max_cost_usd: float = 0.0,
        action: str = "fail",
        fallback_model: str = "claude-haiku-3-5",
    ) -> None:
        self.max_tokens = max_tokens
        self.max_cost_usd = max_cost_usd
        self.action = action
        self.fallback_model = fallback_model
        
        self.consumed_tokens = 0
        self.consumed_cost = 0.0
        self.alerts_triggered: list[str] = []
        self._lock = threading.Lock()

    def add_usage(self, tokens: int, cost_usd: float) -> None:
        """Accumulate token and cost usage. Enforces thresholds according to the action plan."""
        with self._lock:
            self.consumed_tokens += tokens
            self.consumed_cost += cost_usd

            # Check limits
            if self.max_tokens > 0 and self.consumed_tokens > self.max_tokens:
                msg = f"Token budget of {self.max_tokens} breached (consumed: {self.consumed_tokens})"
                self._trigger_alert(msg)
                if self.action == "fail":
                    raise BudgetExceededError(msg)

            if self.max_cost_usd > 0.0 and self.consumed_cost > self.max_cost_usd:
                msg = f"Cost budget of ${self.max_cost_usd:.4f} breached (consumed: ${self.consumed_cost:.4f})"
                self._trigger_alert(msg)
                if self.action == "fail":
                    raise BudgetExceededError(msg)

    def _trigger_alert(self, msg: str) -> None:
        logger.warning(f"[BUDGET ALERT] {msg}")
        self.alerts_triggered.append(msg)

    def should_degrade(self) -> bool:
        """Trigger model degradation if remaining token budget is below 25%."""
        if self.max_tokens <= 0:
            return False
        with self._lock:
            return (self.consumed_tokens / self.max_tokens) >= 0.75

    def compress_prompt(
        self, system: str, messages: list[dict[str, Any]]
    ) -> tuple[str, list[dict[str, Any]]]:
        """Compress the prompt footprint by trimming message history, preserving key turns."""
        if len(messages) <= 3:
            return system, messages

        # Strategy: Keep system prompt, keep first message (user query), and last two turns
        first_msg = messages[0]
        recent_turns = messages[-2:]
        
        compressed_messages = [first_msg]
        # Avoid duplicating the first message if it's already in the recent turns
        if first_msg not in recent_turns:
            compressed_messages.extend(recent_turns)
        else:
            compressed_messages = recent_turns

        logger.info(f"Compressed prompt history: trimmed {len(messages) - len(compressed_messages)} turns")
        return system, compressed_messages

    def trim_rag_context(self, chunks: list[Any], max_allowed_tokens: int) -> list[Any]:
        """Dynamically trim the list of retrieved knowledge chunks to fit within token boundaries."""
        from meshflow.optimization.planner import TokenBudgetPlanner
        
        trimmed: list[Any] = []
        current_tokens = 0
        
        for chunk in chunks:
            text = getattr(chunk, "text", str(chunk))
            tok = TokenBudgetPlanner.estimate_tokens(text)
            if current_tokens + tok > max_allowed_tokens:
                break
            trimmed.append(chunk)
            current_tokens += tok
            
        return trimmed

    def should_early_exit(self, confidence: float, threshold: float = 0.90) -> bool:
        """Identify if intermediate step output has high enough confidence to stop executing further steps."""
        return confidence >= threshold
