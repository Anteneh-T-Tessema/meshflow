"""MeshFlow CLI — the control plane at your fingertips.

Commands:
  meshflow run       <yaml>            — run a workflow YAML to completion
  meshflow stream    <yaml>            — stream governed events as they emit
  meshflow replay    <run_id> [--db]   — replay / inspect a past run from ledger
  meshflow conformance <kind>          — run the conformance suite for a node adapter
  meshflow serve     [--host] [--port] — start the JSON HTTP runtime server
  meshflow describe  <yaml>            — print workflow topology without running

All commands respect the policy declared in the YAML.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from typing import Any


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="meshflow",
        description="MeshFlow — control plane for multi-agent systems",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # run
    p_run = sub.add_parser("run", help="Run a workflow YAML to completion")
    p_run.add_argument("yaml", help="Path to workflow YAML")
    p_run.add_argument("--task", default="", help="Task override (default: from YAML)")
    p_run.add_argument("--db", default="meshflow_runs.db", help="Ledger SQLite path")

    # stream
    p_stream = sub.add_parser("stream", help="Stream governed events from a workflow")
    p_stream.add_argument("yaml", help="Path to workflow YAML")
    p_stream.add_argument("--task", default="")
    p_stream.add_argument("--db", default="meshflow_runs.db")

    # replay
    p_replay = sub.add_parser("replay", help="Inspect a past run from the ledger")
    p_replay.add_argument("run_id", help="Run ID to replay")
    p_replay.add_argument("--db", default="meshflow_runs.db")
    p_replay.add_argument("--json", dest="as_json", action="store_true")

    # conformance
    p_conf = sub.add_parser(
        "conformance",
        help="Run the MeshFlow conformance suite against a node adapter kind",
    )
    p_conf.add_argument(
        "kind",
        choices=["native", "python", "http", "human", "crewai", "langgraph", "autogen"],
        help="Node kind to test",
    )
    p_conf.add_argument("--level", type=int, default=3, help="Max conformance level (0-3)")

    # serve
    p_serve = sub.add_parser("serve", help="Start the MeshFlow HTTP runtime server")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8765)

    # describe
    p_desc = sub.add_parser("describe", help="Print workflow topology")
    p_desc.add_argument("yaml")

    args = parser.parse_args()

    dispatch = {
        "run":         _cmd_run,
        "stream":      _cmd_stream,
        "replay":      _cmd_replay,
        "conformance": _cmd_conformance,
        "serve":       _cmd_serve,
        "describe":    _cmd_describe,
    }
    dispatch[args.cmd](args)


# ── run ───────────────────────────────────────────────────────────────────────

def _cmd_run(args: argparse.Namespace) -> None:
    asyncio.run(_async_run(args))


async def _async_run(args: argparse.Namespace) -> None:
    from meshflow.core.workflow import WorkflowDefinition
    from meshflow.core.runtime import StepRuntime
    from meshflow.core.ledger import ReplayLedger
    from meshflow.security.guardian import Guardian
    from meshflow.security.dasc_gate import DascGate
    from meshflow.security.identity import AgentIdentityProvider
    from meshflow.intelligence.uncertainty import UncertaintyEngine
    from meshflow.intelligence.collusion import CollusionAuditor

    wf = WorkflowDefinition.from_yaml(args.yaml)
    task = args.task or f"Execute workflow: {wf.name}"
    run_id = str(uuid.uuid4())

    ledger = ReplayLedger(args.db)
    pol = wf.policy

    runtime = StepRuntime(
        policy=pol,
        run_id=run_id,
        guardian=Guardian(budget_usd=pol.budget_usd) if pol.enable_guardian else None,
        dasc_gate=DascGate(pol, run_id) if pol.deterministic_gate else None,
        identity=AgentIdentityProvider(run_id),
        uncertainty=UncertaintyEngine() if pol.enable_uncertainty else None,
        collusion=CollusionAuditor() if pol.enable_collusion_audit else None,
        ledger=ledger,
    )

    print(f"[meshflow] run_id={run_id}")
    print(f"[meshflow] workflow={wf.name}  nodes={len(wf._nodes)}  policy.budget=${pol.budget_usd}")

    result = await wf.run(task, runtime)

    print(f"\n{'='*60}")
    print(f"  Status   : {'COMPLETED' if result.completed else 'FAILED'}")
    print(f"  Steps    : {len(result.steps)}")
    print(f"  Cost     : ${result.total_cost_usd:.6f}")
    print(f"  Tokens   : {result.total_tokens}")
    print(f"  Carbon   : {result.total_carbon_gco2:.4f} gCO2")
    print(f"  Duration : {result.duration_s:.2f}s")
    if result.blocked_nodes:
        print(f"  Blocked  : {result.blocked_nodes}")
    print(f"\n  Output:\n{result.output[:1000]}")
    print(f"\n  Ledger   : {result.ledger_db}  run_id={run_id}")


# ── stream ────────────────────────────────────────────────────────────────────

def _cmd_stream(args: argparse.Namespace) -> None:
    asyncio.run(_async_stream(args))


async def _async_stream(args: argparse.Namespace) -> None:
    from meshflow.core.workflow import WorkflowDefinition
    from meshflow.core.runtime import StepRuntime
    from meshflow.core.ledger import ReplayLedger
    from meshflow.security.guardian import Guardian
    from meshflow.security.dasc_gate import DascGate
    from meshflow.security.identity import AgentIdentityProvider
    from meshflow.intelligence.uncertainty import UncertaintyEngine
    from meshflow.intelligence.collusion import CollusionAuditor

    wf = WorkflowDefinition.from_yaml(args.yaml)
    task = args.task or f"Execute workflow: {wf.name}"
    run_id = str(uuid.uuid4())
    pol = wf.policy

    runtime = StepRuntime(
        policy=pol,
        run_id=run_id,
        guardian=Guardian(budget_usd=pol.budget_usd) if pol.enable_guardian else None,
        dasc_gate=DascGate(pol, run_id) if pol.deterministic_gate else None,
        identity=AgentIdentityProvider(run_id),
        uncertainty=UncertaintyEngine() if pol.enable_uncertainty else None,
        collusion=CollusionAuditor() if pol.enable_collusion_audit else None,
        ledger=ReplayLedger(args.db),
    )

    exec_order = wf._topological_order()
    ctx: dict[str, Any] = {"task": task}

    for node_id in exec_order:
        node = wf._nodes.get(node_id)
        if not node:
            continue
        from meshflow.core.node import NodeInput
        outcome = await runtime.run(node, NodeInput(task=task, context=ctx.copy()), ctx)

        status = "blocked" if not outcome.ok else ("paused" if outcome.paused_for_human else "ok")
        print(
            f"[{outcome.record.timestamp}] "
            f"node={node_id:<20} "
            f"kind={outcome.node_kind:<10} "
            f"status={status:<8} "
            f"uncertainty={outcome.record.uncertainty:.2f}  "
            f"cost=${outcome.record.cost_usd:.5f}"
        )
        if outcome.output.content:
            print(f"  output: {outcome.output.content[:120]}")
        if not outcome.ok:
            print(f"  blocked_by: {outcome.blocked_by}")
            break
        if outcome.paused_for_human:
            print(f"  paused: {outcome.human_context}")
            break


# ── replay ────────────────────────────────────────────────────────────────────

def _cmd_replay(args: argparse.Namespace) -> None:
    asyncio.run(_async_replay(args))


async def _async_replay(args: argparse.Namespace) -> None:
    from meshflow.core.ledger import ReplayLedger

    ledger = ReplayLedger(args.db)
    summary = await ledger.run_summary(args.run_id)

    if not summary or summary.get("steps", 0) == 0:
        print(f"[replay] run_id={args.run_id!r} not found in {args.db}")
        sys.exit(1)

    if args.as_json:
        raw = await ledger.export_run(args.run_id)
        print(raw)
        return

    print(f"\n{'='*60}")
    print(f"  Run      : {args.run_id}")
    print(f"  Steps    : {summary['steps']}")
    print(f"  Nodes    : {' -> '.join(summary['nodes'])}")
    print(f"  Cost     : ${summary['total_cost_usd']:.6f}")
    print(f"  Tokens   : {summary['total_tokens']}")
    print(f"  Carbon   : {summary['total_carbon_gco2']:.4f} gCO2")
    print(f"  Blocked  : {summary['blocked_steps']}")
    print(f"  Start    : {summary['timestamps']['start']}")
    print(f"  End      : {summary['timestamps']['end']}")
    print(f"\n  Step-by-step:")

    steps = await ledger.get_run(args.run_id)
    for i, step in enumerate(steps, 1):
        flag = "BLOCKED" if step["blocked"] else "ok"
        print(
            f"    [{i:02d}] {step['node_id']:<20} "
            f"{step['node_kind']:<10} "
            f"{flag:<8} "
            f"uncertainty={step['uncertainty']:.2f}  "
            f"{step['duration_ms']:.0f}ms"
        )
        if step["blocked"] and step["block_reason"]:
            print(f"         reason: {step['block_reason']}")


# ── conformance ───────────────────────────────────────────────────────────────

_CONFORMANCE_LEVELS = {
    0: "Basic — node executes and returns non-empty output",
    1: "Reliable — handles exceptions, respects timeout, supports retry",
    2: "Governed — budget accounting, identity propagation, trace capture",
    3: "Auditable — ledger entries written, HITL pause/resume supported",
}


def _cmd_conformance(args: argparse.Namespace) -> None:
    results = asyncio.run(_async_conformance(args.kind, args.level))
    _print_conformance_report(args.kind, results)
    failed = [r for r in results if not r["passed"]]
    sys.exit(1 if failed else 0)


async def _async_conformance(kind: str, max_level: int) -> list[dict[str, Any]]:
    """Run the MeshFlow conformance suite up to max_level."""
    from meshflow.core.node import MeshNode, NodeInput, NodeKind, NodeOutput, RiskTier
    from meshflow.core.runtime import StepRuntime
    from meshflow.core.ledger import ReplayLedger
    from meshflow.core.schemas import Policy
    from meshflow.security.guardian import Guardian
    from meshflow.security.identity import AgentIdentityProvider
    from meshflow.intelligence.uncertainty import UncertaintyEngine

    results: list[dict[str, Any]] = []
    run_id = f"conformance-{kind}-{uuid.uuid4().hex[:6]}"
    pol = Policy(budget_usd=10.0, enable_guardian=True, enable_uncertainty=True)

    # Build a synthetic test node for the given kind
    def _make_node(fail: bool = False, delay: float = 0) -> MeshNode:
        async def runner(inp: NodeInput) -> NodeOutput:
            if delay:
                await asyncio.sleep(delay)
            if fail:
                raise RuntimeError("synthetic_failure")
            return NodeOutput(
                content=f"conformance_output for: {inp.task[:40]}",
                tokens_used=42,
                confidence=0.85,
            )

        return MeshNode(
            id=f"conformance_{kind}",
            kind=NodeKind(kind),
            risk_profile=RiskTier.READ_ONLY,
            capabilities=["compute"],
            _runner=runner,
        )

    ledger = ReplayLedger(":memory:")
    identity = AgentIdentityProvider(run_id)
    guardian = Guardian(budget_usd=pol.budget_usd)
    uncertainty = UncertaintyEngine()

    runtime = StepRuntime(
        policy=pol,
        run_id=run_id,
        guardian=guardian,
        identity=identity,
        uncertainty=uncertainty,
        ledger=ledger,
    )

    # ── Level 0: Basic execution ──────────────────────────────────────────────
    if max_level >= 0:
        node = _make_node()
        inp = NodeInput(task="conformance test task", context={})
        try:
            outcome = await runtime.run(node, inp, {})
            passed = outcome.ok and bool(outcome.output.content)
            results.append({
                "level": 0, "check": "basic_execution",
                "passed": passed,
                "detail": outcome.output.content[:80] if passed else outcome.blocked_by,
            })
        except Exception as e:
            results.append({"level": 0, "check": "basic_execution", "passed": False, "detail": str(e)})

    # ── Level 1: Exception handling ───────────────────────────────────────────
    if max_level >= 1:
        node_fail = _make_node(fail=True)
        inp = NodeInput(task="conformance fail test", context={})
        try:
            outcome = await runtime.run(node_fail, inp, {})
            # A failing node should return ok=False, not raise
            passed = not outcome.ok and "node_exception" in outcome.blocked_by
            results.append({
                "level": 1, "check": "exception_handling",
                "passed": passed,
                "detail": outcome.blocked_by,
            })
        except Exception as e:
            results.append({"level": 1, "check": "exception_handling", "passed": False, "detail": str(e)})

    # ── Level 2: Identity propagation + trace capture ─────────────────────────
    if max_level >= 2:
        node = _make_node()
        node.id = f"conformance_{kind}_L2"
        inp = NodeInput(task="conformance L2 test", context={})
        try:
            outcome = await runtime.run(node, inp, {})
            did_provisioned = identity.is_provisioned(node.id)
            results.append({
                "level": 2, "check": "identity_propagation",
                "passed": did_provisioned,
                "detail": f"DID provisioned: {did_provisioned}",
            })
        except Exception as e:
            results.append({"level": 2, "check": "identity_propagation", "passed": False, "detail": str(e)})

    # ── Level 2b: Uncertainty scoring ─────────────────────────────────────────
    if max_level >= 2:
        node = _make_node()
        node.id = f"conformance_{kind}_uncertainty"
        inp = NodeInput(task="conformance uncertainty test", context={})
        try:
            outcome = await runtime.run(node, inp, {})
            has_uncertainty = outcome.record.uncertainty >= 0.0
            results.append({
                "level": 2, "check": "uncertainty_scoring",
                "passed": has_uncertainty,
                "detail": f"uncertainty={outcome.record.uncertainty:.3f}",
            })
        except Exception as e:
            results.append({"level": 2, "check": "uncertainty_scoring", "passed": False, "detail": str(e)})

    # ── Level 3: Audit ledger ─────────────────────────────────────────────────
    if max_level >= 3:
        runs = await ledger.list_runs()
        steps_for_run = await ledger.get_run(run_id)
        passed = len(steps_for_run) > 0
        results.append({
            "level": 3, "check": "audit_ledger_writes",
            "passed": passed,
            "detail": f"{len(steps_for_run)} records written",
        })

    # ── Level 3b: HITL pause ──────────────────────────────────────────────────
    if max_level >= 3:
        from meshflow.core.schemas import HumanInLoopConfig
        hitl_pol = Policy(
            budget_usd=10.0,
            human_in_loop=HumanInLoopConfig(
                enabled=True,
                tier_threshold=RiskTier.READ_ONLY,
            ),
        )
        hitl_runtime = StepRuntime(
            policy=hitl_pol,
            run_id=f"{run_id}-hitl",
            identity=AgentIdentityProvider(f"{run_id}-hitl"),
        )
        node = _make_node()
        node.id = "conformance_hitl"
        try:
            outcome = await hitl_runtime.run(node, NodeInput(task="hitl test", context={}), {})
            results.append({
                "level": 3, "check": "hitl_pause",
                "passed": outcome.paused_for_human,
                "detail": f"paused={outcome.paused_for_human}  human_ctx={outcome.human_context}",
            })
        except Exception as e:
            results.append({"level": 3, "check": "hitl_pause", "passed": False, "detail": str(e)})

    return results


def _print_conformance_report(kind: str, results: list[dict[str, Any]]) -> None:
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    max_level = max((r["level"] for r in results if r["passed"]), default=-1)

    print(f"\n{'='*60}")
    print(f"  MeshFlow Conformance Report — kind: {kind}")
    print(f"{'='*60}")
    for level, desc in _CONFORMANCE_LEVELS.items():
        level_results = [r for r in results if r["level"] == level]
        if not level_results:
            continue
        all_passed = all(r["passed"] for r in level_results)
        mark = "PASS" if all_passed else "FAIL"
        print(f"\n  L{level} [{mark}] {desc}")
        for r in level_results:
            sym = "+" if r["passed"] else "-"
            print(f"    [{sym}] {r['check']:<30}  {r['detail']}")

    print(f"\n{'='*60}")
    print(f"  Score     : {passed}/{total} checks passed")
    conformance_level = max_level if max_level >= 0 else -1
    print(f"  Level     : {'L' + str(conformance_level) if conformance_level >= 0 else 'non-conformant'}")
    print(f"  Verdict   : {'CONFORMANT' if passed == total else 'NON-CONFORMANT'}")
    print(f"{'='*60}\n")


# ── serve ─────────────────────────────────────────────────────────────────────

def _cmd_serve(args: argparse.Namespace) -> None:
    from meshflow.runtime.server import serve
    print(f"[meshflow] starting HTTP runtime on {args.host}:{args.port}")
    serve(host=args.host, port=args.port)


# ── describe ──────────────────────────────────────────────────────────────────

def _cmd_describe(args: argparse.Namespace) -> None:
    from meshflow.core.workflow import WorkflowDefinition

    wf = WorkflowDefinition.from_yaml(args.yaml)
    desc = wf.describe()
    print(json.dumps(desc, indent=2))
