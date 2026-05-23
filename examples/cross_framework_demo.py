"""Cross-framework pipeline demo — no API key or external framework required.

Demonstrates the MeshFlow control plane thesis with a realistic 4-node pipeline:

  [CrewAI research crew]  →  [LangGraph validator]  →  [Human gate]  →  [Native summarizer]
  MeshNode.from_crewai()     MeshNode.from_langgraph()  .human_approval()  .from_callable()

Each node is simulated with a Python callable so the demo runs immediately.
Comments throughout show the real framework code you would swap in for production.

Run:
    python examples/cross_framework_demo.py
    python examples/cross_framework_demo.py --no-color
    python examples/cross_framework_demo.py --replay      # inspect ledger after run
"""
from __future__ import annotations

import argparse
import asyncio
import textwrap
import time
from typing import Any

from meshflow import (
    Mesh,
    MeshNode,
    Policy,
    ReplayLedger,
    WorkflowDefinition,
)
from meshflow.core.node import NodeKind, NodeOutput
from meshflow.core.schemas import HumanInLoopConfig, RiskTier


# ── ANSI colours ──────────────────────────────────────────────────────────────

USE_COLOR = True

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text

BOLD   = lambda t: _c("1", t)
DIM    = lambda t: _c("2", t)
GREEN  = lambda t: _c("32", t)
YELLOW = lambda t: _c("33", t)
CYAN   = lambda t: _c("36", t)
RED    = lambda t: _c("31", t)
BLUE   = lambda t: _c("34", t)
MAGENTA= lambda t: _c("35", t)


# ── Node runners — swap these for real framework objects in production ─────────

async def _crewai_research(task: str, context: dict[str, Any]) -> NodeOutput:
    """
    Simulates a CrewAI crew doing market research.

    Real production code:
        from crewai import Agent, Task, Crew
        researcher = Agent(role="Market Analyst", goal="...", backstory="...")
        task_obj   = Task(description=task, agent=researcher)
        crew       = Crew(agents=[researcher], tasks=[task_obj])
        result     = crew.kickoff()                 # ← synchronous
        return NodeOutput(content=str(result), tokens_used=800)

    MeshNode.from_crewai("research", crew) wraps this in an async executor
    and applies the full StepRuntime governance kernel.
    """
    await asyncio.sleep(0.05)   # simulate network/LLM latency
    report = textwrap.dedent(f"""\
        MARKET RESEARCH REPORT
        Task: {task}

        Executive Summary:
        The agentic AI orchestration market is experiencing rapid growth,
        driven by enterprise demand for auditable, policy-governed AI systems.

        Key Findings:
        1. Market size: $4.2B in 2024, projected $18.7B by 2027 (CAGR 64%)
        2. Top pain points: lack of auditability (73%), cost overruns (61%),
           prompt injection vulnerabilities (58%), no human oversight (52%)
        3. Decision makers require: audit trails, budget caps, HITL controls,
           and the ability to mix agents from multiple frameworks
        4. Governance frameworks command a 40% price premium over raw agents

        Confidence: HIGH  Sources: 12 primary interviews, 3 industry reports
    """)
    return NodeOutput(
        content=report,
        structured={"market_size_usd": "4.2B", "cagr": "64%", "confidence": "HIGH"},
        tokens_used=820,
        confidence=0.88,
    )


async def _langgraph_validator(task: str, context: dict[str, Any]) -> NodeOutput:
    """
    Simulates a LangGraph compiled StateGraph doing fact-checking.

    Real production code:
        from langgraph.graph import StateGraph, END
        from typing import TypedDict

        class State(TypedDict):
            report: str
            verdict: str

        def check_node(state: State) -> State:
            # call LLM to verify claims ...
            return {**state, "verdict": "VERIFIED"}

        graph  = StateGraph(State)
        graph.add_node("check", check_node)
        graph.set_entry_point("check")
        graph.add_edge("check", END)
        compiled = graph.compile()          # ← this is what you pass to from_langgraph()

    MeshNode.from_langgraph("validator", compiled) calls compiled.ainvoke()
    and normalises the output to NodeOutput.
    """
    await asyncio.sleep(0.04)
    prior_report = context.get("research_output", "")
    confidence   = context.get("confidence", "UNKNOWN")

    verdict = textwrap.dedent(f"""\
        FACT VALIDATION REPORT
        Input confidence: {confidence}

        Claims verified:
        ✓ Market size $4.2B — corroborated by Gartner Q3 2024 report
        ✓ CAGR 64% — consistent with IDC forecast within ±5%
        ✓ Pain point percentages — validated against 12 primary sources
        ✓ 40% price premium — verified in 3 enterprise procurement studies

        Validation verdict: PASS
        Risk flags: none
        Recommended action: proceed to approval
    """)
    return NodeOutput(
        content=verdict,
        structured={"validation_verdict": "PASS", "risk_flags": 0},
        tokens_used=340,
        confidence=0.93,
    )


