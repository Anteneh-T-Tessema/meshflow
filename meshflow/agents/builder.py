"""Declarative Agent builder — create governed agents without subclassing.

Usage:
    researcher = Agent(
        name="researcher",
        role="researcher",
        model="claude-sonnet-4-6",
        tools=["web_search", "read_file"],
        memory=True,
        system_prompt="You research topics thoroughly and cite sources.",
    )
    result = await researcher.run("What is prompt caching?")
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, TypeVar

from meshflow.agents.base import (
    AgentConfig,
    BaseAgent,
    _build_tool_schema,
    _extract_confidence,
    _parse_json_retry,
)
from meshflow.core.schemas import AgentRole, Policy, RiskTier, policy_for_mode

_T = TypeVar("_T")


_ROLE_MAP: dict[str, AgentRole] = {r.value: r for r in AgentRole}

_ROLE_PROMPTS: dict[AgentRole, str] = {
    AgentRole.PLANNER: (
        "You are a Planner agent. Decompose the task into clear, ordered steps. "
        "Specify which role handles each step, what input it needs, and what it must produce. "
        'Output valid JSON: {"steps": [{"role": "...", "input": "...", "expected_output": "..."}], '
        '"confidence": 0.85}'
    ),
    AgentRole.RESEARCHER: (
        "You are a Researcher agent. Provide thorough, factual answers with source attribution. "
        "Explicitly flag uncertainty. Structure your output clearly.\n"
        "On the very last line write: CONFIDENCE:0.XX"
    ),
    AgentRole.EXECUTOR: (
        "You are an Executor agent. Execute the given step precisely. "
        "Write complete, runnable code when asked. "
        "Declare any irreversible actions as an Intent before proceeding.\n"
        "On the very last line write: CONFIDENCE:0.XX"
    ),
    AgentRole.CRITIC: (
        "You are a Critic agent. Evaluate the output for correctness, completeness, and reasoning quality. "
        "Score 0–10 and list specific issues or strengths. "
        'Output JSON: {"score": N, "issues": [...], "strengths": [...]}'
    ),
    AgentRole.ORCHESTRATOR: (
        "You are an Orchestrator agent. Coordinate other agents, route tasks, and synthesise results. "
        "Ensure every handoff has clear inputs and success criteria."
    ),
    AgentRole.GUARDIAN: (
        "You are a Guardian agent. Review every proposed action for safety, compliance, and policy adherence. "
        "Block or escalate any action that violates policy."
    ),
}


class _BuiltAgent(BaseAgent):
    """Runtime agent produced by the Agent builder."""

    def __init__(
        self,
        config: AgentConfig,
        policy: Policy,
        tools: list[Any],
        memory_enabled: bool,
    ) -> None:
        super().__init__(config, policy)
        self._tools = tools
        self._memory_enabled = memory_enabled
        from meshflow.intelligence.memory import AgentMemory
        self._memory = AgentMemory(
            agent_id=config.agent_id,
            max_working=10,
            max_episodic=50,
            enabled=memory_enabled,
        )

    def remember(self, content: str, metadata: dict[str, Any] | None = None) -> None:
        """Explicitly store a memory entry."""
        self._memory.add(content, metadata)

    def recall(self, query: str, top_k: int = 3) -> list[str]:
        """Retrieve relevant memories by semantic similarity."""
        return self._memory.recall(query, top_k=top_k)

    async def run_typed(
        self,
        task: str,
        output_type: type[_T],
        context: dict[str, Any] | None = None,
    ) -> _T:
        """Run the agent and parse the output into a Pydantic model.

        Retries once with error feedback if the first response is not valid JSON.
        """
        try:
            schema_str = json.dumps(output_type.model_json_schema(), indent=2)  # type: ignore[attr-defined]
        except AttributeError as exc:
            raise TypeError(f"{output_type} must be a Pydantic BaseModel") from exc

        system = (
            f"{self.config.system_prompt}\n\n"
            f"You MUST respond with a JSON object matching this schema:\n{schema_str}\n"
            f"Output ONLY the JSON — no prose, no markdown fences."
        )
        ctx_str = json.dumps(context or {})
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": f"Task: {task}\nContext: {ctx_str}"}
        ]

        last_exc: Exception = ValueError("no attempts made")
        for attempt in range(2):
            content, _, _ = await self.think(messages, system)
            data = _parse_json_retry(content)
            if data is not None:
                try:
                    return output_type.model_validate(data)  # type: ignore[attr-defined]
                except Exception as exc:
                    last_exc = exc
            else:
                last_exc = ValueError(f"response was not valid JSON: {content[:120]}")
            messages += [
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": (
                        f"That was not valid JSON. Error: {last_exc}. "
                        "Return ONLY valid JSON matching the schema."
                    ),
                },
            ]

        raise ValueError(
            f"Failed to produce valid {output_type.__name__} after 2 attempts: {last_exc}"
        )

    async def step(self, task: str, context: dict[str, Any]) -> dict[str, Any]:
        # Build memory context from 4-tier memory (working + recent episodic)
        mem_ctx = self._memory.context_string(max_chars=600) if self._memory_enabled else ""
        if mem_ctx:
            mem_ctx = f"\n\n[Memory]\n{mem_ctx}"

        # If caller passed semantic recall hints, do a retrieval pass
        recall_query = context.get("__recall__", task)
        recalled = self._memory.recall(str(recall_query), top_k=2) if self._memory_enabled else []
        recall_ctx = ""
        if recalled:
            recall_ctx = "\n\n[Retrieved]\n" + "\n".join(f"• {r}" for r in recalled)

        messages = [
            {"role": "user", "content": f"Task: {task}\nContext: {context}{mem_ctx}{recall_ctx}"}
        ]

        if self._tools:
            tool_schemas = [_build_tool_schema(t) for t in self._tools if hasattr(t, "name")]
            tool_fns = {
                t.name: t.fn for t in self._tools if hasattr(t, "name") and hasattr(t, "fn")
            }
            content, tokens, cost = await self.think_with_tools(messages, tool_schemas, tool_fns)
        else:
            content, tokens, cost = await self.think(messages)

        confidence, content = _extract_confidence(content)

        # Store the result in 4-tier memory
        if self._memory_enabled:
            self._memory.add(content, metadata={"task": task[:100], "confidence": confidence})

        return {
            "result": content,
            "agent_name": self.config.agent_id,
            "role": self.config.role.value,
            "tokens": tokens,
            "cost_usd": cost,
            "stated_confidence": confidence,
        }


@dataclass
class Agent:
    """Declarative agent builder — works with any LLM, zero config needed.

    MeshFlow infers the right provider from the model name (CrewAI pattern):

        Agent(name="a", model="gpt-4o")             # → OpenAI
        Agent(name="b", model="claude-opus-4-7")     # → Anthropic
        Agent(name="c", model="gemini-2.0-flash")    # → Google
        Agent(name="d", model="llama3.2")            # → local Ollama
        Agent(name="e", model="groq/llama-3.1-70b")  # → LiteLLM

    Or pass a pre-built LLM / provider explicitly:

        from meshflow import LLM
        agent = Agent(name="f", llm=LLM("gpt-4o", api_key="sk-..."))

    Or let the environment decide (auto_detect_provider picks the best available
    API key or locally running Ollama):

        agent = Agent(name="g", role="researcher")   # no model= needed

    Parameters
    ----------
    name:          Unique identifier for this agent.
    role:          planner / researcher / executor / critic / orchestrator / guardian.
    model:         Any model name string — provider auto-inferred from it.
    llm:           A pre-built LLM instance or any LLMProvider. Overrides model=.
    tools:         List of Tool objects or tool name strings.
    memory:        Enable cross-step memory for this agent.
    system_prompt: Override the default role prompt.
    risk:          Risk tier for actions this agent takes.
    policy:        Governance policy (defaults to standard).
    provider:      Low-level LLMProvider object. Prefer llm= for the unified API.
    """

    name: str
    role: str | AgentRole = AgentRole.EXECUTOR
    model: str = ""              # empty → auto-detect from env; set to fix the model
    llm: Any = None              # LLM instance or any LLMProvider — preferred API
    tools: list[Any] = field(default_factory=list)
    memory: bool = False
    system_prompt: str = ""
    risk: RiskTier = RiskTier.READ_ONLY
    policy: Policy | str | None = None
    provider: Any = None         # low-level escape hatch; prefer llm=
    _prebuilt_node: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.role, str):
            self.role = _ROLE_MAP.get(self.role, AgentRole.EXECUTOR)
        if isinstance(self.policy, str):
            self.policy = policy_for_mode(self.policy)
        if self.policy is None:
            self.policy = policy_for_mode("standard")

    def _resolve_provider(self) -> Any:
        """Return the LLMProvider to use, in priority order:
        1. llm= (LLM class or any LLMProvider)
        2. provider= (raw LLMProvider)
        3. model_to_provider(model) when model is set
        4. auto_detect_provider() from environment
        """
        if self.llm is not None:
            # LLM class forwards the protocol; any LLMProvider also accepted
            return self.llm
        if self.provider is not None:
            return self.provider
        # Let BaseAgent.__init__ handle inference via model name / env
        return None

    def _resolve_model(self) -> str:
        """Return the canonical model string."""
        if self.model:
            return self.model
        # If an LLM class was given, read its model attribute
        if self.llm is not None and hasattr(self.llm, "model") and self.llm.model:
            return self.llm.model
        import os
        return os.environ.get("MESHFLOW_MODEL", "claude-sonnet-4-6")

    def _build(self) -> _BuiltAgent:
        role = self.role if isinstance(self.role, AgentRole) else AgentRole.EXECUTOR
        prompt = self.system_prompt or _ROLE_PROMPTS.get(role, "")
        config = AgentConfig(
            agent_id=self.name,
            role=role,
            model=self._resolve_model(),
            system_prompt=prompt,
            tools=[getattr(t, "name", str(t)) for t in self.tools],
            provider=self._resolve_provider(),
        )
        return _BuiltAgent(config, self.policy, self.tools, self.memory)  # type: ignore[arg-type]

    async def run_typed(
        self,
        task: str,
        output_type: type[_T],
        context: dict[str, Any] | None = None,
    ) -> _T:
        """Run this agent and parse the response into *output_type* (a Pydantic model)."""
        agent = self._build()
        return await agent.run_typed(task, output_type, context)

    async def stream(
        self,
        task: str,
        context: dict[str, Any] | None = None,
    ) -> Any:
        """Async generator that yields token strings as the LLM produces them.

        Usage::

            async for token in agent.stream("Explain HIPAA §164.502"):
                print(token, end="", flush=True)
        """
        from collections.abc import AsyncIterator

        built = self._build()
        role = built.config.role
        prompt = built.config.system_prompt
        model = built.config.model

        mem_ctx = built._memory.context_string(max_chars=600) if built._memory_enabled else ""
        if mem_ctx:
            mem_ctx = f"\n\n[Memory]\n{mem_ctx}"

        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": f"Task: {task}\nContext: {context or {}}{mem_ctx}",
            }
        ]

        import uuid as _uuid
        run_id = str(_uuid.uuid4())[:8]
        step_id = str(_uuid.uuid4())[:8]

        stream_gen: AsyncIterator[Any] = built._provider.stream_complete(
            model=model,
            messages=messages,
            system=prompt,
            max_tokens=built.config.max_tokens,
            agent_id=built.agent_id,
            step_id=step_id,
            run_id=run_id,
        )
        async for chunk in stream_gen:
            text = getattr(chunk, "text", str(chunk))
            if text:
                yield text

    async def run(self, task: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run this agent on a task and return the result dict.

        If the agent was imported from an external framework (LangGraph, IBM,
        OpenAI, A2A, etc.) the prebuilt node runs instead of a new Claude call.
        """
        if self._prebuilt_node is not None:
            from meshflow.core.node import NodeInput, NodeOutput

            node_in = NodeInput(task=task, context=context or {})
            out: NodeOutput = await self._prebuilt_node.run(node_in)
            return {
                "result": out.content,
                "agent_name": self.name,
                "role": str(self.role.value if isinstance(self.role, AgentRole) else self.role),
                "tokens": out.tokens_used,
                "cost_usd": 0.0,
                "stated_confidence": out.confidence,
                "structured": out.structured,
            }
        agent = self._build()
        return await agent.step(task, context or {})

    def to_mesh_node(self) -> Any:
        """Convert to a MeshNode for use inside WorkflowDefinition / Team.

        If the agent was imported from an external framework the prebuilt node
        is returned directly — it already knows how to call the original graph/agent.
        """
        if self._prebuilt_node is not None:
            return self._prebuilt_node

        from meshflow.core.node import MeshNode

        built = self._build()

        async def _runner(task: str, ctx: dict[str, Any]) -> Any:
            from meshflow.core.node import NodeOutput

            result = await built.step(task, ctx)
            return NodeOutput(
                content=result.get("result", ""),
                structured=result,
                tokens_used=result.get("tokens", 0),
                model=self.model,
                confidence=result.get("stated_confidence", 0.8),
            )

        return MeshNode.from_callable(
            self.name,
            _runner,
            risk=self.risk,
            capabilities=[self.role.value if isinstance(self.role, AgentRole) else self.role],
        )
