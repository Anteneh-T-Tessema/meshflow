"""GroupChat — AutoGen-style multi-agent conversational orchestration.

Usage:
    from meshflow.agents.conversation import GroupChat, GroupChatManager

    researcher = Agent(name="researcher", role="researcher")
    coder      = Agent(name="coder",      role="executor")
    critic     = Agent(name="critic",     role="critic")

    chat = GroupChat(
        agents=[researcher, coder, critic],
        max_turns=12,
        speaker_selection="auto",       # round_robin | random | auto | custom
        termination="TERMINATE",        # string keyword OR callable
    )
    manager = GroupChatManager(chat, policy="standard")
    result  = await manager.run("Build a REST API for a todo list")
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from meshflow.core.schemas import Policy, policy_for_mode


SpeakerStrategy = Literal["round_robin", "random", "auto", "custom"]


# ── Message ───────────────────────────────────────────────────────────────────

@dataclass
class ChatMessage:
    sender: str
    content: str
    turn: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.sender}] {self.content}"


# ── GroupChat ─────────────────────────────────────────────────────────────────

@dataclass
class GroupChat:
    """Shared conversation context for a group of agents.

    Parameters
    ----------
    agents:             List of Agent objects participating in the chat.
    max_turns:          Hard cap on conversation turns (default 20).
    speaker_selection:  How the next speaker is chosen.
        - ``round_robin``: agents speak in order, cycling.
        - ``random``:      random agent each turn.
        - ``auto``:        an LLM selects the most appropriate next speaker.
        - ``custom``:      ``speaker_fn(messages, agents) -> Agent``.
    termination:        Stop condition.
        - str:            Conversation ends when any message contains this string.
        - callable:       ``fn(messages: list[ChatMessage]) -> bool``.
    speaker_fn:         Required when ``speaker_selection="custom"``.
    """

    agents: list[Any]  # list[Agent]
    max_turns: int = 20
    speaker_selection: SpeakerStrategy = "round_robin"
    termination: str | Callable[[list[ChatMessage]], bool] = "TERMINATE"
    speaker_fn: Callable | None = None
    allowed_transitions: dict[str, list[str]] | None = None

    def __post_init__(self) -> None:
        if not self.agents:
            raise ValueError("GroupChat requires at least one agent.")
        if self.speaker_selection == "custom" and self.speaker_fn is None:
            raise ValueError("speaker_fn is required when speaker_selection='custom'.")
        self._history: list[ChatMessage] = []
        self._turn: int = 0
        self._rr_index: int = 0

    @property
    def history(self) -> list[ChatMessage]:
        return list(self._history)

    def _add(self, msg: ChatMessage) -> None:
        msg.turn = self._turn
        self._history.append(msg)

    def _format_history(self, last_n: int = 20) -> str:
        return "\n".join(str(m) for m in self._history[-last_n:])

    def _should_terminate(self) -> bool:
        if not self._history:
            return False
        if callable(self.termination):
            return self.termination(self._history)
        keyword = self.termination
        last = self._history[-1].content
        return keyword in last

    def _pick_next(self, last_speaker: Any | None = None) -> Any:
        pool = self.agents
        if self.allowed_transitions:
            last_name = None
            if last_speaker is not None:
                last_name = getattr(last_speaker, "name", None)
            elif self._history:
                last_name = self._history[-1].sender
            
            allowed_names = self.allowed_transitions.get(last_name or "") or []
            candidates = [a for a in self.agents if a.name in allowed_names]
            if candidates:
                pool = candidates

        if self.speaker_selection == "round_robin":
            agent = pool[self._rr_index % len(pool)]
            self._rr_index += 1
            return agent
        if self.speaker_selection == "random":
            return random.choice(pool)
        if self.speaker_selection == "custom" and self.speaker_fn:
            return self.speaker_fn(self._history, pool)
        # "auto" — fall back to round_robin at runtime; GroupChatManager overrides this
        agent = pool[self._rr_index % len(pool)]
        self._rr_index += 1
        return agent


# ── GroupChatManager ──────────────────────────────────────────────────────────

@dataclass
class GroupChatManager:
    """Manages a GroupChat conversation — initiates and drives turns.

    Usage:
        manager = GroupChatManager(chat, policy="standard")
        result  = await manager.run("Design a microservice architecture")
    """

    chat: GroupChat
    policy: Policy | str | None = None
    system_message: str = (
        "You are participating in a group discussion. "
        "Collaborate, build on others' ideas, and help the team reach the best answer. "
        "When the task is complete, include 'TERMINATE' in your final message."
    )

    def __post_init__(self) -> None:
        if isinstance(self.policy, str):
            self.policy = policy_for_mode(self.policy)
        if self.policy is None:
            self.policy = policy_for_mode("standard")

    async def run(
        self,
        task: str,
        context: dict[str, Any] | None = None,
    ) -> "ConversationResult":
        """Run the group chat until termination or max_turns."""
        chat = self.chat
        chat._history.clear()
        chat._turn = 0
        chat._rr_index = 0

        # Seed with the human/system task
        chat._add(ChatMessage(sender="user", content=task))

        last_speaker = None

        while chat._turn < chat.max_turns:
            chat._turn += 1

            # Pick next agent
            if chat.speaker_selection == "auto":
                next_agent = await self._auto_select(chat, last_speaker)
            else:
                next_agent = chat._pick_next(last_speaker)

            # Build context for this agent
            agent_task = (
                f"{self.system_message}\n\n"
                f"=== Conversation so far ===\n{chat._format_history()}\n\n"
                f"=== Your turn ({next_agent.name}) ===\n"
                f"Respond to the conversation above. "
                f"If the task is complete, end your message with 'TERMINATE'."
            )

            # Run the agent
            result = await next_agent.run(agent_task, context or {})
            content = result.get("result", "")

            msg = ChatMessage(
                sender=next_agent.name,
                content=content,
                metadata={
                    "tokens": result.get("tokens", 0),
                    "cost_usd": result.get("cost_usd", 0.0),
                    "confidence": result.get("stated_confidence", 0.8),
                },
            )
            chat._add(msg)
            last_speaker = next_agent

            if chat._should_terminate():
                break

        # Build result
        total_tokens = sum(m.metadata.get("tokens", 0) for m in chat._history)
        total_cost = sum(m.metadata.get("cost_usd", 0.0) for m in chat._history)

        return ConversationResult(
            messages=chat.history,
            total_turns=chat._turn,
            total_tokens=total_tokens,
            total_cost_usd=total_cost,
            terminated=chat._should_terminate() or chat._turn >= chat.max_turns,
            last_message=chat._history[-1].content if chat._history else "",
            participants=[a.name for a in chat.agents],
        )

    async def _auto_select(
        self,
        chat: GroupChat,
        last_speaker: Any | None,
    ) -> Any:
        """Use an LLM to select the most appropriate next speaker."""
        if len(chat.agents) == 1:
            return chat.agents[0]

        agent_names = [a.name for a in chat.agents]
        if chat.allowed_transitions:
            last_name = getattr(last_speaker, "name", None) if last_speaker else (chat._history[-1].sender if chat._history else None)
            allowed_names = chat.allowed_transitions.get(last_name or "") or []
            candidates = [name for name in agent_names if name in allowed_names]
            if candidates:
                agent_names = candidates

        if len(agent_names) == 1:
            for agent in chat.agents:
                if agent.name == agent_names[0]:
                    return agent

        history_str = chat._format_history(last_n=10)

        selector_prompt = (
            f"You are a meeting facilitator. Given this conversation:\n\n"
            f"{history_str}\n\n"
            f"Choose the SINGLE most appropriate next speaker from: {agent_names}.\n"
            f"Consider: who has the most relevant expertise, who hasn't spoken recently, "
            f"and what the conversation needs next.\n\n"
            f"Reply with ONLY the agent name, nothing else."
        )

        # Use the first agent's infrastructure to call the LLM for selection
        selector = chat.agents[0]
        try:
            result = await selector.run(selector_prompt, {})
            chosen_name = result.get("result", "").strip().split()[0]
            for agent in chat.agents:
                if agent.name == chosen_name:
                    return agent
        except Exception:
            pass

        # Fallback to round-robin if selection fails
        return chat._pick_next(last_speaker)

    def stream(
        self,
        task: str,
        context: dict[str, Any] | None = None,
    ):
        """Async generator yielding ChatMessage objects as they are produced."""
        return self._stream_impl(task, context)

    async def _stream_impl(
        self,
        task: str,
        context: dict[str, Any] | None = None,
    ):
        chat = self.chat
        chat._history.clear()
        chat._turn = 0
        chat._rr_index = 0

        seed = ChatMessage(sender="user", content=task)
        chat._add(seed)
        yield seed

        last_speaker = None

        while chat._turn < chat.max_turns:
            chat._turn += 1

            if chat.speaker_selection == "auto":
                next_agent = await self._auto_select(chat, last_speaker)
            else:
                next_agent = chat._pick_next(last_speaker)

            agent_task = (
                f"{self.system_message}\n\n"
                f"=== Conversation so far ===\n{chat._format_history()}\n\n"
                f"=== Your turn ({next_agent.name}) ===\nRespond now."
            )

            result = await next_agent.run(agent_task, context or {})
            content = result.get("result", "")

            msg = ChatMessage(
                sender=next_agent.name,
                content=content,
                metadata={
                    "tokens": result.get("tokens", 0),
                    "cost_usd": result.get("cost_usd", 0.0),
                },
            )
            chat._add(msg)
            last_speaker = next_agent
            yield msg

            if chat._should_terminate():
                break


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class ConversationResult:
    """Output of a completed GroupChat conversation."""

    messages: list[ChatMessage]
    total_turns: int
    total_tokens: int
    total_cost_usd: float
    terminated: bool
    last_message: str
    participants: list[str]

    def transcript(self) -> str:
        """Full formatted conversation transcript."""
        return "\n\n".join(str(m) for m in self.messages)

    def messages_from(self, sender: str) -> list[ChatMessage]:
        """Filter messages by sender name."""
        return [m for m in self.messages if m.sender == sender]


__all__ = [
    "GroupChat",
    "GroupChatManager",
    "ConversationResult",
    "ChatMessage",
    "SpeakerStrategy",
]
