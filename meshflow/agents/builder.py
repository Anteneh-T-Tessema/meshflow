"""Declarative Agent builder — create governed agents without subclassing.

Usage:
    researcher = Agent(
        name="researcher",
        role="researcher",
        model="claude-sonnet-4-6",
        tools=["web_search", "read_file"],
        skills=["data_analysis", "web_search"],   # augment system prompt
        memory=True,
        system_prompt="You research topics thoroughly and cite sources.",
    )
    result = await researcher.run("What is prompt caching?")

MCP server integration (CrewAI-style):
    agent = Agent(
        name="mcp_agent",
        role="executor",
        mcps=["https://mcp.example.com/sse"],     # HTTP SSE MCP server
    )
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
        input_guardrails: list[Any] | None = None,
        output_guardrails: list[Any] | None = None,
        knowledge: list[Any] | None = None,
        memory_backend: Any = None,
        memory_session_id: str = "",
    ) -> None:
        super().__init__(config, policy)
        self._tools = tools
        self._memory_enabled = memory_enabled
        from meshflow.intelligence.memory import AgentMemory
        from meshflow.security.guardrails import GuardrailStack
        self._memory = AgentMemory(
            agent_id=config.agent_id,
            max_working=10,
            max_episodic=50,
            enabled=memory_enabled,
        )
        self._memory_backend: Any = memory_backend
        self._memory_session_id: str = memory_session_id or config.agent_id
        # Restore persisted memory if backend available
        if memory_backend is not None and memory_enabled:
            self._restore_memory()
        self._input_stack = GuardrailStack(input_guardrails or [], mode="strict")
        self._output_stack = GuardrailStack(output_guardrails or [], mode="strict")
        if knowledge:
            from meshflow.intelligence.knowledge import AgentKnowledge
            self._knowledge: Any = AgentKnowledge(knowledge)
        else:
            self._knowledge = None

    def _restore_memory(self) -> None:
        from meshflow.intelligence.memory_backends import restore_memory
        snapshot = self._memory_backend.load(self._memory_session_id)
        if snapshot is not None:
            restore_memory(self._memory, snapshot)

    def _persist_memory(self) -> None:
        if self._memory_backend is None or not self._memory_enabled:
            return
        from meshflow.intelligence.memory_backends import snapshot_from_memory
        self._memory_backend.save(
            self._memory_session_id, snapshot_from_memory(self._memory)
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
        from meshflow.security.guardrails import GuardrailViolation

        # ── Input guardrails ──────────────────────────────────────────────────
        guardrail_results: list[Any] = []
        if self._input_stack.guardrails:
            try:
                _, task, in_results = self._input_stack.run(task)
                guardrail_results.extend(in_results)
            except GuardrailViolation as exc:
                return {
                    "result": f"[BLOCKED by {exc.result.guardrail_name}] {exc.result.reason}",
                    "agent_name": self.config.agent_id,
                    "role": self.config.role.value,
                    "tokens": 0,
                    "cost_usd": 0.0,
                    "stated_confidence": 0.0,
                    "blocked": True,
                    "guardrail": exc.result.guardrail_name,
                    "guardrail_reason": exc.result.reason,
                }

        # ── Build memory context ──────────────────────────────────────────────
        mem_ctx = self._memory.context_string(max_chars=600) if self._memory_enabled else ""
        if mem_ctx:
            mem_ctx = f"\n\n[Memory]\n{mem_ctx}"

        recall_query = context.get("__recall__", task)
        recalled = self._memory.recall(str(recall_query), top_k=2) if self._memory_enabled else []
        recall_ctx = ""
        if recalled:
            recall_ctx = "\n\n[Retrieved]\n" + "\n".join(f"• {r}" for r in recalled)

        # ── Knowledge retrieval (RAG) ─────────────────────────────────────────
        knowledge_ctx = ""
        if self._knowledge:
            k_text = self._knowledge.context_string(task, max_chars=1500)
            if k_text:
                knowledge_ctx = f"\n\n[Knowledge]\n{k_text}"

        messages = [
            {"role": "user", "content": f"Task: {task}\nContext: {context}{mem_ctx}{recall_ctx}{knowledge_ctx}"}
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

        # ── Output guardrails ─────────────────────────────────────────────────
        if self._output_stack.guardrails:
            try:
                _, content, out_results = self._output_stack.run(content)
                guardrail_results.extend(out_results)
            except GuardrailViolation as exc:
                return {
                    "result": f"[BLOCKED by {exc.result.guardrail_name}] {exc.result.reason}",
                    "agent_name": self.config.agent_id,
                    "role": self.config.role.value,
                    "tokens": tokens,
                    "cost_usd": cost,
                    "stated_confidence": confidence,
                    "blocked": True,
                    "guardrail": exc.result.guardrail_name,
                    "guardrail_reason": exc.result.reason,
                }

        # ── Memory ────────────────────────────────────────────────────────────
        if self._memory_enabled:
            self._memory.add(content, metadata={"task": task[:100], "confidence": confidence})
            self._persist_memory()

        return {
            "result": content,
            "agent_name": self.config.agent_id,
            "role": self.config.role.value,
            "tokens": tokens,
            "cost_usd": cost,
            "stated_confidence": confidence,
            "blocked": False,
            "guardrail_results": guardrail_results,
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
    skills:            Built-in skill names that augment the system prompt.
                       e.g. ["python", "data_analysis", "security"].
                       See meshflow.agents.skills.list_skills() for all names.
    mcps:              MCP server URLs (str) or StdioServerParams to connect to.
                       Tools from each server are added to the agent automatically.
    input_guardrails:  Guardrail instances applied to the task text BEFORE the LLM.
                       Block, warn, or modify input. e.g. [PIIBlockGuardrail()].
    output_guardrails: Guardrail instances applied to the LLM output BEFORE returning.
                       e.g. [ConfidenceGuardrail(0.7), LengthGuardrail(max_chars=2000)].
    knowledge:         Knowledge sources for auto-retrieval at each step.
                       Accepts file paths (str), raw text snippets (str),
                       VectorStore objects, or KnowledgeSource objects.
                       Retrieved chunks are injected as [Knowledge] context.
    memory:            Enable cross-step memory for this agent.
    system_prompt:     Override the default role prompt.
    risk:              Risk tier for actions this agent takes.
    policy:            Governance policy (defaults to standard).
    provider:          Low-level LLMProvider object. Prefer llm= for the unified API.
    """

    name: str
    role: str | AgentRole = AgentRole.EXECUTOR
    model: str = ""              # empty → auto-detect from env; set to fix the model
    llm: Any = None              # LLM instance or any LLMProvider — preferred API
    tools: list[Any] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)   # built-in skill names
    mcps: list[Any] = field(default_factory=list)     # MCP server URLs or params
    input_guardrails: list[Any] = field(default_factory=list)   # Guardrail instances
    output_guardrails: list[Any] = field(default_factory=list)  # Guardrail instances
    knowledge: list[Any] = field(default_factory=list)          # str | VectorStore | KnowledgeSource
    memory: bool = False
    memory_backend: Any = None     # MemoryBackend instance or "sqlite://path.db" shorthand
    memory_session_id: str = ""    # defaults to agent.name when empty
    cache: Any = None              # LLMCache instance, True (→ InMemoryCache), or False
    healing: Any = None            # HealingPolicy instance or None (disabled)
    handoffs: list[Any] = field(default_factory=list)  # peer agents this agent can transfer to
    delegates: list[Agent] = field(default_factory=list)  # peer agents this agent can delegate subtasks to
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

        # Resolve named prompt from registry (prompt= "name" or "name:version_id")
        if not prompt and hasattr(self, "_prompt_ref") and self._prompt_ref:
            try:
                from meshflow.prompts.core import PromptRegistry
                _reg = getattr(self, "_prompt_registry", None) or PromptRegistry()
                parts = self._prompt_ref.split(":", 1)
                _name = parts[0]
                _ver = parts[1] if len(parts) > 1 else None
                tmpl = _reg.get(_name, version=_ver)
                prompt = str(tmpl)
            except Exception:
                pass

        # Augment system prompt with built-in skill descriptions
        if self.skills:
            from meshflow.agents.skills import skill_prompt
            extra = skill_prompt(self.skills)
            if extra:
                prompt = f"{prompt}\n\n{extra}" if prompt else extra

        # Resolve MCP tools from server URLs / params
        all_tools = list(self.tools)
        if self.mcps:
            mcp_tools = self._resolve_mcp_tools()
            all_tools = all_tools + mcp_tools

        # Resolve delegation tools
        if self.delegates:
            from meshflow.tools.registry import Tool
            for d in self.delegates:
                d_name = d.name
                d_role = d.role.value if hasattr(d.role, "value") else str(d.role)

                async def _delegate_call(task: str, _target=d) -> str:
                    res = await _target.run(task)
                    return res.get("result", "")

                async def _ask_call(question: str, _target=d) -> str:
                    res = await _target.run(question)
                    return res.get("result", "")

                all_tools.append(Tool(
                    name=f"delegate_to_{d_name}",
                    description=f"Delegate a subtask to the {d_name} agent ({d_role}). Input should be the subtask description.",
                    fn=_delegate_call,
                    risk=RiskTier.READ_ONLY,
                ))
                all_tools.append(Tool(
                    name=f"ask_question_to_{d_name}",
                    description=f"Ask a specific question to the {d_name} agent ({d_role}). Input should be the question.",
                    fn=_ask_call,
                    risk=RiskTier.READ_ONLY,
                ))

        config = AgentConfig(
            agent_id=self.name,
            role=role,
            model=self._resolve_model(),
            system_prompt=prompt,
            tools=[getattr(t, "name", str(t)) for t in all_tools],
            provider=self._resolve_provider(),
        )
        built = _BuiltAgent(
            config,
            self.policy,            # type: ignore[arg-type]
            all_tools,
            self.memory,
            list(self.input_guardrails),
            list(self.output_guardrails),
            list(self.knowledge) if self.knowledge else None,
            memory_backend=self._resolve_memory_backend(),
            memory_session_id=self.memory_session_id or self.name,
        )
        # Wrap the fully-resolved provider with cache AFTER BaseAgent.__init__ sets it
        if self.cache is not None and self.cache is not False:
            from meshflow.cache.provider import CachedProvider
            from meshflow.cache.core import InMemoryCache
            llm_cache = self.cache if self.cache is not True else InMemoryCache()
            built._provider = CachedProvider(built._provider, llm_cache)
        return built

    def _resolve_memory_backend(self) -> Any:
        """Resolve memory_backend= to a MemoryBackend instance."""
        backend = self.memory_backend
        if backend is None:
            return None
        if isinstance(backend, str):
            from meshflow.intelligence.memory_backends import SQLiteMemoryBackend
            if backend.startswith("sqlite://"):
                path = backend[len("sqlite://"):]
                return SQLiteMemoryBackend(path or "meshflow_memory.db")
            # bare path
            return SQLiteMemoryBackend(backend)
        return backend  # already a MemoryBackend instance

    def _resolve_mcp_tools(self) -> list[Any]:
        """Convert mcps= list into Tool objects via MCPGateway manifests.

        Each entry can be:
        - A URL string  → registers as a trusted HTTP MCP server manifest
        - An object with .command (StdioServerParams-like) → stdio transport

        Returns a list of lightweight Tool wrappers that call the gateway.
        """
        from meshflow.mcp.gateway import MCPGateway, ToolManifest
        from meshflow.tools.registry import Tool
        from meshflow.core.schemas import RiskTier as _RT

        gateway = MCPGateway(budget_usd_per_turn=0.10)
        tools: list[Any] = []

        for entry in self.mcps:
            if isinstance(entry, str):
                server_uri = entry
                tool_name = f"mcp_{re.sub(r'[^a-z0-9]', '_', server_uri.lower())[:32]}"
                manifest = ToolManifest(
                    tool_name=tool_name,
                    server_uri=server_uri,
                    description=f"MCP tool from {server_uri}",
                    trusted=True,
                )
                gateway.register_tool(manifest)

                async def _call(params: dict[str, Any], _uri: str = server_uri, _name: str = tool_name) -> str:
                    result = await gateway.call(_name, params, agent_id=self.name, cost_per_call=0.0)
                    return str(result)

                tools.append(Tool(
                    name=tool_name,
                    description=f"MCP tool at {server_uri}",
                    fn=_call,
                    risk=_RT.EXTERNAL_IO,
                ))
            else:
                # Stdio-style params object — skip (requires running subprocess)
                pass

        return tools

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

    async def run_structured(
        self,
        task: str,
        schema: Any,
        *,
        max_retries: int = 3,
        context: dict[str, Any] | None = None,
    ) -> Any:
        """Run this agent and guarantee structured output matching *schema*.

        Parameters
        ----------
        task:        The task description / prompt.
        schema:      A Pydantic model class **or** a plain JSON Schema dict.
        max_retries: How many LLM calls to attempt before raising
                     :exc:`~meshflow.agents.structured.StructuredOutputError`.
        context:     Optional extra context passed to the agent.

        Returns a :class:`~meshflow.agents.structured.StructuredOutputResult`
        whose ``.data`` is a validated Pydantic instance (or dict for plain schemas).

        Raises
        ------
        StructuredOutputError
            When all *max_retries* attempts fail to produce valid structured output.
        """
        from meshflow.agents.structured import (
            StructuredOutputParser,
            StructuredOutputResult,
            StructuredOutputError,
        )

        parser = StructuredOutputParser(schema, max_retries=max_retries)
        built = self._build()

        total_tokens = 0
        total_cost = 0.0
        last_raw = ""
        last_err = ""

        # Augment system prompt to enforce JSON-only output
        original_system = built.config.system_prompt
        built.config.system_prompt = original_system + parser.SYSTEM_SUFFIX

        for attempt in range(1, max_retries + 1):
            if attempt == 1:
                prompt = parser.build_prompt(task)
            else:
                prompt = parser.build_retry_prompt(last_raw, last_err)

            result = await built.step(prompt, context or {})
            last_raw = result.get("result", "")
            total_tokens += result.get("tokens", 0)
            total_cost += result.get("cost_usd", 0.0)

            try:
                data = parser.parse(last_raw)
                return StructuredOutputResult(
                    data=data,
                    raw=last_raw,
                    attempts=attempt,
                    schema_name=parser.schema_name,
                    tokens=total_tokens,
                    cost_usd=total_cost,
                )
            except Exception as exc:
                last_err = str(exc)

        raise StructuredOutputError(
            f"Failed to produce valid {parser.schema_name} after {max_retries} attempts. "
            f"Last error: {last_err}",
            last_raw=last_raw,
            attempts=max_retries,
        )

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
        import time as _time
        _t0 = _time.monotonic()
        agent = self._build()
        result = await agent.step(task, context or {})
        _dur_ms = (_time.monotonic() - _t0) * 1000
        try:
            from meshflow.observability.genai import record_agent_step, is_enabled
            if is_enabled():
                record_agent_step(
                    agent_name=self.name,
                    role=str(self.role.value if isinstance(self.role, AgentRole) else self.role),
                    model=self._resolve_model(),
                    tokens_in=result.get("tokens", 0) // 2,
                    tokens_out=result.get("tokens", 0) - result.get("tokens", 0) // 2,
                    cost_usd=result.get("cost_usd", 0.0),
                    confidence=result.get("stated_confidence", 1.0),
                    blocked=result.get("blocked", False),
                    run_id=str(context.get("run_id", "") if context else ""),
                )
        except Exception:
            pass
        return result

    async def run_multimodal(
        self,
        task: str,
        inputs: list[Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run this agent on a task that includes multi-modal content.

        Parameters
        ----------
        task:    Text prompt / instruction for the agent.
        inputs:  List of :class:`~meshflow.multimodal.ImageInput`,
                 :class:`~meshflow.multimodal.DocumentInput`, or
                 :class:`~meshflow.multimodal.AudioInput` objects.
        context: Optional extra context forwarded to the agent step.

        Returns the same result dict as :meth:`run`.
        """
        from meshflow.multimodal.inputs import build_multimodal_message

        built = self._build()
        blocks = build_multimodal_message(task, inputs)
        messages: list[dict[str, Any]] = [{"role": "user", "content": blocks}]

        text, tokens, cost = await built.think(messages)
        return {
            "result": text,
            "agent_name": self.name,
            "role": str(self.role.value if isinstance(self.role, AgentRole) else self.role),
            "tokens": tokens,
            "cost_usd": cost,
            "multimodal_inputs": len(inputs),
            "blocked": False,
        }

    async def run_with_handoffs(
        self,
        task: str,
        context: dict[str, Any] | None = None,
        *,
        config: Any = None,
    ) -> Any:
        """Run this agent with peer-to-peer handoff support.

        If this agent's response contains ``TRANSFER_TO:<name>``, control
        transfers to the matching agent in ``self.handoffs``.

        Returns a :class:`~meshflow.agents.handoff.HandoffResult`.
        """
        from meshflow.agents.handoff import run_with_handoffs as _run
        return await _run(self, task, context, config=config)

    async def run_with_healing(
        self,
        task: str,
        context: dict[str, Any] | None = None,
        *,
        policy: Any = None,
    ) -> Any:
        """Run this agent with automatic self-healing on failure or low confidence.

        Uses ``self.healing`` policy by default; *policy* overrides it per-call.

        Returns a :class:`~meshflow.agents.healing.HealingResult` whose
        ``.to_dict()`` includes ``healed``, ``healing_attempts``, and
        ``healing_strategies_tried`` in addition to all normal result fields.
        """
        from meshflow.agents.healing import run_with_healing as _run
        return await _run(self, task, context, policy=policy or self.healing)

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
