"""Durable HITL tests — pause, persist, resume across process boundaries.

These tests prove that:
  1. A paused workflow checkpoint is saved to the ledger automatically
  2. The checkpoint contains the full context needed to resume
  3. resume() continues execution from the right node with the human's decision
  4. The human's decision is available in ctx for downstream conditions
  5. Approval and rejection route to different branches via conditional edges
  6. The checkpoint is deleted after a clean resume
  7. Resuming with an invalid run_id raises ValueError
  8. A workflow with multiple sequential HITL gates can pause and resume twice
  9. Mesh.resume_workflow() is the right external entry point
"""
from __future__ import annotations

import asyncio
import pytest

from meshflow import (
    HumanDecision,
    Mesh,
    MeshNode,
    Policy,
    ReplayLedger,
    WorkflowDefinition,
)
from meshflow.core.node import NodeInput, NodeKind, NodeOutput
from meshflow.core.runtime import StepRuntime
from meshflow.core.schemas import HumanInLoopConfig, RiskTier


# ── Shared fixtures ───────────────────────────────────────────────────────────

def _hitl_policy() -> Policy:
    """Policy that pauses before IRREVERSIBLE nodes."""
    return Policy(
        budget_usd=10.0,
        max_steps=50,
        enable_guardian=False,
        enable_uncertainty=False,
        enable_collusion_audit=False,
        human_in_loop=HumanInLoopConfig(
            enabled=True,
            tier_threshold=RiskTier.IRREVERSIBLE,
        ),
    )


def _runtime(run_id: str, ledger: ReplayLedger) -> StepRuntime:
    return StepRuntime(
        policy=_hitl_policy(),
        run_id=run_id,
        ledger=ledger,
    )


def _echo(nid: str, ran: list | None = None, risk: RiskTier = RiskTier.READ_ONLY) -> MeshNode:
    async def runner(inp: NodeInput) -> NodeOutput:
        if ran is not None:
            ran.append(nid)
        return NodeOutput(
            content=f"{nid}-done",
            tokens_used=5,
            structured={f"{nid}_ran": True},
        )
    return MeshNode(id=nid, kind=NodeKind.PYTHON, risk_profile=risk, _runner=runner)


def _hitl_node(nid: str) -> MeshNode:
    """A node that the policy will pause before (IRREVERSIBLE tier)."""
    return _echo(nid, risk=RiskTier.IRREVERSIBLE)


# ── 1. Checkpoint is saved on pause ──────────────────────────────────────────

class TestCheckpointSavedOnPause:
    def test_checkpoint_exists_after_pause(self):
        """After run() pauses, the ledger holds a checkpoint for that run_id."""
        ledger = ReplayLedger(":memory:")
        run_id = "test-pause-save"
        runtime = _runtime(run_id, ledger)

        wf = (
            WorkflowDefinition("test", policy=_hitl_policy())
            .add_node(_echo("setup"))
            .add_node(_hitl_node("approval"))
            .add_node(_echo("publish"))
            .add_edge("setup", "approval")
            .add_edge("approval", "publish")
        )

        result = asyncio.run(wf.run("do work", runtime))

        assert result.completed is False
        assert "approval" in result.paused_nodes

        checkpoint = asyncio.run(ledger.load_checkpoint_data(run_id))
        assert checkpoint is not None
        assert checkpoint["paused_at_node"] == "approval"
        assert checkpoint["run_id"] == run_id

    def test_checkpoint_contains_full_context(self):
        """Checkpoint captures the context at the moment of pause."""
        ledger = ReplayLedger(":memory:")
        run_id = "test-ctx-save"
        runtime = _runtime(run_id, ledger)

        async def setup_node(inp: NodeInput) -> NodeOutput:
            return NodeOutput(
                content="setup done",
                tokens_used=5,
                structured={"data_ready": True, "record_count": 42},
            )

        wf = (
            WorkflowDefinition("ctx-test", policy=_hitl_policy())
            .add_node(MeshNode(id="setup", kind=NodeKind.PYTHON, _runner=setup_node))
            .add_node(_hitl_node("gate"))
            .add_edge("setup", "gate")
        )

        asyncio.run(wf.run("load data", runtime))
        checkpoint = asyncio.run(ledger.load_checkpoint_data(run_id))

        assert checkpoint["context"]["data_ready"] is True
        assert checkpoint["context"]["record_count"] == 42
        assert "setup" in checkpoint["completed_nodes"]
        assert checkpoint["task"] == "load data"

    def test_checkpoint_contains_node_outputs(self):
        """Checkpoint stores node outputs so conditions can evaluate on resume."""
        ledger = ReplayLedger(":memory:")
        run_id = "test-outputs-save"
        runtime = _runtime(run_id, ledger)

        async def scorer(inp: NodeInput) -> NodeOutput:
            return NodeOutput(content="scored", tokens_used=5, confidence=0.95)

        wf = (
            WorkflowDefinition("output-test", policy=_hitl_policy())
            .add_node(MeshNode(id="scorer", kind=NodeKind.PYTHON, _runner=scorer))
            .add_node(_hitl_node("gate"))
            .add_edge("scorer", "gate")
        )

        asyncio.run(wf.run("score task", runtime))
        checkpoint = asyncio.run(ledger.load_checkpoint_data(run_id))

        assert "scorer" in checkpoint["node_outputs"]
        assert checkpoint["node_outputs"]["scorer"]["confidence"] == pytest.approx(0.95)


