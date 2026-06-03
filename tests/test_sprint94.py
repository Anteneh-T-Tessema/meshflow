"""Sprint 94 — per-bucket Thompson Sampling, router.history(), Go RunAgentStructured."""
from __future__ import annotations

import os
import pytest

os.environ.setdefault("MESHFLOW_MOCK", "1")


def _router(**kw):
    from meshflow import AdaptiveModelTierRouter, ModelTier, RouterOutcomeStore
    defaults = dict(
        tiers=[
            ModelTier("fast",  "llama3.2",  max_tokens=512),
            ModelTier("smart", "mistral",   max_tokens=2048),
            ModelTier("large", "gpt-4o",    max_tokens=4096),
        ],
        store=RouterOutcomeStore(path=":memory:"),
        adapt_mode="manual",
    )
    defaults.update(kw)
    return AdaptiveModelTierRouter(**defaults)


# ═══════════════════════════════════════════════════════════════════════════════
# Per-composite-bucket posteriors
# ═══════════════════════════════════════════════════════════════════════════════

class TestPerBucketPosteriors:
    def test_bucket_posteriors_initialised(self):
        r = _router()
        assert len(r._ts_alpha_b) == 3   # 3 tiers
        assert len(r._ts_alpha_b[0]) == r._ts_n_buckets
        assert all(a == 1.0 for row in r._ts_alpha_b for a in row)
        assert all(b == 1.0 for row in r._ts_beta_b  for b in row)

    def test_n_buckets_default_5(self):
        r = _router()
        assert r._ts_n_buckets == 5

    def test_record_outcome_updates_bucket_posterior(self):
        r = _router()
        # Route a short task (composite ≈ 0.0 → bucket 0)
        result = r.route("hi", run_id="r1")
        tier_pos = next(i for i, t in enumerate(r._tiers) if t.name == result.tier)
        alpha_b_before = r._ts_alpha_b[tier_pos][0]

        r.record_outcome("r1", success=True, quality=0.9)
        # Bucket should have been incremented (composite near 0 → bucket 0)
        # (composite may not be exactly 0, but should be bucket 0 or 1 for "hi")
        updated = any(
            r._ts_alpha_b[tier_pos][b] > alpha_b_before
            for b in range(r._ts_n_buckets)
        )
        assert updated

    def test_record_failure_updates_bucket_beta(self):
        r = _router()
        result = r.route("hi", run_id="r1")
        tier_pos = next(i for i, t in enumerate(r._tiers) if t.name == result.tier)
        beta_b_before = list(r._ts_beta_b[tier_pos])
        r.record_outcome("r1", success=False)
        updated = any(
            r._ts_beta_b[tier_pos][b] > beta_b_before[b]
            for b in range(r._ts_n_buckets)
        )
        assert updated

    def test_bucket_posterior_used_when_enough_data(self):
        """When a bucket has >= 5 obs, route() uses bucket posterior, not global."""
        r = _router()
        # Manually set bucket 0 (composite [0, 0.2)) for fast tier to have high success
        r._ts_alpha_b[0][0] = 50.0   # 49 successes in bucket 0
        r._ts_beta_b[0][0]  = 2.0    # 1 failure → bucket mean ≈ 0.96
        # Global posterior for fast is neutral (1, 1)
        assert r._ts_alpha[0] == 1.0

        # Short task → composite ≈ 0, bucket 0 → fast tier should be chosen reliably
        fast_count = sum(1 for _ in range(20) if r.route("hi").tier == "fast")
        # With bucket mean ≈ 0.96 >> threshold 0.5, fast should always be chosen
        assert fast_count >= 15

    def test_bucket_posterior_not_used_when_insufficient_data(self):
        """When bucket has < 5 obs, falls back to global posterior."""
        r = _router()
        # Bucket 0 has only 3 obs (< _ts_bucket_min_obs=5) — should use global
        r._ts_alpha_b[0][0] = 3.0
        r._ts_beta_b[0][0]  = 1.0
        # Global for fast: uniform prior (1,1) → both global and bucket are neutral
        # Routing should work without error
        result = r.route("hi")
        assert result.tier in [t.name for t in r._tiers]

    def test_snapshot_includes_bucket_posteriors(self):
        r = _router()
        r._ts_alpha_b[0][0] = 10.0
        snap = r.snapshot()
        assert "ts_alpha_b" in snap
        assert "ts_beta_b"  in snap
        assert snap["ts_alpha_b"][0][0] == 10.0

    def test_load_restores_bucket_posteriors(self, tmp_path):
        from meshflow import AdaptiveModelTierRouter, RouterOutcomeStore
        r = _router()
        r._ts_alpha_b[1][2] = 20.0   # tier 1, bucket 2
        r._ts_beta_b[2][4]  = 8.0    # tier 2, bucket 4
        path = str(tmp_path / "router.json")
        r.save(path)

        r2 = AdaptiveModelTierRouter.load(path, store=RouterOutcomeStore(path=":memory:"))
        assert r2._ts_alpha_b[1][2] == 20.0
        assert r2._ts_beta_b[2][4]  == 8.0

    def test_load_missing_bucket_posteriors_defaults_to_uniform(self, tmp_path):
        """Old state files without ts_alpha_b default to uniform prior."""
        import json
        from meshflow import AdaptiveModelTierRouter, RouterOutcomeStore
        data = {
            "smart_threshold": 0.33, "large_threshold": 0.67,
            "route_count": 5, "adapt_every": 50, "exploration_rate": 0.1,
            "adapt_mode": "auto", "last_adapted_at": None,
            "ts_alpha": [5.0, 1.0, 1.0], "ts_beta": [2.0, 1.0, 1.0],
            # no ts_alpha_b / ts_beta_b
            "tiers": [
                {"name": "fast",  "model": "llama3.2", "max_tokens": 512},
                {"name": "smart", "model": "mistral",  "max_tokens": 2048},
                {"name": "large", "model": "gpt-4o",   "max_tokens": 4096},
            ],
        }
        path = str(tmp_path / "old.json")
        json.dump(data, open(path, "w"))
        r = AdaptiveModelTierRouter.load(path, store=RouterOutcomeStore(path=":memory:"))
        # Should default to uniform prior (1.0 everywhere)
        assert all(a == 1.0 for row in r._ts_alpha_b for a in row)


