import json
import urllib.error
from unittest.mock import patch, MagicMock

import pytest

from meshflow.cloud.dataset_hub import DatasetHub


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    monkeypatch.setenv("MESHFLOW_API_KEY", "test-key")
    monkeypatch.setenv("MESHFLOW_CLOUD_URL", "https://api.meshflow.test")
    monkeypatch.setenv("MESHFLOW_CLOUD_ENABLED", "1")


def test_dataset_hub_disabled_without_key(monkeypatch):
    monkeypatch.delenv("MESHFLOW_API_KEY", raising=False)
    assert DatasetHub.push("ds1", [{"input": "test"}]) is False
    assert DatasetHub.pull("ds1") == []
    assert DatasetHub.list() == []
    assert DatasetHub.delete("ds1") is False


@patch("urllib.request.urlopen")
def test_dataset_hub_push_success(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"id": "ds-123", "ok": True}).encode()
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    result = DatasetHub.push("my_dataset", [{"input": "test", "expected_output": "test_out"}])

    assert result is True
    mock_urlopen.assert_called_once()
    req = mock_urlopen.call_args[0][0]
    assert req.method == "POST"
    assert "api/ingest/datasets" in req.full_url
    assert req.get_header("X-meshflow-key") == "test-key"


@patch("urllib.request.urlopen")
def test_dataset_hub_pull_success(mock_urlopen):
    mock_resp = MagicMock()
    payload = {
        "rows": [
            {"input": "in1", "expected_output": "out1"},
            {"input": "in2", "expected_output": "out2"},
        ]
    }
    mock_resp.read.return_value = json.dumps(payload).encode()
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    rows = DatasetHub.pull("my_dataset")
    assert len(rows) == 2
    assert rows[0]["input"] == "in1"

    req = mock_urlopen.call_args[0][0]
    assert req.method == "GET"
    assert "name=my_dataset" in req.full_url


@patch("urllib.request.urlopen")
def test_dataset_hub_list_success(mock_urlopen):
    mock_resp = MagicMock()
    payload = [
        {"name": "ds1", "row_count": 10},
        {"name": "ds2", "row_count": 5},
    ]
    mock_resp.read.return_value = json.dumps(payload).encode()
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    datasets = DatasetHub.list()
    assert len(datasets) == 2
    assert datasets[0]["name"] == "ds1"

    req = mock_urlopen.call_args[0][0]
    assert req.method == "GET"


@patch("urllib.request.urlopen")
def test_dataset_hub_delete_success(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"ok": True}).encode()
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    assert DatasetHub.delete("my_dataset") is True
    req = mock_urlopen.call_args[0][0]
    assert req.method == "DELETE"


@patch("urllib.request.urlopen")
def test_dataset_hub_network_error(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.URLError("connection refused")

    assert DatasetHub.push("ds", []) is False
    assert DatasetHub.pull("ds") == []
    assert DatasetHub.list() == []
    assert DatasetHub.delete("ds") is False
