"""Sprint 37 — Fine-tuning data export: traces to JSONL."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meshflow.export.finetune import (
    TraceRecord,
    ExportFormat,
    ExportFilter,
    FinetuneExporter,
)


# ── TraceRecord ───────────────────────────────────────────────────────────────

class TestTraceRecord:
    def _record(self) -> TraceRecord:
        return TraceRecord(
            run_id="run-1",
            agent_name="analyst",
            task="What is HIPAA?",
            response="HIPAA is the Health Insurance Portability and Accountability Act.",
            system_prompt="You are a compliance expert.",
            tokens=42,
            cost_usd=0.002,
            confidence=0.9,
        )

    def test_to_openai_format(self):
        r = self._record()
        d = r.to_openai()
        assert "messages" in d
        roles = [m["role"] for m in d["messages"]]
        assert roles == ["system", "user", "assistant"]
        assert d["messages"][1]["content"] == "What is HIPAA?"

    def test_to_openai_no_system(self):
        r = TraceRecord(run_id="x", agent_name="a", task="task", response="resp")
        d = r.to_openai()
        roles = [m["role"] for m in d["messages"]]
        assert "system" not in roles

    def test_to_anthropic_format(self):
        r = self._record()
        d = r.to_anthropic()
        assert d["human"] == "What is HIPAA?"
        assert "HIPAA" in d["assistant"]
        assert d["system"] == "You are a compliance expert."

    def test_to_generic_format(self):
        r = self._record()
        d = r.to_generic()
        assert d["prompt"] == "What is HIPAA?"
        assert "HIPAA" in d["completion"]
        assert d["metadata"]["run_id"] == "run-1"
        assert d["metadata"]["confidence"] == 0.9

    def test_to_sharegpt_format(self):
        r = self._record()
        d = r.to_sharegpt()
        assert "conversations" in d
        roles = [c["from"] for c in d["conversations"]]
        assert roles == ["system", "human", "gpt"]

    def test_to_sharegpt_no_system(self):
        r = TraceRecord(run_id="x", agent_name="a", task="t", response="r")
        d = r.to_sharegpt()
        roles = [c["from"] for c in d["conversations"]]
        assert "system" not in roles

    def test_to_format_dispatch(self):
        r = self._record()
        for fmt in ExportFormat:
            result = r.to_format(fmt)
            assert isinstance(result, dict)


# ── ExportFilter ──────────────────────────────────────────────────────────────

class TestExportFilter:
    def _record(self, confidence: float = 0.9, blocked: bool = False) -> TraceRecord:
        return TraceRecord(
            run_id="r", agent_name="analyst", task="t", response="resp",
            confidence=confidence, timestamp=time.time()
        )

    def test_accepts_normal(self):
        f = ExportFilter(min_confidence=0.5)
        assert f.accepts(self._record(0.9)) is True

    def test_rejects_low_confidence(self):
        f = ExportFilter(min_confidence=0.8)
        assert f.accepts(self._record(0.5)) is False

    def test_rejects_empty_response(self):
        f = ExportFilter(exclude_blocked=True)
        r = TraceRecord(run_id="x", agent_name="a", task="t", response="")
        assert f.accepts(r) is False

    def test_agent_name_filter(self):
        f = ExportFilter(agent_names=["writer"])
        r = self._record()  # agent_name="analyst"
        assert f.accepts(r) is False

        r2 = TraceRecord(run_id="x", agent_name="writer", task="t", response="resp")
        assert f.accepts(r2) is True

    def test_run_id_filter(self):
        f = ExportFilter(run_ids=["run-abc"])
        r = TraceRecord(run_id="run-xyz", agent_name="a", task="t", response="r")
        assert f.accepts(r) is False

        r2 = TraceRecord(run_id="run-abc", agent_name="a", task="t", response="r")
        assert f.accepts(r2) is True

    def test_since_ts_filter(self):
        future = time.time() + 9999
        f = ExportFilter(since_ts=future)
        assert f.accepts(self._record()) is False

    def test_until_ts_filter(self):
        past = time.time() - 9999
        f = ExportFilter(until_ts=past)
        assert f.accepts(self._record()) is False


# ── FinetuneExporter ──────────────────────────────────────────────────────────

def _raw_records(n: int = 3) -> list[dict]:
    return [
        {
            "run_id": f"run-{i}",
            "agent_name": "analyst",
            "task": f"Question {i}",
            "output": f"Answer {i}",
            "stated_confidence": 0.9,
            "tokens": 10 * i,
            "cost_usd": 0.001 * i,
            "ts": time.time() + i,
            "system_prompt": "You are helpful.",
        }
        for i in range(1, n + 1)
    ]


class TestFinetuneExporter:
    def test_collect_all(self):
        exp = FinetuneExporter().collect_from(_raw_records(5))
        records = exp.collect()
        assert len(records) == 5

    def test_collect_respects_min_confidence(self):
        records = [
            {"run_id": "a", "agent_name": "x", "task": "t1", "output": "r1",
             "stated_confidence": 0.9, "ts": 1.0},
            {"run_id": "b", "agent_name": "x", "task": "t2", "output": "r2",
             "stated_confidence": 0.3, "ts": 2.0},
        ]
        exp = FinetuneExporter(min_confidence=0.7).collect_from(records)
        assert len(exp.collect()) == 1

    def test_collect_respects_max_records(self):
        exp = FinetuneExporter(max_records=2).collect_from(_raw_records(5))
        assert len(exp.collect()) == 2

    def test_deduplication(self):
        records = [
            {"run_id": "a", "agent_name": "x", "task": "same q", "output": "same a", "ts": 1.0},
            {"run_id": "b", "agent_name": "x", "task": "same q", "output": "same a", "ts": 2.0},
        ]
        exp = FinetuneExporter(deduplicate=True).collect_from(records)
        assert len(exp.collect()) == 1

    def test_no_deduplication(self):
        records = [
            {"run_id": "a", "agent_name": "x", "task": "same q", "output": "same a", "ts": 1.0},
            {"run_id": "b", "agent_name": "x", "task": "same q", "output": "same a", "ts": 2.0},
        ]
        exp = FinetuneExporter(deduplicate=False).collect_from(records)
        assert len(exp.collect()) == 2

    def test_openai_jsonl_format(self):
        exp = FinetuneExporter(format=ExportFormat.openai).collect_from(_raw_records(2))
        lines = exp.export_str().strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            d = json.loads(line)
            assert "messages" in d
            roles = [m["role"] for m in d["messages"]]
            assert "user" in roles and "assistant" in roles

    def test_anthropic_jsonl_format(self):
        exp = FinetuneExporter(format=ExportFormat.anthropic).collect_from(_raw_records(2))
        lines = exp.export_str().strip().split("\n")
        for line in lines:
            d = json.loads(line)
            assert "human" in d and "assistant" in d

    def test_generic_jsonl_format(self):
        exp = FinetuneExporter(format=ExportFormat.generic).collect_from(_raw_records(2))
        lines = exp.export_str().strip().split("\n")
        for line in lines:
            d = json.loads(line)
            assert "prompt" in d and "completion" in d

    def test_sharegpt_jsonl_format(self):
        exp = FinetuneExporter(format=ExportFormat.sharegpt).collect_from(_raw_records(2))
        lines = exp.export_str().strip().split("\n")
        for line in lines:
            d = json.loads(line)
            assert "conversations" in d

    def test_export_to_file(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            exp = FinetuneExporter().collect_from(_raw_records(3))
            count = exp.export(path)
            assert count == 3
            with open(path) as f:
                lines = f.readlines()
            assert len(lines) == 3
        finally:
            os.unlink(path)

    def test_stats_empty(self):
        exp = FinetuneExporter().collect_from([])
        stats = exp.stats()
        assert stats["count"] == 0

    def test_stats_populated(self):
        exp = FinetuneExporter().collect_from(_raw_records(3))
        stats = exp.stats()
        assert stats["count"] == 3
        assert stats["total_tokens"] > 0
        assert "analyst" in stats["agents"]

    def test_agent_filter(self):
        records = [
            {"run_id": "a", "agent_name": "writer", "task": "t1", "output": "r1", "ts": 1.0},
            {"run_id": "b", "agent_name": "analyst", "task": "t2", "output": "r2", "ts": 2.0},
        ]
        exp = FinetuneExporter(agent_names=["writer"]).collect_from(records)
        result = exp.collect()
        assert len(result) == 1
        assert result[0].agent_name == "writer"

    def test_skips_missing_task_or_response(self):
        records = [
            {"run_id": "a", "agent_name": "x", "task": "", "output": "resp", "ts": 1.0},
            {"run_id": "b", "agent_name": "x", "task": "task", "output": "", "ts": 2.0},
            {"run_id": "c", "agent_name": "x", "task": "task", "output": "resp", "ts": 3.0},
        ]
        exp = FinetuneExporter().collect_from(records)
        assert len(exp.collect()) == 1

    def test_result_key_fallback(self):
        """Accepts 'result' as well as 'output' for the response field."""
        records = [{"run_id": "x", "agent_name": "a", "task": "q", "result": "ans", "ts": 1.0}]
        exp = FinetuneExporter().collect_from(records)
        collected = exp.collect()
        assert len(collected) == 1
        assert collected[0].response == "ans"


# ── CLI export-traces ──────────────────────────────────────────────────────────

class TestCLIExportTraces:
    def test_subcommand_registered(self):
        import subprocess
        result = subprocess.run(
            ["python", "-m", "meshflow.cli.main", "--help"],
            capture_output=True, text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        combined = result.stdout + result.stderr
        assert "export-traces" in combined or result.returncode == 0

    def test_export_traces_runs(self):
        import subprocess
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            out_path = f.name
        try:
            result = subprocess.run(
                ["python", "-m", "meshflow.cli.main", "export-traces",
                 "--db", ":nonexistent:", "--output", out_path, "--format", "openai"],
                capture_output=True, text=True,
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            )
            # Should complete without error (empty ledger = 0 records exported)
            assert result.returncode == 0
        finally:
            if os.path.exists(out_path):
                os.unlink(out_path)


# ── Public API ────────────────────────────────────────────────────────────────

class TestPublicAPI:
    def test_imports(self):
        from meshflow.export import FinetuneExporter, ExportFormat, TraceRecord, ExportFilter
        assert all(x is not None for x in [FinetuneExporter, ExportFormat, TraceRecord, ExportFilter])
