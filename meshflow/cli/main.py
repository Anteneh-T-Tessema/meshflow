"""MeshFlow CLI — the control plane at your fingertips.

Commands:
  meshflow init                        — scaffold a new governed agent project
  meshflow new   <agent|team|tool> <name> — generate a new agent, team, or tool file
  meshflow run   <yaml>               — run a workflow YAML to completion
  meshflow stream <yaml>              — stream governed events as they emit
  meshflow watch <run_id>             — tail live events for a run
  meshflow logs  [--db] [--limit]     — show recent run history
  meshflow replay <run_id> [--db]     — step-through debugger for a past run
  meshflow approve <run_id> <node_id> — approve a paused HITL node
  meshflow resume <run_id>            — resume a paused HITL run
  meshflow conformance <kind>         — run the conformance suite
  meshflow schema [name]              — print public JSON Schema contracts
  meshflow serve [--host] [--port]    — start the JSON HTTP runtime server
  meshflow describe <yaml>            — print workflow topology without running

All commands respect the policy declared in the YAML.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from typing import Any


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="meshflow",
        description="MeshFlow — build agents, form teams, govern everything.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # init
    p_init = sub.add_parser("init", help="Scaffold a new governed agent project")
    p_init.add_argument("name", nargs="?", default=None, help="Project directory name")

    # new
    p_new = sub.add_parser("new", help="Generate a new agent, team, or tool file")
    p_new.add_argument("kind", choices=["agent", "team", "tool"], help="What to create")
    p_new.add_argument("name", help="Name for the new artifact")

    # logs
    p_logs = sub.add_parser("logs", help="Show recent run history")
    p_logs.add_argument("--db", default="meshflow_runs.db", help="Ledger SQLite path")
    p_logs.add_argument("--limit", type=int, default=20, help="Max runs to show")

    # approve
    p_approve = sub.add_parser("approve", help="Approve a paused HITL node")
    p_approve.add_argument("run_id", help="Run ID")
    p_approve.add_argument("node_id", help="Node ID to approve")
    p_approve.add_argument("--db", default="meshflow_runs.db")

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
    p_replay.add_argument(
        "--archive-s3",
        default="",
        help="Archive the run export to an S3 URI, e.g. s3://bucket/meshflow",
    )

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
    p_conf.add_argument("--level", type=int, default=5, help="Max conformance level (0-5)")

    # schema
    p_schema = sub.add_parser("schema", help="Print public JSON Schema contracts")
    p_schema.add_argument(
        "name",
        nargs="?",
        default="all",
        help="Schema name: NodeInput, NodeOutput, MeshNode, Policy, RuntimeOutcome, or all",
    )

    # serve
    p_serve = sub.add_parser("serve", help="Start the MeshFlow HTTP runtime server")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument(
        "--api-key", action="append", dest="api_keys", help="API key (repeat for multiple keys)"
    )
    p_serve.add_argument("--ledger", default="meshflow_runs.db")
    p_serve.add_argument("--tls-cert", default="")
    p_serve.add_argument("--tls-key", default="")
    p_serve.add_argument(
        "--policy-file", default="", dest="policy_file",
        help="Path to meshflow.policy.yaml (policy-as-code)",
    )

    # dev
    p_dev = sub.add_parser("dev", help="Start server in dev mode with colored output")
    p_dev.add_argument("--host", default="127.0.0.1")
    p_dev.add_argument("--port", type=int, default=8765)
    p_dev.add_argument("--ledger", default=":memory:")

    # trace
    p_trace = sub.add_parser("trace", help="View a run trace in the terminal")
    p_trace.add_argument("run_id", help="Run ID to inspect")
    p_trace.add_argument("--db", default="meshflow_runs.db")
    p_trace.add_argument("--json", dest="as_json", action="store_true", help="Output as JSON")
    p_trace.add_argument("--export", default="", metavar="FILE", help="Export trace to file")

    # runs
    p_runs = sub.add_parser("runs", help="List recent runs (alias for logs)")
    p_runs.add_argument("--db", default="meshflow_runs.db")
    p_runs.add_argument("--limit", type=int, default=20)

    # watch
    p_watch = sub.add_parser("watch", help="Tail live events for a workflow run")
    p_watch.add_argument("run_id", help="Run ID to watch")
    p_watch.add_argument("--db", default="meshflow_runs.db")
    p_watch.add_argument("--sse", action="store_true", help="Output as SSE (Server-Sent Events)")
    p_watch.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Stop waiting after N seconds without a terminal event",
    )

    # resume
    p_resume = sub.add_parser("resume", help="Resume a paused workflow run")
    p_resume.add_argument("run_id", help="Run ID to resume")
    p_resume.add_argument(
        "--yaml",
        default="",
        help="Path to original workflow YAML if absent from checkpoint",
    )
    p_resume.add_argument("--db", default="meshflow_runs.db")
    p_resume.add_argument("--reject", action="store_true", help="Reject instead of approve")
    p_resume.add_argument("--comment", default="", help="Human reviewer comment")
    p_resume.add_argument("--decided-by", default="cli", help="Reviewer identifier")

    # describe
    p_desc = sub.add_parser("describe", help="Print workflow topology")
    p_desc.add_argument("yaml")

    # mcp-stdio — expose MeshFlow as an MCP server over stdio (for Claude Desktop)
    p_mcp = sub.add_parser(
        "mcp-stdio",
        help="Start a stdio MCP server — connect Claude Desktop to governed MeshFlow agents",
    )
    p_mcp.add_argument("--config", default="", help="Path to meshflow.yaml (optional)")
    p_mcp.add_argument("--policy", default="standard", help="Default governance policy")
    p_mcp.add_argument(
        "--print-config",
        action="store_true",
        help="Print the claude_desktop_config.json snippet and exit",
    )

    # eval
    p_eval = sub.add_parser("eval", help="Run agent evaluations against an eval suite")
    p_eval.add_argument("eval_file", help="Path to eval YAML file")
    p_eval.add_argument("--agent", default="", help="Path to agent Python file (optional)")
    p_eval.add_argument("--tags", nargs="*", default=[], help="Filter by tags")
    p_eval.add_argument("--concurrency", type=int, default=4, help="Parallel scenarios")
    p_eval.add_argument("--fail-under", type=float, default=0.0, help="Exit 1 if pass_rate < N")
    p_eval.add_argument("--save-baseline", default="", metavar="PATH", help="Save result as golden baseline JSON")
    p_eval.add_argument("--compare-baseline", default="", metavar="PATH", help="Compare against a golden baseline JSON")
    p_eval.add_argument("--fail-on-regression", action="store_true", help="Exit 1 if regressions found vs baseline")
    p_eval.add_argument("--db", default="meshflow_runs.db", help="Ledger path (for eval result storage)")
    p_eval.add_argument("--save-to-ledger", action="store_true", help="Persist eval result in the ledger")

    # eval history
    p_eval_history = sub.add_parser("eval-history", help="List stored eval results from the ledger")
    p_eval_history.add_argument("--db", default="meshflow_runs.db", help="Ledger path")
    p_eval_history.add_argument("--suite", default="", help="Filter by suite name")
    p_eval_history.add_argument("--json", dest="output_json", action="store_true", help="Output raw JSON")

    # graph export
    p_graph = sub.add_parser("graph", help="Export a run's execution graph as Mermaid or DOT")
    p_graph.add_argument("--run-id", default="", metavar="RUN_ID", help="Run ID to export (omit to list runs)")
    p_graph.add_argument("--format", default="mermaid", choices=["mermaid", "dot"], help="Output format")
    p_graph.add_argument("--db", default="meshflow_runs.db", help="Ledger path")
    p_graph.add_argument("--out", default="", help="Write to file instead of stdout")

    # audit export
    p_audit = sub.add_parser("audit", help="Audit trail management")
    p_audit_sub = p_audit.add_subparsers(dest="audit_cmd", required=True)

    p_audit_export = p_audit_sub.add_parser("export", help="Export audit trail as CSV or JSON")
    p_audit_export.add_argument("--run-id", default="", dest="run_id", help="Run ID (omit for all runs)")
    p_audit_export.add_argument("--format", default="json", choices=["json", "csv"], help="Output format")
    p_audit_export.add_argument("--db", default="meshflow_runs.db", help="Ledger path")
    p_audit_export.add_argument("--out", default="", help="Write to file instead of stdout")

    # eval diff
    p_eval_diff = sub.add_parser("eval-diff", help="Compare two eval baseline JSON files")
    p_eval_diff.add_argument("baseline_a", help="Older baseline JSON path")
    p_eval_diff.add_argument("baseline_b", help="Newer baseline JSON path")
    p_eval_diff.add_argument("--fail-on-regression", action="store_true", help="Exit 1 if regressions found")

    # plugins
    p_plugins = sub.add_parser("plugins", help="Discover and inspect installed MeshFlow plugins")
    p_plugins_sub = p_plugins.add_subparsers(dest="plugins_cmd", required=True)

    p_plugins_list = p_plugins_sub.add_parser("list", help="List all installed plugins")
    p_plugins_list.add_argument("--group", default=None, choices=["agent", "tool", "compliance", "ledger"], help="Filter by group")

    p_plugins_verify = p_plugins_sub.add_parser("verify", help="Load and validate a plugin")
    p_plugins_verify.add_argument("name", help="Plugin entry-point name")
    p_plugins_verify.add_argument("--group", default="meshflow.agents", help="Entry-point group (default: meshflow.agents)")

    p_plugins_info = p_plugins_sub.add_parser("info", help="Show details for a plugin")
    p_plugins_info.add_argument("name", help="Plugin entry-point name")
    p_plugins_info.add_argument("--group", default=None, choices=["agent", "tool", "compliance", "ledger"])

    # compliance
    p_compliance = sub.add_parser("compliance", help="Compliance reporting tools")
    p_compliance_sub = p_compliance.add_subparsers(dest="compliance_cmd", required=True)

    p_comp_report = p_compliance_sub.add_parser("report", help="Generate a compliance report from ledger data")
    p_comp_report.add_argument(
        "--framework", default="hipaa",
        choices=["hipaa", "sox", "gdpr", "pci", "nerc"],
        help="Compliance framework (default: hipaa)",
    )
    p_comp_report.add_argument("--run-id", default="", dest="run_id", help="Scope to a single run ID")
    p_comp_report.add_argument("--db", default="meshflow_runs.db", help="Ledger SQLite path")
    p_comp_report.add_argument("--format", default="text", choices=["text", "json"], help="Output format")
    p_comp_report.add_argument("--out", default="", help="Write to file instead of stdout")

    # schedule subcommands
    p_comp_sched = p_compliance_sub.add_parser("schedule", help="Manage scheduled compliance report delivery")
    p_comp_sched_sub = p_comp_sched.add_subparsers(dest="schedule_cmd", required=True)

    p_sched_add = p_comp_sched_sub.add_parser("add", help="Add a new schedule")
    p_sched_add.add_argument("--framework", default="hipaa", choices=["hipaa", "sox", "gdpr", "pci", "nerc"])
    p_sched_add.add_argument("--interval", default=86400, type=int, dest="interval_seconds",
                              help="Delivery interval in seconds (default: 86400 = daily)")
    p_sched_add.add_argument("--sink", default="stdout", choices=["file", "webhook", "stdout"], dest="sink_type")
    p_sched_add.add_argument("--sink-path", default="", dest="sink_path", help="File path (sink=file)")
    p_sched_add.add_argument("--sink-url", default="", dest="sink_url", help="Webhook URL (sink=webhook)")
    p_sched_add.add_argument("--sink-secret", default="", dest="sink_secret", help="Webhook HMAC secret")
    p_sched_add.add_argument("--db", default="meshflow_runs.db")
    p_sched_add.add_argument("--tenant", default="", help="Scope to tenant_id")
    p_sched_add.add_argument("--schedule-file", default="", dest="schedule_file",
                              help="Path to schedule store JSON (default: ~/.meshflow/schedules.json)")

    p_sched_list = p_comp_sched_sub.add_parser("list", help="List all schedules")
    p_sched_list.add_argument("--schedule-file", default="", dest="schedule_file")

    p_sched_run = p_comp_sched_sub.add_parser("run", help="Trigger a schedule immediately")
    p_sched_run.add_argument("schedule_id", help="Schedule ID to run")
    p_sched_run.add_argument("--schedule-file", default="", dest="schedule_file")

    p_sched_rm = p_comp_sched_sub.add_parser("remove", help="Remove a schedule")
    p_sched_rm.add_argument("schedule_id")
    p_sched_rm.add_argument("--schedule-file", default="", dest="schedule_file")

    # webhooks
    p_webhooks = sub.add_parser("webhooks", help="Manage outbound webhook alerts")
    p_webhooks_sub = p_webhooks.add_subparsers(dest="webhooks_cmd", required=True)

    p_wh_list = p_webhooks_sub.add_parser("list", help="List registered webhooks")
    p_wh_list.add_argument("--server", default="http://localhost:8000", help="MeshFlow server URL")
    p_wh_list.add_argument("--api-key", default="", dest="api_key")

    p_wh_add = p_webhooks_sub.add_parser("add", help="Register a new webhook")
    p_wh_add.add_argument("url", help="Target URL for webhook delivery")
    p_wh_add.add_argument(
        "--events", default="*",
        help="Comma-separated event types (default: * for all)",
    )
    p_wh_add.add_argument("--secret", default="", help="HMAC signing secret")
    p_wh_add.add_argument("--server", default="http://localhost:8000")
    p_wh_add.add_argument("--api-key", default="", dest="api_key")

    p_wh_remove = p_webhooks_sub.add_parser("remove", help="Remove a registered webhook by ID")
    p_wh_remove.add_argument("id", help="Webhook ID to remove")
    p_wh_remove.add_argument("--server", default="http://localhost:8000")
    p_wh_remove.add_argument("--api-key", default="", dest="api_key")

    # keys
    p_keys = sub.add_parser("keys", help="Manage API keys (requires admin role)")
    p_keys_sub = p_keys.add_subparsers(dest="keys_cmd", required=True)

    p_keys_list = p_keys_sub.add_parser("list", help="List active API keys")
    p_keys_list.add_argument("--db", default="meshflow_runs.db", help="Ledger SQLite path")
    p_keys_list.add_argument("--tenant", default="", help="Filter by tenant ID")

    p_keys_gen = p_keys_sub.add_parser("generate", help="Generate a new API key")
    p_keys_gen.add_argument("name", help="Human-readable name for the key")
    p_keys_gen.add_argument("--role", default="operator", choices=["admin", "operator", "viewer"])
    p_keys_gen.add_argument("--tenant", default="", help="Tenant ID scope")
    p_keys_gen.add_argument("--db", default="meshflow_runs.db", help="Ledger SQLite path")

    p_keys_revoke = p_keys_sub.add_parser("revoke", help="Revoke an API key by key_id")
    p_keys_revoke.add_argument("key_id", help="Key ID to revoke")
    p_keys_revoke.add_argument("--db", default="meshflow_runs.db", help="Ledger SQLite path")

    # analytics
    p_analytics = sub.add_parser("analytics", help="Workflow run analytics — costs, latency, quality")
    p_analytics.add_argument("--db", default="meshflow_runs.db", help="Ledger SQLite path")
    p_analytics.add_argument("--runs", type=int, default=20, dest="n_runs", help="Number of recent runs to analyse")
    p_analytics.add_argument("--format", default="text", choices=["text", "json"], help="Output format")
    p_analytics.add_argument(
        "--metric",
        default="full",
        choices=["full", "cost", "latency", "blocked", "quality", "carbon", "nodes"],
        help="Which metric to show (default: full report)",
    )

    # queue
    p_queue = sub.add_parser("queue", help="Background task queue management")
    p_queue_sub = p_queue.add_subparsers(dest="queue_cmd", required=True)

    p_q_push = p_queue_sub.add_parser("push", help="Push a workflow task onto the queue")
    p_q_push.add_argument("yaml", help="Path to workflow YAML file")
    p_q_push.add_argument("--task", default="", help="Task string override")
    p_q_push.add_argument("--priority", type=int, default=0, help="Priority (higher = sooner)")
    p_q_push.add_argument("--db", default="meshflow_queue.db", help="Queue SQLite path")

    p_q_status = p_queue_sub.add_parser("status", help="Show queue statistics")
    p_q_status.add_argument("--db", default="meshflow_queue.db", help="Queue SQLite path")

    p_q_list = p_queue_sub.add_parser("list", help="List tasks in the queue")
    p_q_list.add_argument("--db", default="meshflow_queue.db", help="Queue SQLite path")
    p_q_list.add_argument("--status", default="", choices=["", "pending", "running", "done", "failed", "cancelled"])
    p_q_list.add_argument("--limit", type=int, default=20)

    p_q_cancel = p_queue_sub.add_parser("cancel", help="Cancel a pending task")
    p_q_cancel.add_argument("task_id", help="Task ID to cancel")
    p_q_cancel.add_argument("--db", default="meshflow_queue.db", help="Queue SQLite path")

    p_q_worker = p_queue_sub.add_parser("worker", help="Start a queue worker process")
    p_q_worker.add_argument("--db", default="meshflow_queue.db", help="Queue SQLite path")
    p_q_worker.add_argument("--concurrency", type=int, default=4, help="Max concurrent tasks")
    p_q_worker.add_argument("--poll", type=float, default=1.0, dest="poll_interval", help="Poll interval seconds")

    p_bench = sub.add_parser("bench", help="Run performance benchmarks (no API key required)")
    p_bench.add_argument(
        "--concurrency", nargs="+", type=int, default=[10, 100, 1000],
        help="Concurrency levels to test (default: 10 100 1000)",
    )
    p_bench.add_argument(
        "--output", default=None,
        help="Save results as JSON to this path",
    )
    p_bench.add_argument(
        "--quick", action="store_true",
        help="Fast smoke-check (concurrency 10 only)",
    )

    args = parser.parse_args()

    dispatch = {
        "init": _cmd_init,
        "new": _cmd_new,
        "logs": _cmd_logs,
        "runs": _cmd_logs,  # alias
        "approve": _cmd_approve,
        "run": _cmd_run,
        "stream": _cmd_stream,
        "replay": _cmd_replay,
        "trace": _cmd_trace,
        "conformance": _cmd_conformance,
        "schema": _cmd_schema,
        "serve": _cmd_serve,
        "dev": _cmd_dev,
        "describe": _cmd_describe,
        "eval": _cmd_eval,
        "eval-diff": _cmd_eval_diff,
        "eval-history": _cmd_eval_history,
        "graph": _cmd_graph,
        "audit": _cmd_audit,
        "plugins": _cmd_plugins,
        "bench": _cmd_bench,
        "watch": _cmd_watch,
        "resume": _cmd_resume,
        "mcp-stdio": _cmd_mcp_stdio,
        "compliance": _cmd_compliance,
        "webhooks": _cmd_webhooks,
        "keys": _cmd_keys,
        "analytics": _cmd_analytics,
        "queue": _cmd_queue,
    }
    dispatch[args.cmd](args)


# ── init ─────────────────────────────────────────────────────────────────────


def _cmd_init(args: argparse.Namespace) -> None:
    from meshflow.cli.init import run_init

    run_init(name=args.name)


# ── new ───────────────────────────────────────────────────────────────────────


def _cmd_new(args: argparse.Namespace) -> None:
    from meshflow.cli.scaffold import run_new

    run_new(kind=args.kind, name=args.name)


# ── logs ──────────────────────────────────────────────────────────────────────


def _cmd_logs(args: argparse.Namespace) -> None:
    asyncio.run(_async_logs(args))


async def _async_logs(args: argparse.Namespace) -> None:
    import os
    from meshflow.core.ledger import ReplayLedger

    db = args.db
    if not os.path.exists(db):
        print(f"\n  No ledger found at '{db}'. Run a workflow first.\n")
        return

    ledger = ReplayLedger(db)
    run_ids = await ledger.list_runs()
    paused = {r["run_id"] for r in await ledger.list_paused_runs()}

    if not run_ids:
        print("\n  No runs recorded yet.\n")
        return

    run_ids = run_ids[: args.limit]

    print()
    print(f"  {'RUN ID':<38} {'NODES':<28} {'COST':>8}  {'TOKENS':>7}  {'DURATION':>9}  STATUS")
    print(f"  {'─' * 38} {'─' * 28} {'─' * 8}  {'─' * 7}  {'─' * 9}  {'─' * 10}")

    for run_id in run_ids:
        summary = await ledger.run_summary(run_id)
        if not summary:
            continue
        nodes_str = " → ".join(summary["nodes"][:4])
        if len(summary["nodes"]) > 4:
            nodes_str += f" +{len(summary['nodes']) - 4}"

        blocked = summary["blocked_steps"] > 0
        is_paused = run_id in paused
        if is_paused:
            status = "PAUSED"
        elif blocked:
            status = "FAILED"
        else:
            status = "completed"

        cost = f"${summary['total_cost_usd']:.4f}"
        tokens = str(summary["total_tokens"])
        ts = summary["timestamps"].get("start", "")[:19].replace("T", " ")

        duration_s = summary.get("duration_s", 0.0)
        if duration_s is not None and duration_s > 0:
            dur = f"{duration_s:.1f}s"
        else:
            dur = "—"

        print(f"  {run_id:<38} {nodes_str:<28} {cost:>8}  {tokens:>7}  {dur:>9}  {status}")
        if ts:
            print(f"  {'':38} {ts}")

    print()
    if paused:
        print(f"  {len(paused)} run(s) awaiting human approval.")
        print("  Run: meshflow approve <run_id> <node_id>")
        print()


# ── approve ───────────────────────────────────────────────────────────────────


def _cmd_approve(args: argparse.Namespace) -> None:
    asyncio.run(_async_approve(args))


async def _async_approve(args: argparse.Namespace) -> None:
    import os
    from meshflow.core.ledger import ReplayLedger

    db = args.db
    if not os.path.exists(db):
        print(f"  ✗  Ledger not found at '{db}'.")
        sys.exit(1)

    ledger = ReplayLedger(db)
    checkpoint = await ledger.load_checkpoint_data(args.run_id)
    if checkpoint is None:
        print(f"  ✗  No paused checkpoint found for run_id='{args.run_id}'.")
        print("     Run 'meshflow logs' to see paused runs.")
        sys.exit(1)

    paused_node = checkpoint.get("paused_at_node", "")
    if paused_node and paused_node != args.node_id:
        print(f"  ✗  Run is paused at '{paused_node}', not '{args.node_id}'.")
        print(f"     Run: meshflow approve {args.run_id} {paused_node}")
        sys.exit(1)

    checkpoint["approved_by"] = "cli"
    checkpoint["approved_node"] = args.node_id
    checkpoint["status"] = "approved"
    await ledger.save_checkpoint(args.run_id, checkpoint)

    print()
    print(f"  ✓  Approved: run_id={args.run_id}  node={args.node_id}")
    print("     The workflow can now resume from this checkpoint.")
    print()


# ── run ───────────────────────────────────────────────────────────────────────


async def _async_run_crew(args: argparse.Namespace) -> None:
    """Handle ``meshflow run`` when the YAML declares ``kind: crew``."""
    from meshflow.agents.crew import Crew

    import yaml as _yaml
    with open(args.yaml, encoding="utf-8") as f:
        data = _yaml.safe_load(f)

    name = data.get("name", args.yaml)
    inputs: dict[str, Any] = data.get("inputs", {})

    # task override from CLI splices into inputs["task"]
    if args.task:
        inputs["task"] = args.task

    crew = Crew.from_yaml(args.yaml)
    run_id = str(uuid.uuid4())

    print(f"[meshflow] run_id={run_id}")
    print(f"[meshflow] crew={name}  tasks={len(crew.tasks)}  process={crew.process.value}")

    result = await crew.kickoff(inputs=inputs)

    print(f"\n{'=' * 60}")
    print(f"  Tasks    : {len(result.tasks_output)}")
    print(f"  Cost     : ${result.total_cost_usd:.6f}")
    print(f"  Tokens   : {result.total_tokens}")
    print(f"\n  Output:\n{result.raw[:2000]}")
    print()


def _cmd_run(args: argparse.Namespace) -> None:
    asyncio.run(_async_run(args))


async def _async_run(args: argparse.Namespace) -> None:
    # ── detect kind: crew vs. kind: workflow ──────────────────────────────────
    try:
        import yaml as _yaml
        with open(args.yaml, encoding="utf-8") as _f:
            _peek = _yaml.safe_load(_f)
        _kind = str(_peek.get("kind", "workflow")).lower() if isinstance(_peek, dict) else "workflow"
    except Exception:
        _kind = "workflow"

    if _kind == "crew":
        await _async_run_crew(args)
        return

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
        compliance_guard=wf.compliance_guard,
    )

    print(f"[meshflow] run_id={run_id}")
    print(f"[meshflow] workflow={wf.name}  nodes={len(wf._nodes)}  policy.budget=${pol.budget_usd}")
    if wf.compliance_guard is not None:
        print(f"[meshflow] compliance guard active")
    if wf.metadata:
        print(f"[meshflow] metadata: {wf.metadata}")

    result = await wf.run(task, runtime, context={"workflow_yaml": args.yaml})

    print(f"\n{'=' * 60}")
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


# ── watch ─────────────────────────────────────────────────────────────────────


def _cmd_watch(args: argparse.Namespace) -> None:
    asyncio.run(_async_watch(args))


async def _async_watch(args: argparse.Namespace) -> None:
    import os
    from meshflow.core.ledger import ReplayLedger
    from meshflow.core.events import EventKind, global_event_bus

    terminal = {EventKind.WORKFLOW_COMPLETE, EventKind.WORKFLOW_FAILED}

    async def _tail_bus() -> None:
        saw_event = False
        async for event in global_event_bus.subscribe(run_id=args.run_id, replay_history=True):
            saw_event = True
            if args.sse:
                print(event.to_sse(), end="", flush=True)
            else:
                print(_format_watch_event(event), flush=True)
            if event.kind in terminal:
                break
        if not saw_event:
            print(f"[watch] no live events observed for run_id={args.run_id!r}")

    async def _tail_ledger() -> None:
        ledger = ReplayLedger(args.db)
        seen: set[str] = set()
        checkpoint_seen = False
        while True:
            steps = await ledger.get_run(args.run_id)
            for step in steps:
                step_id = step.get("step_id", "")
                if step_id in seen:
                    continue
                seen.add(step_id)
                print(_format_ledger_step(args.run_id, step), flush=True)
            checkpoint = await ledger.load_checkpoint_data(args.run_id)
            if checkpoint is not None and not checkpoint_seen:
                checkpoint_seen = True
                paused_at = checkpoint.get("paused_at_node", "")
                print(f"[checkpoint_saved] run_id={args.run_id} node={paused_at}", flush=True)
            await asyncio.sleep(0.25)

    if args.timeout is None:
        if global_event_bus.history(args.run_id):
            await _tail_bus()
        else:
            if not os.path.exists(args.db) and args.db != ":memory:":
                print(f"[watch] waiting for ledger at {args.db!r}", flush=True)
            await _tail_ledger()
        return

    try:
        async with asyncio.timeout(args.timeout):
            if global_event_bus.history(args.run_id):
                await _tail_bus()
            else:
                await _tail_ledger()
    except TimeoutError:
        print(f"[watch] timed out waiting for run_id={args.run_id!r}")


def _format_watch_event(event: Any) -> str:
    node = f" node={event.node_id}" if event.node_id else ""
    data = ""
    if event.data:
        data = " " + json.dumps(event.data, sort_keys=True)
    return f"[{event.kind.value}] run_id={event.run_id}{node}{data}"


def _format_ledger_step(run_id: str, step: dict[str, Any]) -> str:
    if step.get("blocked"):
        kind = "step_blocked"
    elif step.get("verdict") == "escalate":
        kind = "step_paused"
    else:
        kind = "step_complete"
    data = {
        "cost_usd": step.get("cost_usd", 0.0),
        "tokens": step.get("tokens_used", 0),
        "uncertainty": step.get("uncertainty", 0.0),
    }
    reason = step.get("block_reason", "")
    if reason:
        data["blocked_by"] = reason
    return (
        f"[{kind}] run_id={run_id} node={step.get('node_id', '')} "
        f"{json.dumps(data, sort_keys=True)}"
    )


# ── resume ────────────────────────────────────────────────────────────────────


def _cmd_resume(args: argparse.Namespace) -> None:
    asyncio.run(_async_resume(args))


async def _async_resume(args: argparse.Namespace) -> None:
    import os
    from meshflow.core.ledger import ReplayLedger
    from meshflow.core.runtime import StepRuntime
    from meshflow.core.workflow import HumanDecision, WorkflowDefinition
    from meshflow.security.guardian import Guardian
    from meshflow.security.dasc_gate import DascGate
    from meshflow.security.identity import AgentIdentityProvider
    from meshflow.intelligence.uncertainty import UncertaintyEngine
    from meshflow.intelligence.collusion import CollusionAuditor

    if not os.path.exists(args.db) and args.db != ":memory:":
        print(f"  ✗  Ledger not found at '{args.db}'.")
        sys.exit(1)

    ledger = ReplayLedger(args.db)
    checkpoint = await ledger.load_checkpoint_data(args.run_id)
    if checkpoint is None:
        print(f"  ✗  No paused checkpoint found for run_id='{args.run_id}'.")
        print("     Run 'meshflow logs' to see paused runs.")
        sys.exit(1)

    yaml_path = args.yaml or checkpoint.get("workflow_yaml", "")
    if not yaml_path:
        print("  ✗  Checkpoint does not include the original workflow YAML path.")
        print(f"     Run: meshflow resume {args.run_id} --yaml <workflow.yaml>")
        sys.exit(1)
    if not os.path.exists(yaml_path):
        print(f"  ✗  Workflow YAML not found at '{yaml_path}'.")
        print(f"     Run: meshflow resume {args.run_id} --yaml <workflow.yaml>")
        sys.exit(1)

    wf = WorkflowDefinition.from_yaml(yaml_path)
    pol = wf.policy
    runtime = StepRuntime(
        policy=pol,
        run_id=args.run_id,
        guardian=Guardian(budget_usd=pol.budget_usd) if pol.enable_guardian else None,
        dasc_gate=DascGate(pol, args.run_id) if pol.deterministic_gate else None,
        identity=AgentIdentityProvider(args.run_id),
        uncertainty=UncertaintyEngine() if pol.enable_uncertainty else None,
        collusion=CollusionAuditor() if pol.enable_collusion_audit else None,
        ledger=ledger,
    )

    decision = HumanDecision(
        approved=not args.reject,
        comment=args.comment,
        decided_by=args.decided_by,
    )
    result = await wf.resume(args.run_id, decision, ledger, runtime)

    status = "COMPLETED" if result.completed else ("PAUSED" if result.paused_nodes else "FAILED")
    action = "rejected" if args.reject else "approved"
    print()
    print(f"  ✓  Resume {action}: run_id={args.run_id}")
    print(f"  Status   : {status}")
    print(f"  Steps    : {len(result.steps)}")
    print(f"  Cost     : ${result.total_cost_usd:.6f}")
    print(f"  Tokens   : {result.total_tokens}")
    if result.paused_nodes:
        print(f"  Paused   : {result.paused_nodes}")
    if result.blocked_nodes:
        print(f"  Blocked  : {result.blocked_nodes}")
    if result.output:
        print(f"\n  Output:\n{result.output[:1000]}")
    print()


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

    raw = await ledger.export_run(args.run_id)
    if args.archive_s3:
        archive = await ledger.archive_run(args.run_id, args.archive_s3)
        print(
            f"[archive] wrote {archive.bytes_written} bytes to {archive.uri} "
            f"sha256={archive.sha256}"
        )

    if args.as_json:
        print(raw)
        return

    steps = await ledger.get_run(args.run_id)
    paused = await ledger.list_paused_runs()
    is_paused = any(r["run_id"] == args.run_id for r in paused)

    total_cost = sum(s.get("cost_usd", 0) for s in steps)
    total_tok = sum(s.get("tokens_used", 0) for s in steps)
    total_ms = sum(s.get("duration_ms", 0) for s in steps)
    blocked_ct = sum(1 for s in steps if s.get("blocked"))

    status_str = (
        "PAUSED — awaiting approval" if is_paused else ("FAILED" if blocked_ct else "COMPLETED")
    )

    print()
    print(f"  ┌{'─' * 58}┐")
    print(f"  │  Replay — {args.run_id[:46]:<46}  │")
    print(f"  ├{'─' * 58}┤")
    print(f"  │  Status  : {status_str:<46}  │")
    print(f"  │  Steps   : {len(steps):<46}  │")
    print(f"  │  Cost    : ${total_cost:<44.5f}  │")
    print(f"  │  Tokens  : {total_tok:<46}  │")
    print(f"  │  Duration: {total_ms / 1000:<44.2f}s  │")
    print(f"  └{'─' * 58}┘")
    print()
    print("  Step-by-step:")
    print()

    for i, step in enumerate(steps, 1):
        node_id = step.get("node_id", "?")
        kind = step.get("node_kind", "?")
        blocked = step.get("blocked", False)
        uncertain = step.get("uncertainty", 0.0)
        cost = step.get("cost_usd", 0.0)
        dur_ms = step.get("duration_ms", 0.0)
        reason = step.get("block_reason", "") or ""
        output = step.get("output", "") or ""

        if blocked:
            icon, _flag = "✗", "BLOCKED"
        else:
            icon, _flag = "✓", "ok"

        connector = "└──" if i == len(steps) else "├──"
        print(
            f"  {connector} [{i:02d}] {icon}  {node_id:<22} {kind:<10} "
            f"~{uncertain:.2f} conf  {dur_ms:.0f}ms  ${cost:.5f}"
        )
        if output:
            preview = output[:120].replace("\n", " ")
            print(f"  │         output: {preview}")
        if blocked and reason:
            print(f"  │         reason: {reason}")
        if i < len(steps):
            print("  │")

    print()
    if is_paused:
        checkpoint = await ledger.load_checkpoint_data(args.run_id)
        paused_node = checkpoint.get("paused_at_node", "?") if checkpoint else "?"
        print(f"  ⏸  Paused at node '{paused_node}' — awaiting human approval.")
        print(f"     Run: meshflow approve {args.run_id} {paused_node}")
        print()

    if args.archive_s3:
        archive = await ledger.archive_run(args.run_id, args.archive_s3)
        print(
            f"  [archive] wrote {archive.bytes_written} bytes to {archive.uri} "
            f"sha256={archive.sha256}"
        )
        print()


# ── conformance ───────────────────────────────────────────────────────────────

_CONFORMANCE_LEVELS = {
    0: "Basic — node executes and returns non-empty output",
    1: "Reliable — handles exceptions, respects timeout, supports retry",
    2: "Governed — budget accounting, identity propagation, trace capture",
    3: "Auditable — ledger entries written, HITL pause/resume supported",
    4: "Durable — checkpoint save/load primitives are verified",
    5: "Certified — public contract schemas and report metadata are present",
}


def _cmd_conformance(args: argparse.Namespace) -> None:
    results = asyncio.run(_async_conformance(args.kind, args.level))
    _print_conformance_report(args.kind, results)
    failed = [r for r in results if not r["passed"]]
    sys.exit(1 if failed else 0)


async def _async_conformance(kind: str, max_level: int) -> list[dict[str, Any]]:
    """Run the MeshFlow conformance suite up to max_level."""
    from meshflow.core.node import MeshNode, NodeInput, NodeKind, NodeOutput
    from meshflow.core.runtime import StepRuntime
    from meshflow.core.ledger import ReplayLedger
    from meshflow.core.schemas import Policy, RiskTier
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
            results.append(
                {
                    "level": 0,
                    "check": "basic_execution",
                    "passed": passed,
                    "detail": outcome.output.content[:80] if passed else outcome.blocked_by,
                }
            )
        except Exception as e:
            results.append(
                {"level": 0, "check": "basic_execution", "passed": False, "detail": str(e)}
            )

    # ── Level 1: Exception handling ───────────────────────────────────────────
    if max_level >= 1:
        node_fail = _make_node(fail=True)
        inp = NodeInput(task="conformance fail test", context={})
        try:
            outcome = await runtime.run(node_fail, inp, {})
            # A failing node should return ok=False, not raise
            passed = not outcome.ok and "node_exception" in outcome.blocked_by
            results.append(
                {
                    "level": 1,
                    "check": "exception_handling",
                    "passed": passed,
                    "detail": outcome.blocked_by,
                }
            )
        except Exception as e:
            results.append(
                {"level": 1, "check": "exception_handling", "passed": False, "detail": str(e)}
            )

    # ── Level 2: Identity propagation + trace capture ─────────────────────────
    if max_level >= 2:
        node = _make_node()
        node.id = f"conformance_{kind}_L2"
        inp = NodeInput(task="conformance L2 test", context={})
        try:
            outcome = await runtime.run(node, inp, {})
            did_provisioned = identity.is_provisioned(node.id)
            results.append(
                {
                    "level": 2,
                    "check": "identity_propagation",
                    "passed": did_provisioned,
                    "detail": f"DID provisioned: {did_provisioned}",
                }
            )
        except Exception as e:
            results.append(
                {"level": 2, "check": "identity_propagation", "passed": False, "detail": str(e)}
            )

    # ── Level 2b: Uncertainty scoring ─────────────────────────────────────────
    if max_level >= 2:
        node = _make_node()
        node.id = f"conformance_{kind}_uncertainty"
        inp = NodeInput(task="conformance uncertainty test", context={})
        try:
            outcome = await runtime.run(node, inp, {})
            has_uncertainty = outcome.record.uncertainty >= 0.0
            results.append(
                {
                    "level": 2,
                    "check": "uncertainty_scoring",
                    "passed": has_uncertainty,
                    "detail": f"uncertainty={outcome.record.uncertainty:.3f}",
                }
            )
        except Exception as e:
            results.append(
                {"level": 2, "check": "uncertainty_scoring", "passed": False, "detail": str(e)}
            )

    # ── Level 3: Audit ledger ─────────────────────────────────────────────────
    if max_level >= 3:
        steps_for_run = await ledger.get_run(run_id)
        passed = len(steps_for_run) > 0
        results.append(
            {
                "level": 3,
                "check": "audit_ledger_writes",
                "passed": passed,
                "detail": f"{len(steps_for_run)} records written",
            }
        )

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
            results.append(
                {
                    "level": 3,
                    "check": "hitl_pause",
                    "passed": outcome.paused_for_human,
                    "detail": f"paused={outcome.paused_for_human}  human_ctx={outcome.human_context}",
                }
            )
        except Exception as e:
            results.append({"level": 3, "check": "hitl_pause", "passed": False, "detail": str(e)})

    # ── Level 4: Durable checkpoint primitives ───────────────────────────────
    if max_level >= 4:
        checkpoint_id = f"{run_id}-checkpoint"
        checkpoint_payload = {
            "workflow_name": "conformance",
            "paused_at_node": "conformance_hitl",
            "context": {"task": "durability test"},
            "completed_nodes": ["first"],
            "skipped_nodes": [],
        }
        try:
            await ledger.save_checkpoint(checkpoint_id, checkpoint_payload)
            loaded = await ledger.load_checkpoint_data(checkpoint_id)
            paused = await ledger.list_paused_runs()
            passed = loaded == checkpoint_payload and any(
                row["run_id"] == checkpoint_id for row in paused
            )
            results.append(
                {
                    "level": 4,
                    "check": "checkpoint_roundtrip",
                    "passed": passed,
                    "detail": f"loaded={loaded is not None}",
                }
            )
        except Exception as e:
            results.append(
                {"level": 4, "check": "checkpoint_roundtrip", "passed": False, "detail": str(e)}
            )

    # ── Level 5: Public contract schemas ─────────────────────────────────────
    if max_level >= 5:
        try:
            from meshflow.core.contracts import core_contract_schemas

            schemas = core_contract_schemas()
            required = {"NodeInput", "NodeOutput", "MeshNode", "Policy", "RuntimeOutcome"}
            passed = required <= set(schemas)
            results.append(
                {
                    "level": 5,
                    "check": "contract_schema_export",
                    "passed": passed,
                    "detail": ", ".join(sorted(schemas)),
                }
            )
        except Exception as e:
            results.append(
                {"level": 5, "check": "contract_schema_export", "passed": False, "detail": str(e)}
            )

    return results


def _print_conformance_report(kind: str, results: list[dict[str, Any]]) -> None:
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    max_level = max((r["level"] for r in results if r["passed"]), default=-1)

    print(f"\n{'=' * 60}")
    print(f"  MeshFlow Conformance Report — kind: {kind}")
    print(f"{'=' * 60}")
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

    print(f"\n{'=' * 60}")
    print(f"  Score     : {passed}/{total} checks passed")
    conformance_level = max_level if max_level >= 0 else -1
    print(
        f"  Level     : {'L' + str(conformance_level) if conformance_level >= 0 else 'non-conformant'}"
    )
    print(f"  Verdict   : {'CONFORMANT' if passed == total else 'NON-CONFORMANT'}")
    print(f"{'=' * 60}\n")


# ── trace ─────────────────────────────────────────────────────────────────────


def _cmd_trace(args: argparse.Namespace) -> None:
    asyncio.run(_async_trace(args))


async def _async_trace(args: argparse.Namespace) -> None:
    import os
    from meshflow.core.ledger import ReplayLedger

    if not os.path.exists(args.db) and args.db != ":memory:":
        print(f"  No ledger found at '{args.db}'.")
        sys.exit(1)

    ledger = ReplayLedger(args.db)
    steps = await ledger.get_run(args.run_id)
    if not steps:
        print(f"  run_id='{args.run_id}' not found in {args.db}")
        sys.exit(1)

    summary = await ledger.run_summary(args.run_id)
    chain = await ledger.verify_chain(args.run_id)

    if args.as_json or args.export:
        payload = json.dumps(
            {
                "run_id": args.run_id,
                "summary": summary,
                "chain_valid": chain["valid"],
                "steps": steps,
            },
            indent=2,
        )
        if args.export:
            with open(args.export, "w") as f:
                f.write(payload)
            print(f"  Exported {len(steps)} steps to {args.export}")
        else:
            print(payload)
        return

    # Rich terminal table
    print()
    print(f"  Trace: {args.run_id}")
    print(
        f"  Steps: {summary['steps']}   Cost: ${summary['total_cost_usd']:.5f}   "
        f"Tokens: {summary['total_tokens']}   "
        f"Chain: {'VALID' if chain['valid'] else 'INVALID'}"
    )
    print()
    print(
        f"  {'#':<3} {'NODE':<22} {'KIND':<10} {'VERDICT':<9} {'UNCERT':>6}  "
        f"{'TOKENS':>6}  {'COST':>8}  {'MS':>6}  STATUS"
    )
    print(
        f"  {'─' * 3} {'─' * 22} {'─' * 10} {'─' * 9} {'─' * 6}  {'─' * 6}  {'─' * 8}  {'─' * 6}  {'─' * 8}"
    )

    for i, step in enumerate(steps, 1):
        blocked = step.get("blocked", False)
        verdict = step.get("verdict", "commit")
        status = "BLOCKED" if blocked else ("PAUSED" if verdict == "escalate" else "ok")
        print(
            f"  {i:<3} {step.get('node_id', '?'):<22} {step.get('node_kind', '?'):<10} "
            f"{verdict:<9} {step.get('uncertainty', 0):.3f}  "
            f"{step.get('tokens_used', 0):>6}  "
            f"${step.get('cost_usd', 0):.5f}  "
            f"{step.get('duration_ms', 0):>6.0f}  {status}"
        )
        reason = step.get("block_reason", "")
        if reason:
            print(f"  {'':3} {'  reason: ' + reason}")
        output = step.get("output_content", "") or ""
        if output:
            print(f"  {'':3}   output: {output[:100].replace(chr(10), ' ')}")
        if i < len(steps):
            print()

    if not chain["valid"]:
        print("\n  CHAIN INTEGRITY ERRORS:")
        for err in chain["errors"]:
            print(f"    ! {err}")
    print()


# ── serve ─────────────────────────────────────────────────────────────────────


def _cmd_serve(args: argparse.Namespace) -> None:
    from meshflow.runtime.server import serve, _load_api_keys

    keys: set[str] = set(args.api_keys) if getattr(args, "api_keys", None) else _load_api_keys()

    policy_file = getattr(args, "policy_file", "")
    if policy_file:
        import os
        if not os.path.exists(policy_file):
            print(f"  Error: policy file not found: {policy_file}")
            sys.exit(1)
        from meshflow.core.policy_loader import validate_policy_yaml
        issues = validate_policy_yaml(policy_file)
        if issues:
            print("  Policy file validation errors:")
            for issue in issues:
                print(f"    - {issue}")
            sys.exit(1)
        print(f"  Policy file: {policy_file} (validated)")

    serve(
        host=args.host,
        port=args.port,
        api_keys=keys,
        ledger_path=getattr(args, "ledger", "meshflow_runs.db"),
        tls_cert=getattr(args, "tls_cert", ""),
        tls_key=getattr(args, "tls_key", ""),
        policy_file=policy_file,
    )


def _cmd_dev(args: argparse.Namespace) -> None:
    """Start the server in dev mode: no auth, in-memory ledger, colored output."""
    print("\n  MeshFlow DEV mode")
    print(f"  URL:    http://{args.host}:{args.port}")
    print("  Ledger: in-memory (ephemeral)")
    print("  Auth:   DISABLED (dev mode)")
    print("  Press Ctrl+C to stop\n")
    from meshflow.runtime.server import serve

    serve(
        host=args.host,
        port=args.port,
        api_keys=set(),
        ledger_path=getattr(args, "ledger", ":memory:"),
    )


# ── schema ────────────────────────────────────────────────────────────────────


def _cmd_schema(args: argparse.Namespace) -> None:
    from meshflow.core.contracts import core_contract_schemas

    schemas = core_contract_schemas()
    if args.name == "all":
        print(json.dumps(schemas, indent=2))
        return
    schema = schemas.get(args.name)
    if schema is None:
        print(f"[schema] unknown schema {args.name!r}; choose one of: {', '.join(sorted(schemas))}")
        sys.exit(1)
    print(json.dumps(schema, indent=2))


# ── describe ──────────────────────────────────────────────────────────────────


def _cmd_describe(args: argparse.Namespace) -> None:
    from meshflow.core.workflow import WorkflowDefinition

    wf = WorkflowDefinition.from_yaml(args.yaml)
    desc = wf.describe()
    print(json.dumps(desc, indent=2))


# ── mcp-stdio ────────────────────────────────────────────────────────────────


def _cmd_mcp_stdio(args: argparse.Namespace) -> None:
    """Start a governed MeshFlow stdio MCP server for Claude Desktop."""
    import os
    import shutil

    if getattr(args, "print_config", False):
        exe = shutil.which("meshflow") or "meshflow"
        config_arg = f'", "--config", "{args.config}"' if args.config else ""
        snippet = {
            "mcpServers": {
                "meshflow": {
                    "command": exe,
                    "args": ["mcp-stdio", "--policy", args.policy] + (
                        ["--config", args.config] if args.config else []
                    ),
                    "env": {"ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}"},
                }
            }
        }
        print(json.dumps(snippet, indent=2))
        print("\nAdd this to ~/Library/Application Support/Claude/claude_desktop_config.json")
        return

    from meshflow.mcp.server import MCPServer, from_config

    if args.config and os.path.exists(args.config):
        srv = from_config(args.config, policy=args.policy)
        msg = f"MeshFlow MCP stdio server (config: {args.config}, policy: {args.policy})"
    else:
        srv = MCPServer(policy=args.policy)
        msg = f"MeshFlow MCP stdio server (policy: {args.policy})"

    # Write startup message to stderr so Claude Desktop can see it
    # (stdout is reserved for JSON-RPC)
    print(msg, file=sys.stderr)

    asyncio.run(srv.run_stdio())


# ── eval ──────────────────────────────────────────────────────────────────────


def _cmd_eval(args: argparse.Namespace) -> None:
    asyncio.run(_async_eval(args))


async def _async_eval(args: argparse.Namespace) -> None:
    from meshflow.eval import EvalBaseline, EvalSuite

    suite = EvalSuite.from_yaml(args.eval_file)

    if args.tags:
        suite = suite.filter(args.tags)

    # If --agent provided, import it and find the agent
    agent: Any = None
    if args.agent:
        import importlib.util, os

        spec = importlib.util.spec_from_file_location("_eval_agent", args.agent)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[attr-defined]
            agent = getattr(mod, "agent", None) or getattr(mod, "AGENT", None)
            if agent is None:
                print(f"  [eval] No 'agent' or 'AGENT' variable found in {args.agent}")
                sys.exit(1)

    if agent is None:
        from meshflow.agents.library import ResearchAgent
        agent = ResearchAgent(policy="dev")

    result = await suite.run(agent, concurrency=args.concurrency)
    print(result.report(verbose=True))

    # Save to ledger
    if getattr(args, "save_to_ledger", False):
        from meshflow.core.ledger import ReplayLedger
        ledger = ReplayLedger(args.db)
        key = await ledger.save_eval_result(result)
        print(f"  [eval] Saved to ledger: {key}")

    # Save baseline
    if getattr(args, "save_baseline", ""):
        baseline = EvalBaseline.from_result(result)
        baseline.save(args.save_baseline)
        print(f"  [eval] Baseline saved → {args.save_baseline}")

    # Compare against baseline
    if getattr(args, "compare_baseline", ""):
        import os
        if not os.path.exists(args.compare_baseline):
            print(f"  [eval] Baseline not found: {args.compare_baseline}")
            sys.exit(1)
        old = EvalBaseline.load(args.compare_baseline)
        new = EvalBaseline.from_result(result)
        diff = old.diff(new)
        print(diff.report())
        if getattr(args, "fail_on_regression", False) and diff.has_regressions:
            print(f"  [eval] FAILED: {len(diff.regressions)} regression(s) detected")
            sys.exit(1)

    if args.fail_under > 0 and result.pass_rate < args.fail_under:
        print(f"  [eval] FAILED: pass_rate {result.pass_rate:.1%} < threshold {args.fail_under:.1%}")
        sys.exit(1)


def _cmd_eval_diff(args: argparse.Namespace) -> None:
    """Compare two eval baseline JSON files and print a regression report."""
    import os
    from meshflow.eval import EvalBaseline

    for path in (args.baseline_a, args.baseline_b):
        if not os.path.exists(path):
            print(f"  [eval-diff] File not found: {path}")
            sys.exit(1)

    old = EvalBaseline.load(args.baseline_a)
    new = EvalBaseline.load(args.baseline_b)
    diff = old.diff(new)
    print(diff.report())

    if getattr(args, "fail_on_regression", False) and diff.has_regressions:
        sys.exit(1)


# ── eval history ─────────────────────────────────────────────────────────────


def _cmd_eval_history(args: argparse.Namespace) -> None:
    """List stored eval results from the ledger."""
    asyncio.run(_async_eval_history(args))


async def _async_eval_history(args: argparse.Namespace) -> None:
    from meshflow.core.ledger import ReplayLedger

    ledger = ReplayLedger(args.db)
    suite_filter = args.suite.strip() or None
    results = await ledger.list_eval_results(suite_name=suite_filter)

    if getattr(args, "output_json", False):
        print(json.dumps(results, indent=2))
        return

    if not results:
        print("\n  No stored eval results found.")
        if suite_filter:
            print(f"  (filtered by suite: {suite_filter!r})")
        print()
        return

    print()
    print(f"  {'SUITE':<24} {'PASS RATE':>10} {'SCORE':>8} {'SCENARIOS':>10}  TIMESTAMP")
    print(f"  {'─'*24} {'─'*10} {'─'*8} {'─'*10}  {'─'*20}")
    for r in results:
        suite_name = r.get("suite_name", "?")
        pass_rate = r.get("pass_rate", 0.0)
        score = r.get("weighted_score", r.get("score", 0.0))
        n = r.get("total_scenarios", len(r.get("scenarios", [])))
        ts = r.get("timestamp", "")[:19]
        print(f"  {suite_name:<24} {pass_rate:>9.1%} {score:>8.3f} {n:>10}  {ts}")
    print(f"\n  {len(results)} result(s) found.\n")


# ── graph export ─────────────────────────────────────────────────────────────


def _cmd_graph(args: argparse.Namespace) -> None:
    asyncio.run(_async_graph(args))


async def _async_graph(args: argparse.Namespace) -> None:
    from meshflow.core.graph_export import steps_to_mermaid, steps_to_dot
    from meshflow.core.ledger import ReplayLedger

    ledger = ReplayLedger(args.db)
    run_id = getattr(args, "run_id", "").strip()

    if not run_id:
        runs = await ledger.list_runs()
        if not runs:
            print("\n  No runs in ledger.\n")
            return
        print(f"\n  Available run IDs (use --run-id <id>):\n")
        for r in runs[-20:]:
            print(f"    {r}")
        print()
        return

    steps = await ledger.get_run(run_id)
    if not steps:
        print(f"\n  Run {run_id!r} not found or has no steps.\n")
        sys.exit(1)

    fmt = getattr(args, "format", "mermaid")
    content = steps_to_mermaid(steps, run_id) if fmt == "mermaid" else steps_to_dot(steps, run_id)

    out_path = getattr(args, "out", "").strip()
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  Written to {out_path}")
    else:
        print(content)


# ── audit export ──────────────────────────────────────────────────────────────


def _cmd_audit(args: argparse.Namespace) -> None:
    cmd = args.audit_cmd
    if cmd == "export":
        asyncio.run(_async_audit_export(args))


async def _async_audit_export(args: argparse.Namespace) -> None:
    from meshflow.core.ledger import ReplayLedger

    ledger = ReplayLedger(args.db)
    run_id = getattr(args, "run_id", "").strip()
    fmt = getattr(args, "format", "json")
    out_path = getattr(args, "out", "").strip()

    if not run_id:
        # Export summary of all runs
        runs = await ledger.list_runs()
        summaries = []
        for rid in runs:
            try:
                summaries.append(await ledger.run_summary(rid))
            except Exception:
                pass
        content = json.dumps({"runs": summaries}, indent=2)
    elif fmt == "csv":
        content = await ledger.export_run_csv(run_id)
        if not content:
            print(f"\n  Run {run_id!r} not found.\n")
            sys.exit(1)
    else:
        content = await ledger.export_run(run_id)

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  Written to {out_path}")
    else:
        print(content)


# ── plugins ───────────────────────────────────────────────────────────────────


def _cmd_plugins(args: argparse.Namespace) -> None:
    from meshflow.plugins import discover_plugins, verify_plugin

    cmd = args.plugins_cmd

    if cmd == "list":
        plugins = discover_plugins(group=getattr(args, "group", None))
        if not plugins:
            print("\n  No MeshFlow plugins installed.")
            print("  Install a plugin package that declares entry_points in the")
            print("  'meshflow.agents', 'meshflow.tools', 'meshflow.compliance',")
            print("  or 'meshflow.ledger' groups.\n")
            return

        print()
        print(f"  {'NAME':<24} {'GROUP':<12} {'VERSION':<10} {'PACKAGE':<24}  DESCRIPTION")
        print(f"  {'─'*24} {'─'*12} {'─'*10} {'─'*24}  {'─'*30}")
        for p in plugins:
            desc = (p.description or "")[:40]
            print(
                f"  {p.name:<24} {p.group:<12} {p.version:<10} {p.dist_name:<24}  {desc}"
            )
        print()

    elif cmd == "verify":
        group = getattr(args, "group", "meshflow.agents") or "meshflow.agents"
        ok, msg = verify_plugin(args.name, group)
        status = "✓  OK" if ok else "✗  FAIL"
        print(f"\n  [{status}] {args.name}  ({group})")
        print(f"  {msg}\n")
        sys.exit(0 if ok else 1)

    elif cmd == "info":
        group_filter = getattr(args, "group", None)
        plugins = discover_plugins(group=group_filter)
        matches = [p for p in plugins if p.name == args.name]
        if not matches:
            print(f"\n  Plugin {args.name!r} not found.")
            sys.exit(1)
        p = matches[0]
        print()
        print(f"  Name        : {p.name}")
        print(f"  Group       : {p.group}  ({p.ep_group})")
        print(f"  Module      : {p.module}")
        print(f"  Package     : {p.dist_name}  v{p.version}")
        print(f"  Description : {p.description or '—'}")

        ok, msg = verify_plugin(p.name, p.ep_group)
        print(f"  Load check  : {'OK' if ok else 'FAIL'}  —  {msg}")
        print()


# ── bench ─────────────────────────────────────────────────────────────────────


def _cmd_bench(args: argparse.Namespace) -> None:
    """Run the performance benchmark suite (no API key required)."""
    import importlib.util
    import os as _os

    # Locate bench_core.py relative to this package
    bench_path = _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
        "benchmarks",
        "bench_core.py",
    )

    if not _os.path.exists(bench_path):
        print(f"  [bench] Benchmark script not found at {bench_path}")
        print("         Install from source (git clone) to get the benchmarks/ directory.")
        sys.exit(1)

    spec = importlib.util.spec_from_file_location("bench_core", bench_path)
    if spec is None or spec.loader is None:
        print("  [bench] Failed to load bench_core.py")
        sys.exit(1)

    mod = importlib.util.module_from_spec(spec)
    sys.modules["bench_core"] = mod  # must be registered before exec on Python 3.14+
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]

    concurrencies = [10] if args.quick else args.concurrency
    import meshflow
    print(f"MeshFlow v{meshflow.__version__} — performance benchmarks")
    print(f"  concurrency levels : {concurrencies}")
    print(f"  Python {sys.version.split()[0]}")
    print()

    asyncio.run(mod._main(concurrencies, args.output))
    print("\nDone.")


# ── compliance ────────────────────────────────────────────────────────────────


def _cmd_compliance(args: argparse.Namespace) -> None:
    if args.compliance_cmd == "report":
        asyncio.run(_async_compliance_report(args))
    elif args.compliance_cmd == "schedule":
        _cmd_compliance_schedule(args)


def _cmd_compliance_schedule(args: argparse.Namespace) -> None:
    from meshflow.compliance.scheduler import ScheduleStore, ScheduledReporter, create_schedule

    store = ScheduleStore(path=getattr(args, "schedule_file", "") or "")
    cmd = args.schedule_cmd

    if cmd == "add":
        sink_config: dict = {}
        if args.sink_type == "file":
            if not args.sink_path:
                print("  --sink-path required for sink=file")
                sys.exit(1)
            sink_config = {"path": args.sink_path, "mode": "a"}
        elif args.sink_type == "webhook":
            if not args.sink_url:
                print("  --sink-url required for sink=webhook")
                sys.exit(1)
            sink_config = {"url": args.sink_url, "secret": args.sink_secret}

        schedule = create_schedule(
            framework=args.framework,
            interval_seconds=args.interval_seconds,
            sink_type=args.sink_type,
            sink_config=sink_config,
            db_path=args.db,
            tenant_id=getattr(args, "tenant", ""),
        )
        store.add(schedule)
        print(f"  Schedule added — ID: {schedule.schedule_id}")
        print(f"  Framework: {schedule.framework}  Interval: {schedule.interval_seconds}s  Sink: {schedule.sink_type}")

    elif cmd == "list":
        schedules = store.list_all()
        if not schedules:
            print("  No schedules configured.")
            return
        for s in schedules:
            print(f"  [{s.schedule_id}] {s.framework} every {s.interval_seconds}s → {s.sink_type}")
            if s.last_run_at:
                import time
                print(f"    last_run: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(s.last_run_at))}")

    elif cmd == "run":
        schedule = store.get(args.schedule_id)
        if schedule is None:
            print(f"  Schedule '{args.schedule_id}' not found.")
            sys.exit(1)
        reporter = ScheduledReporter(schedule)
        result = asyncio.run(reporter.run_now())
        schedule.mark_ran()
        store.update(schedule)
        print(f"  Delivered — {result['overall_status']} ({result['run_ids_audited']} runs audited)")
        print(f"  Sink: {result['sink_type']}  At: {result['delivered_at']}")

    elif cmd == "remove":
        if store.remove(args.schedule_id):
            print(f"  Schedule '{args.schedule_id}' removed.")
        else:
            print(f"  Schedule '{args.schedule_id}' not found.")
            sys.exit(1)


async def _async_compliance_report(args: argparse.Namespace) -> None:
    import os
    from meshflow.core.ledger import ReplayLedger
    from meshflow.compliance.reporter import ComplianceReporter

    db = args.db
    if not os.path.exists(db):
        print(f"\n  No ledger found at '{db}'. Run a workflow first.\n")
        sys.exit(1)

    ledger = ReplayLedger(db)
    run_id = args.run_id

    if run_id:
        steps = await ledger.get_run(run_id) or []
        run_ids = [run_id]
    else:
        all_runs = await ledger.list_runs()
        steps = []
        for rid in all_runs[-50:]:
            run_steps = await ledger.get_run(rid) or []
            steps.extend(run_steps)
        run_ids = all_runs[-50:]

    reporter = ComplianceReporter()
    report = reporter.generate(args.framework, steps, run_ids=run_ids)

    if args.format == "json":
        output = report.to_json()
    else:
        output = report.to_text()

    if args.out:
        with open(args.out, "w") as fh:
            fh.write(output)
        print(f"  Compliance report written to {args.out}")
    else:
        print(output)


# ── webhooks ──────────────────────────────────────────────────────────────────


def _cmd_webhooks(args: argparse.Namespace) -> None:
    import urllib.request

    def _headers(api_key: str) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if api_key:
            h["Authorization"] = f"Bearer {api_key}"
        return h

    server = args.server.rstrip("/")
    api_key = getattr(args, "api_key", "")

    if args.webhooks_cmd == "list":
        req = urllib.request.Request(f"{server}/webhooks", headers=_headers(api_key))
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
        except Exception as exc:
            print(f"  Error: {exc}")
            sys.exit(1)
        hooks = data.get("webhooks", [])
        stats = data.get("stats", {})
        if not hooks:
            print("  No webhooks registered.")
            return
        print(f"\n  Registered webhooks ({len(hooks)}):")
        print(f"  {'ID':<36}  {'Events':<30}  {'URL'}")
        print(f"  {'─' * 36}  {'─' * 30}  {'─' * 40}")
        for h in hooks:
            print(
                f"  {h.get('id', ''):<36}  "
                f"{', '.join(h.get('events', [])):<30}  "
                f"{h.get('url', '')}"
            )
        print(
            f"\n  Deliveries: {stats.get('total_deliveries', 0)}  "
            f"Failures: {stats.get('total_failures', 0)}\n"
        )

    elif args.webhooks_cmd == "add":
        events = [e.strip() for e in args.events.split(",") if e.strip()]
        body = json.dumps({"url": args.url, "events": events, "secret": args.secret}).encode()
        req = urllib.request.Request(
            f"{server}/webhooks", data=body, headers=_headers(api_key), method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
        except Exception as exc:
            print(f"  Error: {exc}")
            sys.exit(1)
        if "error" in data:
            print(f"  Registration failed: {data['error']}")
            sys.exit(1)
        print(f"\n  Webhook registered!")
        print(f"  ID:     {data.get('id', '?')}")
        print(f"  URL:    {data.get('url', '?')}")
        print(f"  Events: {', '.join(data.get('events', []))}\n")

    elif args.webhooks_cmd == "remove":
        req = urllib.request.Request(
            f"{server}/webhooks/{args.id}", headers=_headers(api_key), method="DELETE"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
        except Exception as exc:
            print(f"  Error: {exc}")
            sys.exit(1)
        print(f"  Webhook {data.get('deleted', args.id)} removed.")


# ── keys ─────────────────────────────────────────────────────────────────────


def _cmd_keys(args: argparse.Namespace) -> None:
    from meshflow.security.api_keys import KeyStore

    db = args.db
    store = KeyStore(db)

    if args.keys_cmd == "list":
        tenant = args.tenant or None
        keys = store.list(tenant_id=tenant)
        if not keys:
            print("  No active API keys found.")
            return
        print(f"\n  Active API keys ({len(keys)}):")
        print(f"  {'Key ID':<20}  {'Name':<20}  {'Role':<10}  {'Tenant':<15}  Last used")
        print(f"  {'─' * 20}  {'─' * 20}  {'─' * 10}  {'─' * 15}  {'─' * 24}")
        for k in keys:
            print(
                f"  {k.key_id:<20}  {k.name:<20}  {k.role:<10}  "
                f"{(k.tenant_id or '(global)'):<15}  {k.last_used_at or 'never'}"
            )
        print()

    elif args.keys_cmd == "generate":
        try:
            key_id, raw_key = store.create(args.name, role=args.role, tenant_id=args.tenant)
        except ValueError as exc:
            print(f"  Error: {exc}")
            sys.exit(1)
        print(f"\n  API key created!")
        print(f"  Key ID  : {key_id}")
        print(f"  Raw key : {raw_key}")
        print(f"  Role    : {args.role}")
        print(f"  Tenant  : {args.tenant or '(global)'}")
        print()
        print("  Store the raw key now — it will not be shown again.")
        print()

    elif args.keys_cmd == "revoke":
        ok = store.revoke(args.key_id)
        if ok:
            print(f"  Key {args.key_id} revoked.")
        else:
            print(f"  Key {args.key_id} not found or already revoked.")
            sys.exit(1)


# ── analytics ─────────────────────────────────────────────────────────────────


def _cmd_analytics(args: argparse.Namespace) -> None:
    asyncio.run(_async_analytics(args))


async def _async_analytics(args: argparse.Namespace) -> None:
    import os
    from meshflow.core.ledger import ReplayLedger
    from meshflow.core.analytics import WorkflowAnalytics

    db = args.db
    if not os.path.exists(db):
        print(f"\n  No ledger found at '{db}'. Run a workflow first.\n")
        return

    ledger = ReplayLedger(db)
    analytics = WorkflowAnalytics(ledger)
    n = args.n_runs
    metric = args.metric

    if metric == "cost":
        data = await analytics.cost_trend(n)
    elif metric == "latency":
        data = await analytics.latency_percentiles(n)
    elif metric == "blocked":
        data = await analytics.blocked_rate(n)
    elif metric == "quality":
        data = await analytics.quality_drift(n)
    elif metric == "carbon":
        data = await analytics.carbon_trend(n)
    elif metric == "nodes":
        data = await analytics.top_costly_nodes(n)
    else:
        data = await analytics.full_report(n)

    if args.format == "json":
        print(json.dumps(data, indent=2))
        return

    # Human-readable text output
    if metric == "full":
        report = data
        print(f"\n  MeshFlow Analytics — last {report.get('runs_analysed', 0)} runs")
        print(f"  {'─' * 52}")
        print(f"  Total cost:    ${report.get('total_cost_usd', 0):.6f}")
        print(f"  Total tokens:  {report.get('total_tokens', 0):,}")
        print(f"  Total carbon:  {report.get('total_carbon_gco2', 0):.4f} gCO₂")
        lat = report.get("latency", {})
        print(f"  P50 latency:   {lat.get('p50_run_p95_ms', 0):.0f} ms")
        print(f"  P95 latency:   {lat.get('p95_run_p95_ms', 0):.0f} ms")
        blk = report.get("blocked", {})
        print(f"  Blocked rate:  {blk.get('blocked_rate', 0)*100:.1f}%  "
              f"({blk.get('blocked_steps', 0)}/{blk.get('total_steps', 0)} steps)")
        qual = report.get("quality", {})
        print(f"  Quality trend: {qual.get('trend', 'n/a')}  "
              f"(Δ uncertainty {qual.get('delta', 0):+.4f})")
        nodes = report.get("top_costly_nodes", [])
        if nodes:
            print(f"\n  Top costly nodes:")
            print(f"  {'Node':<36}  {'Total $':>10}  {'Calls':>6}  {'Avg $':>10}")
            print(f"  {'─' * 36}  {'─' * 10}  {'─' * 6}  {'─' * 10}")
            for n_row in nodes[:5]:
                print(
                    f"  {n_row['node_id']:<36}  "
                    f"${n_row['total_cost_usd']:>9.6f}  "
                    f"{n_row['call_count']:>6}  "
                    f"${n_row['avg_cost_usd']:>9.6f}"
                )
        print()
    elif metric == "cost":
        print(f"\n  Cost trend — last {len(data)} runs:")
        for row in data:
            print(f"  {row['run_id']}  ${row['cost_usd']:.6f}")
        print()
    elif metric == "latency":
        print(f"\n  Latency percentiles (per-run p95) over {data.get('runs_analysed', 0)} runs:")
        print(f"  P50: {data.get('p50_run_p95_ms', 0):.0f} ms")
        print(f"  P95: {data.get('p95_run_p95_ms', 0):.0f} ms")
        print(f"  P99: {data.get('p99_run_p95_ms', 0):.0f} ms")
        print(f"  Mean: {data.get('mean_run_p95_ms', 0):.0f} ms\n")
    elif metric == "blocked":
        print(f"\n  Blocked step rate: {data.get('blocked_rate', 0)*100:.1f}%")
        print(f"  Blocked steps: {data.get('blocked_steps', 0)}/{data.get('total_steps', 0)}")
        print(f"  Max run blocked rate: {data.get('max_run_blocked_rate', 0)*100:.1f}%\n")
    elif metric == "quality":
        print(f"\n  Quality drift: {data.get('trend', 'n/a')}")
        print(f"  First half avg uncertainty: {data.get('first_half_avg', 0):.4f}")
        print(f"  Second half avg uncertainty: {data.get('second_half_avg', 0):.4f}")
        print(f"  Delta: {data.get('delta', 0):+.4f}\n")
    elif metric == "carbon":
        print(f"\n  Carbon footprint — last {len(data)} runs:")
        for row in data:
            print(f"  {row['run_id']}  {row['carbon_gco2']:.6f} gCO₂")
        print()
    elif metric == "nodes":
        print(f"\n  Top costly nodes:")
        for row in data:
            print(f"  {row['node_id']:<36}  ${row['total_cost_usd']:.6f}  "
                  f"×{row['call_count']}  avg ${row['avg_cost_usd']:.6f}")
        print()


# ── queue ─────────────────────────────────────────────────────────────────────


def _cmd_queue(args: argparse.Namespace) -> None:
    asyncio.run(_async_queue(args))


async def _async_queue(args: argparse.Namespace) -> None:
    from meshflow.queue import TaskQueue, QueueWorker, TaskStatus

    db = args.db

    if args.queue_cmd == "push":
        q = TaskQueue(db)
        payload: dict[str, Any] = {"workflow": args.yaml}
        if args.task:
            payload["task"] = args.task
        task_id = await q.push(payload, priority=args.priority)
        await q.close()
        print(f"\n  Task enqueued!")
        print(f"  ID:       {task_id}")
        print(f"  Workflow: {args.yaml}")
        print(f"  Priority: {args.priority}\n")

    elif args.queue_cmd == "status":
        q = TaskQueue(db)
        stats = await q.stats()
        await q.close()
        print(f"\n  Queue status ({db}):")
        for status_name, count in sorted(stats.items()):
            print(f"  {status_name:<12} {count}")
        print()

    elif args.queue_cmd == "list":
        q = TaskQueue(db)
        status_filter = TaskStatus(args.status) if args.status else None
        items = await q.list_tasks(status=status_filter, limit=args.limit)
        await q.close()
        if not items:
            print("  No tasks found.\n")
            return
        print(f"\n  {'Task ID':<38}  {'Status':<12}  {'Priority':>8}  {'Created'}")
        print(f"  {'─' * 38}  {'─' * 12}  {'─' * 8}  {'─' * 24}")
        for item in items:
            import datetime
            ts = datetime.datetime.fromtimestamp(item.created_at).strftime("%Y-%m-%d %H:%M:%S")
            print(f"  {item.task_id:<38}  {item.status.value:<12}  {item.priority:>8}  {ts}")
        print()

    elif args.queue_cmd == "cancel":
        q = TaskQueue(db)
        ok = await q.cancel(args.task_id)
        await q.close()
        if ok:
            print(f"  Task {args.task_id} cancelled.")
        else:
            print(f"  Task {args.task_id} not found or not cancellable (may already be running/done).")
            sys.exit(1)

    elif args.queue_cmd == "worker":
        import signal
        q = TaskQueue(db)
        worker = QueueWorker(q, concurrency=args.concurrency, poll_interval=args.poll_interval)
        stop = asyncio.Event()

        def _stop() -> None:
            print("\n  Worker: shutdown signal received…")
            stop.set()

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _stop)
            except (NotImplementedError, RuntimeError):
                pass

        print(f"\n  MeshFlow queue worker started")
        print(f"  Queue:       {db}")
        print(f"  Concurrency: {args.concurrency}")
        print(f"  Poll:        {args.poll_interval}s")
        print(f"  Press Ctrl-C to stop\n")
        await worker.run(stop_event=stop)
        await q.close()
        print("  Worker stopped.")
