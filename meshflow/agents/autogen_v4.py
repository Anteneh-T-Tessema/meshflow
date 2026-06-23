"""AutoGen 0.4+ native parity — MeshFlow implementations of the autogen-agentchat
actor model patterns.

Implements the AutoGen 0.4+ public API surface *natively* so MeshFlow users can
write AutoGen-style code without installing the autogen package.  All agents run
under the MeshFlow governance kernel.

AutoGen 0.4+ concepts implemented
----------------------------------
AssistantAgent       — LLM-backed agent; primary ``on_messages()`` interface
UserProxyAgent       — Human-in-the-loop or scripted proxy
SocietyOfMind        — Nested multi-agent team where each agent keeps its own
                       context; produces a synthesised final answer
MagenticOne          — Microsoft's orchestrator + specialist pattern; one
                       orchestrator decomposes and assigns sub-tasks to a pool
                       of specialists, iterates until done or max_rounds
TopicSubscription    — Pub/sub subscription binding (type or default)
DefaultTopicId       — Sentinel for the default topic
TypeSubscription     — Topic subscription by Python type
TextMessage          — Canonical message envelope (AutoGen 0.4 protocol)
ChatMessage          — Alias kept for compatibility
ToolCallMessage      — Tool invocation envelope
ToolCallResultMessage — Tool result envelope
CancellationToken    — Cooperative cancellation signal
AgentRuntime         — Minimal in-process runtime (mirrors SingleThreadedAgentRuntime)

Usage::

    from meshflow.agents.autogen_v4 import (
        AssistantAgent, UserProxyAgent, SocietyOfMind,
        MagenticOne, TextMessage, CancellationToken,
    )
    from meshflow.agents.base import EchoProvider

    assistant = AssistantAgent("helper", provider=EchoProvider("Sure!"))
    user      = UserProxyAgent("user",   max_auto_reply=2)

    # AutoGen 0.4 on_messages interface
    token = CancellationToken()
    response = asyncio.run(
        assistant.on_messages([TextMessage(content="hi", source="user")], token)
    )
    print(response.chat_message.content)

    # SocietyOfMind
    team = SocietyOfMind([assistant], inner_termination="MAX_MESSAGES")
    result = team.run("Explain reinforcement learning in 2 sentences.")

    # MagenticOne
    team = MagenticOne(
        orchestrator=AssistantAgent("orch"),
        agents=[
            AssistantAgent("coder",    description="writes code"),
            AssistantAgent("searcher", description="searches the web"),
        ],
        max_rounds=5,
    )
    result = team.run("Build a sentiment analyser in Python.")
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence


# ── Cancellation ──────────────────────────────────────────────────────────────

class CancellationToken:
    """Cooperative cancellation signal (mirrors AutoGen 0.4 CancellationToken)."""

    def __init__(self) -> None:
        self._cancelled = False
        self._callbacks: list[Callable[[], None]] = []

    def cancel(self) -> None:
        self._cancelled = True
        for cb in self._callbacks:
            cb()

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def add_callback(self, cb: Callable[[], None]) -> None:
        self._callbacks.append(cb)


# ── Message types ─────────────────────────────────────────────────────────────

@dataclass
class TextMessage:
    """Plain text message — primary message type in AutoGen 0.4."""
    content: str
    source: str = "user"
    type: str = "TextMessage"


@dataclass
class ChatMessage:
    """Alias for TextMessage; kept for AutoGen 0.2/0.3 compatibility."""
    content: str
    source: str = "user"
    role: str = "user"
    type: str = "ChatMessage"


@dataclass
class ToolCallMessage:
    """Tool invocation request message."""
    content: list[dict[str, Any]] = field(default_factory=list)
    source: str = "assistant"
    type: str = "ToolCallMessage"


@dataclass
class ToolCallResultMessage:
    """Tool invocation result message."""
    content: list[dict[str, Any]] = field(default_factory=list)
    source: str = "tool"
    type: str = "ToolCallResultMessage"


from typing import Union

# Union type mirroring AutoGen's AgentMessage
AgentMessage = Union[TextMessage, ChatMessage, ToolCallMessage, ToolCallResultMessage]

# ── Response ─────────────────────────────────────────────────────────────────

@dataclass
class Response:
    """Response from an agent's ``on_messages`` call."""
    chat_message: TextMessage
    inner_messages: list[AgentMessage] = field(default_factory=list)


