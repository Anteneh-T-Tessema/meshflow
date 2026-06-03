"""Sprint 95 Features Demo — Showcase all new capabilities in MeshFlow.

This demo runs a simulated market research and report generation workflow,
demonstrating the eight features introduced in Sprint 95:

1. Zero-boilerplate `@tool` auto-schema generation from docstrings and type hints.
2. Parallel agent execution with `Workflow.add_parallel`.
3. Agent memory auto-compression with character threshold checks.
4. Workflow refinement loops using `Workflow.run_until` with quality conditions.
5. Mid-run Cost Cap enforcement (`CostCap`) to prevent runaway API spend.
6. Structured Shared State — Pydantic-based state shared across workflow agents.
7. Declarative Conditional Routing — dynamic branching based on state conditions.
8. Human-in-the-Loop Breakpoints — pause, inspect, resume with human feedback.
"""
import asyncio
from typing import Any

from pydantic import BaseModel

from meshflow import Agent, Workflow, tool, Tool
from meshflow.agents.base import LLMProvider
from meshflow.core.workflow import CostCap


# ═══════════════════════════════════════════════════════════════════════════════
# Mock LLM Provider
# ═══════════════════════════════════════════════════════════════════════════════

class DemoLLMProvider(LLMProvider):
    """Mock provider that returns pre-configured responses and simulates cost."""

    def __init__(self, responses: list[str], cost_per_call: float = 0.02):
        self.responses = responses
        self.idx = 0
        self.cost_per_call = cost_per_call

    async def complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        response_format: str | None = None,
    ) -> tuple[str, int, float]:
        resp = self.responses[self.idx]
        if self.idx < len(self.responses) - 1:
            self.idx += 1
        return resp, len(resp.split()), self.cost_per_call

    async def complete_with_tools(self, *args, **kwargs) -> tuple[str, int, float]:
        return await self.complete("", [], "", 0)

    async def stream_complete(self, *args, **kwargs):
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Zero-Boilerplate @tool
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def fetch_competitor_data(company: str, metric: str = "pricing") -> str:
    """Fetch recent competitor data.

    Args:
        company (str): The name of the competitor company.
        metric (str): The metric to fetch (e.g., pricing, features, market_share).
    """
    data = {
        "AcmeCorp": {
            "pricing": "$99/user/month flat rate.",
            "features": "Generative reports, role-based workflows, HIPAA compliance.",
        },
        "BetaInc": {
            "pricing": "Usage-based billing starting at $0.05 per API call.",
            "features": "Subprocess execution sandboxing, OIDC support.",
        }
    }
    return data.get(company, {}).get(metric, "Metric or company not found.")


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Structured Shared State schema
# ═══════════════════════════════════════════════════════════════════════════════

class ResearchState(BaseModel):
    """Shared state passed between all agents in the workflow."""
    query: str = ""
    pricing_notes: str = ""
    feature_notes: str = ""
    draft_report: str = ""
    quality_score: int = 0
    feedback: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# Demo runner
# ═══════════════════════════════════════════════════════════════════════════════