async def _native_summarizer(task: str, context: dict[str, Any]) -> NodeOutput:
    """
    A native MeshFlow callable that produces the final executive summary.
    This is the kind of agent that lives inside MeshFlow — no adapter needed.
    """
    await asyncio.sleep(0.03)
    verdict  = context.get("validator_output", "")
    approval = context.get("approve_output",   "approved")

    summary = textwrap.dedent(f"""\
        EXECUTIVE SUMMARY
        ═══════════════════════════════════════════════

        Task: {task}

        The research and validation pipeline completed successfully.

        Market Opportunity:
        The agentic AI governance market represents a $4.2B opportunity
        growing at 64% CAGR. Enterprise buyers are willing to pay a 40%
        premium for auditable, policy-governed orchestration systems.

        Validation Status: PASS (0 risk flags)
        Human Approval: {approval.upper()}

        Recommendation: Proceed to product development.

        ═══════════════════════════════════════════════
        Generated by MeshFlow governed pipeline
        All steps audited · costs tracked · identity verified
    """)
    return NodeOutput(
        content=summary,
        tokens_used=210,
        confidence=0.95,
    )


# ── Build the workflow ─────────────────────────────────────────────────────────

def build_workflow(auto_approve: bool = True) -> WorkflowDefinition:
    """
    Build a 4-node cross-framework pipeline.

    Node kinds used:
      crewai    — research crew
      langgraph — fact-check graph
      human     — compliance approval gate
      python    — native summarizer callable
    """
    def _approval_fn(task: str) -> str:
        if auto_approve:
            print(f"\n  {YELLOW('[HITL]')} Auto-approving in demo mode: {task[:60]}...")
            return "approved"
        return input(f"\n  {BOLD('[HITL] Approve this pipeline output? [yes/no]:')} ").strip()

    # Build nodes using the same API as real framework objects
    research_node = MeshNode(
        id="research_crew",
        kind=NodeKind.CREWAI,                    # declares origin framework
        risk_profile=RiskTier.INTERNAL,
        capabilities=["market_research", "web_search"],
        _runner=lambda inp: _crewai_research(inp.task, inp.context),
    )

    validator_node = MeshNode(
        id="fact_validator",
        kind=NodeKind.LANGGRAPH,
        risk_profile=RiskTier.INTERNAL,
        capabilities=["fact_check", "source_verification"],
        _runner=lambda inp: _langgraph_validator(inp.task, inp.context),
    )

    approval_node = MeshNode.human_approval(
        "approve",
        prompt_fn=_approval_fn,
    )

    summary_node = MeshNode.from_callable(
        "executive_summary",
        _native_summarizer,
        risk=RiskTier.READ_ONLY,
    )

    return (
        WorkflowDefinition(
            "cross_framework_pipeline",
            policy=Policy(
                budget_usd=2.00,
                max_steps=10,
                enable_guardian=True,
                enable_uncertainty=True,
                enable_collusion_audit=True,
                human_in_loop=HumanInLoopConfig(
                    enabled=False,          # we handle approval explicitly via node
                ),
            ),
        )
        .add_node(research_node)
        .add_node(validator_node)
        .add_node(approval_node)
        .add_node(summary_node)
        .add_edge("research_crew",   "fact_validator")
        .add_edge("fact_validator",  "approve")
        .add_edge("approve",         "executive_summary")
        .set_entry("research_crew")
        .set_terminal("executive_summary")
    )


# ── Pretty-print helpers ───────────────────────────────────────────────────────

_KIND_LABEL = {
    "crewai":    MAGENTA("CrewAI crew"),
    "langgraph": BLUE("LangGraph graph"),
    "human":     YELLOW("Human approval"),
    "python":    GREEN("Native callable"),
}

def _print_header() -> None:
    print()
    print(BOLD("━" * 62))
    print(BOLD("  MeshFlow Cross-Framework Pipeline Demo"))
    print(BOLD("━" * 62))
    print(DIM("  Every node passes through the identical StepRuntime kernel"))
    print(DIM("  regardless of which framework produced it."))
    print()
    print(DIM("  Pipeline:"))
    print(DIM("    CrewAI research → LangGraph validator → Human gate → Summarizer"))
    print()

def _print_step(node_id: str, kind: str, ok: bool, uncertainty: float,
                cost_usd: float, tokens: int, duration_ms: float,
                block_reason: str = "") -> None:
    status  = GREEN("✓ ok") if ok else RED("✗ blocked")
    kind_lbl = _KIND_LABEL.get(kind, kind)
    print(
        f"  {CYAN(node_id):<30} "
        f"[{kind_lbl}]"
    )
    print(
        f"    status={status:<18} "
        f"confidence={1-uncertainty:.0%}  "
        f"tokens={tokens:<5}  "
        f"{duration_ms:.0f}ms"
    )
    if block_reason:
        print(f"    {RED('blocked_by:')} {block_reason}")

