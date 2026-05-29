"""MeshFlow testing utilities — mock nodes, assertion helpers, and fixtures.

Import everything from ``meshflow.testing`` in your test files.

Usage::

    from meshflow.testing import (
        MockNode,
        WorkflowAssertion,
        assert_node_executed,
        assert_cost_within,
        fake_agent,
        make_workflow,
    )
"""

from meshflow.testing.mock_nodes import MockNode, EchoNode, FailNode, CounterNode
from meshflow.testing.assertions import (
    WorkflowAssertion,
    assert_node_executed,
    assert_node_not_executed,
    assert_node_blocked,
    assert_cost_within,
    assert_tokens_within,
    assert_output_contains,
    assert_completed,
)
from meshflow.testing.helpers import fake_agent, make_workflow, make_runtime

__all__ = [
    "MockNode",
    "EchoNode",
    "FailNode",
    "CounterNode",
    "WorkflowAssertion",
    "assert_node_executed",
    "assert_node_not_executed",
    "assert_node_blocked",
    "assert_cost_within",
    "assert_tokens_within",
    "assert_output_contains",
    "assert_completed",
    "fake_agent",
    "make_workflow",
    "make_runtime",
]
