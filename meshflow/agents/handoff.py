"""Handoff pattern — peer-to-peer agent transfer (OpenAI Agents SDK style).

Unlike subagents (parent spawns child with isolated context), handoffs
*transfer full ownership* to a peer agent.  The receiving agent gets the
conversation history and takes responsibility for the next response.

Signal convention
-----------------
An agent signals a handoff by including the text::

    TRANSFER_TO:<agent_name>[:<optional reason>]

anywhere in its output.  ``run_with_handoffs`` detects this, locates the
named agent in the sender's ``handoffs`` list, and calls it with the
forwarded task.  If no signal is detected the result is returned as-is.

Usage::

    from meshflow.agents.builder import Agent
    from meshflow.agents.handoff import HandoffConfig, HandoffResult, run_with_handoffs

    billing  = Agent(name="billing",  role="executor")
    support  = Agent(name="support",  role="executor")
    triage   = Agent(name="triage",   role="orchestrator",
                     handoffs=[billing, support])

    result = await run_with_handoffs(triage, "I can't download my invoice.")
    print(result.final_agent, result.output)

Or via the Agent convenience method::

    result = await triage.run_with_handoffs("I can't download my invoice.")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from meshflow.agents.builder import Agent


# TRANSFER_TO:agent-name[:optional reason text]
_HANDOFF_RE = re.compile(
    r"TRANSFER_TO\s*:\s*([\w][\w\-]*)(?:\s*:\s*([^\n]*))?",
    re.IGNORECASE,
)


# ── Config ─────────────────────────────────────────────────────────────────────

@dataclass
class HandoffConfig:
    """Controls handoff chain behaviour.

    Parameters
    ----------
    max_depth:               Maximum number of hops before stopping.
    history_mode:            How to forward history to the next agent.
                             ``"full"`` — full previous output + original task.
                             ``"last_n"`` — tail of previous output + task.
                             ``"none"`` — original task only (no history).
    history_n:               Characters to include in ``"last_n"`` mode.
    """

    max_depth: int = 5
    history_mode: str = "full"   # "full" | "last_n" | "none"
    history_n: int = 500


# ── Result types ───────────────────────────────────────────────────────────────

@dataclass
class HandoffLink:
    """One hop in a handoff chain."""

    from_agent: str
    to_agent: str
    task: str
    reason: str = ""
    tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class HandoffResult:
    """Result of a handoff-orchestrated run.

    Attributes
    ----------
    output:          Final text output from the last agent in the chain.
    final_agent:     Name of the agent that produced ``output``.
    chain:           Ordered list of :class:`HandoffLink` objects (one per hop).
    total_tokens:    Sum of tokens across all agents.
    total_cost_usd:  Sum of costs across all agents.
    transferred:     ``True`` if at least one handoff occurred.
    """

    output: str
    final_agent: str
    chain: list[HandoffLink] = field(default_factory=list)
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    transferred: bool = False

    # ── dict-like access (mirrors HealingResult / A2AResponse convention) ────

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self.to_dict()

    def to_dict(self) -> dict[str, Any]:
        return {
            "output": self.output,
            "result": self.output,          # alias for compatibility with step() callers
            "final_agent": self.final_agent,
            "chain": [
                {
                    "from": lk.from_agent,
                    "to": lk.to_agent,
                    "task": lk.task,
                    "reason": lk.reason,
                    "tokens": lk.tokens,
                    "cost_usd": lk.cost_usd,
                }
                for lk in self.chain
            ],
            "total_tokens": self.total_tokens,
            "total_cost_usd": self.total_cost_usd,
            "transferred": self.transferred,
            "hops": len(self.chain),
        }


# ── Orchestrator ───────────────────────────────────────────────────────────────

async def run_with_handoffs(
    agent: "Agent",
    task: str,
    context: dict[str, Any] | None = None,
    config: HandoffConfig | None = None,
) -> HandoffResult:
    """Run a task through an agent network, following ``TRANSFER_TO`` signals.

    Each agent is called with the current task.  If its output contains a
    ``TRANSFER_TO:<name>`` signal and a matching agent exists in
    ``agent.handoffs``, control transfers to that agent with the history
    forwarded according to *config.history_mode*.  The loop repeats until no
    signal is found or *config.max_depth* hops have been taken.

    Parameters
    ----------
    agent:   Entry-point agent.
    task:    Initial task string.
    context: Optional context dict passed to every agent.
    config:  :class:`HandoffConfig` (defaults used if omitted).
    """
    cfg = config or HandoffConfig()
    ctx = context or {}
    chain: list[HandoffLink] = []
    total_tokens = 0
    total_cost = 0.0
    current_agent = agent
    current_task = task

    for _depth in range(cfg.max_depth + 1):
        result = await current_agent.run(current_task, ctx)
        output = result.get("result", "")
        tokens = result.get("tokens", 0)
        cost = result.get("cost_usd", 0.0)
        total_tokens += tokens
        total_cost += cost

        # ── Detect handoff signal ────────────────────────────────────────────
        match = _HANDOFF_RE.search(output)
        if match is None or _depth == cfg.max_depth:
            return HandoffResult(
                output=output,
                final_agent=current_agent.name,
                chain=chain,
                total_tokens=total_tokens,
                total_cost_usd=total_cost,
                transferred=bool(chain),
            )

        target_name = match.group(1)
        reason = (match.group(2) or "").strip()

        target_agent = _find_target(current_agent, target_name)
        if target_agent is None:
            # Unknown agent — stop, return what we have
            return HandoffResult(
                output=output,
                final_agent=current_agent.name,
                chain=chain,
                total_tokens=total_tokens,
                total_cost_usd=total_cost,
                transferred=bool(chain),
            )

        chain.append(HandoffLink(
            from_agent=current_agent.name,
            to_agent=target_name,
            task=current_task,
            reason=reason,
            tokens=tokens,
            cost_usd=cost,
        ))

        # ── Build forwarded task according to history_mode ───────────────────
        if cfg.history_mode == "none":
            current_task = task
        elif cfg.history_mode == "last_n":
            tail = output[-cfg.history_n:] if len(output) > cfg.history_n else output
            current_task = f"[context from {current_agent.name}]: {tail}\n\n{task}"
        else:  # "full"
            current_task = (
                f"[forwarded from {current_agent.name}]\n"
                f"Original task: {task}\n"
                f"Previous output: {output}"
            )

        current_agent = target_agent

    # Unreachable (max_depth guard above returns), but satisfies type-checker
    return HandoffResult(
        output="",
        final_agent=current_agent.name,
        chain=chain,
        total_tokens=total_tokens,
        total_cost_usd=total_cost,
        transferred=bool(chain),
    )


def _find_target(agent: "Agent", name: str) -> "Agent | None":
    """Find a named agent in ``agent.handoffs``."""
    handoffs = getattr(agent, "handoffs", None) or []
    for h in handoffs:
        if getattr(h, "name", None) == name:
            return h
    return None


__all__ = ["HandoffConfig", "HandoffLink", "HandoffResult", "run_with_handoffs"]