# ═══════════════════════════════════════════════════════════════════════════════
# router.history()
# ═══════════════════════════════════════════════════════════════════════════════

class TestRouterHistory:
    def test_history_empty_at_start(self):
        r = _router()
        assert r.history() == []

    def test_history_grows_after_record_outcome(self):
        r = _router()
        result = r.route("task", run_id="r1")
        r.record_outcome("r1", success=True, quality=0.85)
        h = r.history()
        assert len(h) == 1

    def test_history_entry_has_required_fields(self):
        r = _router()
        result = r.route("task", run_id="r1")
        r.record_outcome("r1", success=True, quality=0.85, latency_ms=200.0, actual_cost_usd=0.0)
        entry = r.history(1)[0]
        for field in ("routing_id", "tier", "model", "composite",
                      "was_exploration", "effective_success", "quality",
                      "latency_ms", "cost_usd", "ts_mean"):
            assert field in entry, f"missing field: {field}"

    def test_history_records_tier(self):
        r = _router()
        result = r.route("task", run_id="r1")
        r.record_outcome("r1", success=True)
        entry = r.history(1)[0]
        assert entry["tier"] == result.tier

    def test_history_records_model(self):
        r = _router()
        result = r.route("task", run_id="r1")
        r.record_outcome("r1", success=True)
        entry = r.history(1)[0]
        assert entry["model"] == result.model

    def test_history_records_effective_success(self):
        r = _router()
        r.route("task", run_id="r1")
        r.record_outcome("r1", success=True, quality=0.9)
        assert r.history(1)[0]["effective_success"] is True

    def test_history_records_failure(self):
        r = _router()
        r.route("task", run_id="r1")
        r.record_outcome("r1", success=False)
        assert r.history(1)[0]["effective_success"] is False

    def test_history_n_parameter(self):
        r = _router()
        for i in range(10):
            r.route(f"task {i}", run_id=f"r{i}")
            r.record_outcome(f"r{i}", success=True)
        assert len(r.history(5)) == 5
        assert len(r.history(10)) == 10
        assert len(r.history(100)) == 10  # only 10 recorded

    def test_history_capped_at_maxlen(self):
        r = _router()
        for i in range(r._history_maxlen + 20):
            r.route(f"t{i}", run_id=f"r{i}")
            r.record_outcome(f"r{i}", success=True)
        assert len(r._history) <= r._history_maxlen

    def test_history_ts_mean_in_range(self):
        r = _router()
        r.route("task", run_id="r1")
        r.record_outcome("r1", success=True, quality=0.9)
        ts_mean = r.history(1)[0]["ts_mean"]
        assert 0.0 <= ts_mean <= 1.0

    def test_history_routing_id_matches(self):
        r = _router()
        r.route("task", run_id="my-id")
        r.record_outcome("my-id", success=True)
        entry = r.history(1)[0]
        assert entry["routing_id"] == "my-id"

    def test_history_exported_on_router(self):
        r = _router()
        assert hasattr(r, "history")
        assert callable(r.history)


# ═══════════════════════════════════════════════════════════════════════════════
# explain() shows bucket posteriors
# ═══════════════════════════════════════════════════════════════════════════════

class TestExplainBucketPosteriors:
    def test_explain_shows_global_and_bucket(self):
        r = _router()
        exp = r.explain("short task")
        assert "global" in exp
        assert "bucket[" in exp

    def test_explain_shows_active_source(self):
        r = _router()
        # No data → should show "global"
        exp = r.explain("task")
        assert "← global" in exp

    def test_explain_shows_bucket_as_active_when_enough_data(self):
        r = _router()
        # Bucket 0 for fast tier with enough data
        r._ts_alpha_b[0][0] = 10.0
        r._ts_beta_b[0][0]  = 2.0
        exp = r.explain("hi")  # short task → bucket 0
        assert "← bucket" in exp
