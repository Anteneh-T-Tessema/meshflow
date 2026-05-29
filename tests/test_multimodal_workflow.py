"""Sprint 69 — Multi-modal workflow node tests."""

from __future__ import annotations

import asyncio
import pytest
from meshflow.core.node import MeshNode, NodeInput, NodeOutput, NodeKind


# ── NodeInput.attachments ─────────────────────────────────────────────────────


def test_node_input_attachments_default_empty():
    inp = NodeInput(task="hello")
    assert inp.attachments == []


def test_node_input_attachments_set():
    block = {"type": "image", "source": {"type": "url", "url": "https://example.com/img.png"}}
    inp = NodeInput(task="analyze this", attachments=[block])
    assert len(inp.attachments) == 1
    assert inp.attachments[0]["type"] == "image"


# ── from_native passes attachments via __attachments__ context ────────────────


@pytest.mark.asyncio
async def test_native_runner_injects_attachments():
    received_context = {}

    class FakeAgent:
        async def step(self, task, context):
            received_context.update(context)
            return {"result": "ok", "tokens": 0, "cost_usd": 0.0, "stated_confidence": 0.9}

    node = MeshNode.from_native("n1", FakeAgent())
    attachment = {"type": "image", "source": {"type": "base64", "data": "abc"}}
    inp = NodeInput(task="describe image", attachments=[attachment])
    await node.run(inp)

    assert "__attachments__" in received_context
    assert received_context["__attachments__"][0]["type"] == "image"


@pytest.mark.asyncio
async def test_native_runner_no_attachments_no_key():
    received_context = {}

    class FakeAgent:
        async def step(self, task, context):
            received_context.update(context)
            return {"result": "ok", "tokens": 0, "cost_usd": 0.0, "stated_confidence": 0.9}

    node = MeshNode.from_native("n2", FakeAgent())
    await node.run(NodeInput(task="plain task"))
    assert "__attachments__" not in received_context


# ── YAML loader stores attachments in node.metadata ──────────────────────────


def test_yaml_loader_stores_attachments():
    from meshflow.core.workflow import WorkflowDefinition
    import tempfile, os, yaml as _yaml

    attachment_block = {"type": "image", "source": {"type": "url", "url": "https://example.com/x.png"}}
    wf_data = {
        "name": "mm_wf",
        "nodes": {
            "vision": {
                "kind": "native",
                "role": "executor",
                "attachments": [attachment_block],
            },
        },
        "edges": [],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        _yaml.safe_dump(wf_data, f)
        path = f.name

    try:
        wf = WorkflowDefinition.from_yaml(path)
        node = wf._nodes["vision"]
        assert "attachments" in node.metadata
        assert node.metadata["attachments"][0]["type"] == "image"
    finally:
        os.unlink(path)


def test_yaml_loader_no_attachments_no_key():
    from meshflow.core.workflow import WorkflowDefinition
    import tempfile, os, yaml as _yaml

    wf_data = {
        "name": "plain_wf",
        "nodes": {"plain": {"kind": "native", "role": "executor"}},
        "edges": [],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        _yaml.safe_dump(wf_data, f)
        path = f.name

    try:
        wf = WorkflowDefinition.from_yaml(path)
        node = wf._nodes["plain"]
        assert node.metadata.get("attachments", []) == []
    finally:
        os.unlink(path)


# ── _BuiltAgent.step multi-part message construction ─────────────────────────


@pytest.mark.asyncio
async def test_built_agent_step_multimodal_message():
    """When __attachments__ is in context, step() builds a list content block."""
    from unittest.mock import AsyncMock, MagicMock
    from meshflow.agents.builder import _BuiltAgent
    from meshflow.agents.base import AgentConfig
    from meshflow.core.schemas import AgentRole, Policy

    cfg = AgentConfig(agent_id="va", role=AgentRole.EXECUTOR, model="test")
    policy = Policy()

    agent = _BuiltAgent(cfg, policy, tools=[], memory_enabled=False)

    captured_messages = []

    async def fake_think(messages, system=None, **kw):
        captured_messages.extend(messages)
        return ("result text", 10, 0.001)

    agent.think = fake_think  # type: ignore[assignment]

    att = {"type": "image", "source": {"type": "url", "url": "https://x.com/img.png"}}
    await agent.step("describe this", {"__attachments__": [att]})

    assert captured_messages, "No messages captured"
    content = captured_messages[0]["content"]
    assert isinstance(content, list), "Expected multi-part content list"
    assert content[0]["type"] == "image"
    text_block = next(b for b in content if isinstance(b, dict) and b.get("type") == "text")
    assert "describe this" in text_block["text"]
