"""AgentSession — stateful multi-turn conversation with history compression.

Maintains a conversation history across multiple ``chat()`` calls.  When the
history grows past *max_history_turns* the oldest turns are summarised into a
single context message so the context window never overflows.

Usage::

    from meshflow.agents.session import AgentSession
    from meshflow import Agent

    session = AgentSession(Agent(name="assistant", role="executor"))

    r1 = await session.chat("What is prompt caching in Claude?")
    r2 = await session.chat("How does it affect cost?")   # context preserved
    r3 = await session.chat("Summarise what we discussed")

    print(session.history)   # full turn list
    session.reset()          # clear state
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from meshflow.agents.builder import Agent


# ── Turn ──────────────────────────────────────────────────────────────────────

@dataclass
class Turn:
    role: str       # "user" | "assistant"
    content: str
    tokens: int = 0
    cost_usd: float = 0.0


# ── Session result ────────────────────────────────────────────────────────────

@dataclass
class SessionResult:
    reply: str
    turn_number: int
    tokens: int
    cost_usd: float
    total_tokens: int
    total_cost_usd: float


# ── AgentSession ──────────────────────────────────────────────────────────────

_COMPRESS_SYSTEM = (
    "You are a conversation summariser. Compress the following conversation turns "
    "into a single concise context paragraph that preserves all key facts, "
    "decisions, and open questions. Output ONLY the summary, no headers."
)


class AgentSession:
    """Multi-turn stateful conversation wrapper around any Agent.

    Parameters
    ----------
    agent:
        The MeshFlow Agent to chat with.
    max_history_turns:
        When history exceeds this, compress oldest half into a summary (default 20).
    system_context:
        Optional extra context injected into every turn.
    """

    def __init__(
        self,
        agent: Agent,
        max_history_turns: int = 20,
        system_context: str = "",
        context_pruner: Any = None,
    ) -> None:
        self._agent = agent
        self._max_history = max_history_turns
        self._system_context = system_context
        self._context_pruner = context_pruner  # SlidingWindowPruner | SummaryPruner | None
        self._history: list[Turn] = []
        self._summary: str = ""  # compressed older turns
        self._turn_count = 0
        self._total_tokens = 0
        self._total_cost = 0.0

    @property
    def history(self) -> list[Turn]:
        return list(self._history)

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    @property
    def total_cost_usd(self) -> float:
        return self._total_cost

    def reset(self) -> None:
        """Clear all conversation state."""
        self._history = []
        self._summary = ""
        self._turn_count = 0
        self._total_tokens = 0
        self._total_cost = 0.0

    async def chat(self, message: str, extra_context: dict[str, Any] | None = None) -> SessionResult:
        """Send a message and get a reply, with full conversation history."""
        self._turn_count += 1

        # Build context block from summary + recent history
        context_parts: list[str] = []
        if self._summary:
            context_parts.append(f"[Earlier conversation summary]\n{self._summary}")
        if self._history:
            context_parts.append("[Recent turns]")
            for turn in self._history:
                context_parts.append(f"{turn.role.upper()}: {turn.content}")
        if self._system_context:
            context_parts.append(f"[System context]\n{self._system_context}")

        # ── ContextCompactor: prune history before sending ────────────────────
        if self._context_pruner is not None and context_parts:
            try:
                import inspect as _inspect
                history_msgs = [
                    {"role": "user" if i % 2 == 0 else "assistant", "content": p}
                    for i, p in enumerate(context_parts)
                ]
                _prune = self._context_pruner.prune(history_msgs)
                if _inspect.isawaitable(_prune):
                    _prune = await _prune
                pruned_parts = [m["content"] for m in _prune.messages]
                context_parts = pruned_parts
            except Exception:
                pass  # best-effort

        task = message
        ctx: dict[str, Any] = {"conversation": "\n".join(context_parts)}
        if extra_context:
            ctx.update(extra_context)

        out = await self._agent.run(task, ctx)
        reply = out.get("result", "")
        tokens = out.get("tokens", 0)
        cost = out.get("cost_usd", 0.0)

        self._total_tokens += tokens
        self._total_cost += cost

        self._history.append(Turn(role="user", content=message))
        self._history.append(Turn(role="assistant", content=reply, tokens=tokens, cost_usd=cost))

        # Compress if needed
        if len(self._history) > self._max_history:
            await self._compress()

        return SessionResult(
            reply=reply,
            turn_number=self._turn_count,
            tokens=tokens,
            cost_usd=cost,
            total_tokens=self._total_tokens,
            total_cost_usd=self._total_cost,
        )

    async def _compress(self) -> None:
        """Summarise the oldest half of history into self._summary."""
        half = len(self._history) // 2
        to_compress = self._history[:half]
        self._history = self._history[half:]

        turns_text = "\n".join(
            f"{t.role.upper()}: {t.content}" for t in to_compress
        )
        if self._summary:
            turns_text = f"[Previous summary]\n{self._summary}\n\n[New turns]\n{turns_text}"

        compress_out = await self._agent.run(
            turns_text,
            {"__system_override__": _COMPRESS_SYSTEM},
        )
        new_summary = compress_out.get("result", turns_text[:500])
        self._total_tokens += compress_out.get("tokens", 0)
        self._total_cost += compress_out.get("cost_usd", 0.0)

        self._summary = (
            f"{self._summary}\n\n{new_summary}" if self._summary else new_summary
        ).strip()
