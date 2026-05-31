"""Mock nodes for workflow unit testing.

Usage::

    from meshflow.testing import MockNode, EchoNode, FailNode

    # Echo: returns whatever task string is passed in
    echo = EchoNode("step1")

    # Fixed response
    mock = MockNode("step2", response="fixed output", tokens=50, cost=0.001)

    # Always blocks
    blocked = FailNode("validator", reason="PII detected")

    # Counter: counts how many times it was called
    counter = CounterNode("loop_step")
    wf.add_node(counter)
    ...
    assert counter.call_count == 3
"""

from __future__ import annotations

from typing import Any

from meshflow.core.node import MeshNode, NodeInput, NodeKind, NodeOutput
from meshflow.core.schemas import RiskTier


class MockNode(MeshNode):
    """A MeshNode that returns a fixed response.  Tracks call history.

    Parameters
    ----------
    node_id:    Node identifier.
    response:   Fixed ``content`` string returned for every call.
    tokens:     Simulated token count.
    cost:       Simulated cost in USD.
    confidence: Simulated confidence score (0–1).
    structured: Optional structured output dict.
    """

    def __init__(
        self,
        node_id: str,
        response: str = "mock output",
        tokens: int = 10,
        cost: float = 0.001,
        confidence: float = 0.9,
        structured: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            id=node_id,
            kind=NodeKind.PYTHON,
            risk_profile=RiskTier.READ_ONLY,
            capabilities=["mock"],
        )
        self._response = response
        self._tokens = tokens
        self._cost = cost
        self._confidence = confidence
        self._structured = structured or {}
        self.call_count: int = 0
        self.call_history: list[NodeInput] = []

    async def run(self, node_input: NodeInput) -> NodeOutput:
        self.call_count += 1
        self.call_history.append(node_input)
        return NodeOutput(
            content=self._response,
            tokens_used=self._tokens,
            confidence=self._confidence,
            structured=self._structured,
            metadata={"mock": True, "call_count": self.call_count},
        )

    def reset(self) -> None:
        self.call_count = 0
        self.call_history.clear()


class EchoNode(MockNode):
    """A MeshNode that echoes the input task as its output."""

    def __init__(self, node_id: str, tokens: int = 5, cost: float = 0.0) -> None:
        super().__init__(node_id, response="", tokens=tokens, cost=cost)

    async def run(self, node_input: NodeInput) -> NodeOutput:
        self.call_count += 1
        self.call_history.append(node_input)
        return NodeOutput(
            content=node_input.task,
            tokens_used=self._tokens,
            confidence=0.95,
            metadata={"echo": True},
        )


class FailNode(MeshNode):
    """A MeshNode that always returns a blocked/failed outcome.

    Useful for testing error-handling paths in workflows.
    """

    def __init__(
        self,
        node_id: str,
        reason: str = "mock failure",
        confidence: float = 0.0,
    ) -> None:
        super().__init__(
            id=node_id,
            kind=NodeKind.PYTHON,
            risk_profile=RiskTier.READ_ONLY,
            capabilities=["mock_fail"],
        )
        self._reason = reason
        self._confidence = confidence
        self.call_count: int = 0

    async def run(self, node_input: NodeInput) -> NodeOutput:
        self.call_count += 1
        return NodeOutput(
            content=f"[BLOCKED] {self._reason}",
            confidence=self._confidence,
            metadata={"blocked": True, "reason": self._reason},
        )


class CounterNode(MockNode):
    """A MockNode that also tracks token and cost accumulation per call."""

    def __init__(
        self,
        node_id: str,
        response: str = "counted",
        tokens_per_call: int = 10,
        cost_per_call: float = 0.001,
    ) -> None:
        super().__init__(node_id, response=response, tokens=tokens_per_call, cost=cost_per_call)
        self.total_tokens: int = 0
        self.total_cost: float = 0.0

    async def run(self, node_input: NodeInput) -> NodeOutput:
        result = await super().run(node_input)
        self.total_tokens += self._tokens
        self.total_cost += self._cost
        return result


class DelayNode(MockNode):
    """A MockNode that sleeps for ``delay_s`` seconds before returning.

    Useful for testing timeout and latency-related behaviour.
    """

    def __init__(self, node_id: str, delay_s: float = 0.1, **kwargs: Any) -> None:
        super().__init__(node_id, **kwargs)
        self._delay = delay_s

    async def run(self, node_input: NodeInput) -> NodeOutput:
        import asyncio
        await asyncio.sleep(self._delay)
        return await super().run(node_input)


__all__ = ["MockNode", "EchoNode", "FailNode", "CounterNode", "DelayNode"]