# ── Termination conditions ─────────────────────────────────────────────────────

@dataclass
class MaxMessageTermination:
    """Stop after *max_messages* total messages in the thread."""
    max_messages: int

    def __call__(self, messages: list[AgentMessage]) -> bool:
        return len(messages) >= self.max_messages


@dataclass
class TextMentionTermination:
    """Stop when *text* appears in any assistant message."""
    text: str

    def __call__(self, messages: list[AgentMessage]) -> bool:
        for m in messages:
            if isinstance(m, (TextMessage, ChatMessage)) and self.text in m.content:
                return True
        return False


@dataclass
class OrTerminationCondition:
    """Fires if *any* condition fires."""
    conditions: list[Any]

    def __call__(self, messages: list[AgentMessage]) -> bool:
        return any(c(messages) for c in self.conditions)


@dataclass
class AndTerminationCondition:
    """Fires if *all* conditions fire."""
    conditions: list[Any]

    def __call__(self, messages: list[AgentMessage]) -> bool:
        return all(c(messages) for c in self.conditions)


# ── Topic pub/sub ─────────────────────────────────────────────────────────────

@dataclass
class DefaultTopicId:
    """Sentinel for the default topic (AutoGen 0.4 DefaultTopicId)."""
    type: str = "default"
    source: str = ""


@dataclass
class TypeSubscription:
    """Subscribe to messages of a given Python type."""
    topic_type: str
    agent_type: str


@dataclass
class DefaultSubscription:
    """Subscribe to the default topic."""
    agent_type: str = ""


TopicSubscription = Union[TypeSubscription, DefaultSubscription]


# ── AssistantAgent ─────────────────────────────────────────────────────────────

class AssistantAgent:
    """LLM-backed assistant agent — AutoGen 0.4 ``on_messages`` interface.

    Parameters
    ----------
    name:
        Unique identifier.
    description:
        Human-readable description used by orchestrators when choosing which
        specialist to delegate to.
    system_message:
        System prompt override.
    provider:
        MeshFlow LLMProvider.  Defaults to sandbox echo in test mode.
    tools:
        List of MeshFlow Tool objects made available to this agent.
    model:
        Model string (e.g. ``"claude-sonnet-4-6"``).
    memory:
        Whether to enable cross-turn memory.
    mode:
        ``"sandbox"`` for testing (no real API calls).
    reflect_on_tool_use:
        When True the agent makes a follow-up LLM call to summarise tool
        results (mirrors the AutoGen 0.4 reflect_on_tool_use flag).
    """

    def __init__(
        self,
        name: str,
        description: str = "",
        system_message: str = "",
        provider: Any = None,
        tools: list[Any] | None = None,
        model: str = "",
        memory: bool = False,
        mode: str = "production",
        reflect_on_tool_use: bool = False,
    ) -> None:
        self.name = name
        self.description = description or f"AI assistant agent ({name})"
        self.system_message = system_message
        self.provider = provider
        self.tools = tools or []
        self.model = model
        self.memory = memory
        self.mode = mode
        self.reflect_on_tool_use = reflect_on_tool_use
        self._message_history: list[AgentMessage] = []

    # ── AutoGen 0.4 primary interface ─────────────────────────────────────────

    async def on_messages(
        self,
        messages: Sequence[AgentMessage],
        cancellation_token: CancellationToken | None = None,
    ) -> Response:
        """Process *messages* and return a :class:`Response`.

        Mirrors ``autogen_agentchat.agents.AssistantAgent.on_messages()``.
        """
        if cancellation_token and cancellation_token.cancelled:
            return Response(chat_message=TextMessage(content="[cancelled]", source=self.name))

        self._message_history.extend(messages)

        task = " ".join(
            m.content for m in messages
            if isinstance(m, (TextMessage, ChatMessage))
        ) or "respond"

        from meshflow.agents.builder import Agent
        from meshflow.core.workflow import Workflow

        a = Agent(
            name=self.name,
            system_prompt=self.system_message,
            tools=self.tools,
            memory=self.memory,
            mode=self.mode,
            model=self.model or "",
        )
        if self.provider is not None:
            a.provider = self.provider

        wf = Workflow(mode=self.mode)
        wf.add(a)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, wf.run, task)
        reply = result.output or ""

        reply_msg = TextMessage(content=reply, source=self.name)
        self._message_history.append(reply_msg)
        return Response(chat_message=reply_msg, inner_messages=[])

    async def on_messages_stream(
        self,
        messages: Sequence[AgentMessage],
        cancellation_token: CancellationToken | None = None,
    ) -> Any:
        """Streaming variant — yields inner messages then a final Response."""
        response = await self.on_messages(messages, cancellation_token)
        yield response

    async def on_reset(self, cancellation_token: CancellationToken | None = None) -> None:
        """Clear message history (mirrors AutoGen 0.4 on_reset)."""
        self._message_history.clear()

    # ── Convenience sync run ──────────────────────────────────────────────────

    def run_sync(self, task: str) -> str:
        """Convenience synchronous wrapper for single-message runs."""
        from meshflow.integrations._utils import run_sync as _run_sync
        resp = _run_sync(self.on_messages([TextMessage(content=task, source="user")]))
        return resp.chat_message.content


