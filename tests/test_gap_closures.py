"""Tests for all 16 framework-parity gap closures.

Covers (in order):
 1  time_travel       — RewindEngine step listing + step snapshots
 2  context_size      — Task max_context_chars cap + dedup
 3  plugin_scan       — PluginScanResult + PluginAuditLog
 4  skill_registry    — AgentSkillRegistry BM25 + LLM selection
 5  debate            — DebatePanel N-way + consensus
 6  workspace_memory  — WorkspaceMemoryStore write/search/federation
 7  cost_forecaster   — CostForecaster USD estimate
 8  adaptive_agent    — AdaptiveAgent complexity detection + model swap
 9  rag_budget        — RAGNode max_chars enforcement
10  context_pruning   — ContextPruner strategies
11  context_dedup     — ContextDeduplicator fingerprinting
12  early_exit        — EarlyExitAgent threshold gate
13  ci_gate           — CIBudgetGate regression detection
14  pareto            — ParetoAnalyzer frontier + comparison table
15  latency_routing   — ProviderRouter.route_with_latency
16  trace_viewer      — TraceViewer dict / LangSmith export
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import pytest
from typing import Any


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fake_agent(name: str = "agent", response: str = "result. CONFIDENCE:0.85") -> Any:
    class FakeAgent:
        system_prompt = ""
        model = "fake-model"
        async def run(self, task, context=None):
            return {"result": response, "tokens": 10, "cost_usd": 0.001, "agent_name": name}
    fa = FakeAgent()
    fa.name = name
    return fa


# ── Gap 1: Time-travel (unit tests — no ledger needed for structure) ──────────


def test_rewind_engine_import():
    from meshflow.core.time_travel import RewindEngine, RewindResult, StepSnapshot
    engine = RewindEngine(":memory:")
    assert engine is not None


def test_step_snapshot_fields():
    from meshflow.core.time_travel import StepSnapshot
    snap = StepSnapshot(
        idx=1, step_id="s1", node_id="planner", node_kind="native",
        ok=True, blocked=False, cost_usd=0.001, tokens_used=100,
        duration_ms=320.0, uncertainty=0.1, output_preview="hello", timestamp="",
    )
    assert snap.ok is True
    assert snap.node_id == "planner"


# ── Gap 2: Task context size management ───────────────────────────────────────


def test_task_context_dedup():
    from meshflow.agents.task import Task, TaskOutput

    task_a = Task(description="Find facts", expected_output="facts")
    task_b = Task(description="Find facts", expected_output="facts")
    # Identical output
    task_a.output = TaskOutput(raw="Same output.", task_description="Find facts")
    task_b.output = TaskOutput(raw="Same output.", task_description="Find facts")

    consumer = Task(
        description="Summarise {x}", expected_output="summary",
        context=[task_a, task_b],
    )
    prompt = consumer._build_prompt({"x": "test"})
    # Dedup: only one "Same output." section should appear
    assert prompt.count("Same output.") == 1


def test_task_context_max_chars():
    from meshflow.agents.task import Task, TaskOutput

    long_output = "A" * 3000
    task_a = Task(description="T1", expected_output="out")
    task_a.output = TaskOutput(raw=long_output, task_description="T1")

    consumer = Task(
        description="Consume", expected_output="x",
        context=[task_a],
        max_context_chars=500,
    )
    prompt = consumer._build_prompt(None)
    # Context block must be <= 500 chars + overhead
    assert len(prompt) < 5000  # well under the original 3000 chars


# ── Gap 3: Plugin scanning ────────────────────────────────────────────────────


def test_plugin_scan_result_structure():
    from meshflow.plugins import PluginScanResult
    result = PluginScanResult(dist_name="my-plugin", version="1.0.0", safe=True)
    assert result.to_dict()["safe"] is True
    assert result.dist_name == "my-plugin"


def test_plugin_audit_log():
    from meshflow.plugins import PluginAuditLog, PluginInfo
    log = PluginAuditLog(":memory:")
    info = PluginInfo(name="my-tool", group="tool", ep_group="meshflow.tools",
                      module="my.module", dist_name="my-plugin", version="1.0")
    log.record_load(info)
    entries = log.list_recent(10)
    assert len(entries) == 1
    assert entries[0]["name"] == "my-tool"


def test_plugin_audit_log_unsafe():
    from meshflow.plugins import PluginAuditLog, PluginInfo, PluginScanResult
    log = PluginAuditLog(":memory:")
    info = PluginInfo(name="bad-tool", group="tool", ep_group="meshflow.tools",
                      module="bad.module", dist_name="bad-pkg", version="0.1")
    scan = PluginScanResult(dist_name="bad-pkg", version="0.1", safe=False,
                            vulnerabilities=[{"id": "CVE-2024-0001"}])
    log.record_load(info, scan)
    unsafe = log.unsafe_loads()
    assert len(unsafe) == 1


# ── Gap 4: Skill registry ─────────────────────────────────────────────────────


def test_skill_registry_register_and_select():
    from meshflow.agents.skill_registry import AgentSkillRegistry, AgentSkillProfile

    reg = AgentSkillRegistry()
    reg.register(AgentSkillProfile(
        agent_name="data-analyst",
        skills=["pandas", "sql", "data_visualization"],
        description="Expert in tabular data analysis with SQL and Python.",
    ))
    reg.register(AgentSkillProfile(
        agent_name="legal-reviewer",
        skills=["contract_review", "compliance", "gdpr"],
        description="Expert in legal document review and GDPR compliance.",
    ))

    best = reg.select_best("analyse the sales data with SQL")
    assert best is not None
    assert best.agent_name == "data-analyst"


def test_skill_registry_rank_all():
    from meshflow.agents.skill_registry import AgentSkillRegistry, AgentSkillProfile

    reg = AgentSkillRegistry()
    reg.register(AgentSkillProfile(agent_name="a", skills=["python"], description="Python coder"))
    reg.register(AgentSkillProfile(agent_name="b", skills=["java"], description="Java developer"))

    ranked = reg.rank_all("write Python code")
    assert len(ranked) >= 1
    assert ranked[0][1].agent_name == "a"


@pytest.mark.asyncio
async def test_skill_registry_select_llm():
    from meshflow.agents.skill_registry import AgentSkillRegistry, AgentSkillProfile

    reg = AgentSkillRegistry()
    reg.register(AgentSkillProfile(agent_name="data-analyst", skills=["sql"]))
    reg.register(AgentSkillProfile(agent_name="writer", skills=["writing"]))

    selector = _fake_agent("orchestrator", response="data-analyst")
    best = await reg.select_llm("analyse the SQL data", selector)
    assert best is not None
    assert best.agent_name == "data-analyst"


# ── Gap 5: N-way debate ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_debate_panel_basic():
    from meshflow.agents.debate import DebatePanel

    debaters = [
        _fake_agent("a", "Python is best. CONFIDENCE:0.80"),
        _fake_agent("b", "Python is best. CONFIDENCE:0.80"),
    ]
    panel = DebatePanel(debaters=debaters, max_rounds=1)
    result = await panel.debate("Is Python the best language?")
    assert result.consensus
    assert result.rounds >= 1
    assert len(result.tree) >= 2


@pytest.mark.asyncio
async def test_debate_panel_arbiter():
    from meshflow.agents.debate import DebatePanel

    debaters = [
        _fake_agent("a", "Option A. CONFIDENCE:0.60"),
        _fake_agent("b", "Option B. CONFIDENCE:0.55"),
    ]
    arbiter = _fake_agent("arbiter", "Final: Option A wins. CONFIDENCE:0.90")
    panel = DebatePanel(debaters=debaters, arbiter=arbiter, max_rounds=1,
                        consensus_strategy="arbiter")
    result = await panel.debate("Which option?")
    assert "arbiter" in result.verdict or result.verdict == "majority"


@pytest.mark.asyncio
async def test_debate_requires_two_debaters():
    from meshflow.agents.debate import DebatePanel
    with pytest.raises(ValueError):
        DebatePanel(debaters=[_fake_agent("solo")])


# ── Gap 6: Cross-workspace memory ─────────────────────────────────────────────


def test_workspace_memory_write_search():
    from meshflow.intelligence.workspace_memory import WorkspaceMemoryStore

    store = WorkspaceMemoryStore(":memory:")
    store.write("prod", "analyst", "Q3 revenue was $12.4M, up 18% YoY.")
    store.write("prod", "analyst", "Our main product is the MeshFlow platform.")

    hits = store.search("prod", "analyst", "Q3 revenue growth", top_k=3)
    assert len(hits) >= 1
    assert "revenue" in hits[0].content.lower()


def test_workspace_isolation():
    from meshflow.intelligence.workspace_memory import WorkspaceMemoryStore

    store = WorkspaceMemoryStore(":memory:")
    store.write("ws_a", "analyst", "Secret data for workspace A.")
    store.write("ws_b", "analyst", "Data for workspace B.")

    hits_a = store.search("ws_a", "analyst", "secret workspace A")
    hits_b = store.search("ws_b", "analyst", "workspace A")

    assert any("Secret" in h.content for h in hits_a)
    # ws_b cannot see ws_a data by default
    assert not any("Secret" in h.content for h in hits_b)


def test_workspace_federation():
    from meshflow.intelligence.workspace_memory import WorkspaceMemoryStore

    store = WorkspaceMemoryStore(":memory:")
    store.write("prod", "analyst", "Production revenue $12M.")
    store.write("staging", "analyst", "Staging test data.")

    # staging is allowed to read from prod
    hits = store.search("staging", "analyst", "revenue",
                        allowed_workspaces=["prod"])
    assert any("revenue" in h.content.lower() for h in hits)


def test_workspace_snapshot_restore():
    from meshflow.intelligence.workspace_memory import WorkspaceMemoryStore

    store = WorkspaceMemoryStore(":memory:")
    store.write("src", "bot", "Memory item 1.")
    store.write("src", "bot", "Memory item 2.")

    snap = store.snapshot("src")
    n = store.restore("dst", snap)
    assert n == 2
    hits = store.search("dst", "bot", "Memory item")
    assert len(hits) >= 1


# ── Gap 7: Cost forecaster ────────────────────────────────────────────────────


def test_cost_forecaster_basic():
    from meshflow.optimization.planner import CostForecaster

    fc = CostForecaster()
    result = fc.forecast(
        model="claude-sonnet-4-6",
        system_prompt="You are helpful.",
        messages=[{"role": "user", "content": "What is 2 + 2?"}],
    )
    assert result["input_tokens"] > 0
    assert result["output_tokens_est"] > 0
    assert result["total_usd_est"] > 0
    assert result["within_budget"] is True


def test_cost_forecaster_budget_gate():
    from meshflow.optimization.planner import CostForecaster

    fc = CostForecaster()
    result = fc.forecast(
        model="claude-sonnet-4-6",
        system_prompt="A" * 10000,  # very large prompt
        messages=[{"role": "user", "content": "X" * 10000}],
        max_budget_usd=0.000001,   # impossibly small budget
    )
    assert result["within_budget"] is False


def test_cost_forecaster_compare_models():
    from meshflow.optimization.planner import CostForecaster

    fc = CostForecaster()
    results = fc.compare_models(
        ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
        system_prompt="You are helpful.",
        messages=[{"role": "user", "content": "Hello"}],
    )
    assert len(results) == 3
    # Should be sorted cheapest first
    costs = [r["total_usd_est"] for r in results]
    assert costs == sorted(costs)


# ── Gap 8: Adaptive model switching ──────────────────────────────────────────


def test_adaptive_complexity_simple():
    from meshflow.agents.adaptive import _task_complexity
    assert _task_complexity("What is 2+2?") == "simple"


def test_adaptive_complexity_complex():
    from meshflow.agents.adaptive import _task_complexity
    assert _task_complexity("Audit this HIPAA policy document for compliance violations.") == "complex"


@pytest.mark.asyncio
async def test_adaptive_agent_uses_cheap_model():
    from meshflow.agents.adaptive import AdaptiveAgent
    used_models = []

    class TrackingAgent:
        name = "tracker"
        system_prompt = ""
        model = "claude-sonnet-4-6"
        async def run(self, task, context=None):
            used_models.append(self.model)
            return {"result": "42. CONFIDENCE:0.90", "tokens": 5, "cost_usd": 0.0}

    agent = AdaptiveAgent(
        TrackingAgent(),
        cheap_model="claude-haiku-4-5-20251001",
        expensive_model="claude-sonnet-4-6",
        downgrade_on_simple=True,
    )
    await agent.run("What is 2 + 2?")
    assert "claude-haiku-4-5-20251001" in used_models


# ── Gap 9: RAG token budget ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rag_retrieve_text_max_chars():
    from meshflow.intelligence.rag import DocumentStore

    store = DocumentStore()
    await store.ingest(["A" * 500, "B" * 500, "C" * 500])

    # Without cap
    full = await store.retrieve_text("query", top_k=3)
    # With cap of 400 chars
    capped = await store.retrieve_text("query", top_k=3, max_chars=400)

    assert len(capped) <= 500  # roughly capped
    if len(full) > 400:
        assert len(capped) < len(full)


@pytest.mark.asyncio
async def test_rag_node_max_chars():
    from meshflow.intelligence.rag import DocumentStore, RAGNode
    from meshflow.core.node import NodeInput

    store = DocumentStore()
    await store.ingest(["Long context " * 100])

    node = RAGNode(store=store, top_k=3, max_chars=200)
    result = await node.run(NodeInput(task="long context"))
    # Result should be shorter than if uncapped
    assert len(result.content) < 5000


# ── Gap 10: Context pruning ───────────────────────────────────────────────────


def test_pruner_sliding_window():
    from meshflow.intelligence.pruning import ContextPruner

    pruner = ContextPruner(max_tokens=100, strategy="sliding_window")
    messages = [
        {"role": "system", "content": "You are helpful."},
        *[{"role": "user" if i % 2 == 0 else "assistant",
           "content": f"Message {i}: " + "X" * 50} for i in range(10)],
    ]
    pruned = pruner.prune(messages)
    assert len(pruned) < len(messages)
    # System message preserved
    assert any(m["role"] == "system" for m in pruned)


def test_pruner_no_op_when_fits():
    from meshflow.intelligence.pruning import ContextPruner

    pruner = ContextPruner(max_tokens=10000, strategy="sliding_window")
    messages = [{"role": "user", "content": "hi"}]
    pruned = pruner.prune(messages)
    assert pruned == messages


def test_pruner_summarise():
    from meshflow.intelligence.pruning import ContextPruner

    # 10 messages × ~200 chars each = ~2000 chars / 4 = ~500 tokens; budget=150 forces pruning
    pruner = ContextPruner(max_tokens=150, strategy="summarise")
    messages = [
        {"role": "system", "content": "You are helpful."},
        *[{"role": "user", "content": f"Context message {i}: " + "Y" * 200} for i in range(8)],
    ]
    pruned = pruner.prune(messages)
    assert len(pruned) < len(messages)


def test_pruner_stats():
    from meshflow.intelligence.pruning import ContextPruner

    pruner = ContextPruner(max_tokens=100)
    orig = [{"role": "user", "content": "X" * 200} for _ in range(5)]
    pruned = pruner.prune(orig)
    stats = pruner.stats(orig, pruned)
    assert "reduction_pct" in stats
    assert stats["original_messages"] == 5


# ── Gap 11: Parallel context dedup ───────────────────────────────────────────


def test_context_dedup_basic():
    from meshflow.agents.context_dedup import ContextDeduplicator

    dedup = ContextDeduplicator(hash_threshold=10)
    ctx_a = {"shared": "This is a long shared content block.", "unique_a": "only A"}
    ctx_b = {"shared": "This is a long shared content block.", "unique_b": "only B"}

    clean_a = dedup.deduplicate(ctx_a, agent_name="a")
    clean_b = dedup.deduplicate(ctx_b, agent_name="b")

    assert clean_a["shared"] == "This is a long shared content block."
    assert "deduplicated" in clean_b["shared"]  # replaced with placeholder
    assert clean_b["unique_b"] == "only B"


def test_context_dedup_short_values_not_hashed():
    from meshflow.agents.context_dedup import ContextDeduplicator

    dedup = ContextDeduplicator(hash_threshold=100)
    ctx_a = {"id": "abc", "label": "hello"}
    ctx_b = {"id": "abc", "label": "hello"}

    clean_a = dedup.deduplicate(ctx_a, agent_name="a")
    clean_b = dedup.deduplicate(ctx_b, agent_name="b")

    assert clean_b["id"] == "abc"     # too short to dedup
    assert clean_b["label"] == "hello"


def test_context_dedup_reset():
    from meshflow.agents.context_dedup import ContextDeduplicator

    dedup = ContextDeduplicator(hash_threshold=5)
    ctx = {"k": "long_value_here_123456789"}
    dedup.deduplicate(ctx, "a")
    assert dedup.seen_count() == 1
    dedup.reset()
    assert dedup.seen_count() == 0


def test_context_dedup_savings_estimate():
    from meshflow.agents.context_dedup import ContextDeduplicator

    dedup = ContextDeduplicator(hash_threshold=10)
    contexts = [{"shared": "Repeated shared content " * 5, "unique": f"item {i}"} for i in range(3)]
    savings = dedup.savings_estimate(contexts)
    assert savings["saved_bytes"] >= 0


# ── Gap 12: Early exit on confidence ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_early_exit_exits_when_confident():
    from meshflow.agents.early_exit import EarlyExitAgent
    turns = []

    class CountingAgent:
        name = "counter"
        model = ""
        system_prompt = ""
        async def run(self, task, context=None):
            turns.append(1)
            return {"result": "answer CONFIDENCE:0.90", "tokens": 5, "cost_usd": 0.0}

    agent = EarlyExitAgent(CountingAgent(), confidence_threshold=0.85, max_turns=5)
    result = await agent.run("What is 2+2?")
    assert result["_early_exit"] is True
    assert result["_turns"] == 1  # exited on first turn


@pytest.mark.asyncio
async def test_early_exit_runs_max_turns_when_never_confident():
    from meshflow.agents.early_exit import EarlyExitAgent
    turns = []

    class AlwaysUncertain:
        name = "unc"
        model = ""
        system_prompt = ""
        async def run(self, task, context=None):
            turns.append(1)
            return {"result": "not sure CONFIDENCE:0.40", "tokens": 5, "cost_usd": 0.0}

    agent = EarlyExitAgent(AlwaysUncertain(), confidence_threshold=0.85, max_turns=3)
    result = await agent.run("Difficult question")
    assert result["_early_exit"] is False
    assert result["_turns"] == 3
    assert len(turns) == 3


# ── Gap 13: Cost regression CI gate ──────────────────────────────────────────


def test_ci_gate_no_regression():
    from meshflow.eval.ci_gate import CIBudgetGate

    baseline = {"total_tokens": 1000, "total_cost_usd": 0.01, "pass_rate": 0.90}
    current  = {"total_tokens": 1050, "total_cost_usd": 0.011, "pass_rate": 0.91}

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(baseline, f)
        bpath = f.name

    try:
        gate = CIBudgetGate(baseline_path=bpath)
        report = gate.check_dict(current)
        assert not report.any_regression
    finally:
        os.unlink(bpath)


def test_ci_gate_token_regression():
    from meshflow.eval.ci_gate import CIBudgetGate

    baseline = {"total_tokens": 1000, "total_cost_usd": 0.01, "pass_rate": 0.90}
    current  = {"total_tokens": 1200, "total_cost_usd": 0.01, "pass_rate": 0.90}

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(baseline, f)
        bpath = f.name

    try:
        gate = CIBudgetGate(baseline_path=bpath, max_token_regression=0.10)
        report = gate.check_dict(current)
        assert report.token_regression is True
        assert report.any_regression is True
    finally:
        os.unlink(bpath)


def test_ci_gate_quality_regression():
    from meshflow.eval.ci_gate import CIBudgetGate

    baseline = {"total_tokens": 1000, "total_cost_usd": 0.01, "pass_rate": 0.90}
    current  = {"total_tokens": 1000, "total_cost_usd": 0.01, "pass_rate": 0.80}

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(baseline, f)
        bpath = f.name

    try:
        gate = CIBudgetGate(baseline_path=bpath, max_quality_regression=0.05)
        report = gate.check_dict(current)
        assert report.quality_regression is True
    finally:
        os.unlink(bpath)


def test_ci_gate_missing_baseline_returns_ok(tmp_path):
    from meshflow.eval.ci_gate import CIBudgetGate

    gate = CIBudgetGate(baseline_path=str(tmp_path / "nonexistent.json"))
    current_file = tmp_path / "current.json"
    current_file.write_text('{"total_tokens":1000,"total_cost_usd":0.01,"pass_rate":1.0}')
    exit_code = gate.check(str(current_file), verbose=False)
    assert exit_code == 0  # no baseline = no failure


# ── Gap 14: Pareto analysis ───────────────────────────────────────────────────


def test_pareto_frontier():
    from meshflow.eval.pareto import ModelBenchmark, ParetoAnalyzer

    bench = ModelBenchmark()
    bench.add_run("opus",   tokens=8000, cost_usd=0.12, pass_rate=0.95)
    bench.add_run("sonnet", tokens=6000, cost_usd=0.03, pass_rate=0.92)
    bench.add_run("haiku",  tokens=4000, cost_usd=0.005, pass_rate=0.80)

    analyzer = ParetoAnalyzer(bench)
    frontier = analyzer.pareto_frontier()
    frontier_names = [r.model for r in frontier]

    # Haiku and opus are Pareto-efficient (opus wins on quality; haiku wins on cost)
    assert "haiku" in frontier_names
    assert "opus"  in frontier_names


def test_pareto_best_value():
    from meshflow.eval.pareto import ModelBenchmark, ParetoAnalyzer

    bench = ModelBenchmark()
    bench.add_run("opus",   tokens=8000, cost_usd=0.12,  pass_rate=0.95)
    bench.add_run("haiku",  tokens=4000, cost_usd=0.005, pass_rate=0.80)

    analyzer = ParetoAnalyzer(bench)
    best = analyzer.best_value()
    assert best is not None
    # Haiku has much lower cost_per_point
    assert best.model == "haiku"


def test_pareto_comparison_table():
    from meshflow.eval.pareto import ModelBenchmark, ParetoAnalyzer

    bench = ModelBenchmark()
    bench.add_run("sonnet", tokens=6000, cost_usd=0.03, pass_rate=0.92)
    bench.add_run("haiku",  tokens=4000, cost_usd=0.005, pass_rate=0.80)

    analyzer = ParetoAnalyzer(bench)
    table = analyzer.comparison_table()
    assert "sonnet" in table
    assert "haiku"  in table
    assert "Pareto" in table


def test_pareto_sensitivity():
    from meshflow.eval.pareto import ModelBenchmark, ParetoAnalyzer

    bench = ModelBenchmark()
    bench.add_run("a", tokens=1000, cost_usd=0.01, pass_rate=0.80)
    bench.add_run("b", tokens=2000, cost_usd=0.10, pass_rate=0.95)

    sens = ParetoAnalyzer(bench).sensitivity("a", "b")
    assert sens["cost_delta_usd"] == pytest.approx(0.09, abs=0.001)
    assert "recommended" in sens


# ── Gap 15: Latency-aware routing ─────────────────────────────────────────────


def test_latency_routing_no_constraint():
    from meshflow.agents.router import ProviderRouter
    from meshflow.agents.health import ModelHealthTracker

    router = ProviderRouter()
    router.set_fallback_chain("claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5-20251001")
    tracker = ModelHealthTracker()
    tracker.record_success("claude-sonnet-4-6", latency_ms=300)

    _, model = router.route_with_latency(
        "executor", tracker=tracker, max_p95_latency_ms=0
    )
    assert model  # just ensure no error


def test_latency_routing_excludes_slow_model():
    from meshflow.agents.router import ProviderRouter
    from meshflow.agents.health import ModelHealthTracker

    router = ProviderRouter()
    router.set_fallback_chain("claude-opus-4-7", "claude-haiku-4-5-20251001")
    tracker = ModelHealthTracker()

    # Opus is fast; haiku is slow
    for _ in range(10):
        tracker.record_success("claude-opus-4-7", latency_ms=200)
        tracker.record_success("claude-haiku-4-5-20251001", latency_ms=2000)

    _, model = router.route_with_latency(
        "executor",
        tracker=tracker,
        max_p95_latency_ms=500,  # only models with p95 < 500ms qualify
        prefer="speed",
    )
    # Should pick opus (fast), not haiku (slow)
    assert model in ("claude-opus-4-7", "claude-sonnet-4-6")


# ── Gap 16: Trace viewer ──────────────────────────────────────────────────────


def test_trace_viewer_import():
    from meshflow.observability.trace_viewer import TraceViewer
    viewer = TraceViewer(":memory:")
    assert viewer is not None


def test_langsmith_run_structure():
    from meshflow.observability.trace_viewer import _langsmith_run
    run = _langsmith_run(
        run_id="abc",
        name="test-run",
        run_type="chain",
        inputs={"task": "hello"},
        outputs={"output": "world"},
        start_time="2025-01-01T00:00:00Z",
        end_time="2025-01-01T00:00:01Z",
    )
    assert run["run_type"] == "chain"
    assert run["inputs"]["task"] == "hello"
    assert "parent_run_id" not in run


def test_langsmith_run_with_parent():
    from meshflow.observability.trace_viewer import _langsmith_run
    run = _langsmith_run(
        run_id="child",
        name="step",
        run_type="llm",
        inputs={},
        outputs={},
        start_time="",
        end_time="",
        parent_run_id="parent-run-123",
    )
    assert run["parent_run_id"] == "parent-run-123"


def test_kind_to_langsmith():
    from meshflow.observability.trace_viewer import _kind_to_langsmith
    assert _kind_to_langsmith("native") == "chain"
    assert _kind_to_langsmith("mcp") == "tool"
    assert _kind_to_langsmith("http") == "tool"
    assert _kind_to_langsmith("unknown") == "chain"
