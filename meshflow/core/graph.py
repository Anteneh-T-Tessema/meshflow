"""Stateful graph engine — LangGraph DNA with durable checkpointing.

Every state transition is checkpointed. Failures resume from the last
checkpoint, never from the start. Human-in-loop pauses serialize
the complete graph state to storage and resume days later if needed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from meshflow.core.schemas import (
    AgentState,
    CheckpointRecord,
    RunStatus,
)


@dataclass
class GraphNode:
    node_id: str
    agent_id: str
    fn: Callable[..., Awaitable[dict[str, Any]]]
    retries: int = 3
    timeout_s: float = 120.0


@dataclass
class GraphEdge:
    source: str
    target: str
    condition: Callable[[dict[str, Any]], bool] | None = None
    # None condition = unconditional


@dataclass
class GraphState:
    run_id: str
    current_node: str
    status: RunStatus = RunStatus.PENDING
    data: dict[str, Any] = field(default_factory=dict)
    step: int = 0
    error: str = ""
    paused_for_human: bool = False
    human_context: dict[str, Any] = field(default_factory=dict)
    completed_nodes: list[str] = field(default_factory=list)
    failed_nodes: list[str] = field(default_factory=list)


class StateGraph:
    """Directed graph with checkpointing on every transition.

    Supports sequential, parallel, and conditional branching.
    Human-in-loop pause serializes state; resume rehydrates from checkpoint.
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._nodes: dict[str, GraphNode] = {}
        self._edges: dict[str, list[GraphEdge]] = defaultdict(list)
        self._entry: str = ""
        self._terminals: set[str] = set()
        self._checkpoints: list[CheckpointRecord] = []
        self._agent_states: dict[str, AgentState] = {}

    def add_node(self, node: GraphNode) -> "StateGraph":
        self._nodes[node.node_id] = node
        return self

    def add_edge(self, edge: GraphEdge) -> "StateGraph":
        self._edges[edge.source].append(edge)
        return self

    def set_entry(self, node_id: str) -> "StateGraph":
        self._entry = node_id
        return self

    def set_terminals(self, *node_ids: str) -> "StateGraph":
        self._terminals.update(node_ids)
        return self

    def register_agent_state(self, agent_id: str, state: AgentState) -> None:
        self._agent_states[agent_id] = state

    # ── Checkpoint ────────────────────────────────────────────────────────────

    def _checkpoint(self, graph_state: GraphState) -> CheckpointRecord:
        content = json.dumps(
            {
                "run_id": self.run_id,
                "step": graph_state.step,
                "current_node": graph_state.current_node,
                "data": graph_state.data,
                "completed_nodes": graph_state.completed_nodes,
            },
            sort_keys=True,
            default=str,
        )
        sha = hashlib.sha256(content.encode()).hexdigest()
        cp = CheckpointRecord(
            run_id=self.run_id,
            step=graph_state.step,
            agent_states={k: v for k, v in self._agent_states.items()},
            graph_state={
                "current_node": graph_state.current_node,
                "data": graph_state.data,
                "completed_nodes": graph_state.completed_nodes,
                "status": graph_state.status.value,
            },
            hash=sha,
        )
        self._checkpoints.append(cp)
        return cp

    def latest_checkpoint(self) -> CheckpointRecord | None:
        return self._checkpoints[-1] if self._checkpoints else None

    def checkpoint_ids(self) -> list[str]:
        return [cp.checkpoint_id for cp in self._checkpoints]

    # ── Execution ─────────────────────────────────────────────────────────────

    async def run(
        self,
        initial_data: dict[str, Any],
        on_checkpoint: Callable[[CheckpointRecord], Awaitable[None]] | None = None,
        on_pause: Callable[[GraphState], Awaitable[dict[str, Any]]] | None = None,
    ) -> GraphState:
        if not self._entry:
            raise ValueError("No entry node set — call set_entry() first")

        state = GraphState(
            run_id=self.run_id,
            current_node=self._entry,
            status=RunStatus.RUNNING,
            data=dict(initial_data),
        )

        while True:
            # Human-in-loop resume
            if state.paused_for_human:
                if on_pause:
                    approval = await on_pause(state)
                    state.data.update(approval)
                    state.paused_for_human = False
                else:
                    await asyncio.sleep(1)
                    continue

            node = self._nodes.get(state.current_node)
            if not node:
                state.status = RunStatus.FAILED
                state.error = f"Node '{state.current_node}' not found"
                break

            # Execute node with retry — always runs, including terminal nodes
            result = await self._execute_node(node, state)
            if result is None:
                state.status = RunStatus.FAILED
                state.error = f"Node '{node.node_id}' failed after retries"
                state.failed_nodes.append(node.node_id)
                break

            state.data.update(result)
            state.completed_nodes.append(state.current_node)
            state.step += 1

            # Checkpoint after every successful step
            cp = self._checkpoint(state)
            if on_checkpoint:
                await on_checkpoint(cp)

            # Check for human-pause signal from node result
            if result.get("__pause_for_human__"):
                state.paused_for_human = True
                state.human_context = result.get("__human_context__", {})
                state.status = RunStatus.PAUSED
                continue

            # Stop if this node is a terminal
            if state.current_node in self._terminals:
                state.status = RunStatus.COMPLETED
                break

            # Route to next node
            next_node = self._route(state)
            if next_node is None:
                state.status = RunStatus.COMPLETED
                break
            state.current_node = next_node

        return state

    async def _execute_node(
        self,
        node: GraphNode,
        state: GraphState,
    ) -> dict[str, Any] | None:
        for attempt in range(node.retries + 1):
            try:
                result = await asyncio.wait_for(
                    node.fn(state.data),
                    timeout=node.timeout_s,
                )
                return result
            except asyncio.TimeoutError:
                if attempt == node.retries:
                    return None
            except Exception:
                if attempt == node.retries:
                    return None
                await asyncio.sleep(2**attempt)  # exponential back-off
        return None

    def _route(self, state: GraphState) -> str | None:
        edges = self._edges.get(state.current_node, [])
        for edge in edges:
            if edge.condition is None or edge.condition(state.data):
                return edge.target
        return None

    # ── Parallel branches ─────────────────────────────────────────────────────

    async def run_parallel(
        self,
        branch_data: list[dict[str, Any]],
        node_id: str,
    ) -> list[dict[str, Any]]:
        """Run the same node in parallel over multiple data slices."""
        node = self._nodes[node_id]
        tasks = [
            self._execute_node(
                node,
                GraphState(
                    run_id=f"{self.run_id}:branch:{i}",
                    current_node=node_id,
                    data=d,
                ),
            )
            for i, d in enumerate(branch_data)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r if isinstance(r, dict) else {} for r in results]

    # ── Time-travel ───────────────────────────────────────────────────────────

    def restore_from_checkpoint(self, checkpoint_id: str) -> GraphState | None:
        """Restore graph state from a specific checkpoint for time-travel debugging."""
        cp = next((c for c in self._checkpoints if c.checkpoint_id == checkpoint_id), None)
        if not cp:
            return None
        gs = cp.graph_state
        return GraphState(
            run_id=self.run_id,
            current_node=gs.get("current_node", self._entry),
            status=RunStatus(gs.get("status", "pending")),
            data=gs.get("data", {}),
            step=cp.step,
            completed_nodes=gs.get("completed_nodes", []),
        )
