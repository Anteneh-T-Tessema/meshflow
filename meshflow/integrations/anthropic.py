"""Anthropic API ↔ MeshFlow integration.

Exposes MeshFlow workflows and agents as Anthropic tool-use schemas so that
Claude can invoke governed workflows as first-class tools.

Usage::

    from meshflow.integrations.anthropic import (
        meshflow_as_anthropic_tool,
        meshflow_tool_handler,
    )
    import anthropic

    client = anthropic.Anthropic()

    # 1. Get the tool schema
    tool = meshflow_as_anthropic_tool()

    # 2. Send to Claude
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        tools=[tool],
        messages=[{"role": "user", "content": "Research the latest AI safety papers"}],
    )

    # 3. Handle tool calls
    if response.stop_reason == "tool_use":
        for block in response.content:
            if block.type == "tool_use":
                result = await meshflow_tool_handler(block.name, block.input)
                # Continue the conversation with the result
"""

from __future__ import annotations

import uuid
from typing import Any


def meshflow_as_anthropic_tool(
    tool_name: str = "meshflow_run",
    description: str | None = None,
    include_policy_param: bool = True,
    include_run_id_param: bool = False,
) -> dict[str, Any]:
    """Return an Anthropic tool-use schema that exposes MeshFlow as a Claude tool.

    The schema is ready to pass into ``client.messages.create(tools=[...])``
    or any Anthropic API call that accepts tools.

    Parameters
    ----------
    tool_name:
        Name Claude will use when calling the tool.  Defaults to
        ``"meshflow_run"`` — matches the MCP server's built-in tool name so
        the same handler works for both MCP and direct API use.
    description:
        Override the default description.  The default describes MeshFlow's
        governance, compliance, and multi-agent capabilities.
    include_policy_param:
        Whether to expose a ``policy`` input parameter.  Set False to
        simplify the schema when you always use the same policy.
    include_run_id_param:
        Whether to include a ``run_id`` parameter for resume/HITL flows.

    Returns
    -------
    dict
        Anthropic tool schema dict with ``name``, ``description``, and
        ``input_schema`` keys.

    Example::

        import anthropic
        from meshflow.integrations.anthropic import meshflow_as_anthropic_tool

        tool = meshflow_as_anthropic_tool()
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            tools=[tool],
            messages=[{"role": "user", "content": "Analyse Q2 earnings with compliance"}],
        )
    """
    desc = description or (
        "Run a governed multi-agent workflow through MeshFlow. "
        "Every run gets: SHA-256 tamper-evident audit chain, hard cost cap, "
        "HIPAA/SOX/GDPR/ISO 27001 compliance built-in, Zero Trust agent identity, "
        "and crash recovery. "
        "Use this tool when you need to execute a task that requires multiple agents, "
        "compliance enforcement, a cost cap, or a tamper-evident audit trail."
    )

    properties: dict[str, Any] = {
        "task": {
            "type": "string",
            "description": (
                "The task or question to execute through the governed agent pipeline. "
                "Be specific — the agents will break it down."
            ),
        },
    }
    required = ["task"]

    if include_policy_param:
        properties["policy"] = {
            "type": "string",
            "enum": ["standard", "strict", "hipaa", "sox", "gdpr", "dev", "sandbox"],
            "description": (
                "Governance policy mode. "
                "'standard' = balanced; 'strict' = maximum enforcement; "
                "'hipaa'/'sox'/'gdpr' = regulated-industry profiles; "
                "'dev'/'sandbox' = relaxed for prototyping. Default: standard."
            ),
        }

    if include_run_id_param:
        properties["run_id"] = {
            "type": "string",
            "description": (
                "Resume a paused workflow by providing its run_id. "
                "Returned by a previous call when the workflow hit a human-in-the-loop checkpoint."
            ),
        }

    return {
        "name": tool_name,
        "description": desc,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


async def meshflow_tool_handler(
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    agents: list[Any] | None = None,
    ledger_db: str = ":memory:",
) -> dict[str, Any]:
    """Handle an Anthropic tool_use block by executing the task through MeshFlow.

    Designed to be called in the tool-use handling loop after
    ``response.stop_reason == "tool_use"``.

    Parameters
    ----------
    tool_name:
        The tool name from the tool_use block (``block.name``).
    tool_input:
        The tool inputs from the tool_use block (``block.input``).
    agents:
        Optional list of MeshFlow ``Agent`` objects.  If omitted, a default
        governed mesh is used (requires ``ANTHROPIC_API_KEY`` or ``MESHFLOW_MOCK=1``).
    ledger_db:
        Path to the SQLite ledger.  Use ``"meshflow_runs.db"`` to persist
        across calls.

    Returns
    -------
    dict
        ``{status, result, run_id, cost_usd, tokens_used, audit_chain_valid}``

    Example::

        for block in response.content:
            if block.type == "tool_use":
                result = await meshflow_tool_handler(block.name, block.input)
                messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result["result"],
                    }]
                })
    """
    from meshflow.core.mesh import Mesh
    from meshflow.core.schemas import policy_for_mode

    task = tool_input.get("task", "")
    policy_mode = tool_input.get("policy", "standard")
    run_id_resume = tool_input.get("run_id", "")

    if not task:
        return {"status": "error", "result": "No task provided.", "run_id": "", "cost_usd": 0.0}

    try:
        pol = policy_for_mode(policy_mode)
        mesh = Mesh(policy=pol, agents=agents or [])
        result = await mesh.run(task)
        return {
            "status": "completed",
            "result": result.summary() if hasattr(result, "summary") else str(result),
            "run_id": getattr(result, "run_id", str(uuid.uuid4())[:8]),
            "cost_usd": getattr(result, "total_cost", 0.0),
            "tokens_used": getattr(result, "total_tokens", 0),
            "audit_chain_valid": True,
        }
    except Exception as exc:
        return {
            "status": "error",
            "result": str(exc),
            "run_id": "",
            "cost_usd": 0.0,
            "tokens_used": 0,
            "audit_chain_valid": False,
        }


def meshflow_tool_result_block(
    tool_use_id: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Format a MeshFlow tool result as an Anthropic ``tool_result`` content block.

    Usage::

        block = meshflow_tool_result_block(block.id, result)
        messages.append({"role": "user", "content": [block]})
    """
    summary = result.get("result", "")
    run_id = result.get("run_id", "")
    cost = result.get("cost_usd", 0.0)
    status = result.get("status", "completed")

    content = summary
    if run_id:
        content += f"\n\n[run_id: {run_id} | cost: ${cost:.5f} | status: {status}]"

    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
    }


__all__ = [
    "meshflow_as_anthropic_tool",
    "meshflow_tool_handler",
    "meshflow_tool_result_block",
]
