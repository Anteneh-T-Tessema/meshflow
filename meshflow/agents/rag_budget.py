"""RAG token budget — enforce max_chars/max_tokens per knowledge injection.

Prevents unbounded context growth when agents use large knowledge bases.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


_CHARS_PER_TOKEN = 4  # conservative estimate when tiktoken is unavailable


def _count_tokens(text: str) -> int:
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // _CHARS_PER_TOKEN)


@dataclass
class KnowledgeBudgetResult:
    """Result of applying a token budget to a knowledge block list."""
    included: list[str]
    truncated: list[str]
    dropped: list[str]
    total_chars: int
    total_tokens: int
    budget_chars: int
    budget_tokens: int

    @property
    def over_budget(self) -> bool:
        return bool(self.truncated) or bool(self.dropped)

    def to_prompt_text(self) -> str:
        """Merge included blocks into a single knowledge string."""
        return "\n\n".join(self.included)


@dataclass
class RAGTokenBudget:
    """Enforce a character/token ceiling on knowledge injected into prompts.

    Parameters
    ----------
    max_chars:
        Hard limit on total characters across all knowledge blocks.
        Blocks are truncated or dropped until under this limit.
    max_tokens:
        Alternative limit in approximate tokens (1 token ≈ 4 chars).
        If both are set, the stricter limit applies.
    strategy:
        ``"truncate"`` — keep as many blocks as possible, truncating the last
        one that overflows.
        ``"drop"`` — keep whole blocks in order, drop the first one that doesn't
        fit (and all subsequent ones).
        ``"tail"`` — prefer the most recent blocks (reverse priority order).
    separator:
        String used to join multiple blocks into a single knowledge string.

    Example
    -------
    ::

        budget = RAGTokenBudget(max_chars=4000)
        result = budget.apply(["large doc...", "another doc..."])
        knowledge_text = result.to_prompt_text()
    """

    max_chars: int | None = None
    max_tokens: int | None = None
    strategy: str = "truncate"
    separator: str = "\n\n"

    def __post_init__(self) -> None:
        if self.max_chars is None and self.max_tokens is None:
            raise ValueError("At least one of max_chars or max_tokens must be set")
        if self.strategy not in ("truncate", "drop", "tail"):
            raise ValueError(f"Unknown strategy '{self.strategy}'. Use 'truncate', 'drop', or 'tail'")

    def _effective_char_limit(self) -> int:
        limits = []
        if self.max_chars is not None:
            limits.append(self.max_chars)
        if self.max_tokens is not None:
            limits.append(self.max_tokens * _CHARS_PER_TOKEN)
        return min(limits)

    def _effective_token_limit(self) -> int:
        if self.max_tokens is not None:
            return self.max_tokens
        return self.max_chars // _CHARS_PER_TOKEN  # type: ignore[operator]

    def apply(self, blocks: Sequence[str | Any]) -> KnowledgeBudgetResult:
        """Apply the budget to a list of knowledge blocks.

        Parameters
        ----------
        blocks:
            List of strings (or objects with a ``text`` / ``content`` attribute)
            to budget.

        Returns
        -------
        KnowledgeBudgetResult
        """
        char_limit = self._effective_char_limit()
        token_limit = self._effective_token_limit()

        # Normalise blocks to strings
        texts: list[str] = []
        for b in blocks:
            if isinstance(b, str):
                texts.append(b)
            elif hasattr(b, "text"):
                texts.append(str(b.text))
            elif hasattr(b, "content"):
                texts.append(str(b.content))
            else:
                texts.append(str(b))

        if self.strategy == "tail":
            texts = list(reversed(texts))

        included: list[str] = []
        truncated: list[str] = []
        dropped: list[str] = []
        used_chars = 0

        for text in texts:
            remaining = char_limit - used_chars
            if remaining <= 0:
                dropped.append(text)
                continue

            if len(text) <= remaining:
                included.append(text)
                used_chars += len(text)
            elif self.strategy == "truncate":
                trimmed = text[:remaining].rstrip()
                included.append(trimmed)
                truncated.append(text)
                used_chars += len(trimmed)
            else:  # "drop" or "tail"
                dropped.append(text)

        if self.strategy == "tail":
            included = list(reversed(included))
            truncated = list(reversed(truncated))
            dropped = list(reversed(dropped))

        total_chars = sum(len(t) for t in included)
        total_tokens = _count_tokens(self.separator.join(included)) if included else 0

        return KnowledgeBudgetResult(
            included=included,
            truncated=truncated,
            dropped=dropped,
            total_chars=total_chars,
            total_tokens=total_tokens,
            budget_chars=char_limit,
            budget_tokens=token_limit,
        )

    def apply_to_text(self, blocks: Sequence[str | Any]) -> str:
        """Convenience wrapper that returns the merged, budgeted string directly."""
        return self.apply(blocks).to_prompt_text()