# ── UserProxyAgent ─────────────────────────────────────────────────────────────

class UserProxyAgent:
    """Human-in-the-loop or scripted proxy agent.

    In automated mode (``input_func`` not set), auto-replies with
    ``auto_reply_message`` up to ``max_auto_reply`` times, then terminates.

    Parameters
    ----------
    name:
        Unique identifier.
    description:
        Human-readable description.
    input_func:
        Optional callable ``(prompt: str) -> str`` for interactive input.
        When absent, auto-reply mode is used.
    max_auto_reply:
        Maximum number of automatic replies before stopping.
    auto_reply_message:
        Message to send in auto-reply mode.
    """

    def __init__(
        self,
        name: str = "user_proxy",
        description: str = "Human user proxy",
        input_func: Callable[[str], str] | None = None,
        max_auto_reply: int = 3,
        auto_reply_message: str = "TERMINATE",
    ) -> None:
        self.name = name
        self.description = description
        self._input_func = input_func
        self.max_auto_reply = max_auto_reply
        self.auto_reply_message = auto_reply_message
        self._reply_count = 0
        self._message_history: list[AgentMessage] = []

    async def on_messages(
        self,
        messages: Sequence[AgentMessage],
        cancellation_token: CancellationToken | None = None,
    ) -> Response:
        """Return human input or auto-reply."""
        self._message_history.extend(messages)

        if self._input_func is not None:
            last = next(
                (m.content for m in reversed(messages)
                 if isinstance(m, (TextMessage, ChatMessage))), ""
            )
            reply = self._input_func(last)
        elif self._reply_count < self.max_auto_reply:
            self._reply_count += 1
            reply = self.auto_reply_message
        else:
            reply = "TERMINATE"

        msg = TextMessage(content=reply, source=self.name)
        self._message_history.append(msg)
        return Response(chat_message=msg, inner_messages=[])

    async def on_reset(self, cancellation_token: CancellationToken | None = None) -> None:
        self._message_history.clear()
        self._reply_count = 0


# ── SocietyOfMind ─────────────────────────────────────────────────────────────

