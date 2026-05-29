"""TeachableAgent — learn from user corrections mid-conversation (AutoGen parity gap).

When a user response contains correction signals ("actually", "no, ", "correct:",
etc.) the agent automatically extracts a (mistake → correction) pair and stores
it in entity memory under ``teachings/<agent_id>``.  On every subsequent run all
stored teachings are prepended to the system prompt as a [Learned Corrections]
block so the agent avoids repeating past mistakes.

Usage — decorator pattern::

    from meshflow import Agent
    from meshflow.agents.teachable import TeachableAgent

    base = Agent(name="assistant", role="executor", model="claude-sonnet-4-6")
    agent = TeachableAgent(base)

    result = await agent.run("What is the capital of Australia?")
    # Suppose result says "Sydney"

    # User corrects it explicitly
    await agent.run("Actually, the capital of Australia is Canberra, not Sydney.")

    # Next run picks up the correction automatically
    result = await agent.run("Capital of Australia?")

Usage — explicit API::

    agent.teach(
        original="The capital of Australia is Sydney.",
        correction="The capital of Australia is Canberra.",
    )
    print(agent.teachings())
    agent.forget_teaching("teaching_<key>")

Via the Agent builder::

    agent = Agent(name="assistant", teachable=True)
    result = await agent.run("...")
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

_CORRECTION_TRIGGERS = re.compile(
    r"actually,?\s+|no,?\s+|that'?s?\s+(?:not|wrong|incorrect)\s+|"
    r"correct(?:ion)?:?\s+|let me correct\s+|you should\s+|"
    r"the (?:right|correct) answer is\s+|you(?:'re|\s+are) wrong",
    re.IGNORECASE,
)

_FACT_KEY_PREFIX = "teaching_"


def _detect_correction(text: str) -> bool:
    return bool(_CORRECTION_TRIGGERS.search(text))


def _make_key(original: str) -> str:
    return _FACT_KEY_PREFIX + hashlib.md5(original.encode()).hexdigest()[:8]


class TeachableAgent:
    """Wraps any MeshFlow ``Agent`` with a persistent correction-learning loop.

    Parameters
    ----------
    base_agent:
        The ``Agent`` instance to wrap.
    storage_path:
        SQLite path for the entity memory.  Defaults to ``":memory:"`` (not
        persistent across restarts); pass a file path for durable teaching.
    """

    def __init__(self, base_agent: Any, *, storage_path: str = ":memory:") -> None:
        self._base = base_agent
        self._agent_id: str = getattr(base_agent, "name", "agent")
        from meshflow.intelligence.entity_memory import EntityMemory

        self._entity_memory = EntityMemory(storage_path)
        self._ns = f"teachings/{self._agent_id}"
        self._last_response: str = ""  # tracks last output for cross-call correction detection

    # ── Teaching API ──────────────────────────────────────────────────────────

    def teach(self, original: str, correction: str) -> None:
        """Explicitly store a (mistake → correction) pair."""
        key = _make_key(original)
        self._entity_memory.remember(
            self._ns,
            key,
            f"ORIGINAL: {original[:300]} | CORRECTION: {correction[:300]}",
        )

    def teachings(self) -> list[dict[str, str]]:
        """Return all stored correction pairs as a list of dicts."""
        facts = self._entity_memory.recall_entity(self._ns)
        result: list[dict[str, str]] = []
        for key, val in facts.items():
            if " | CORRECTION: " in val:
                parts = val.split(" | CORRECTION: ", 1)
                original = parts[0].replace("ORIGINAL: ", "", 1)
                correction = parts[1]
            else:
                original = val
                correction = ""
            result.append({"key": key, "original": original, "correction": correction})
        return result

    def forget_teaching(self, key: str) -> None:
        """Remove a specific teaching by its key string."""
        self._entity_memory.forget_fact(self._ns, key)

    def forget_all_teachings(self) -> None:
        """Wipe all stored teachings for this agent."""
        self._entity_memory.forget(self._ns)

    # ── Context injection ──────────────────────────────────────────────────────

    def _build_teachings_block(self) -> str:
        all_t = self.teachings()
        if not all_t:
            return ""
        lines = ["[Learned Corrections]"]
        for t in all_t:
            if t["correction"]:
                lines.append(f"- Instead of: {t['original'][:120]}")
                lines.append(f"  Use:         {t['correction'][:120]}")
        return "\n".join(lines) if len(lines) > 1 else ""

    # ── run ───────────────────────────────────────────────────────────────────

    async def run(self, task: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run with learned corrections injected; auto-detect and store new ones."""
        ctx = dict(context or {})

        # Auto-detect corrections from user input; check both current context and
        # internal state so correction-detection works across consecutive bare calls.
        if _detect_correction(task):
            last_response = ctx.get("_last_response", self._last_response)
            if last_response:
                self.teach(last_response, task)

        # Build teachings prefix and inject into system prompt
        block = self._build_teachings_block()
        original_prompt = getattr(self._base, "system_prompt", "")
        if block:
            new_prompt = f"{block}\n\n{original_prompt}" if original_prompt else block
            try:
                object.__setattr__(self._base, "system_prompt", new_prompt)
            except (AttributeError, TypeError):
                pass

        result = await self._base.run(task, ctx)

        # Restore original prompt
        if block:
            try:
                object.__setattr__(self._base, "system_prompt", original_prompt)
            except (AttributeError, TypeError):
                pass

        # Persist last response so next call can detect what's being corrected
        self._last_response = result.get("result", "")
        result["_last_response"] = self._last_response
        return result

    # ── Transparent attribute proxy ───────────────────────────────────────────

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)

    def __repr__(self) -> str:
        return f"TeachableAgent(base={self._base!r}, ns={self._ns!r})"


__all__ = ["TeachableAgent"]
