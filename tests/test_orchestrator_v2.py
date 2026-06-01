import asyncio
import pytest
from meshflow import WorkflowDefinition, MeshNode, NodeOutput, StepRuntime
from meshflow.core.runtime import RuntimeOutcome, StepRecord

class DummyRuntime(StepRuntime):
    def __init__(self, run_id="test-run"):
        self._run_id = run_id
        # Minimal mock ledger if needed
        self._ledger = None

    async def run(self, node, node_input, context):
        # We can implement a custom mock runner logic per node kind or node metadata
        # By default, call the node's runner
        try:
            return RuntimeOutcome(
                ok=True,
                node_id=node.id,
                node_kind=node.kind.value,
                output=await node.run(node_input),
                record=StepRecord(
                    run_id=self._run_id,
                    step_id="step-1",
                    node_id=node.id,
                    node_kind=node.kind.value,
                    input_task=node_input.task,
                    output_content="",
                    verdict="approved",
                    blocked=False,
                    block_reason="",
                    uncertainty=0.0,
                    cost_usd=0.0,
                    tokens_used=0,
                    carbon_gco2=0.0,
                    duration_ms=0.0,
                    timestamp="",
                ),
                blocked_by="",
                paused_for_human=False,
                human_context={}
            )
        except Exception as e:
            raise e

@pytest.mark.asyncio
async def test_context_bus_merge_strategies():
    # Define a workflow with parallel nodes writing to common keys
    wf = WorkflowDefinition(name="context_bus_test")
    wf.context_bus = {
        "merge_strategies": {
            "list_key": "append",
            "dict_key": "append",
            "str_key": "append",
            "conf_key": "select_highest_confidence",
            "and_key": "logical_and",
            "or_key": "logical_or"
        }
    }

    async def run_node_a(task, ctx):
        return NodeOutput(
            content="A",
            confidence=0.9,
            structured={
                "list_key": [1, 2],
                "dict_key": {"a": 1},
                "str_key": "Hello",
                "conf_key": "value_a",
                "and_key": True,
                "or_key": False
            }
        )

    async def run_node_b(task, ctx):
        return NodeOutput(
            content="B",
            confidence=0.7,
            structured={
                "list_key": [3, 4],
                "dict_key": {"b": 2},
                "str_key": "World",
                "conf_key": "value_b",
                "and_key": False,
                "or_key": True
            }
        )

    node_a = MeshNode.from_callable("node_a", run_node_a)
    node_b = MeshNode.from_callable("node_b", run_node_b)

    wf.add_node(node_a)
    wf.add_node(node_b)

    # They run in parallel
    wf.set_terminal("node_a", "node_b")

    runtime = DummyRuntime()
    result = await wf.run(task="test context bus", runtime=runtime)

    # Verify updates in shared context after merge
    ctx = result.steps[0].record.run_id  # Just checking how ctx is returned or accumulated in result
    # The actual context is passed by reference, but we want to assert on the context modified inside run()
    # Let's inspect the last step or we can check the context passed. Wait, run() returns WorkflowResult.
    # To check the final context, we can evaluate on a downstream node, or we can check the inner context by running a downstream node.
    # Let's add a downstream node "synthesizer" to inspect the context!
    context_captured = {}

    async def run_synthesizer(task, ctx):
        context_captured.update(ctx)
        return "Done"

    synthesizer = MeshNode.from_callable("synthesizer", run_synthesizer)
    wf.add_node(synthesizer)
    wf.add_edge("node_a", "synthesizer")
    wf.add_edge("node_b", "synthesizer")
    wf.set_terminal("synthesizer")

    result = await wf.run(task="test context bus", runtime=runtime)
    assert result.completed is True

    # Check context bus merged values
    assert context_captured["list_key"] == [1, 2, 3, 4] or context_captured["list_key"] == [3, 4, 1, 2]
    assert context_captured["dict_key"] == {"a": 1, "b": 2}
    assert "Hello" in context_captured["str_key"] and "World" in context_captured["str_key"]
    assert context_captured["conf_key"] == "value_a"  # 0.9 confidence vs 0.7 confidence
    assert context_captured["and_key"] is False
    assert context_captured["or_key"] is True


@pytest.mark.asyncio
async def test_branch_timeouts_and_retries():
    wf = WorkflowDefinition(name="timeout_retry_test")
    
    attempts = 0
    async def run_flaky_node(task, ctx):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            await asyncio.sleep(2.0)  # Will timeout
            return "Timeout"
        return "Success"

    node = MeshNode.from_callable("flaky_node", run_flaky_node)
    # Set timeout to 0.1s and max_retries to 3
    node.metadata["timeout_s"] = 0.1
    node.metadata["max_retries"] = 3
    node.metadata["retry_on_fail"] = True

    wf.add_node(node)
    wf.set_terminal("flaky_node")

    runtime = DummyRuntime()
    result = await wf.run(task="test timeout", runtime=runtime)

    assert result.completed is True
    assert attempts == 3  # Failed twice (timeouts), succeeded on 3rd attempt
    assert result.output == "Success"


