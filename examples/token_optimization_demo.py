"""Example demonstrating Token-Optimized Development capabilities:
1. Design-time Planning (TokenBudgetPlanner & ModelSizingAdvisor)
2. Enforcing constraints with the @token_budget decorator
3. Dynamic Model Degradation (switching to fallback models under budget pressure)
4. Prompt History Compression and context trimming
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from meshflow import Agent, AgentRole, token_budget
from meshflow.optimization.planner import TokenBudgetPlanner, ModelSizingAdvisor
from meshflow.optimization.tracker import OptimizationTracker


def run_design_time_planning_demo():
    print("\n--- 1. Design-Time Token Planning & Model Sizing Advisor ---")
    
    planner = TokenBudgetPlanner()
    advisor = ModelSizingAdvisor()
    
    # 1. Estimate prompt tokens
    system_prompt = "You are an expert software engineer specializing in system architecture."
    user_query = "Please design a high-throughput transaction ledger system using event sourcing."
    
    print(f"System Prompt: '{system_prompt}'")
    print(f"User Query: '{user_query}'")
    
    sys_tokens = planner.estimate_tokens(system_prompt)
    query_tokens = planner.estimate_tokens(user_query)
    print(f"Estimated System Prompt Tokens: {sys_tokens}")
    print(f"Estimated User Query Tokens: {query_tokens}")
    
    # 2. Plan a complete agent turn budget
    class DummyTool:
        name = "query_database"
        description = "Runs SQL queries against the core banking database schema."
        
    tools = [DummyTool()]
    messages = [{"role": "user", "content": user_query}]
    
    plan = planner.plan_budget(system_prompt, messages, tools=tools)
    print("\nPre-Run Estimated Turn Budget:")
    print(f"  - System Prompt Tokens: {plan['system_tokens']}")
    print(f"  - Message Tokens:       {plan['message_tokens']}")
    print(f"  - Tool Schema Tokens:   {plan['tool_tokens']}")
    print(f"  - Total Estimated Input:{plan['total_estimated_in']}")

    # 3. Request a Model Recommendation based on task complexity
    task_simple = "Summarize this ticket"
    task_complex = "Diagnose and optimize this memory leak in python subprocess execution"
    
    rec_simple = advisor.recommend_model(task_simple)
    rec_complex = advisor.recommend_model(task_complex)
    
    print(f"\nModel Recommendations:")
    print(f"  - Simple task ('{task_simple}'): -> Recommend {rec_simple}")
    print(f"  - Complex task ('{task_complex}'): -> Recommend {rec_complex}")


async def run_decorator_and_degradation_demo():
    print("\n--- 2. Runtime Budget Enforcement & Model Degradation ---")

    # Define an agent wrapped with the @token_budget decorator
    # If budget headroom falls below 25%, it swaps to the cheaper fallback model
    @token_budget(max_tokens=1000, action="downgrade", fallback_model="claude-haiku-3-5")
    async def execute_task_with_budget(task: str, context=None):
        agent = Agent(name="BudgetConsciousAgent", role=AgentRole.RESEARCHER)
        
        # Mock provider to inspect what model was used
        provider_mock = MagicMock()
        provider_mock.complete = AsyncMock(return_value=("Mocked execution completed.", 50, 0.0005))
        agent.provider = provider_mock
        
        # Trigger run
        print(f"Executing task: '{task}'")
        await agent.run(task, context=context)
        
        # Inspect model used
        called_model = provider_mock.complete.call_args[1]["model"]
        tracker = context["_optimization_tracker"]
        print(f"  - Tokens Consumed: {tracker.consumed_tokens}/{tracker.max_tokens}")
        print(f"  - Model Selected by runtime: {called_model}")
        return tracker
        
    # Scenario A: First run under budget
    print("Scenario A: Initial execution starts with high headroom")
    context_a = {}
    tracker_a = await execute_task_with_budget("Generate simple audit summary.", context=context_a)
    
    # Scenario B: High token consumption -> Model degradation triggered
    print("\nScenario B: Token budget is heavily depleted (>= 75% consumed)")
    context_b = {}
    # Pre-populate usage to simulate previous heavy turns (e.g. 800 tokens out of 1000)
    tracker_dummy = OptimizationTracker(max_tokens=1000, fallback_model="claude-haiku-3-5")
    tracker_dummy.add_usage(800, 0.008)
    context_b["_optimization_tracker"] = tracker_dummy
    
    await execute_task_with_budget("Generate detailed database migration plan.", context=context_b)


def run_prompt_compression_demo():
    print("\n--- 3. Prompt History & Context Compression ---")
    
    tracker = OptimizationTracker()
    system_prompt = "You are a helpful customer support agent."
    
    # Generate a long message history
    history = [
        {"role": "user", "content": "Hello, my order is missing."},
        {"role": "assistant", "content": "I can help with that. Can you provide the order ID?"},
        {"role": "user", "content": "Yes, it is ID-48932."},
        {"role": "assistant", "content": "Thanks. I see it was shipped yesterday."},
        {"role": "user", "content": "Great, can I get a tracking link?"},
        {"role": "assistant", "content": "Sure, here is the link: tracking.com/123"},
        {"role": "user", "content": "Can you also explain the return policy?"},
    ]
    
    print(f"Original conversation history size: {len(history)} turns.")
    
    # Compress the prompt history using tracker rules
    sys_out, compressed_history = tracker.compress_prompt(system_prompt, history)
    
    print(f"Compressed history size: {len(compressed_history)} turns.")
    print("Kept turns:")
    for turn in compressed_history:
        print(f"  - [{turn['role'].upper()}]: '{turn['content']}'")


async def main():
    print("=" * 60)
    print("MeshFlow Token-Optimized Development Showcase")
    print("=" * 60)
    run_design_time_planning_demo()
    await run_decorator_and_degradation_demo()
    run_prompt_compression_demo()
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
