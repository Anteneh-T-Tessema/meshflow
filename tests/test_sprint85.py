"""Sprint 85 — router persistence, YAML config, RouterReport, routing-report CLI."""
from __future__ import annotations

import json
import os
import tempfile
import pytest

os.environ.setdefault("MESHFLOW_MOCK", "1")


# ── helpers ───────────────────────────────────────────────────────────────────

def _router(smart=0.33, large=0.67, exploration_rate=0.0, adapt_mode="manual"):
    from meshflow import AdaptiveModelTierRouter, ModelTier, RouterOutcomeStore
    return AdaptiveModelTierRouter(
        tiers=[
            ModelTier("fast",  "llama3.2",  max_tokens=512),
            ModelTier("smart", "mistral",   max_tokens=2048),
            ModelTier("large", "gpt-4o",    max_tokens=4096),
        ],
        smart_threshold=smart,
        large_threshold=large,
        adapt_every=50,
        exploration_rate=exploration_rate,
        adapt_mode=adapt_mode,
        store=RouterOutcomeStore(path=":memory:"),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# snapshot() / save() / load()
# ═══════════════════════════════════════════════════════════════════════════════

class TestRouterSnapshot:
    def test_snapshot_contains_thresholds(self):
        r = _router(smart=0.25, large=0.55)
        snap = r.snapshot()
        assert snap["smart_threshold"] == pytest.approx(0.25)
        assert snap["large_threshold"] == pytest.approx(0.55)

    def test_snapshot_contains_tiers(self):
        r = _router()
        snap = r.snapshot()
        names = [t["name"] for t in snap["tiers"]]
        assert names == ["fast", "smart", "large"]

    def test_snapshot_contains_route_count(self):
        r = _router()
        r.route("t1")
        r.route("t2")
        assert r.snapshot()["route_count"] == 2

    def test_snapshot_is_json_serialisable(self):
        r = _router()
        snap = r.snapshot()
        json.dumps(snap)  # must not raise

    def test_save_creates_file(self, tmp_path):
        r = _router(smart=0.28, large=0.60)
        path = str(tmp_path / "router.json")
        r.save(path)
        assert os.path.exists(path)

    def test_save_then_load_restores_thresholds(self, tmp_path):
        r = _router(smart=0.28, large=0.60)
        r.route("task")  # bump route_count
        path = str(tmp_path / "router.json")
        r.save(path)

        from meshflow import AdaptiveModelTierRouter, RouterOutcomeStore
        r2 = AdaptiveModelTierRouter.load(path, store=RouterOutcomeStore(path=":memory:"))
        assert r2._smart == pytest.approx(0.28)
        assert r2._large == pytest.approx(0.60)
        assert r2._route_count == 1

    def test_load_restores_tier_definitions(self, tmp_path):
        r = _router()
        path = str(tmp_path / "router.json")
        r.save(path)

        from meshflow import AdaptiveModelTierRouter, RouterOutcomeStore
        r2 = AdaptiveModelTierRouter.load(path, store=RouterOutcomeStore(path=":memory:"))
        tiers = r2.tiers()
        assert len(tiers) == 3
        assert tiers[0].model == "llama3.2"
        assert tiers[2].model == "gpt-4o"

    def test_load_override_is_honoured(self, tmp_path):
        r = _router(smart=0.28)
        path = str(tmp_path / "router.json")
        r.save(path)

        from meshflow import AdaptiveModelTierRouter, RouterOutcomeStore
        r2 = AdaptiveModelTierRouter.load(
            path, smart_threshold=0.40, store=RouterOutcomeStore(path=":memory:")
        )
        assert r2._smart == pytest.approx(0.40)   # override wins

    def test_load_missing_file_raises(self, tmp_path):
        from meshflow import AdaptiveModelTierRouter
        with pytest.raises(FileNotFoundError):
            AdaptiveModelTierRouter.load(str(tmp_path / "nonexistent.json"))

    def test_save_is_atomic(self, tmp_path):
        """save() writes .tmp then replaces — no partial file left on success."""
        r = _router()
        path = str(tmp_path / "router.json")
        r.save(path)
        assert not os.path.exists(path + ".tmp")
        assert os.path.exists(path)


# ═══════════════════════════════════════════════════════════════════════════════
# to_yaml() / from_yaml()
# ═══════════════════════════════════════════════════════════════════════════════

class TestRouterYaml:
    def test_to_yaml_creates_file(self, tmp_path):
        r = _router()
        path = str(tmp_path / "router.yaml")
        r.to_yaml(path)
        assert os.path.exists(path)

    def test_to_yaml_contains_thresholds(self, tmp_path):
        r = _router(smart=0.30, large=0.65)
        path = str(tmp_path / "router.yaml")
        r.to_yaml(path)
        content = open(path).read()
        assert "smart_threshold: 0.3" in content
        assert "large_threshold: 0.65" in content

    def test_to_yaml_contains_tier_models(self, tmp_path):
        r = _router()
        path = str(tmp_path / "router.yaml")
        r.to_yaml(path)
        content = open(path).read()
        assert "llama3.2" in content
        assert "gpt-4o" in content

    def test_from_yaml_roundtrip_thresholds(self, tmp_path):
        r = _router(smart=0.27, large=0.61)
        path = str(tmp_path / "router.yaml")
        r.to_yaml(path)

        from meshflow import AdaptiveModelTierRouter, RouterOutcomeStore
        r2 = AdaptiveModelTierRouter.from_yaml(path, store=RouterOutcomeStore(path=":memory:"))
        assert r2._smart == pytest.approx(0.27)
        assert r2._large == pytest.approx(0.61)

    def test_from_yaml_roundtrip_tiers(self, tmp_path):
        r = _router()
        path = str(tmp_path / "router.yaml")
        r.to_yaml(path)

        from meshflow import AdaptiveModelTierRouter, RouterOutcomeStore
        r2 = AdaptiveModelTierRouter.from_yaml(path, store=RouterOutcomeStore(path=":memory:"))
        tiers = r2.tiers()
        assert len(tiers) == 3
        assert tiers[0].name == "fast"
        assert tiers[0].model == "llama3.2"

    def test_from_yaml_is_local_preserved(self, tmp_path):
        from meshflow import AdaptiveModelTierRouter, ModelTier, RouterOutcomeStore
        r = AdaptiveModelTierRouter(
            tiers=[
                ModelTier("fast",  "corp-llm",  max_tokens=512,  is_local=True),
                ModelTier("large", "gpt-4o",    max_tokens=4096, is_local=False),
            ],
            store=RouterOutcomeStore(path=":memory:"),
        )
        path = str(tmp_path / "router.yaml")
        r.to_yaml(path)

        r2 = AdaptiveModelTierRouter.from_yaml(path, store=RouterOutcomeStore(path=":memory:"))
        tiers = r2.tiers()
        assert tiers[0].is_local is True
        assert tiers[1].is_local is False

    def test_from_yaml_override_works(self, tmp_path):
        r = _router(smart=0.33)
        path = str(tmp_path / "router.yaml")
        r.to_yaml(path)

        from meshflow import AdaptiveModelTierRouter, RouterOutcomeStore
        r2 = AdaptiveModelTierRouter.from_yaml(
            path, smart_threshold=0.45, store=RouterOutcomeStore(path=":memory:")
        )
        assert r2._smart == pytest.approx(0.45)

    def test_from_yaml_missing_file_raises(self, tmp_path):
        from meshflow import AdaptiveModelTierRouter
        with pytest.raises(FileNotFoundError):
            AdaptiveModelTierRouter.from_yaml(str(tmp_path / "nofile.yaml"))

    def test_yaml_file_is_human_readable(self, tmp_path):
        r = _router()
        path = str(tmp_path / "router.yaml")
        r.to_yaml(path)
        content = open(path).read()
        # Should be plain text, no binary, no base64
        assert all(ord(c) < 128 for c in content)
        assert "tiers:" in content
        assert "name:" in content
        assert "model:" in content


# ═══════════════════════════════════════════════════════════════════════════════
# RouterReport / router.report()
# ═══════════════════════════════════════════════════════════════════════════════

class TestRouterReport:
    def _router_with_outcomes(self):
        import uuid
        from meshflow.agents.adaptation import RoutingOutcome
        r = _router()
        for i in range(10):
            run_id = str(uuid.uuid4())
            result = r.route(f"task {i}", run_id=run_id)
            r.record_outcome(
                run_id, success=True,
                quality=0.85, latency_ms=200.0, actual_cost_usd=0.0,
            )
        return r

    def test_report_returns_router_report(self):
        from meshflow import RouterReport
        r = _router()
        rep = r.report()
        assert isinstance(rep, RouterReport)

    def test_report_thresholds(self):
        r = _router(smart=0.28, large=0.60)
        rep = r.report()
        assert rep.smart_threshold == pytest.approx(0.28)
        assert rep.large_threshold == pytest.approx(0.60)

    def test_report_route_count(self):
        r = _router()
        r.route("t1")
        r.route("t2")
        rep = r.report()
        assert rep.route_count == 2

    def test_report_tier_distribution(self):
        r = self._router_with_outcomes()
        rep = r.report()
        assert sum(rep.tier_distribution.values()) > 0

    def test_report_outcomes_analyzed(self):
        r = self._router_with_outcomes()
        rep = r.report()
        assert rep.outcomes_analyzed == 10

    def test_report_cost_fields_non_negative(self):
        r = self._router_with_outcomes()
        rep = r.report()
        assert rep.actual_cost_usd >= 0.0
        assert rep.always_large_cost_usd >= 0.0

    def test_report_str_contains_key_fields(self):
        r = self._router_with_outcomes()
        s = str(r.report())
        assert "smart_threshold" in s
        assert "Tier distribution" in s
        assert "Cost summary" in s

    def test_report_savings_pct_zero_on_empty_store(self):
        r = _router()
        rep = r.report()
        assert rep.savings_pct == 0.0

    def test_router_report_exported_from_meshflow(self):
        from meshflow import RouterReport
        assert RouterReport is not None


# ═══════════════════════════════════════════════════════════════════════════════
# RouterOutcomeStore.export_csv()
# ═══════════════════════════════════════════════════════════════════════════════

class TestExportCsv:
    def _store_with_data(self):
        import uuid
        from meshflow import RouterOutcomeStore
        from meshflow.agents.adaptation import RoutingOutcome
        store = RouterOutcomeStore(path=":memory:")
        for i in range(5):
            store.record(RoutingOutcome.build(
                run_id=str(uuid.uuid4()), task=f"task {i}",
                composite_score=0.1 * i, model="llama3.2", tier="fast",
                success=True, quality_score=0.8,
            ))
        return store

    def test_export_csv_creates_file(self, tmp_path):
        from meshflow.agents.adaptation import export_outcomes_csv
        store = self._store_with_data()
        path = str(tmp_path / "outcomes.csv")
        n = export_outcomes_csv(store, path)
        assert n == 5
        assert os.path.exists(path)

    def test_export_csv_has_header(self, tmp_path):
        from meshflow.agents.adaptation import export_outcomes_csv
        store = self._store_with_data()
        path = str(tmp_path / "outcomes.csv")
        export_outcomes_csv(store, path)
        header = open(path).readline()
        assert "tier" in header
        assert "composite_score" in header

    def test_export_csv_row_count(self, tmp_path):
        from meshflow.agents.adaptation import export_outcomes_csv
        store = self._store_with_data()
        path = str(tmp_path / "outcomes.csv")
        export_outcomes_csv(store, path)
        lines = open(path).readlines()
        assert len(lines) == 6  # 1 header + 5 data rows

    def test_export_csv_empty_store_returns_zero(self, tmp_path):
        from meshflow import RouterOutcomeStore
        from meshflow.agents.adaptation import export_outcomes_csv
        store = RouterOutcomeStore(path=":memory:")
        path = str(tmp_path / "empty.csv")
        n = export_outcomes_csv(store, path)
        assert n == 0

    def test_store_export_csv_method(self, tmp_path):
        """RouterOutcomeStore.export_csv() convenience method."""
        store = self._store_with_data()
        path = str(tmp_path / "via_method.csv")
        n = store.export_csv(path)
        assert n == 5


# ═══════════════════════════════════════════════════════════════════════════════
# routing-report CLI
# ═══════════════════════════════════════════════════════════════════════════════

class TestRoutingReportCli:
    def _run(self, args: list[str], capsys) -> str:
        import argparse
        from meshflow.cli.main import _cmd_routing_report

        parser = argparse.ArgumentParser()
        parser.add_argument("--db",     default=":memory:")
        parser.add_argument("--state",  default="")
        parser.add_argument("--export", default="")
        parser.add_argument("--json",   dest="as_json", action="store_true")
        ns = parser.parse_args(args)
        _cmd_routing_report(ns)
        return capsys.readouterr().out

    def test_empty_store_prints_report(self, capsys):
        out = self._run([], capsys)
        assert "Routing Report" in out

    def test_json_output(self, capsys):
        out = self._run(["--json"], capsys)
        data = json.loads(out)
        assert "smart_threshold" in data
        assert "outcomes_stored" in data

    def test_json_has_cost_fields(self, capsys):
        out = self._run(["--json"], capsys)
        data = json.loads(out)
        assert "actual_cost_usd" in data
        assert "cost_saved_usd" in data

    def test_state_file_loaded(self, tmp_path, capsys):
        r = _router(smart=0.28, large=0.60)
        state_path = str(tmp_path / "state.json")
        r.save(state_path)
        out = self._run([f"--state={state_path}", "--json"], capsys)
        data = json.loads(out)
        assert data["smart_threshold"] == pytest.approx(0.28)

    def test_export_csv_mode(self, tmp_path, capsys):
        csv_path = str(tmp_path / "out.csv")
        out = self._run([f"--export={csv_path}"], capsys)
        assert "Exported" in out
        # Empty store → 0 rows, but file may or may not exist (no rows = skip write)
