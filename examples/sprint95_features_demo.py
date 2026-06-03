"""Sprint 95 Features Demo — Showcase new capabilities in MeshFlow.

This demo runs a simulated market research and report generation workflow,
demonstrating the five new features introduced in Sprint 95:
1. Zero-boilerplate `@tool` auto-schema generation from docstrings and type hints.
2. Parallel agent execution with `Workflow.add_parallel`.
3. Agent memory auto-compression with character threshold checks.
4. Workflow refinement loops using `Workflow.run_until` with quality conditions.
5. Mid-run Cost Cap enforcement (`CostCap`) to prevent runaway API spend.
"""
import asyncio
from typing import Any
from meshflow import Agent, Workflow, tool, Tool
from meshflow.agents.base import LLMProvider
from meshflow.core.workflow import CostCap

# 1. Zero-Boilerplate @tool decorator
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


# Mock LLM Provider to simulate cost charging, memory, and sequential outputs
class DemoLLMProvider(LLMProvider):
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
        # Return next pre-configured response
        resp = self.responses[self.idx]
        if self.idx < len(self.responses) - 1:
            self.idx += 1
        return resp, len(resp.split()), self.cost_per_call

    async def complete_with_tools(self, *args, **kwargs) -> tuple[str, int, float]:
        return await self.complete("", [], "", 0)

    async def stream_complete(self, *args, **kwargs):
        pass


async def main():
    print("=" * 60)
    print("MeshFlow Sprint 95 Advanced Features Demo")
    print("=" * 60)

    # 1. Verify Tool Auto-Schema
    print("\n[1] Testing Zero-Boilerplate @tool schema generation:")
    print(f"Tool Name        : {fetch_competitor_data.name}")
    print(f"Tool Description : {fetch_competitor_data.description}")
    schema = fetch_competitor_data.input_schema()
    print("Parameters Derived:")
    for param_name, info in schema["properties"].items():
        print(f"  - {param_name} ({info['type']}): {info.get('description', '')}")

    # 2. Setup parallel research agents
    # We assign them a mock provider that simulates successful outputs and minor costs
    pricing_provider = DemoLLMProvider(["[Research] AcmeCorp pricing is $99/mo; BetaInc is $0.05/call."], cost_per_call=0.01)
    features_provider = DemoLLMProvider(["[Research] AcmeCorp offers HIPAA; BetaInc has sandbox execution."], cost_per_call=0.01)

    agent_pricing = Agent(name="PricingResearcher", provider=pricing_provider)
    agent_features = Agent(name="FeaturesResearcher", provider=features_provider)

    # 3. Setup refinement loop writer
    # The writer will draft a report.
    # On first run, it fails the quality threshold (CONFIDENCE: 0.75).
    # On the second run, it succeeds (CONFIDENCE: 0.95).
    writer_responses = [
        "First draft report: ACMECorp has $99 flat rate. BetaInc has sandbox.\nCONFIDENCE: 0.75",
        "Synthesized Competitor Report:\n- AcmeCorp ($99/user/month flat rate, HIPAA compliant)\n- BetaInc (usage-based pricing, sandbox sandboxing)\nCONFIDENCE: 0.95"
    ]
    writer_provider = DemoLLMProvider(writer_responses, cost_per_call=0.02)
    agent_writer = Agent(name="SynthesisWriter", provider=writer_provider)

    # Build the Workflow using add_parallel
    wf = Workflow()
    wf.add_parallel(agent_pricing, agent_features)
    wf.add(agent_writer)

    print("\n[2] Executing Workflow with Parallel Steps and Refinement Loops:")
    # We want to run the workflow until the output contains a confidence value >= 0.90
    # Let's pass "confidence >= 0.9" as the condition string!
    result = await wf._run_until_async(
        task="Create a comparative report for AcmeCorp and BetaInc",
        condition="confidence >= 0.9",
        max_steps=3
    )

    print(f"Workflow Completed : {result.completed}")
    print(f"Total Steps run    : {len(result.steps)}")
    print(f"Total Workflow Cost: ${result.total_cost_usd:.4f}")
    print(f"Final Output:\n{result.output}\n")

    # 4. Agent Memory Compression
    print("[3] Testing Agent Memory Compression:")
    # Let's create an agent with memory enabled and a compression threshold of 80 characters.
    summary_provider = DemoLLMProvider(["[Summary of working memory content]"], cost_per_call=0.00)
    memory_agent = Agent(
        name="MemoryAgent",
        memory=True,
        memory_compress_threshold=80,
        provider=summary_provider
    )
    built_agent = memory_agent._build()
    
    # We load some long content into working memory to trigger compression
    built_agent._memory.add("This is a very long memory statement about competitor features.")
    built_agent._memory.add("This is another very long memory statement about competitor pricing structures.")
    
    print(f"Initial working memory items: {built_agent._memory.working_count}")
    print(f"Initial episodic memory items: {built_agent._memory.episodic_count}")
    
    # Run a step on the agent to trigger automatic compression
    await built_agent.step("Check competitor data", {})
    
    print(f"Working memory items post-step (most recent kept): {built_agent._memory.working_count}")
    print(f"Episodic memory items post-step (compressed summary added): {built_agent._memory.episodic_count}")
    print(f"Compressed Memory Content: {built_agent._memory._episodic[0].content}")

    # 5. Cost Cap Protection (Budget Limit Exceeded mid-run)
    print("\n[4] Testing Cost Cap Protection (mid-run overrun):")
    # Let's set a CostCap of $0.03
    wf_capped = Workflow(cost_cap=CostCap(usd=0.03))
    
    # We add 3 sequential agents each costing $0.02.
    # Total cost for 2 steps is $0.04, which exceeds $0.03 limit.
    a1 = Agent(name="Step1Agent", provider=DemoLLMProvider(["Output 1"], cost_per_call=0.02))
    a2 = Agent(name="Step2Agent", provider=DemoLLMProvider(["Output 2"], cost_per_call=0.02))
    a3 = Agent(name="Step3Agent", provider=DemoLLMProvider(["Output 3"], cost_per_call=0.02))
    
    wf_capped.add(a1).add(a2).add(a3)
    
    # Run the capped workflow
    capped_result = wf_capped.run("Run sequentially under a strict budget cap")
    print(f"Workflow Completed : {capped_result.completed}")
    print(f"Total Cost Accrued : ${capped_result.total_cost_usd:.4f}")
    print(f"Blocked Agents     : {capped_result.blocked_nodes}")


if __name__ == "__main__":
    asyncio.run(main())