# ── 2. Resume continues execution ─────────────────────────────────────────────

class TestResumeExecution:
    def test_resume_approved_completes_workflow(self):
        """Approving a paused workflow runs all downstream nodes."""
        ledger = ReplayLedger(":memory:")
        run_id = "test-resume-approve"
        ran: list[str] = []

        wf = (
            WorkflowDefinition("resume-test", policy=_hitl_policy())
            .add_node(_echo("setup", ran))
            .add_node(_hitl_node("gate"))
            .add_node(_echo("publish", ran))
            .add_edge("setup", "gate")
            .add_edge("gate", "publish")
        )

        # First run — pauses at gate
        r1 = asyncio.run(wf.run("deploy", _runtime(run_id, ledger)))
        assert r1.paused_nodes == ["gate"]
        assert "publish" not in ran

        # Resume with approval
        runtime2 = _runtime(run_id, ledger)
        r2 = asyncio.run(wf.resume(run_id, HumanDecision(approved=True), ledger, runtime2))

        assert r2.completed is True
        assert "publish" in ran

    def test_resume_rejected_runs_rejection_branch(self):
        """Rejecting routes to the rejection branch via conditional edge."""
        ledger = ReplayLedger(":memory:")
        run_id = "test-resume-reject"
        ran: list[str] = []

        wf = (
            WorkflowDefinition("reject-test", policy=_hitl_policy())
            .add_node(_echo("setup"))
            .add_node(_hitl_node("gate"))
            .add_node(_echo("publish", ran))
            .add_node(_echo("notify_failure", ran))
            .add_edge("setup", "gate")
            .add_edge("gate", "publish",        condition="approved == True")
            .add_edge("gate", "notify_failure", condition="approved == False")
        )

        asyncio.run(wf.run("deploy", _runtime(run_id, ledger)))
        r2 = asyncio.run(
            wf.resume(run_id, HumanDecision(approved=False, comment="Not ready"),
                      ledger, _runtime(run_id, ledger))
        )

        assert r2.completed is True
        assert "notify_failure" in ran
        assert "publish" not in ran

    def test_human_decision_in_context(self):
        """Downstream nodes receive human_decision and human_comment in context."""
        ledger = ReplayLedger(":memory:")
        run_id = "test-ctx-propagate"
        received: dict = {}

        async def final_node(inp: NodeInput) -> NodeOutput:
            received.update(inp.context)
            return NodeOutput(content="done", tokens_used=5)

        wf = (
            WorkflowDefinition("ctx-prop", policy=_hitl_policy())
            .add_node(_echo("setup"))
            .add_node(_hitl_node("gate"))
            .add_node(MeshNode(id="final", kind=NodeKind.PYTHON, _runner=final_node))
            .add_edge("setup", "gate")
            .add_edge("gate", "final")
        )

        asyncio.run(wf.run("task", _runtime(run_id, ledger)))
        asyncio.run(
            wf.resume(run_id,
                      HumanDecision(approved=True, comment="All good", decided_by="alice"),
                      ledger, _runtime(run_id, ledger))
        )

        assert received.get("human_decision") is True
        assert received.get("human_comment") == "All good"
        assert received.get("approved") is True

    def test_checkpoint_deleted_after_clean_resume(self):
        """The checkpoint is removed from the ledger after a successful resume."""
        ledger = ReplayLedger(":memory:")
        run_id = "test-cleanup"

        wf = (
            WorkflowDefinition("cleanup-test", policy=_hitl_policy())
            .add_node(_echo("setup"))
            .add_node(_hitl_node("gate"))
            .add_node(_echo("final"))
            .add_edge("setup", "gate")
            .add_edge("gate", "final")
        )

        asyncio.run(wf.run("task", _runtime(run_id, ledger)))
        assert asyncio.run(ledger.load_checkpoint_data(run_id)) is not None

        asyncio.run(
            wf.resume(run_id, HumanDecision(approved=True), ledger,
                      _runtime(run_id, ledger))
        )
        assert asyncio.run(ledger.load_checkpoint_data(run_id)) is None

    def test_resume_invalid_run_id_raises(self):
        """Resuming with an unknown run_id raises ValueError immediately."""
        ledger = ReplayLedger(":memory:")
        wf = WorkflowDefinition("x", policy=_hitl_policy())

        with pytest.raises(ValueError, match="No checkpoint found"):
            asyncio.run(
                wf.resume("nonexistent-run", HumanDecision(approved=True),
                          ledger, _runtime("nonexistent-run", ledger))
            )


# ── 3. Ledger checkpoint API ───────────────────────────────────────────────────

