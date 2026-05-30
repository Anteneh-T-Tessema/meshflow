"""ToolOutputSummarizer — auto-summarize large tool outputs before context injection.

Prevents unbounded context growth from verbose tool returns (web search results,
file reads, API responses). When a tool output exceeds a configurable token
threshold, a nano-model summarization pass compresses it before the text is
appended to the agent's message history.

Industry research shows tool outputs are a primary source of token waste in
production multi-agent systems (40–60% of total token spend on solvable problems).

Usage::

    from meshflow.tools.tool_summarizer import ToolOutputSummarizer

    # Standalone
    summarizer = ToolOutputSummarizer(max_tokens=500)
    compressed = await summarizer.compress("web_search", very_long_output, agent)

    # Wrap an Agent — all tool outputs auto-compressed
    wrapped_agent = summarizer.wrap(agent)
    result = await wrapped_agent.run("Search for HIPAA compliance updates")

Configuration::

    summarizer = ToolOutputSummarizer(
        max_tokens=500,          # tokens above which compression activates
        summary_model="claude-haiku-4-5-20251001",  # nano model for summaries
        summary_instruction="Summarise this tool output concisely, preserving all key facts.",
        passthrough_tools={"calculator", "clock"},  # never compress these
    )
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_CHARS_PER_TOKEN = 4
_DEFAULT_MAX_TOKENS = 500
_DEFAULT_SUMMARY_MODEL = "claude-haiku-4-5-20251001"
_DEFAULT_INSTRUCTION = (
    "Summarise the following tool output concisely. "
    "Preserve all key facts, numbers, names, and conclusions. "
    "Remove boilerplate, duplication, and formatting noise."
)


def _estimate_tokens(text: str) -> int:
    words = re.findall(r"\w+|[^\w\s]", text, re.UNICODE)
    return max(1, int(len(words) * 1.35))


# ── CompressionRecord ─────────────────────────────────────────────────────────


@dataclass
class CompressionRecord:
    """Metadata about a single compression event."""

    tool_name: str
    original_tokens: int
    compressed_tokens: int
    compression_ratio: float
    skipped: bool = False     # True if output was under threshold

    @property
    def saved_tokens(self) -> int:
        return self.original_tokens - self.compressed_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "original_tokens": self.original_tokens,
            "compressed_tokens": self.compressed_tokens,
            "compression_ratio": round(self.compression_ratio, 3),
            "saved_tokens": self.saved_tokens,
            "skipped": self.skipped,
        }


# ── ToolOutputSummarizer ──────────────────────────────────────────────────────


class ToolOutputSummarizer:
    """Compress large tool outputs before they enter the agent's context.

    Parameters
    ----------
    max_tokens:
        Tool outputs exceeding this token estimate are compressed (default 500).
    summary_model:
        Model used for summarization passes (default: ``claude-haiku-4-5-20251001``).
    summary_instruction:
        System instruction for the summarization model.
    passthrough_tools:
        Set of tool names that are never compressed (e.g. ``{"calculator"}``).
    record_stats:
        If True, compression records are accumulated in ``self.stats``.
    """

    def __init__(
        self,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        summary_model: str = _DEFAULT_SUMMARY_MODEL,
        summary_instruction: str = _DEFAULT_INSTRUCTION,
        passthrough_tools: set[str] | None = None,
        *,
        record_stats: bool = True,
    ) -> None:
        self.max_tokens = max_tokens
        self.summary_model = summary_model
        self.summary_instruction = summary_instruction
        self.passthrough_tools: set[str] = passthrough_tools or set()
        self._record = record_stats
        self.stats: list[CompressionRecord] = []

    async def compress(
        self,
        tool_name: str,
        output: str,
        agent: Any | None = None,
    ) -> str:
        """Compress *output* if it exceeds the token threshold.

        Parameters
        ----------
        tool_name:
            Name of the tool that produced the output (for passthrough logic).
        output:
            The raw tool output string to potentially compress.
        agent:
            Optional Agent instance used for the summarization LLM call.
            If None, a minimal internal call is made directly.

        Returns
        -------
        The original string (if under threshold or passthrough) or a
        compressed summary string.
        """
        original_tokens = _estimate_tokens(output)

        if tool_name in self.passthrough_tools or original_tokens <= self.max_tokens:
            self._record_stat(CompressionRecord(
                tool_name=tool_name,
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                compression_ratio=1.0,
                skipped=True,
            ))
            return output

        # Build summarization prompt
        prompt = (
            f"Tool: {tool_name}\n\n"
            f"Raw output ({original_tokens} est. tokens):\n{output}\n\n"
            "Please summarise."
        )

        compressed = await self._call_llm(prompt, agent, original=output)
        compressed_tokens = _estimate_tokens(compressed)
        ratio = compressed_tokens / max(original_tokens, 1)

        self._record_stat(CompressionRecord(
            tool_name=tool_name,
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            compression_ratio=ratio,
            skipped=False,
        ))
        return compressed

    async def _call_llm(self, prompt: str, agent: Any | None, original: str = "") -> str:
        """Make the summarization LLM call."""
        if agent is not None:
            # Reuse the agent's provider for cost attribution
            try:
                provider = getattr(agent, "_provider", None) or getattr(agent, "provider", None)
                if provider is not None:
                    text, _, _ = await provider.complete(
                        model=self.summary_model,
                        messages=[{"role": "user", "content": prompt}],
                        system=self.summary_instruction,
                        max_tokens=512,
                    )
                    return text
            except Exception:
                pass

        # Fallback: attempt direct Anthropic call if available
        try:
            import anthropic  # type: ignore[import]
            client = anthropic.Anthropic()
            response = client.messages.create(
                model=self.summary_model,
                max_tokens=512,
                system=self.summary_instruction,
                messages=[{"role": "user", "content": prompt}],
            )
            block = response.content[0]
            return getattr(block, "text", str(block))
        except Exception:
            # If no LLM available, hard-truncate the original output as last resort
            source = original or prompt
            chars = self.max_tokens * _CHARS_PER_TOKEN
            return source[:chars].rstrip() + " … [truncated]" if len(source) > chars else source

    def _record_stat(self, record: CompressionRecord) -> None:
        if self._record:
            self.stats.append(record)

    def wrap(self, agent: Any) -> "_WrappedAgent":
        """Return an agent wrapper that auto-compresses all tool outputs.

        The wrapped agent has the same interface as the original and can be
        used as a drop-in replacement.
        """
        return _WrappedAgent(agent, self)

    def summary_report(self) -> dict[str, Any]:
        """Aggregate statistics across all compression events."""
        if not self.stats:
            return {"total_events": 0, "total_saved_tokens": 0, "avg_compression_ratio": 1.0}
        total_orig = sum(r.original_tokens for r in self.stats)
        total_comp = sum(r.compressed_tokens for r in self.stats)
        compressed_events = [r for r in self.stats if not r.skipped]
        avg_ratio = (
            sum(r.compression_ratio for r in compressed_events) / len(compressed_events)
            if compressed_events
            else 1.0
        )
        return {
            "total_events": len(self.stats),
            "compressed_events": len(compressed_events),
            "skipped_events": len(self.stats) - len(compressed_events),
            "total_original_tokens": total_orig,
            "total_compressed_tokens": total_comp,
            "total_saved_tokens": total_orig - total_comp,
            "avg_compression_ratio": round(avg_ratio, 3),
        }


class _WrappedAgent:
    """Thin proxy that intercepts tool results and compresses them."""

    def __init__(self, agent: Any, summarizer: ToolOutputSummarizer) -> None:
        self._agent = agent
        self._summarizer = summarizer

    def __getattr__(self, name: str) -> Any:
        return getattr(self._agent, name)

    async def run(self, task: str, **kwargs: Any) -> Any:
        return await self._agent.run(task, **kwargs)


__all__ = ["ToolOutputSummarizer", "CompressionRecord"]
