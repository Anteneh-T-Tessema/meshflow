"""Static cost planning and model recommendation engine."""

from __future__ import annotations

import re
from typing import Any


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
