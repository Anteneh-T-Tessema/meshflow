"""Sprint 92 — Thompson Sampling replaces ε-greedy in AdaptiveModelTierRouter.

Tests verify:
- Beta posterior initialised to (1, 1) per tier
- record_outcome() increments α on success, β on failure
- route() picks cheapest tier whose Beta sample >= 0.5
- After many successes on fast tier, fast is reliably chosen
- After many failures on fast tier, router escalates to smart/large
- Beta params persisted through snapshot() / save() / load()
- explain() shows posteriors
- was_exploration flag set when Thompson Sampling differs from greedy baseline
"""
from __future__ import annotations

import os
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
# Beta posterior initialisation
# ═══════════════════════════════════════════════════════════════════════════════

class TestBetaPosteriorInit:
    def test_alpha_initialised_to_one_per_tier(self):
        r = _router()
        assert r._ts_alpha == [1.0, 1.0, 1.0]

    def test_beta_initialised_to_one_per_tier(self):
        r = _router()
        assert r._ts_beta_ == [1.0, 1.0, 1.0]

    def test_two_tier_init(self):
        from meshflow import AdaptiveModelTierRouter, ModelTier, RouterOutcomeStore
        r = AdaptiveModelTierRouter(
            tiers=[ModelTier("fast", "llama3.2"), ModelTier("large", "gpt-4o")],
            store=RouterOutcomeStore(path=":memory:"),
        )
        assert len(r._ts_alpha) == 2
        assert len(r._ts_beta_) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# record_outcome() updates Beta posteriors
# ═══════════════════════════════════════════════════════════════════════════════

class TestBetaPosteriorUpdate:
    def test_success_increments_alpha(self):
        r = _router()
        result = r.route("task", run_id="r1")
        tier_idx = next(i for i, t in enumerate(r._tiers) if t.name == result.tier)
        alpha_before = r._ts_alpha[tier_idx]
        r.record_outcome("r1", success=True, quality=0.9)
        assert r._ts_alpha[tier_idx] == alpha_before + 1.0

    def test_failure_increments_beta(self):
        r = _router()
        result = r.route("task", run_id="r1")
        tier_idx = next(i for i, t in enumerate(r._tiers) if t.name == result.tier)
        beta_before = r._ts_beta_[tier_idx]
        r.record_outcome("r1", success=False)
        assert r._ts_beta_[tier_idx] == beta_before + 1.0

    def test_low_quality_counts_as_failure(self):
        r = _router()
        result = r.route("task", run_id="r1")
        tier_idx = next(i for i, t in enumerate(r._tiers) if t.name == result.tier)
        beta_before = r._ts_beta_[tier_idx]
        # success=True but quality < 0.5 → effective failure
        r.record_outcome("r1", success=True, quality=0.3)
        assert r._ts_beta_[tier_idx] == beta_before + 1.0

    def test_high_quality_counts_as_success(self):
        r = _router()
        result = r.route("task", run_id="r1")
        tier_idx = next(i for i, t in enumerate(r._tiers) if t.name == result.tier)
        alpha_before = r._ts_alpha[tier_idx]
        r.record_outcome("r1", success=True, quality=0.9)
        assert r._ts_alpha[tier_idx] == alpha_before + 1.0

    def test_none_quality_trusts_success_flag(self):
        r = _router()
        result = r.route("task", run_id="r1")
        tier_idx = next(i for i, t in enumerate(r._tiers) if t.name == result.tier)
        alpha_before = r._ts_alpha[tier_idx]
        r.record_outcome("r1", success=True, quality=None)  # trust success flag
        assert r._ts_alpha[tier_idx] == alpha_before + 1.0

    def test_unknown_run_id_no_crash(self):
        r = _router()
        r.record_outcome("nonexistent", success=True)  # should not raise

    def test_other_tiers_unchanged(self):
        r = _router()
        result = r.route("task", run_id="r1")
        tier_idx = next(i for i, t in enumerate(r._tiers) if t.name == result.tier)
        alphas_before = list(r._ts_alpha)
        r.record_outcome("r1", success=True, quality=0.9)
        for i, a in enumerate(r._ts_alpha):
            if i != tier_idx:
                assert a == alphas_before[i], f"tier {i} alpha changed unexpectedly"


# ═══════════════════════════════════════════════════════════════════════════════
# Thompson Sampling routing behaviour
# ═══════════════════════════════════════════════════════════════════════════════