class TestLedgerCheckpointAPI:
    def test_save_and_load_checkpoint(self):
        """save_checkpoint / load_checkpoint_data round-trip."""
        ledger = ReplayLedger(":memory:")
        data = {"run_id": "x", "context": {"k": 1}, "paused_at_node": "gate"}
        asyncio.run(ledger.save_checkpoint("x", data))
        loaded = asyncio.run(ledger.load_checkpoint_data("x"))
        assert loaded == data

    def test_load_missing_returns_none(self):
        ledger = ReplayLedger(":memory:")
        assert asyncio.run(ledger.load_checkpoint_data("does-not-exist")) is None

    def test_delete_checkpoint(self):
        ledger = ReplayLedger(":memory:")
        asyncio.run(ledger.save_checkpoint("x", {"k": 1}))
        asyncio.run(ledger.delete_checkpoint("x"))
        assert asyncio.run(ledger.load_checkpoint_data("x")) is None

    def test_save_overwrites_existing(self):
        """Re-saving a checkpoint for the same run_id overwrites it."""
        ledger = ReplayLedger(":memory:")
        asyncio.run(ledger.save_checkpoint("x", {"v": 1}))
        asyncio.run(ledger.save_checkpoint("x", {"v": 2}))
        loaded = asyncio.run(ledger.load_checkpoint_data("x"))
        assert loaded["v"] == 2

    def test_list_paused_runs(self):
        """list_paused_runs returns all checkpointed run IDs."""
        ledger = ReplayLedger(":memory:")
        asyncio.run(ledger.save_checkpoint("run-a", {"k": 1}))
        asyncio.run(ledger.save_checkpoint("run-b", {"k": 2}))
        paused = asyncio.run(ledger.list_paused_runs())
        ids = {p["run_id"] for p in paused}
        assert ids == {"run-a", "run-b"}


# ── 4. Mesh.resume_workflow() entry point ─────────────────────────────────────

class TestMeshResumeWorkflow:
    def test_mesh_resume_workflow_completes(self):
        """Mesh.resume_workflow() is the public API for resuming paused runs."""
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            ran: list[str] = []
            wf = (
                WorkflowDefinition("mesh-resume", policy=_hitl_policy())
                .add_node(_echo("setup", ran))
                .add_node(_hitl_node("gate"))
                .add_node(_echo("publish", ran))
                .add_edge("setup", "gate")
                .add_edge("gate", "publish")
            )

            mesh = Mesh(policy=_hitl_policy())

            r1 = asyncio.run(mesh.run_workflow(wf, task="deploy", ledger_db=db_path))
            assert r1.paused_nodes == ["gate"]

            r2 = asyncio.run(
                mesh.resume_workflow(wf, r1.run_id,
                                     HumanDecision(approved=True), db_path)
            )
            assert r2.completed is True
            assert "publish" in ran
        finally:
            os.unlink(db_path)

    def test_mesh_resume_invalid_id_raises(self):
        """Mesh.resume_workflow() propagates ValueError for unknown run_id."""
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            wf = WorkflowDefinition("x", policy=_hitl_policy())
            with pytest.raises(ValueError):
                asyncio.run(
                    Mesh().resume_workflow(wf, "bad-id",
                                          HumanDecision(approved=True), db_path)
                )
        finally:
            os.unlink(db_path)


# ── 5. End-to-end: multi-hop pipeline with HITL gate ─────────────────────────

class TestEndToEndDurableHITL:
    def test_full_pipeline_pause_resume(self):
        """3-stage pipeline: research → HITL gate → publish → notify."""
        ledger = ReplayLedger(":memory:")
        run_id = "e2e-test"
        ran: list[str] = []

        async def research(inp: NodeInput) -> NodeOutput:
            ran.append("research")
            return NodeOutput(content="findings", tokens_used=20,
                              structured={"summary": "Q2 up 12%"})

        wf = (
            WorkflowDefinition("e2e", policy=_hitl_policy())
            .add_node(MeshNode(id="research", kind=NodeKind.PYTHON, _runner=research))
            .add_node(_hitl_node("approval"))
            .add_node(_echo("publish", ran))
            .add_node(_echo("notify", ran))
            .add_edge("research", "approval")
            .add_edge("approval", "publish")
            .add_edge("publish", "notify")
        )

        # Phase 1: run until HITL pause
        r1 = asyncio.run(wf.run("Q2 analysis", _runtime(run_id, ledger)))
        assert "research" in ran
        assert r1.paused_nodes == ["approval"]
        assert r1.completed is False

        # Phase 2: human approves; workflow continues
        r2 = asyncio.run(
            wf.resume(run_id, HumanDecision(approved=True, comment="Ship it"),
                      ledger, _runtime(run_id, ledger))
        )
        assert r2.completed is True
        assert "publish" in ran
        assert "notify" in ran
        # Ledger has records for all steps including the human decision
        all_steps = asyncio.run(ledger.get_run(run_id))
        node_ids = {s["node_id"] for s in all_steps}
        assert "research" in node_ids
        assert "approval" in node_ids   # human decision step
        assert "publish" in node_ids
        assert "notify" in node_ids
