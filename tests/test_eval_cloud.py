import pytest
from unittest.mock import patch, MagicMock

from meshflow.eval.runner import EvalSuite

@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    monkeypatch.setenv("MESHFLOW_API_KEY", "test-key")

class MockAgent:
    async def run(self, task, context=None):
        if "France" in task:
            return {"result": "The capital is Paris.", "tokens": 10, "stated_confidence": 0.9}
        if "Germany" in task:
            return {"result": "The capital is Berlin.", "tokens": 10, "stated_confidence": 0.9}
        return {"result": "Unknown", "tokens": 5, "stated_confidence": 0.1}

@patch("meshflow.cloud.dataset_hub.DatasetHub.pull")
@pytest.mark.asyncio
async def test_eval_suite_from_dataset_hub(mock_pull):
    # Mock the cloud returning two rows
    mock_pull.return_value = [
        {"input": "What is the capital of France?", "expected_output": "Paris", "metadata": {"lang": "en"}},
        {"input": "What is the capital of Germany?", "expected_output": "Berlin", "metadata": {"lang": "en"}},
        {"input": "What is the capital of Italy?", "expected_output": "Rome", "metadata": {"lang": "en"}},
    ]

    suite = EvalSuite.from_dataset_hub("geography_ds")
    
    # Verify suite construction
    assert suite.name == "geography_ds"
    assert len(suite.scenarios) == 3
    
    s0 = suite.scenarios[0]
    assert s0.name == "geography_ds_row_0"
    assert s0.input == "What is the capital of France?"
    assert "Paris" in s0.expected_contains
    assert s0.context == {"lang": "en"}

    # Run the suite against our MockAgent
    agent = MockAgent()
    result = await suite.run(agent)
    
    # 2 pass (Paris, Berlin), 1 fail (Rome)
    assert result.total == 3
    assert result.passed == 2
    assert result.failed == 1
    assert result.total_tokens == 25
    
    # Test reporting doesn't crash
    report = result.report()
    assert "geography_ds" in report
    assert "PASS" in report
    assert "FAIL" in report