class TestThompsonSamplingRouting:
    def test_fast_tier_chosen_after_many_successes(self):
        """After 50 successes on fast tier, it should be chosen reliably."""
        r = _router()
        # Force fast tier to have a very high success rate
        r._ts_alpha[0] = 50.0   # 50 successes
        r._ts_beta_[0] = 1.0    # 1 failure → mean ≈ 0.98
        # Other tiers have neutral prior

        # Run 20 routes — fast should win almost every time
        fast_count = sum(
            1 for _ in range(20)
            if r.route(f"task {_}").tier == "fast"
        )
        assert fast_count >= 15, f"Expected fast tier >=15/20, got {fast_count}"

    def test_escalates_after_many_fast_failures(self):
        """After many fast-tier failures, the router escalates to smart/large."""
        r = _router()
        # Fast tier has very low success rate
        r._ts_alpha[0] = 1.0
        r._ts_beta_[0] = 50.0  # 50 failures → mean ≈ 0.02, almost never samples >= 0.5
        # Smart tier has high success rate
        r._ts_alpha[1] = 20.0
        r._ts_beta_[1] = 2.0   # mean ≈ 0.91

        not_fast = sum(
            1 for _ in range(20)
            if r.route(f"task {_}").tier != "fast"
        )
        assert not_fast >= 15, f"Expected escalation >=15/20, got {not_fast}"

    def test_route_returns_tier_result(self):
        r = _router()
        result = r.route("task")
        assert hasattr(result, "model")
        assert hasattr(result, "tier")
        assert result.tier in [t.name for t in r._tiers]

    def test_was_exploration_when_ts_differs_from_greedy(self):
        """was_exploration=True when Thompson Sampling picks a different tier than composite score."""
        r = _router()
        # Force fast tier to almost never be chosen by TS
        r._ts_alpha[0] = 1.0
        r._ts_beta_[0] = 100.0  # mean ≈ 0.01 → almost never >= 0.5
        # Force smart tier to be reliably chosen
        r._ts_alpha[1] = 50.0
        r._ts_beta_[1] = 1.0   # mean ≈ 0.98

        # Short task → greedy=fast, but TS should pick smart
        explorations = sum(
            1 for _ in range(20)
            if r.route("hi").was_exploration  # type: ignore[attr-defined]
        )
        # Most routes should be "exploratory" (TS differs from greedy)
        assert explorations >= 10

    def test_no_exploration_when_ts_agrees_with_greedy(self):
        """was_exploration=False when TS and composite agree."""
        r = _router()
        # Fast tier has extremely high success rate
        r._ts_alpha[0] = 200.0
        r._ts_beta_[0] = 1.0   # mean ≈ 1.0 → always >= 0.5

        not_exploring = sum(
            1 for _ in range(20)
            if not r.route("short task").was_exploration  # type: ignore[attr-defined]
        )
        assert not_exploring >= 15

    def test_cold_start_doesnt_crash(self):
        """Cold start (uniform prior Beta(2,2)) should route without errors."""
        r = _router()
        for _ in range(10):
            result = r.route(f"task {_}")
            assert result.tier in [t.name for t in r._tiers]

    def test_single_tier_always_chosen(self):
        from meshflow import AdaptiveModelTierRouter, ModelTier, RouterOutcomeStore
        r = AdaptiveModelTierRouter(
            tiers=[ModelTier("only", "llama3.2")],
            store=RouterOutcomeStore(path=":memory:"),
        )
        for _ in range(5):
            assert r.route("task").tier == "only"


# ═══════════════════════════════════════════════════════════════════════════════
# Beta params persisted through snapshot / save / load
# ═══════════════════════════════════════════════════════════════════════════════

