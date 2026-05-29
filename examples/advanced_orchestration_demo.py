"""Example demonstrating advanced orchestration capabilities:
1. Conversational Speaker Transition Graphs (GroupChat)
2. State Forking ("Time Travel")
3. Dynamic LLM-driven Agent Delegation
4. Docker-Sandboxed Code Execution
"""

import asyncio
from unittest.mock import AsyncMock
from meshflow import Agent, AgentRole
from meshflow.agents.conversation import GroupChat, GroupChatManager
from meshflow.core.durable import DurableWorkflowExecutor
from meshflow.core.node import NodeOutput
from meshflow.tools.code_interpreter import CodeInterpreter


async def run_transition_graphs_demo():
    print("\n--- 1. Conversational Transition Graphs Demo ---")
    
    # Define team members
    alice = Agent(name="Alice", role=AgentRole.PLANNER)
    bob = Agent(name="Bob", role=AgentRole.EXECUTOR)
    charlie = Agent(name="Charlie", role=AgentRole.CRITIC)
    
    # Define transition graph constraint:
    # Alice can only transition to Bob (Planner -> Coder/Executor)
    # Bob can only transition to Charlie (Coder -> Critic)
    # Charlie can only transition to Alice (Critic -> Planner)
    allowed_transitions = {
        "Alice": ["Bob"],
        "Bob": ["Charlie"],
        "Charlie": ["Alice"],
    }
    
    chat = GroupChat(
        agents=[alice, bob, charlie],
        speaker_selection="round_robin",
        allowed_transitions=allowed_transitions,
    )
    
    # Alice starts
    print(f"Current Speaker: Alice")
    next1 = chat._pick_next(last_speaker=alice)
    print(f"Alice transitions to: {next1.name} (Expected: Bob)")
    
    next2 = chat._pick_next(last_speaker=next1)
    print(f"Bob transitions to: {next2.name} (Expected: Charlie)")
    
    next3 = chat._pick_next(last_speaker=next2)
    print(f"Charlie transitions to: {next3.name} (Expected: Alice)")


async def run_delegation_demo():
    print("\n--- 2. Dynamic Agent Delegation Demo ---")
    
    # Create researcher delegate agent
    researcher = Agent(name="Researcher", role=AgentRole.RESEARCHER)
    
    # LeadPlanner agent has researcher as a delegate
    lead = Agent(name="LeadPlanner", role=AgentRole.PLANNER, delegates=[researcher])
    
    # Build the LeadPlanner agent to see dynamically registered tools
    built_lead = lead._build()
    
    # List registered tools
    tool_names = [t.name for t in built_lead._tools]
    print(f"LeadPlanner registered tools: {tool_names}")
    
    # Mock Researcher run logic to simulate dynamic subtask delegation
    researcher.run = AsyncMock(return_value={"result": "Research finding: Dynamic delegation is 100% functional."})
    
    # Invoke the generated delegation tool
    delegate_tool = next(t for t in built_lead._tools if t.name == "delegate_to_Researcher")
    print(f"Invoking {delegate_tool.name}...")
    result = await delegate_tool.call(task="Research dynamic delegation efficiency")
    print(f"Delegation tool output: '{result}'")


async def run_time_travel_demo():
    print("\n--- 3. State Forking ('Time Travel') Demo ---")
    
    # Initialize a DurableWorkflowExecutor using SQLite in-memory store
    executor = DurableWorkflowExecutor(run_id="original-run", backend="memory")
    
    # Manually populate checkpoints into the executor's store to simulate a run
    print(f"Saving historical checkpoints for 'original-run'...")
    executor._store.save("original-run", "fetch_data", NodeOutput(content="Fetched raw logs"))
    
    # Simulate a brief delay so timestamps are strictly sequential
    await asyncio.sleep(0.01)
    executor._store.save("original-run", "validate_data", NodeOutput(content="Validated logs"))
    
    await asyncio.sleep(0.01)
    executor._store.save("original-run", "process_data", NodeOutput(content="Processed logs"))
    
    # Time Travel: Fork before 'process_data' into 'fork-run-1'
    fork_run_id = "forked-run-branch"
    print(f"Forking 'original-run' to '{fork_run_id}' before 'process_data'...")
    forked_executor = executor.fork(parent_run_id="original-run", before_node_id="process_data", new_run_id=fork_run_id)
    
    # Load completed checkpoints from the forked run
    completed_nodes = list(forked_executor._store.all_completed(fork_run_id).keys())
    print(f"Forked run completed checkpoints: {completed_nodes} (Expected: ['fetch_data', 'validate_data'])")


async def run_docker_sandbox_demo():
    print("\n--- 4. Docker Code Execution Demo ---")
    
    # Initialise CodeInterpreter with docker execution enabled
    # Falls back to local subprocess if Docker is not available in the run environment
    interpreter = CodeInterpreter(docker=True, docker_image="python:3.11-slim")
    
    # If docker executable is found, it executes inside the container; otherwise runs locally
    python_code = "import sys; print(f'Running Python version {sys.version_info.major}.{sys.version_info.minor} inside container')"
    print(f"Executing Python script via Docker-sandboxed interpreter...")
    result = interpreter.run(python_code)
    print(f"Code execution result:\n{str(result).strip()}")


async def main():
    print("=" * 60)
    print("MeshFlow Advanced Orchestration Demo (Parity with Autogen/LangGraph/CrewAI)")
    print("=" * 60)
    await run_transition_graphs_demo()
    await run_delegation_demo()
    await run_time_travel_demo()
    await run_docker_sandbox_demo()
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