class SocietyOfMind:
    """Nested multi-agent team — each inner agent maintains its own context.

    Microsoft AutoGen 0.4's ``SocietyOfMindAgent`` pattern: an outer agent
    runs an inner team, then synthesises their conversation into a single
    coherent answer.

    Parameters
    ----------
    agents:
        Ordered list of :class:`AssistantAgent` instances that form the inner
        team.  They speak round-robin until the termination condition fires.
    termination_condition:
        A callable ``(messages) -> bool``.  Pass ``MaxMessageTermination(n)``
        or ``TextMentionTermination("TERMINATE")`` etc.  Defaults to
        ``MaxMessageTermination(10)``.
    inner_termination:
        Convenience string shorthand: ``"MAX_MESSAGES"`` (10 turns) or
        ``"TERMINATE_KEYWORD"`` (stop on "TERMINATE").
    response_prompt:
        System prompt injected into the synthesis step.
    mode:
        ``"sandbox"`` for offline testing.
    """

    def __init__(
        self,
        agents: list[AssistantAgent],
        termination_condition: Any | None = None,
        inner_termination: str = "MAX_MESSAGES",
        response_prompt: str = "",
        mode: str = "production",
    ) -> None:
        self.agents = agents
        self.mode = mode
        self.response_prompt = response_prompt or (
            "Based on the preceding conversation, provide a clear final answer."
        )
        if termination_condition is not None:
            self._termination = termination_condition
        elif inner_termination == "TERMINATE_KEYWORD":
            self._termination = TextMentionTermination("TERMINATE")
        else:
            self._termination = MaxMessageTermination(10)

    def run(self, task: str, cancellation_token: CancellationToken | None = None) -> str:
        """Run the inner team and return a synthesised answer.  Synchronous."""
        from meshflow.integrations._utils import run_sync
        return run_sync(self.arun(task, cancellation_token))

    async def arun(
        self, task: str, cancellation_token: CancellationToken | None = None
    ) -> str:
        messages: list[AgentMessage] = [TextMessage(content=task, source="user")]
        idx = 0

        while not self._termination(messages):
            if cancellation_token and cancellation_token.cancelled:
                break
            agent = self.agents[idx % len(self.agents)]
            response = await agent.on_messages(messages[-5:], cancellation_token)
            messages.append(response.chat_message)
            idx += 1

        # Synthesis step: ask the first agent to produce a final answer
        synthesis_input = [
            TextMessage(
                content=(
                    f"{self.response_prompt}\n\n"
                    + "\n".join(
                        f"{m.source}: {m.content}"
                        for m in messages
                        if isinstance(m, (TextMessage, ChatMessage))
                    )
                ),
                source="user",
            )
        ]
        synthesiser = self.agents[0]
        result = await synthesiser.on_messages(synthesis_input, cancellation_token)
        return result.chat_message.content


# ── MagenticOne ───────────────────────────────────────────────────────────────

class MagenticOneResult:
    """Result returned by :class:`MagenticOne.run`."""

    def __init__(self, output: str, rounds_used: int, agent_turns: dict[str, int]) -> None:
        self.output = output
        self.rounds_used = rounds_used
        self.agent_turns = agent_turns
        self.completed = True

    def __repr__(self) -> str:
        return (
            f"MagenticOneResult(rounds={self.rounds_used}, "
            f"agents={list(self.agent_turns)}, output={self.output[:60]!r})"
        )


