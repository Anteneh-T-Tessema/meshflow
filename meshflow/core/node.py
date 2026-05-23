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


@dataclass
class NodeInput:
    task: str
    context: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class NodeOutput:
    content: str
    structured: dict[str, Any] = field(default_factory=dict)
    tokens_used: int = 0
    model: str = ""
    confidence: float = 0.8
    metadata: dict[str, Any] = field(default_factory=dict)


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
            result = await agent_instance.step(inp.task, inp.context)
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
    def from_crewai(cls, node_id: str, crew: Any) -> "MeshNode":
        """Wrap a CrewAI Crew as a MeshNode."""

        async def runner(inp: NodeInput) -> NodeOutput:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, lambda: crew.kickoff(inputs={"task": inp.task})
            )
            return NodeOutput(content=str(result))

        return cls(
            id=node_id,
            kind=NodeKind.CREWAI,
            risk_profile=RiskTier.INTERNAL,
            capabilities=["role_task_execution", "crew_coordination"],
            _runner=runner,
        )

    @classmethod
    def from_langgraph(cls, node_id: str, graph: Any) -> "MeshNode":
        """Wrap a LangGraph compiled StateGraph as a MeshNode."""

        async def runner(inp: NodeInput) -> NodeOutput:
            result = await graph.ainvoke({"messages": [{"role": "user", "content": inp.task}]})
            msgs = result.get("messages", [])
            content = msgs[-1].get("content", str(result)) if msgs else str(result)
            return NodeOutput(content=content)

        return cls(
            id=node_id,
            kind=NodeKind.LANGGRAPH,
            risk_profile=RiskTier.INTERNAL,
            capabilities=["graph_execution", "stateful_reasoning"],
            _runner=runner,
        )

    @classmethod
    def from_autogen(cls, node_id: str, agent: Any, manager: Any = None) -> "MeshNode":
        """Wrap an AutoGen ConversableAgent (+ optional GroupChatManager) as a MeshNode."""

        async def runner(inp: NodeInput) -> NodeOutput:
            loop = asyncio.get_event_loop()
            if manager:
                result = await loop.run_in_executor(None, lambda: manager.run(message=inp.task))
            else:
                result = await loop.run_in_executor(
                    None,
                    lambda: agent.generate_reply(messages=[{"content": inp.task, "role": "user"}]),
                )
            return NodeOutput(content=str(result))

        return cls(
            id=node_id,
            kind=NodeKind.AUTOGEN,
            risk_profile=RiskTier.INTERNAL,
            capabilities=["conversational_agents", "group_chat"],
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
