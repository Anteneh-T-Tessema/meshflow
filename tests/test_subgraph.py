"""Sprint 67 — Subgraph / nested graph composition tests."""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from meshflow.core.node import MeshNode, NodeInput, NodeOutput, NodeKind
from meshflow.core.subgraph import SubgraphNode, subgraph_from_yaml
from meshflow.core.schemas import RiskTier


class _FakeWorkflowResult:
    def __init__(self, output: str = "inner result"):
        self.output = output
        self.completed = True
        self.steps = [MagicMock()]
        self.total_cost_usd = 0.01
        self.total_tokens = 100
        self.blocked_nodes: list = []
        self.paused_nodes: list = []


class _FakeWorkflow:
    def __init__(self, name: str = "inner_wf", output: str = "inner result"):
        self.name = name
        self.policy = MagicMock()
        self._output = output

    async def run(self, task, runtime, context=None):
        return _FakeWorkflowResult(self._output)


# ── SubgraphNode.create ───────────────────────────────────────────────────────


def test_subgraph_node_kind():
    inner = _FakeWorkflow()
    node = SubgraphNode.create("sub", inner)
    assert node.kind == NodeKind.SUBGRAPH
    assert node.id == "sub"


@pytest.mark.asyncio
async def test_subgraph_returns_inner_output():
    inner = _FakeWorkflow(output="research complete")
    node = SubgraphNode.create("research", inner)
    result = await node.run(NodeInput(task="do research", context={}))
    assert result.content == "research complete"
    assert result.structured["subgraph_name"] == "inner_wf"
    assert result.structured["subgraph_completed"] is True


@pytest.mark.asyncio
async def test_subgraph_max_depth_guard():
    inner = _FakeWorkflow()
    node = SubgraphNode.create("sub", inner, max_depth=2)
    # Simulate nesting at depth 2 — should be blocked
    inp = NodeInput(task="t", context={"_subgraph_depth": 2})
    result = await node.run(inp)
    assert result.confidence == 0.0
    assert "max depth" in result.content


@pytest.mark.asyncio
async def test_subgraph_depth_increments():
    results = []

    class TrackingWorkflow(_FakeWorkflow):
        async def run(self, task, runtime, context=None):
            results.append(context.get("_subgraph_depth", 0))
            return _FakeWorkflowResult()

    inner = TrackingWorkflow()
    node = SubgraphNode.create("sub", inner)
    await node.run(NodeInput(task="t", context={}))
    assert results == [1]


# ── YAML subgraph kind ────────────────────────────────────────────────────────


def test_workflow_yaml_registers_subgraph_kind():
    from meshflow.core.workflow import WorkflowDefinition

    inner = _FakeWorkflow("inner")
    registry = {"inner_wf": inner}

    import tempfile, os, yaml as _yaml
    wf_data = {
        "name": "outer",
        "nodes": {
            "sub_node": {"kind": "subgraph", "workflow": "inner_wf"},
            "writer": {"kind": "native", "role": "executor"},
        },
        "edges": ["sub_node -> writer"],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        _yaml.safe_dump(wf_data, f)
        path = f.name

    try:
        wf = WorkflowDefinition.from_yaml(path, registry)
        assert "sub_node" in wf._nodes
        assert wf._nodes["sub_node"].kind == NodeKind.SUBGRAPH
    finally:
        os.unlink(path)
