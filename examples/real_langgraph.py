"""Real LangGraph compiled graph wired into a MeshFlow governed pipeline.

Requires:  pip install langgraph langchain-anthropic

This example shows exactly how to take an existing LangGraph StateGraph and submit
it to the MeshFlow control plane. The graph runs its own internal logic and parallel
branches normally — MeshFlow governs the node as a single unit.

Before MeshFlow:
    result = await compiled_graph.ainvoke({"messages": [...]})

After MeshFlow:
    node   = MeshNode.from_langgraph("my_graph", compiled_graph)
    result = await Mesh().run_workflow(wf, task="...")
    # every invocation now has: identity, guardian scan, budget cap,
    # uncertainty scoring, audit ledger, HITL escalation
"""
from __future__ import annotations

import asyncio
import sys
from typing import Annotated, TypedDict


def _check_langgraph() -> None:
    missing = []
    try:
        import langgraph  # noqa: F401
    except ImportError:
        missing.append("langgraph")
    try:
        import langchain_anthropic  # noqa: F401
    except ImportError:
        missing.append("langchain-anthropic")
    if missing:
        print(f"Missing packages: {', '.join(missing)}")
        print(f"Install with:  pip install {' '.join(missing)}")
        sys.exit(1)


_check_langgraph()

from langchain_anthropic import ChatAnthropic  # noqa: E402
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage  # noqa: E402
from langgraph.graph import END, StateGraph  # noqa: E402
from langgraph.graph.message import add_messages  # noqa: E402

from meshflow import Mesh, MeshNode, Policy, WorkflowDefinition  # noqa: E402


# ── Define your LangGraph graph exactly as you normally would ─────────────────

class FactCheckState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    verdict: str
    risk_flags: int


def build_fact_check_graph() -> object:
    """A minimal LangGraph graph that fact-checks a claim."""
    llm = ChatAnthropic(model="claude-haiku-4-5-20251001")

    def fact_checker(state: FactCheckState) -> FactCheckState:
        last = state["messages"][-1].content if state["messages"] else ""
        response = llm.invoke([
            HumanMessage(content=(
                f"Fact-check the following text. Return VERIFIED or UNVERIFIED "
                f"and list any specific risk flags.\n\n{last[:800]}"
            ))
        ])
        verdict = "VERIFIED" if "VERIFIED" in response.content.upper() else "UNVERIFIED"
        return {
            "messages": [response],
            "verdict": verdict,
            "risk_flags": 0 if verdict == "VERIFIED" else 1,
        }

    def risk_assessor(state: FactCheckState) -> FactCheckState:
        # LangGraph internal node — runs inside the graph, invisible to MeshFlow
        if state["risk_flags"] > 0:
            return {**state, "verdict": f"{state['verdict']} (ELEVATED RISK)"}
        return state

    graph = StateGraph(FactCheckState)
    graph.add_node("fact_checker",  fact_checker)
    graph.add_node("risk_assessor", risk_assessor)
    graph.set_entry_point("fact_checker")
    graph.add_edge("fact_checker",  "risk_assessor")
    graph.add_edge("risk_assessor", END)

    return graph.compile()


# ── Wire it into MeshFlow ─────────────────────────────────────────────────────

async def main() -> None:
    compiled_graph = build_fact_check_graph()

    # One line to wrap — your graph is unchanged
    validator_node = MeshNode.from_langgraph("langgraph_validator", compiled_graph)

    wf = (
        WorkflowDefinition(
            "langgraph_governed_pipeline",
            policy=Policy(
                budget_usd=2.00,
                enable_guardian=True,
                enable_uncertainty=True,
            ),
        )
        .add_node(validator_node)
        .add_node(
            MeshNode.from_callable(
                "summarize",
                lambda task, ctx: (
                    f"Validation complete.\n"
                    f"Verdict: {ctx.get('verdict', 'unknown')}\n"
                    f"Risk flags: {ctx.get('risk_flags', 'n/a')}"
                ),
            )
        )
        .add_edge("langgraph_validator", "summarize")
    )

    claim = (
        "Multi-agent systems improve complex task accuracy by 40% and "
        "reduce token cost by 30% compared to single-agent approaches."
    )

    print("Running LangGraph graph inside MeshFlow governance...\n")

    result = await Mesh().run_workflow(wf, task=claim)

    print(f"Status   : {'COMPLETED' if result.completed else 'FAILED'}")
    print(f"Duration : {result.duration_s:.2f}s")
    print(f"Cost     : ${result.total_cost_usd:.5f}")
    print(f"Tokens   : {result.total_tokens}")
    print(f"\nOutput:\n{result.output}")
    print(f"\nLedger   : {result.ledger_db}  run_id={result.run_id}")


if __name__ == "__main__":
    asyncio.run(main())
