"""Universal MeshNode — every agent, crew, graph, callable, or service becomes one.

Every external framework object (LangGraph graph, CrewAI crew, AutoGen agent,
HTTP service, Python callable, human approver) is wrapped as a MeshNode before
being submitted to the StepRuntime governance kernel. This means one consistent
governance path regardless of origin.

Node kinds:
  native    — MeshFlow BaseAgent
  langgraph — LangGraph compiled StateGraph
  crewai    — CrewAI Crew or Flow
  autogen   — AutoGen ConversableAgent / GroupChatManager
  mcp       — MCP tool call routed through MCPGateway
  human     — Human approval / HITL input node
  http      — External HTTP service (JSON in / JSON out)
  python    — Any Python callable (sync or async)
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, cast

from meshflow.core.schemas import RiskTier


class NodeKind(str, Enum):
    NATIVE = "native"
    LANGGRAPH = "langgraph"
    CREWAI = "crewai"
    AUTOGEN = "autogen"
    MCP = "mcp"
    HUMAN = "human"
    HTTP = "http"
    PYTHON = "python"
    SUBGRAPH = "subgraph"


@dataclass
class NodeInput:
    task: str
    context: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    """Pre-serialised multi-modal content blocks (Anthropic API format).

    When non-empty the runner constructs a multi-part message with these
    blocks prepended to the text prompt.  Populate from
    ``MultiModalInput.to_message_block()`` or the workflow YAML
    ``attachments:`` field.
    """


@dataclass
class NodeOutput:
    content: str
    structured: dict[str, Any] = field(default_factory=dict)
    tokens_used: int = 0
    model: str = ""
    confidence: float = 0.8
    metadata: dict[str, Any] = field(default_factory=dict)


# ── ModelRouter helpers ───────────────────────────────────────────────────────

def _router_model(router: Any, task: str) -> str:
    """Call a ModelRouter and return the chosen model string."""
    try:
        result = router.route(task)
        return str(result.model or "")
    except Exception:
        return ""


def _patch_crewai_agents(crew: Any, model: str) -> list[Any]:
    """Patch every CrewAI agent's llm to use *model*; return originals."""
    if not model:
        return []
    originals: list[Any] = []
    agents = getattr(crew, "agents", []) or []
    for agent in agents:
        originals.append(getattr(agent, "llm", None))
        try:
            from meshflow.agents.providers import LLM
            agent.llm = LLM(model)
        except Exception:
            originals[-1] = None  # mark as unpatchable
    return originals


def _restore_crewai_agents(crew: Any, originals: list[Any]) -> None:
    """Restore crew agents' llm attributes from *originals*."""
    agents = getattr(crew, "agents", []) or []
    for agent, original in zip(agents, originals):
        if original is not None:
            try:
                agent.llm = original
            except Exception:
                pass


def _patch_autogen_agent(agent: Any, model: str) -> Any:
    """Patch an AutoGen agent's llm_config with *model*; return original config."""
    if not model:
        return None
    original = getattr(agent, "llm_config", None)
    try:
        import copy
        new_cfg = copy.deepcopy(original) if original else {}
        config_list = new_cfg.get("config_list", [])
        if config_list:
            for entry in config_list:
                entry["model"] = model
        else:
            new_cfg["config_list"] = [{"model": model}]
        agent.llm_config = new_cfg
    except Exception:
        pass
    return original


def _restore_autogen_agent(agent: Any, original_cfg: Any) -> None:
    """Restore an AutoGen agent's llm_config."""
    if original_cfg is not None:
        try:
            agent.llm_config = original_cfg
        except Exception:
            pass