async def main():
    print("=" * 70)
    print("  MeshFlow Sprint 95 — Full Feature Showcase")
    print("=" * 70)

    # ── 1. @tool auto-schema ──────────────────────────────────────────────
    print("\n┌─────────────────────────────────────────────────────────────┐")
    print("│ [1/8] Zero-Boilerplate @tool Schema Generation             │")
    print("└─────────────────────────────────────────────────────────────┘")

    print(f"  Tool Name        : {fetch_competitor_data.name}")
    print(f"  Tool Description : {fetch_competitor_data.description}")
    schema = fetch_competitor_data.input_schema()
    print("  Parameters Derived:")
    for param_name, info in schema["properties"].items():
        print(f"    - {param_name} ({info['type']}): {info.get('description', '')}")

    # ── 2. Parallel execution ─────────────────────────────────────────────
    print("\n┌─────────────────────────────────────────────────────────────┐")
    print("│ [2/8] Parallel Agent Execution                             │")
    print("└─────────────────────────────────────────────────────────────┘")

    pricing_agent = Agent(
        name="PricingResearcher",
        provider=DemoLLMProvider(
            ['{"pricing_notes": "AcmeCorp $99/mo flat; BetaInc $0.05/call usage-based."}'],
            cost_per_call=0.01,
        ),
    )
    features_agent = Agent(
        name="FeaturesResearcher",
        provider=DemoLLMProvider(
            ['{"feature_notes": "AcmeCorp has HIPAA & gen reports; BetaInc has sandbox & OIDC."}'],
            cost_per_call=0.01,
        ),
    )

    wf = Workflow(state_schema=ResearchState, initial_state={"query": "Compare AcmeCorp vs BetaInc"})
    wf.add_parallel(pricing_agent, features_agent)

    result = wf.run("Research competitor landscape for AcmeCorp and BetaInc")
    print(f"  Parallel agents ran : PricingResearcher, FeaturesResearcher")
    print(f"  State.pricing_notes : {wf.state.pricing_notes}")
    print(f"  State.feature_notes : {wf.state.feature_notes}")
    print(f"  Cost               : ${result.total_cost_usd:.4f}")

    # ── 3. Memory compression ─────────────────────────────────────────────
    print("\n┌─────────────────────────────────────────────────────────────┐")
    print("│ [3/8] Agent Memory Auto-Compression                       │")
    print("└─────────────────────────────────────────────────────────────┘")

    summary_provider = DemoLLMProvider(["[Summary of working memory content]"], cost_per_call=0.00)
    memory_agent = Agent(
        name="MemoryAgent",
        memory=True,
        memory_compress_threshold=80,
        provider=summary_provider,
    )
    built_agent = memory_agent._build()
    built_agent._memory.add("This is a very long memory statement about competitor features.")
    built_agent._memory.add("This is another very long memory statement about competitor pricing structures.")

    print(f"  Working memory before : {built_agent._memory.working_count} items")
    print(f"  Episodic memory before: {built_agent._memory.episodic_count} items")

    await built_agent.step("Check competitor data", {})

    print(f"  Working memory after  : {built_agent._memory.working_count} items (most recent kept)")
    print(f"  Episodic memory after : {built_agent._memory.episodic_count} items (compressed summary)")
    print(f"  Compressed content    : {built_agent._memory._episodic[0].content}")

    # ── 4. Refinement loops ───────────────────────────────────────────────
    print("\n┌─────────────────────────────────────────────────────────────┐")
    print("│ [4/8] Workflow Refinement Loops (run_until)                │")
    print("└─────────────────────────────────────────────────────────────┘")

    writer_responses = [
        "First draft report\nCONFIDENCE: 0.75",
        "Synthesized Competitor Report:\n- AcmeCorp ($99/mo, HIPAA)\n- BetaInc ($0.05/call, sandbox)\nCONFIDENCE: 0.95",
    ]
    writer = Agent(name="SynthesisWriter", provider=DemoLLMProvider(writer_responses, cost_per_call=0.02))

    wf_loop = Workflow()
    wf_loop.add(writer)

    loop_result = wf_loop.run_until("Synthesize competitor report", 0.90, max_steps=3)
    print(f"  Completed     : {loop_result.completed}")
    print(f"  Iterations    : met quality threshold ≥ 0.90")
    print(f"  Final output  : {loop_result.output[:80]}...")

    # ── 5. Cost cap enforcement ───────────────────────────────────────────
    print("\n┌─────────────────────────────────────────────────────────────┐")
    print("│ [5/8] Mid-Run Cost Cap Enforcement                        │")
    print("└─────────────────────────────────────────────────────────────┘")

    wf_capped = Workflow(cost_cap=CostCap(usd=0.03))
    a1 = Agent(name="Step1", provider=DemoLLMProvider(["Output 1"], cost_per_call=0.02))
    a2 = Agent(name="Step2", provider=DemoLLMProvider(["Output 2"], cost_per_call=0.02))
    a3 = Agent(name="Step3", provider=DemoLLMProvider(["Output 3"], cost_per_call=0.02))
    wf_capped.add(a1).add(a2).add(a3)

    capped_result = wf_capped.run("Run under strict budget cap")
    print(f"  Budget Limit   : $0.0300")
    print(f"  Actual Cost    : ${capped_result.total_cost_usd:.4f}")
    print(f"  Completed      : {capped_result.completed}")
    print(f"  Blocked Agents : {capped_result.blocked_nodes}")

    # ── 6. Structured Shared State ────────────────────────────────────────
    print("\n┌─────────────────────────────────────────────────────────────┐")
    print("│ [6/8] Structured Shared State (Pydantic)                   │")
    print("└─────────────────────────────────────────────────────────────┘")

    wf_state = Workflow(
        state_schema=ResearchState,
        initial_state={"query": "Full state pipeline demo"},
    )
    researcher = Agent(
        name="Researcher",
        provider=DemoLLMProvider(
            ['{"pricing_notes": "Competitive pricing found", "quality_score": 7}'],
            cost_per_call=0.01,
        ),
    )
    writer2 = Agent(
        name="Writer",
        provider=DemoLLMProvider(
            ['{"draft_report": "Final comprehensive report with all findings", "quality_score": 9}'],
            cost_per_call=0.01,
        ),
    )
    wf_state.add(researcher).add(writer2)
    state_result = wf_state.run("Full pipeline")

    print(f"  State.query         : {wf_state.state.query}")
    print(f"  State.pricing_notes : {wf_state.state.pricing_notes}")
    print(f"  State.quality_score : {wf_state.state.quality_score}")
    print(f"  State.draft_report  : {wf_state.state.draft_report[:60]}...")

    # ── 7. Declarative Conditional Routing ────────────────────────────────
    print("\n┌─────────────────────────────────────────────────────────────┐")
    print("│ [7/8] Declarative Conditional Routing                      │")
    print("└─────────────────────────────────────────────────────────────┘")

    wf_cond = Workflow(state_schema=ResearchState, initial_state={"quality_score": 3})

    deep_dive = Agent(name="DeepDive", provider=DemoLLMProvider(["Deep analysis completed"]))
    quick_summary = Agent(name="QuickSummary", provider=DemoLLMProvider(["Quick summary done"]))

    def route_by_quality(state):
        return "deep" if state.quality_score < 5 else "quick"

    wf_cond.add_conditional(route_by_quality, {"deep": deep_dive, "quick": quick_summary})

    cond_result = wf_cond.run("Analyze results")
    print(f"  State.quality_score : {wf_cond.state.quality_score} (< 5 → 'deep' branch)")
    print(f"  Branch selected     : DeepDive")
    print(f"  Output              : {cond_result.output}")

    # Change score and re-run
    wf_cond2 = Workflow(state_schema=ResearchState, initial_state={"quality_score": 8})
    wf_cond2.add_conditional(route_by_quality, {"deep": deep_dive, "quick": quick_summary})
    cond_result2 = wf_cond2.run("Analyze results")
    print(f"\n  State.quality_score : {wf_cond2.state.quality_score} (≥ 5 → 'quick' branch)")
    print(f"  Branch selected     : QuickSummary")
    print(f"  Output              : {cond_result2.output}")

    # ── 8. Human-in-the-Loop Breakpoints ──────────────────────────────────
    print("\n┌─────────────────────────────────────────────────────────────┐")
    print("│ [8/8] Human-in-the-Loop Breakpoints (Pause & Resume)       │")
    print("└─────────────────────────────────────────────────────────────┘")

    wf_hitl = Workflow(state_schema=ResearchState, initial_state={"query": "breakpoint demo"})

    drafter = Agent(
        name="Drafter",
        provider=DemoLLMProvider(
            ['{"draft_report": "Initial draft of competitor analysis", "quality_score": 6}'],
            cost_per_call=0.01,
        ),
    )
    finalizer = Agent(
        name="Finalizer",
        provider=DemoLLMProvider(
            ['{"draft_report": "Polished report incorporating human feedback", "quality_score": 10}'],
            cost_per_call=0.01,
        ),
    )

    wf_hitl.add(drafter, interrupt_after=True)
    wf_hitl.add(finalizer)

    # Run — pauses after Drafter
    hitl_result = wf_hitl.run("Draft competitor analysis")
    print(f"  Run completed?  : {hitl_result.completed}")
    print(f"  Paused at       : {hitl_result.paused_nodes}")
    print(f"  Draft output    : {wf_hitl.state.draft_report}")
    print(f"  Quality score   : {wf_hitl.state.quality_score}")

    # Simulate human review and resume
    print(f"\n  [Human Review] → Adding feedback: 'Focus more on pricing comparison'")
    hitl_result2 = wf_hitl.resume("Draft competitor analysis", human_input="Focus more on pricing comparison")
    print(f"  Resume completed: {hitl_result2.completed}")
    print(f"  Feedback stored : {wf_hitl.state.feedback}")
    print(f"  Final report    : {wf_hitl.state.draft_report}")
    print(f"  Final score     : {wf_hitl.state.quality_score}")

    # ── Summary ───────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  ✅ All 8 Sprint 95 features demonstrated successfully!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