def _print_ledger_summary(summary: dict) -> None:
    print()
    print(BOLD("  Ledger Summary"))
    print(DIM("  " + "─" * 40))
    print(f"    run_id       {DIM(summary.get('run_id', '')[:24])}...")
    print(f"    steps        {summary.get('steps', 0)}")
    print(f"    nodes        {' → '.join(summary.get('nodes', []))}")
    print(f"    total cost   ${summary.get('total_cost_usd', 0):.6f}")
    print(f"    total tokens {summary.get('total_tokens', 0)}")
    print(f"    blocked      {summary.get('blocked_steps', 0)}")


# ── Main ───────────────────────────────────────────────────────────────────────

async def main(replay: bool = False) -> None:
    _print_header()

    task = (
        "Analyse the agentic AI orchestration market and produce "
        "an executive briefing for the board."
    )

    ledger_db = "meshflow_demo_runs.db"
    ledger    = ReplayLedger(ledger_db)
    wf        = build_workflow(auto_approve=True)

    print(BOLD("  Task:"))
    print(f"    {task}")
    print()
    print(BOLD("  Governed execution starting..."))
    print(DIM("  " + "─" * 58))

    run_start = time.monotonic()
    result = await Mesh().run_workflow(wf, task=task, ledger_db=ledger_db)
    total_s = time.monotonic() - run_start

    print(DIM("  " + "─" * 58))
    print()

    # Print per-step summary from ledger
    print(BOLD("  Step-by-step governance log:"))
    print()
    steps = await ledger.get_run(result.run_id)
    for step in steps:
        _print_step(
            node_id     = step["node_id"],
            kind        = step["node_kind"],
            ok          = not step["blocked"],
            uncertainty = step["uncertainty"],
            cost_usd    = step["cost_usd"],
            tokens      = step["tokens_used"],
            duration_ms = step["duration_ms"],
            block_reason= step.get("block_reason", ""),
        )
        print()

    # Final result
    print(BOLD("━" * 62))
    status_str = GREEN("COMPLETED") if result.completed else RED("FAILED")
    print(f"  Status   : {status_str}")
    print(f"  Duration : {total_s:.2f}s  (governance overhead <10ms/step)")
    print(f"  Cost     : ${result.total_cost_usd:.6f}")
    print(f"  Tokens   : {result.total_tokens}")
    print(f"  Carbon   : {result.total_carbon_gco2:.4f} gCO2eq")
    print(BOLD("━" * 62))
    print()

    # Final output
    print(BOLD("  Final output:"))
    print()
    for line in result.output.splitlines():
        print(f"    {line}")
    print()

    # Ledger summary
    summary = await ledger.run_summary(result.run_id)
    _print_ledger_summary(summary)

    print()
    print(DIM(f"  Full run history stored in: {ledger_db}"))
    print(DIM(f"  Inspect with:  meshflow replay {result.run_id} --db {ledger_db}"))
    print()

    if replay:
        print(BOLD("  Replaying run from ledger..."))
        print()
        raw = await ledger.export_run(result.run_id)
        import json
        parsed = json.loads(raw)
        for i, step in enumerate(parsed["steps"], 1):
            print(f"  [{i:02d}] node={step['node_id']:<22} verdict={step['verdict']:<10} "
                  f"confidence={1-step['uncertainty']:.0%}  "
                  f"output: {step['output_content'][:60].strip()}...")
        print()


def _print_real_code_note() -> None:
    print()
    print(BOLD("  ── How to swap in real frameworks ──────────────────────"))
    print()
    print(DIM("  Replace the Python callable nodes with real objects:"))
    print()
    print("""  # CrewAI
  from crewai import Agent, Task, Crew
  crew = Crew(agents=[analyst], tasks=[research_task])
  MeshNode.from_crewai("research_crew", crew)

  # LangGraph
  from langgraph.graph import StateGraph, END
  compiled = graph.compile()
  MeshNode.from_langgraph("fact_validator", compiled)

  # AutoGen
  from autogen import ConversableAgent, GroupChatManager
  MeshNode.from_autogen("debate", agent, manager=group_manager)

  # HTTP service
  MeshNode.from_http("scorer", "https://api.example.com/score")
""")
    print(DIM("  Everything else — governance, ledger, policy, streaming —"))
    print(DIM("  is identical regardless of which framework you use."))
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MeshFlow cross-framework demo")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colours")
    parser.add_argument("--replay",   action="store_true", help="Show ledger replay after run")
    parser.add_argument("--show-adapters", action="store_true", help="Print real framework code")
    args = parser.parse_args()

    if args.no_color:
        USE_COLOR = False

    if args.show_adapters:
        _print_real_code_note()
    else:
        asyncio.run(main(replay=args.replay))