class MagenticOne:
    """Microsoft MAGENTIC-ONE orchestrator + specialist team pattern.

    The *orchestrator* decomposes the task and assigns sub-tasks to
    specialist agents.  After each specialist turn, the orchestrator reviews
    progress and either assigns the next sub-task or declares completion.

    Parameters
    ----------
    orchestrator:
        The :class:`AssistantAgent` that plans and reviews.
    agents:
        List of specialist :class:`AssistantAgent` instances.  The
        orchestrator selects one by name each round.
    max_rounds:
        Hard upper bound on total agent turns (default 10).
    cooperative:
        When True specialists can see each other's prior outputs (default
        True).  Set False to isolate specialists.
    mode:
        ``"sandbox"`` for offline testing.
    """

    def __init__(
        self,
        orchestrator: AssistantAgent,
        agents: list[AssistantAgent],
        max_rounds: int = 10,
        cooperative: bool = True,
        mode: str = "production",
    ) -> None:
        self.orchestrator = orchestrator
        self.agents = {a.name: a for a in agents}
        self.max_rounds = max_rounds
        self.cooperative = cooperative
        self.mode = mode

    def run(self, task: str) -> MagenticOneResult:
        from meshflow.integrations._utils import run_sync
        return run_sync(self.arun(task))

    async def arun(self, task: str) -> MagenticOneResult:
        agent_descriptions = "\n".join(
            f"  - {name}: {a.description}" for name, a in self.agents.items()
        )
        ledger: list[str] = []
        agent_turns: dict[str, int] = {n: 0 for n in self.agents}
        token = CancellationToken()

        for round_n in range(self.max_rounds):
            # Orchestrator plans: choose next agent + sub-task
            context = "\n".join(ledger[-6:]) if ledger else "(no progress yet)"
            plan_prompt = (
                f"Task: {task}\n\n"
                f"Available specialists:\n{agent_descriptions}\n\n"
                f"Progress so far:\n{context}\n\n"
                f"If the task is COMPLETE, reply with exactly: DONE\n"
                f"Otherwise reply with: AGENT:<agent_name>\nSUBTASK:<subtask description>"
            )
            orch_resp = await self.orchestrator.on_messages(
                [TextMessage(content=plan_prompt, source="user")], token
            )
            orch_text = orch_resp.chat_message.content.strip()

            if "DONE" in orch_text:
                break

            # Parse AGENT: / SUBTASK:
            chosen_agent_name = ""
            subtask = task
            for line in orch_text.splitlines():
                if line.startswith("AGENT:"):
                    chosen_agent_name = line[6:].strip()
                elif line.startswith("SUBTASK:"):
                    subtask = line[8:].strip()

            agent = self.agents.get(chosen_agent_name) or next(iter(self.agents.values()))

            context_msg = ""
            if self.cooperative and ledger:
                context_msg = "\n\nContext from prior steps:\n" + "\n".join(ledger[-3:])

            spec_resp = await agent.on_messages(
                [TextMessage(content=subtask + context_msg, source="orchestrator")], token
            )
            spec_output = spec_resp.chat_message.content
            ledger.append(f"[{agent.name}] {spec_output}")
            agent_turns[agent.name] = agent_turns.get(agent.name, 0) + 1

        # Final synthesis by orchestrator
        synthesis_prompt = (
            f"Original task: {task}\n\n"
            f"Work completed:\n" + "\n".join(ledger) + "\n\n"
            "Provide a complete, clear final answer."
        )
        final_resp = await self.orchestrator.on_messages(
            [TextMessage(content=synthesis_prompt, source="user")], token
        )

        return MagenticOneResult(
            output=final_resp.chat_message.content,
            rounds_used=round_n + 1,
            agent_turns=agent_turns,
        )


# ── AgentRuntime (minimal in-process runtime) ─────────────────────────────────

class AgentRuntime:
    """Minimal in-process runtime — mirrors AutoGen 0.4 SingleThreadedAgentRuntime.

    Supports:
    - ``register(agent_type, factory)`` — register an agent factory
    - ``send_message(message, recipient)`` — direct message delivery
    - ``publish_message(message, topic_id)`` — pub/sub delivery
    - ``run_until_idle()`` — drain the message queue
    """

    def __init__(self) -> None:
        self._agents: dict[str, Any] = {}
        self._subscriptions: dict[str, list[str]] = {}  # topic_type → [agent_name]
        self._queue: asyncio.Queue[tuple[Any, str]] = asyncio.Queue()
        self._token = CancellationToken()

    def register(self, agent_type: str, factory: Callable[[], Any]) -> None:
        self._agents[agent_type] = factory()

    def add_subscription(self, subscription: TopicSubscription) -> None:
        if isinstance(subscription, TypeSubscription):
            self._subscriptions.setdefault(subscription.topic_type, []).append(
                subscription.agent_type
            )
        elif isinstance(subscription, DefaultSubscription):
            self._subscriptions.setdefault("default", []).append(subscription.agent_type)

    async def send_message(
        self,
        message: AgentMessage,
        recipient: str,
        cancellation_token: CancellationToken | None = None,
    ) -> None:
        await self._queue.put((message, recipient))

    async def publish_message(
        self,
        message: AgentMessage,
        topic_id: DefaultTopicId | str,
        cancellation_token: CancellationToken | None = None,
    ) -> None:
        topic_type = topic_id.type if isinstance(topic_id, DefaultTopicId) else str(topic_id)
        for agent_name in self._subscriptions.get(topic_type, []):
            await self._queue.put((message, agent_name))

    async def run_until_idle(self) -> None:
        while not self._queue.empty():
            message, agent_name = await self._queue.get()
            agent = self._agents.get(agent_name)
            if agent and hasattr(agent, "on_messages"):
                await agent.on_messages([message], self._token)