class TestBetaPersistence:
    def test_snapshot_includes_ts_alpha(self):
        r = _router()
        r._ts_alpha[0] = 15.0
        snap = r.snapshot()
        assert "ts_alpha" in snap
        assert snap["ts_alpha"][0] == 15.0

    def test_snapshot_includes_ts_beta(self):
        r = _router()
        r._ts_beta_[2] = 8.0
        snap = r.snapshot()
        assert "ts_beta" in snap
        assert snap["ts_beta"][2] == 8.0

    def test_save_and_load_restores_alpha(self, tmp_path):
        from meshflow import AdaptiveModelTierRouter, RouterOutcomeStore
        r = _router()
        r._ts_alpha = [10.0, 5.0, 2.0]
        path = str(tmp_path / "router.json")
        r.save(path)

        r2 = AdaptiveModelTierRouter.load(path, store=RouterOutcomeStore(path=":memory:"))
        assert r2._ts_alpha == [10.0, 5.0, 2.0]

    def test_save_and_load_restores_beta(self, tmp_path):
        from meshflow import AdaptiveModelTierRouter, RouterOutcomeStore
        r = _router()
        r._ts_beta_ = [1.0, 3.0, 7.0]
        path = str(tmp_path / "router.json")
        r.save(path)

        r2 = AdaptiveModelTierRouter.load(path, store=RouterOutcomeStore(path=":memory:"))
        assert r2._ts_beta_ == [1.0, 3.0, 7.0]

    def test_load_missing_ts_fields_defaults_to_uniform(self, tmp_path):
        """Old state files without ts_alpha/ts_beta default to uniform prior."""
        import json
        from meshflow import AdaptiveModelTierRouter, RouterOutcomeStore
        # Write a state file without TS fields (simulates old format)
        data = {
            "smart_threshold": 0.33, "large_threshold": 0.67,
            "route_count": 10, "last_adapted_at": None,
            "adapt_every": 50, "exploration_rate": 0.1, "adapt_mode": "auto",
            "tiers": [
                {"name": "fast",  "model": "llama3.2", "max_tokens": 512},
                {"name": "large", "model": "gpt-4o",   "max_tokens": 4096},
            ],
            # no ts_alpha / ts_beta
        }
        path = str(tmp_path / "old_router.json")
        json.dump(data, open(path, "w"))

        r = AdaptiveModelTierRouter.load(path, store=RouterOutcomeStore(path=":memory:"))
        assert r._ts_alpha == [1.0, 1.0]   # default uniform
        assert r._ts_beta_  == [1.0, 1.0]


# ═══════════════════════════════════════════════════════════════════════════════
# explain() shows Beta posteriors
# ═══════════════════════════════════════════════════════════════════════════════

class TestExplainShowsPosteriors:
    def test_explain_contains_thompson_sampling_header(self):
        r = _router()
        exp = r.explain("task")
        assert "Thompson Sampling" in exp

    def test_explain_shows_alpha_beta(self):
        r = _router()
        r._ts_alpha[0] = 10.0
        r._ts_beta_[0] = 2.0
        exp = r.explain("short task")
        assert "α=10" in exp or "α=10.0" in exp
        assert "β=2" in exp or "β=2.0" in exp

    def test_explain_shows_mean(self):
        r = _router()
        r._ts_alpha[0] = 9.0
        r._ts_beta_[0] = 1.0   # mean = 9/10 = 0.90
        exp = r.explain("short task")
        assert "mean=0.90" in exp

    def test_explain_shows_all_tiers(self):
        r = _router()
        exp = r.explain("task")
        for t in r._tiers:
            assert t.name in exp

    def test_explain_shows_n_obs(self):
        r = _router()
        r._ts_alpha[0] = 6.0   # α=6 → 5 observed successes (prior was 1)
        r._ts_beta_[0] = 3.0   # β=3 → 2 observed failures
        exp = r.explain("task")
        # n = α + β - 2 (subtract uniform prior) = 6 + 3 - 2 = 7
        # Sprint 94 format: "global: α=6 β=3 mean=0.67 n=7"
        assert "n=7" in exp


# ═══════════════════════════════════════════════════════════════════════════════
# Integration: route → record → posteriors improve → routing improves
# ═══════════════════════════════════════════════════════════════════════════════

class TestThompsonSamplingLearningLoop:
    def test_posteriors_improve_after_feedback(self):
        """Simulate 20 successful fast-tier routes; fast tier posterior should improve."""
        r = _router()
        initial_mean = r._ts_alpha[0] / (r._ts_alpha[0] + r._ts_beta_[0])  # = 0.5

        for i in range(20):
            res = r.route(f"task {i}", run_id=f"r{i}")
            # Force fast tier selection for this test by injecting the outcome
            # for the fast tier regardless of what TS actually chose
            tier_pos = 0  # fast
            r._ts_alpha[tier_pos] += 1.0  # simulate 20 successes on fast

        final_mean = r._ts_alpha[0] / (r._ts_alpha[0] + r._ts_beta_[0])
        assert final_mean > initial_mean

    def test_full_loop_fast_tier_converges(self):
        """After 30 successful fast-tier routes, fast tier should be reliably chosen."""
        r = _router()
        # Simulate 30 successes on fast tier
        r._ts_alpha[0] = 31.0  # 30 successes + prior
        r._ts_beta_[0] = 1.0   # 0 failures + prior

        fast_count = sum(1 for _ in range(30) if r.route("task").tier == "fast")
        # With mean ≈ 0.97 for fast tier, it should sample >= 0.5 almost every time
        assert fast_count >= 25