@dataclass
class MeshNode:
    """Universal node that StepRuntime governs.

    Do not instantiate directly — use the factory classmethods so the
    correct kind, risk_profile, and capabilities are set automatically.
    """

    id: str
    kind: NodeKind
    risk_profile: RiskTier = RiskTier.READ_ONLY
    capabilities: list[str] = field(default_factory=list)
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    _runner: Callable[[NodeInput], Awaitable[NodeOutput]] | None = field(default=None, repr=False)

    async def run(self, node_input: NodeInput) -> NodeOutput:
        if self._runner is None:
            raise NotImplementedError(f"Node '{self.id}' has no runner configured")
        return await self._runner(node_input)

    # ── Factory methods ───────────────────────────────────────────────────────

    @classmethod
    def from_native(cls, agent_id: str, agent_instance: Any) -> "MeshNode":
        """Wrap a MeshFlow BaseAgent as a MeshNode."""

        async def runner(inp: NodeInput) -> NodeOutput:
            ctx = inp.context
            if inp.attachments:
                ctx = {**inp.context, "__attachments__": inp.attachments}
            result = await agent_instance.step(inp.task, ctx)
            content = str(
                result.get("execution_result")
                or result.get("research")
                or result.get("plan")
                or result.get("output")
                or ""
            )
            return NodeOutput(
                content=content,
                structured=result,
                tokens_used=result.get("tokens", 0),
                model=result.get("model", ""),
                confidence=float(result.get("stated_confidence", 0.8)),
            )

        return cls(
            id=agent_id,
            kind=NodeKind.NATIVE,
            risk_profile=RiskTier.READ_ONLY,
            capabilities=["text_generation", "reasoning"],
            _runner=runner,
        )

    @classmethod
    def from_callable(
        cls,
        node_id: str,
        fn: Callable[..., Any],
        risk: RiskTier = RiskTier.READ_ONLY,
        capabilities: list[str] | None = None,
    ) -> "MeshNode":
        """Wrap any Python callable (sync or async) as a MeshNode."""

        async def runner(inp: NodeInput) -> NodeOutput:
            if inspect.iscoroutinefunction(fn):
                result = await fn(inp.task, inp.context)
            else:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, fn, inp.task, inp.context)
            if isinstance(result, NodeOutput):
                return result
            if isinstance(result, str):
                return NodeOutput(content=result)
            if isinstance(result, dict):
                return NodeOutput(content=result.get("output", str(result)), structured=result)
            return NodeOutput(content=str(result))

        return cls(
            id=node_id,
            kind=NodeKind.PYTHON,
            risk_profile=risk,
            capabilities=capabilities or ["compute"],
            _runner=runner,
        )

    @classmethod
    def from_crewai(
        cls,
        node_id: str,
        crew: Any,
        model_router: Any = None,
    ) -> "MeshNode":
        """Wrap a CrewAI Crew as a MeshNode.

        Parameters
        ----------
        model_router:
            Optional MeshFlow ``ModelRouter``.  When provided, the router
            selects the model for this task and patches every crew agent's
            ``llm`` attribute before ``kickoff()`` is called.  The original
            ``llm`` is restored afterwards so the crew object is not mutated
            permanently.
        """

        async def runner(inp: NodeInput) -> NodeOutput:
            loop = asyncio.get_event_loop()

            guardian = inp.context.get("_guardian")
            ledger = inp.context.get("_ledger")
            if guardian:
                from meshflow.security.guardrail_engine import CrewAIGuardCallback
                agents = getattr(crew, "agents", []) or []
                for agent in agents:
                    llm = getattr(agent, "llm", None)
                    if llm:
                        if hasattr(llm, "callbacks"):
                            if llm.callbacks is None:
                                llm.callbacks = []
                            if not any(isinstance(cb, CrewAIGuardCallback) for cb in llm.callbacks):
                                llm.callbacks.append(CrewAIGuardCallback(guardian, ledger))

            if model_router is not None:
                # Route task → model, patch crew agents, restore after
                routed_model = _router_model(model_router, inp.task)
                originals = _patch_crewai_agents(crew, routed_model)
                try:
                    result = await loop.run_in_executor(
                        None, lambda: crew.kickoff(inputs={"task": inp.task})
                    )
                finally:
                    _restore_crewai_agents(crew, originals)
            else:
                result = await loop.run_in_executor(
                    None, lambda: crew.kickoff(inputs={"task": inp.task})
                )
            return NodeOutput(content=str(result))

        return cls(
            id=node_id,
            kind=NodeKind.CREWAI,
            risk_profile=RiskTier.INTERNAL,
            capabilities=["role_task_execution", "crew_coordination"],
            metadata={"model_router": model_router is not None},
            _runner=runner,
        )

    @classmethod
    def from_langgraph(
        cls,
        node_id: str,
        graph: Any,
        model_router: Any = None,
        graph_factory: "Callable[[str], Any] | None" = None,
    ) -> "MeshNode":
        """Wrap a LangGraph compiled StateGraph as a MeshNode.

        Parameters
        ----------
        model_router:
            Optional MeshFlow ``ModelRouter``.  Two modes:

            1. **Configurable graph** (preferred): if the graph was compiled
               with ``configurable`` support, the routed model is passed via
               ``config={"configurable": {"model": "<model>"}}`` at invocation
               time — no graph recompilation needed.

            2. **Graph factory** (for non-configurable graphs): pass
               ``graph_factory=lambda model: build_graph(model)`` alongside
               ``model_router``.  The router selects the model, the factory
               rebuilds the graph, and the rebuilt graph handles the task.
               Results are cached per model so the factory isn't called on
               every step.
        graph_factory:
            Callable ``(model: str) -> compiled_graph``.  Required when using
            ``model_router`` with non-configurable LangGraph graphs.
        """

        _graph_cache: dict[str, Any] = {}

        async def runner(inp: NodeInput) -> NodeOutput:
            active_graph = graph

            guardian = inp.context.get("_guardian")
            ledger = inp.context.get("_ledger")
            callbacks = []
            if guardian:
                from meshflow.security.guardrail_engine import LangGraphGuardCallback
                callbacks.append(LangGraphGuardCallback(guardian, ledger))

            if model_router is not None:
                routed_model = _router_model(model_router, inp.task)
                if graph_factory is not None:
                    # Factory mode: rebuild graph for this model (cached)
                    if routed_model not in _graph_cache:
                        _graph_cache[routed_model] = graph_factory(routed_model)
                    active_graph = _graph_cache[routed_model]
                    result = await active_graph.ainvoke(
                        {"messages": [{"role": "user", "content": inp.task}]},
                        config={"callbacks": callbacks}
                    )
                else:
                    # Configurable mode: pass model via LangGraph config
                    result = await active_graph.ainvoke(
                        {"messages": [{"role": "user", "content": inp.task}]},
                        config={"configurable": {"model": routed_model}, "callbacks": callbacks},
                    )
            else:
                result = await active_graph.ainvoke(
                    {"messages": [{"role": "user", "content": inp.task}]},
                    config={"callbacks": callbacks}
                )

            msgs = result.get("messages", [])
            content = msgs[-1].get("content", str(result)) if msgs else str(result)
            return NodeOutput(content=content)

        return cls(
            id=node_id,
            kind=NodeKind.LANGGRAPH,
            risk_profile=RiskTier.INTERNAL,
            capabilities=["graph_execution", "stateful_reasoning"],
            metadata={"model_router": model_router is not None},
            _runner=runner,
        )

    @classmethod
    def from_autogen(
        cls,
        node_id: str,
        agent: Any,
        manager: Any = None,
        model_router: Any = None,
    ) -> "MeshNode":
        """Wrap an AutoGen ConversableAgent (+ optional GroupChatManager) as a MeshNode.

        Parameters
        ----------
        model_router:
            Optional MeshFlow ``ModelRouter``.  When provided, the router
            selects a model and patches ``agent.llm_config["config_list"]``
            before each call, restoring the original config afterwards.
        """

        async def runner(inp: NodeInput) -> NodeOutput:
            loop = asyncio.get_event_loop()

            guardian = inp.context.get("_guardian")
            ledger = inp.context.get("_ledger")
            if guardian:
                from meshflow.security.guardrail_engine import _register_autogen_guard
                _register_autogen_guard(agent, guardian, ledger)
                if manager:
                    _register_autogen_guard(manager, guardian, ledger)

            if model_router is not None:
                routed_model = _router_model(model_router, inp.task)
                original_cfg = _patch_autogen_agent(agent, routed_model)
                try:
                    if manager:
                        result = await loop.run_in_executor(
                            None, lambda: manager.run(message=inp.task)
                        )
                    else:
                        result = await loop.run_in_executor(
                            None,
                            lambda: agent.generate_reply(
                                messages=[{"content": inp.task, "role": "user"}]
                            ),
                        )
                finally:
                    _restore_autogen_agent(agent, original_cfg)
            else:
                if manager:
                    result = await loop.run_in_executor(
                        None, lambda: manager.run(message=inp.task)
                    )
                else:
                    result = await loop.run_in_executor(
                        None,
                        lambda: agent.generate_reply(
                            messages=[{"content": inp.task, "role": "user"}]
                        ),
                    )
            return NodeOutput(content=str(result))

        return cls(
            id=node_id,
            kind=NodeKind.AUTOGEN,
            risk_profile=RiskTier.INTERNAL,
            capabilities=["conversational_agents", "group_chat"],
            metadata={"model_router": model_router is not None},
            _runner=runner,
        )

    @classmethod
    def human_approval(
        cls,
        node_id: str,
        prompt_fn: Callable[[str], str] | None = None,
    ) -> "MeshNode":
        """Human-in-the-loop node. Blocks until a human provides input."""

        async def runner(inp: NodeInput) -> NodeOutput:
            if prompt_fn:
                response = prompt_fn(inp.task)
            else:
                print(f"\n[HITL] Node '{node_id}' requires human input:")
                print(f"  Task: {inp.task[:300]}")
                response = input("  Your response: ").strip()
            return NodeOutput(
                content=response,
                metadata={"human_approved": True, "node_id": node_id},
            )

        return cls(
            id=node_id,
            kind=NodeKind.HUMAN,
            risk_profile=RiskTier.READ_ONLY,
            capabilities=["human_judgment", "approval"],
            _runner=runner,
        )

    @classmethod
    def from_http(
        cls,
        node_id: str,
        url: str,
        method: str = "POST",
        headers: dict[str, str] | None = None,
        risk: RiskTier = RiskTier.EXTERNAL_IO,
    ) -> "MeshNode":
        """Wrap an external HTTP service (JSON protocol) as a MeshNode."""
        import json
        import urllib.request

        async def runner(inp: NodeInput) -> NodeOutput:
            payload = json.dumps({"task": inp.task, "context": inp.context}).encode()
            req = urllib.request.Request(
                url,
                data=payload,
                headers={**(headers or {}), "Content-Type": "application/json"},
                method=method,
            )
            loop = asyncio.get_event_loop()

            def _call() -> dict[str, Any]:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return cast(dict[str, Any], json.loads(resp.read()))

            body = await loop.run_in_executor(None, _call)
            return NodeOutput(content=body.get("output", str(body)), structured=body)

        return cls(
            id=node_id,
            kind=NodeKind.HTTP,
            risk_profile=risk,
            capabilities=["external_service", "http"],
            _runner=runner,
        )
