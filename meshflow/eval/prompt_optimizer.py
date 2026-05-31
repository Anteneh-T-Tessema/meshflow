"""Prompt auto-optimizer — analyze FeedbackRecords and suggest prompt improvements.

Closes the prompt-optimization gap: takes collected human feedback, identifies
correction patterns, and either generates improved prompts automatically (with
an LLM) or provides structured improvement suggestions.

Usage — LLM-driven optimization::

    from meshflow.eval.prompt_optimizer import PromptOptimizer
    from meshflow.eval.feedback import FeedbackStore
    from meshflow import Agent

    store = FeedbackStore("meshflow_feedback.db")
    optimizer = PromptOptimizer(store)

    # Analyze feedback and generate an improved prompt
    optimizer_agent = Agent(name="optimizer", role="critic", model="claude-sonnet-4-6")
    result = await optimizer.optimize(
        agent_name="billing-agent",
        current_prompt="You are a billing assistant. Answer questions about invoices.",
        optimizer_agent=optimizer_agent,
    )
    print(result.improved_prompt)
    print(result.changes_summary)

Usage — rule-based suggestions (no LLM required)::

    suggestions = optimizer.analyze_patterns("billing-agent")
    for s in suggestions:
        print(s.category, s.description)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ── Pattern categories ────────────────────────────────────────────────────────

PATTERN_CATEGORIES = {
    "verbosity":   r"\b(too (long|verbose|wordy)|be (more )?concise|shorter)\b",
    "tone":        r"\b(too (formal|casual|harsh|aggressive)|friendlier|nicer|professional)\b",
    "accuracy":    r"\b(wrong|incorrect|inaccurate|false|mistaken|error)\b",
    "completeness":r"\b(missing|incomplete|forgot|omit|left out|should (also|include))\b",
    "format":      r"\b(format|structure|table|bullet|list|JSON|markdown|header)\b",
    "confidence":  r"\b(uncertain|unsure|confident|guess|maybe|probably)\b",
    "citation":    r"\b(source|reference|cite|evidence|proof|back it up)\b",
}


# ── Suggestion dataclass ──────────────────────────────────────────────────────

@dataclass
class PromptSuggestion:
    """One improvement suggestion derived from feedback patterns."""

    category: str
    description: str
    frequency: int             # how many feedback records triggered this
    example_correction: str = ""
    suggested_addition: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "category":           self.category,
            "description":        self.description,
            "frequency":          self.frequency,
            "example_correction": self.example_correction[:200],
            "suggested_addition": self.suggested_addition,
        }


@dataclass
class OptimizationResult:
    """Result of a prompt optimization run."""

    agent_name: str
    original_prompt: str
    improved_prompt: str
    changes_summary: str
    suggestions: list[PromptSuggestion] = field(default_factory=list)
    feedback_records_analyzed: int = 0
    correction_rate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name":                 self.agent_name,
            "original_prompt":            self.original_prompt[:300],
            "improved_prompt":            self.improved_prompt[:1000],
            "changes_summary":            self.changes_summary,
            "suggestions":                [s.to_dict() for s in self.suggestions],
            "feedback_records_analyzed":  self.feedback_records_analyzed,
            "correction_rate":            round(self.correction_rate, 4),
        }


# ── PromptOptimizer ───────────────────────────────────────────────────────────

class PromptOptimizer:
    """Analyzes FeedbackStore records and generates prompt improvement suggestions.

    Parameters
    ----------
    store:     The :class:`~meshflow.eval.feedback.FeedbackStore` to read from.
    min_count: Minimum number of feedback records before suggesting changes.
    """

    def __init__(self, store: Any, *, min_count: int = 3) -> None:
        self._store = store
        self._min_count = min_count

    # ── Rule-based pattern analysis (no LLM required) ─────────────────────────

    def analyze_patterns(self, agent_name: str = "") -> list[PromptSuggestion]:
        """Analyze feedback records and return rule-based improvement suggestions."""
        records = self._store.list(agent_name=agent_name, limit=10_000)
        if not records:
            return []

        corrections = [r for r in records if r.has_correction]
        if not corrections:
            return []

        # Count pattern hits across all correction texts
        category_hits: dict[str, list[str]] = {k: [] for k in PATTERN_CATEGORIES}
        for rec in corrections:
            combined = f"{rec.correction} {rec.metadata.get('feedback', '')}"
            combined_lower = combined.lower()
            for cat, pattern in PATTERN_CATEGORIES.items():
                if re.search(pattern, combined_lower):
                    category_hits[cat].append(rec.correction[:150])

        suggestions: list[PromptSuggestion] = []
        for cat, examples in category_hits.items():
            if len(examples) < self._min_count:
                continue
            desc, addition = _category_to_suggestion(cat, len(examples))
            suggestions.append(PromptSuggestion(
                category=cat,
                description=desc,
                frequency=len(examples),
                example_correction=examples[0] if examples else "",
                suggested_addition=addition,
            ))

        # Sort by frequency descending
        suggestions.sort(key=lambda s: s.frequency, reverse=True)
        return suggestions

    # ── LLM-driven optimization ────────────────────────────────────────────────

    async def optimize(
        self,
        agent_name: str,
        current_prompt: str,
        optimizer_agent: Any,
        *,
        max_corrections: int = 20,
    ) -> OptimizationResult:
        """Use an LLM agent to rewrite *current_prompt* based on feedback patterns.

        Parameters
        ----------
        agent_name:       Name of the agent whose prompt is being optimized.
        current_prompt:   The agent's current system prompt text.
        optimizer_agent:  An ``Agent`` instance used to generate the improved prompt.
        max_corrections:  Max number of example corrections to include.
        """
        records = self._store.list(agent_name=agent_name, limit=10_000)
        corrections = [r for r in records if r.has_correction][:max_corrections]
        suggestions = self.analyze_patterns(agent_name)
        correction_rate = (
            len(corrections) / max(len(records), 1) if records else 0.0
        )

        if not corrections:
            return OptimizationResult(
                agent_name=agent_name,
                original_prompt=current_prompt,
                improved_prompt=current_prompt,
                changes_summary="No corrections available — prompt unchanged.",
                feedback_records_analyzed=len(records),
                correction_rate=0.0,
            )

        # Build the optimization request
        correction_examples = "\n".join(
            f"  [{i+1}] Original: {r.original_output[:120]!r}\n"
            f"       Correction: {r.correction[:120]!r}"
            for i, r in enumerate(corrections)
        )
        suggestion_text = "\n".join(
            f"  - [{s.category}] {s.description} (seen {s.frequency}x)"
            for s in suggestions[:5]
        )

        optimization_prompt = (
            f"You are a prompt engineer. Improve the following system prompt based on "
            f"human feedback collected from {len(records)} runs.\n\n"
            f"CURRENT SYSTEM PROMPT:\n{current_prompt}\n\n"
            f"FEEDBACK PATTERNS:\n{suggestion_text or '(none identified)'}\n\n"
            f"EXAMPLE CORRECTIONS (latest {len(corrections)}):\n{correction_examples}\n\n"
            "TASK: Rewrite the system prompt to address these patterns. "
            "Output ONLY the improved prompt text followed by a brief CHANGES: summary.\n"
            "Format:\n"
            "IMPROVED PROMPT:\n<new prompt here>\n\n"
            "CHANGES:\n<1-3 bullet points explaining what changed>"
        )

        result = await optimizer_agent.run(optimization_prompt)
        raw = result.get("result", "")

        improved, changes = _parse_optimizer_output(raw, current_prompt)

        return OptimizationResult(
            agent_name=agent_name,
            original_prompt=current_prompt,
            improved_prompt=improved,
            changes_summary=changes,
            suggestions=suggestions,
            feedback_records_analyzed=len(records),
            correction_rate=correction_rate,
        )

    # ── Batch optimization ─────────────────────────────────────────────────────

    async def optimize_all(
        self,
        agent_prompts: dict[str, str],
        optimizer_agent: Any,
    ) -> dict[str, OptimizationResult]:
        """Optimize prompts for multiple agents concurrently."""
        import asyncio
        tasks = {
            name: self.optimize(name, prompt, optimizer_agent)
            for name, prompt in agent_prompts.items()
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        return {
            name: (res if not isinstance(res, Exception) else OptimizationResult(
                agent_name=name,
                original_prompt=agent_prompts[name],
                improved_prompt=agent_prompts[name],
                changes_summary=f"Optimization failed: {res}",
            ))
            for name, res in zip(tasks.keys(), results)
        }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _category_to_suggestion(cat: str, freq: int) -> tuple[str, str]:
    """Return (human description, suggested prompt addition) for a category."""
    mapping = {
        "verbosity":    (
            f"Outputs are too verbose (seen {freq}×) — consider adding conciseness guidance.",
            "Be concise — limit responses to the essential information only.",
        ),
        "tone":         (
            f"Tone issues detected (seen {freq}×) — consider adjusting tone guidance.",
            "Use a professional, helpful, and direct tone.",
        ),
        "accuracy":     (
            f"Accuracy issues reported (seen {freq}×) — consider adding verification guidance.",
            "Only state facts you are confident about. Flag uncertain information explicitly.",
        ),
        "completeness": (
            f"Incomplete outputs reported (seen {freq}×) — consider adding completeness guidance.",
            "Ensure all required aspects of the task are addressed before responding.",
        ),
        "format":       (
            f"Formatting issues reported (seen {freq}×) — consider adding format instructions.",
            "Structure your response clearly using appropriate formatting (lists, tables, etc.).",
        ),
        "confidence":   (
            f"Confidence calibration issues (seen {freq}×) — consider adding confidence guidance.",
            "Express appropriate uncertainty when you are not sure. Use phrases like 'I believe' or 'This may vary'.",
        ),
        "citation":     (
            f"Missing citations reported (seen {freq}×) — consider adding citation requirements.",
            "Cite sources or evidence when making factual claims.",
        ),
    }
    return mapping.get(cat, (f"{cat} issues (seen {freq}×)", ""))


def _parse_optimizer_output(raw: str, fallback: str) -> tuple[str, str]:
    """Parse the optimizer LLM's output into (improved_prompt, changes_summary)."""
    prompt_match = re.search(
        r"IMPROVED PROMPT[:\s]*\n(.*?)(?:\n\nCHANGES:|$)", raw, re.DOTALL
    )
    changes_match = re.search(r"CHANGES[:\s]*\n(.*?)$", raw, re.DOTALL)

    improved = prompt_match.group(1).strip() if prompt_match else fallback
    changes = changes_match.group(1).strip() if changes_match else "Prompt updated based on feedback."
    return improved, changes


__all__ = ["PromptOptimizer", "PromptSuggestion", "OptimizationResult"]
