"""IBM watsonx.ai ↔ MeshFlow integration.

Bridges IBM watsonx.ai agents and tools into the MeshFlow governance layer.

Usage:
    from meshflow.integrations.ibm import agent_from_watsonx, tool_from_watsonx_function

    # Wrap an IBM watsonx.ai model as a governed MeshFlow Agent
    agent = agent_from_watsonx(
        api_key="...",
        project_id="...",
        model_id="ibm/granite-34b-code-instruct",
        name="ibm_coder",
        role="executor",
    )

    # Import a watsonx function tool
    tool = tool_from_watsonx_function(wx_function, name="query_db")
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from meshflow.core.schemas import RiskTier
from meshflow.tools.registry import Tool


def agent_from_watsonx(
    api_key: str,
    project_id: str,
    model_id: str = "ibm/granite-34b-code-instruct",
    name: str = "watsonx_agent",
    role: str = "executor",
    policy: Any = None,
    region: str = "us-south",
) -> Any:
    """Wrap an IBM watsonx.ai model as a governed MeshFlow Agent.

    Requires ibm-watsonx-ai: pip install ibm-watsonx-ai

    Args:
        api_key:    IBM Cloud API key
        project_id: watsonx.ai project ID
        model_id:   Model ID (e.g. "ibm/granite-34b-code-instruct")
        name:       Agent name in MeshFlow
        role:       Agent role (planner/researcher/executor/critic)
        policy:     MeshFlow policy mode or Policy object
        region:     IBM Cloud region
    """
    from meshflow.agents.builder import Agent

    async def _step(task: str, context: dict[str, Any]) -> Any:
        from meshflow.core.node import NodeOutput

        try:
            from ibm_watsonx_ai import Credentials
            from ibm_watsonx_ai.foundation_models import ModelInference

            creds = Credentials(
                url=f"https://{region}.ml.cloud.ibm.com",
                api_key=api_key,
            )
            model = ModelInference(
                model_id=model_id,
                credentials=creds,
                project_id=project_id,
            )
            prompt = f"Task: {task}\nContext: {context}\nResponse:"
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: model.generate_text(prompt=prompt),
            )
            return NodeOutput(
                content=str(result),
                structured={"model": model_id, "region": region},
                confidence=0.8,
            )
        except ImportError:
            return NodeOutput(
                content=f"[watsonx stub] Task received: {task[:100]}",
                structured={"model": model_id, "stub": True},
                confidence=0.5,
            )

    from meshflow.core.node import MeshNode

    node = MeshNode.from_callable(
        name,
        _step,
        risk=RiskTier.EXTERNAL_IO,
        capabilities=["watsonx", "ibm", role],
    )

    a = Agent(name=name, role=role, policy=policy)
    a._prebuilt_node = node
    return a


def tool_from_watsonx_function(
    fn: Any,
    name: str | None = None,
    description: str = "",
    risk: RiskTier = RiskTier.EXTERNAL_IO,
) -> Tool:
    """Wrap an IBM watsonx function tool as a MeshFlow Tool.

    Works with plain Python callables registered as watsonx tools.
    """
    tool_name = str(name or getattr(fn, "__name__", "watsonx_tool"))
    tool_desc = str(description or (fn.__doc__ or "").strip().split("\n")[0] or tool_name)

    async def _call(**kwargs: Any) -> Any:
        if inspect.iscoroutinefunction(fn):
            return await fn(**kwargs)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: fn(**kwargs))

    return Tool(
        name=tool_name,
        description=tool_desc,
        fn=_call,
        risk=risk,
        tags=["watsonx", "ibm"],
    )