@pytest.mark.asyncio
async def test_decentralized_handoff():
    wf = WorkflowDefinition(name="handoff_test")

    async def run_router(task, ctx):
        # Dynamically hands off to worker_b, bypassing static successor worker_a
        return NodeOutput(
            content="Route to B",
            structured={"next_node": "worker_b"}
        )

    async def run_worker_a(task, ctx):
        return "Worker A run"

    async def run_worker_b(task, ctx):
        return "Worker B run"

    router = MeshNode.from_callable("router", run_router)
    worker_a = MeshNode.from_callable("worker_a", run_worker_a)
    worker_b = MeshNode.from_callable("worker_b", run_worker_b)

    wf.add_node(router).add_node(worker_a).add_node(worker_b)
    
    # Static edge router -> worker_a
    wf.add_edge("router", "worker_a")
    
    # worker_b is not statically connected from router, but is a terminal node
    wf.set_terminal("worker_a", "worker_b")

    runtime = DummyRuntime()
    result = await wf.run(task="handoff", runtime=runtime)

    # worker_a should be skipped because of the handoff bypassing static successors.
    # worker_b should be executed because it was dynamically targeted.
    completed_nodes = [step.node_id for step in result.steps if step.ok]
    assert "router" in completed_nodes
    assert "worker_b" in completed_nodes
    assert "worker_a" not in completed_nodes
    assert "worker_a" in result.skipped_nodes


@pytest.mark.asyncio
async def test_fan_in_rules():
    # We want to test "any" and "majority" fan-in rules
    # Let's create a fan-out of 3 nodes: node_1 (success), node_2 (fails), node_3 (fails)
    # synthesizer_any has fan_in_rule: "any". It should execute.
    # synthesizer_majority has fan_in_rule: "majority". It should be skipped/failed.
    wf = WorkflowDefinition(name="fan_in_test")

    async def run_success(task, ctx):
        return "OK"

    async def run_fail(task, ctx):
        raise ValueError("Failed")

    n1 = MeshNode.from_callable("n1", run_success)
    n2 = MeshNode.from_callable("n2", run_fail)
    n3 = MeshNode.from_callable("n3", run_fail)

    # Synthesizers
    async def run_synth(task, ctx):
        return "Synth OK"

    synth_any = MeshNode.from_callable("synth_any", run_synth)
    synth_any.metadata["fan_in_rule"] = "any"

    synth_maj = MeshNode.from_callable("synth_maj", run_synth)
    synth_maj.metadata["fan_in_rule"] = "majority"

    wf.add_node(n1).add_node(n2).add_node(n3)
    wf.add_node(synth_any).add_node(synth_maj)

    # n1, n2, n3 -> synth_any
    wf.add_edge("n1", "synth_any")
    wf.add_edge("n2", "synth_any")
    wf.add_edge("n3", "synth_any")

    # n1, n2, n3 -> synth_maj
    wf.add_edge("n1", "synth_maj")
    wf.add_edge("n2", "synth_maj")
    wf.add_edge("n3", "synth_maj")

    wf.set_terminal("synth_any", "synth_maj")

    runtime = DummyRuntime()
    result = await wf.run(task="fan-in", runtime=runtime)

    completed_nodes = [step.node_id for step in result.steps if step.ok]
    assert "n1" in completed_nodes
    assert "synth_any" in completed_nodes
    assert "synth_maj" not in completed_nodes
    assert "synth_maj" in result.skipped_nodes


@pytest.mark.asyncio
async def test_dynamic_replanning():
    wf = WorkflowDefinition(name="replan_test")

    new_yaml = """
name: replan_test
policy:
  max_replans: 3
nodes:
  start: {kind: python, ref: run_start}
  new_node: {kind: python, ref: run_new_node}
edges:
  - start -> new_node
terminal:
  - new_node
"""

    async def run_start(task, ctx):
        return NodeOutput(
            content="Replanning...",
            structured={"replanned_workflow_yaml": new_yaml}
        )

    async def run_new_node(task, ctx):
        return "New Node Success"

    start_node = MeshNode.from_callable("start", run_start)

    wf._node_registry = {"run_start": run_start, "run_new_node": run_new_node}
    wf.add_node(start_node)
    wf.set_terminal("start")

    runtime = DummyRuntime()
    result = await wf.run(task="replan", runtime=runtime)

    assert result.completed is True
    completed_nodes = [step.node_id for step in result.steps if step.ok]
    assert "start" in completed_nodes
    assert "new_node" in completed_nodes
    assert result.output == "New Node Success"
