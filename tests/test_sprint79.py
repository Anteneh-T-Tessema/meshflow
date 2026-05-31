from meshflow import Workflow, Agent, CostCap, WorkflowResult, EchoProvider

def test_7_line_promise_sandbox():
    # 7 lines. Production-safe. No configuration.
    wf = Workflow(cost_cap=CostCap(usd=5.00), mode="sandbox")
    wf.add(Agent('researcher'), Agent('analyst'), Agent('writer'))
    result = wf.run('Write a competitive analysis of Flowise')

    # Verify that printing the result returns the final agent's text output directly
    assert isinstance(result, WorkflowResult)
    output_str = str(result)
    assert "[sandbox: Writer]" in output_str
    assert "Simulated completion for task:" in output_str

    # Verify sandbox behavior (zero real token cost, successful run)
    assert result.completed is True
    assert result.total_cost_usd == 0.0
    assert result.total_tokens > 0  # SandboxProvider generates length estimates

    # Verify that the steps were recorded
    assert len(result.steps) == 3
    for outcome in result.steps:
        assert outcome.ok is True
        assert outcome.record.cost_usd == 0.0

def test_cost_cap_enforcement():
    # Set an impossibly low cost cap (e.g. -1.0 usd)
    wf = Workflow(cost_cap=CostCap(usd=-1.00), mode="production")
    
    # We use EchoProvider to avoid real LLM credentials/network calls.
    # The budget pre-check will block it immediately since 0.0 usd spent > -1.0 usd budget.
    wf.add(Agent('researcher', provider=EchoProvider()))
    
    result = wf.run('Any task')
    
    # Budget violation marks the run as not completed and blocks the node
    assert result.completed is False
    assert "researcher" in result.blocked_nodes
    # Verify that the first step recorded the budget block reason
    assert len(result.steps) == 1
    assert result.steps[0].ok is False
    assert "budget" in result.steps[0].blocked_by
