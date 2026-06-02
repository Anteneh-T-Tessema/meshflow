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


def build_parser() -> argparse.ArgumentParser:
    """Return the fully-configured argument parser (testable without running main)."""
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
    # Time-travel: rewind to a step and re-run with optional overrides
    p_replay.add_argument("--rewind", type=int, default=0, metavar="STEP",
                          help="Rewind to step N (1-based) and re-run from there")
    p_replay.add_argument("--model", default="", dest="rewind_model",
                          help="Override model for the rewound portion")
    p_replay.add_argument("--prompt", default="", dest="rewind_prompt",
                          help="Prepend text to every agent system prompt in the rewound run")
    p_replay.add_argument("--yaml", default="", dest="rewind_yaml",
                          help="Workflow YAML to use for the rewound run")
    p_replay.add_argument("--inject", nargs="*", metavar="KEY=VALUE", default=[],
                          help="State injection: --inject user_tier=enterprise region=eu-west-1")
    # Branch & Compare mode
    p_replay.add_argument("--branch-compare", action="store_true",
                          help="Run Branch & Compare: fork run with multiple configs and diff outputs")
    p_replay.add_argument("--forks", nargs="*", metavar="LABEL:model=M[,prompt=P]", default=[],
                          help="Fork configs for --branch-compare, e.g. "
                               "baseline:model=sonnet haiku:model=haiku,prompt=be-concise")
    p_replay.add_argument("--compare-step", type=int, default=1,
                          help="Step index to fork from in --branch-compare mode (default: 1)")
    # Interactive replay: diff and fork
    p_replay.add_argument("--diff", default="", metavar="RUN_ID_B",
                          help="Diff this run against another run: meshflow replay <a> --diff <b>")
    p_replay.add_argument("--fork-at", type=int, default=-1, metavar="STEP",
                          help="Fork this run at step N and print the new run ID")

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
    # OIDC / SSO flags
    p_serve.add_argument(
        "--oidc-issuer", default="", dest="oidc_issuer",
        help="OIDC issuer URL (e.g. https://dev-abc.okta.com). "
             "Overrides MESHFLOW_OIDC_ISSUER env var.",
    )
    p_serve.add_argument(
        "--oidc-audience", default="", dest="oidc_audience",
        help="Expected JWT audience claim (default: meshflow-api). "
             "Overrides MESHFLOW_OIDC_AUDIENCE env var.",
    )
    p_serve.add_argument(
        "--oidc-role-claim", default="", dest="oidc_role_claim",
        help="JWT claim name containing groups/roles (default: groups). "
             "Overrides MESHFLOW_OIDC_ROLE_CLAIM env var.",
    )

    # dev
    p_dev = sub.add_parser("dev", help="Start server in dev mode with colored output")
    p_dev.add_argument("--host", default="127.0.0.1")
    p_dev.add_argument("--port", type=int, default=8765)
    p_dev.add_argument("--ledger", default=":memory:")

    # studio
    p_studio = sub.add_parser("studio", help="Start the MeshFlow Studio visual designer")
    p_studio.add_argument("--host", default="127.0.0.1")
    p_studio.add_argument("--port", type=int, default=8765)

    # codegen
    p_codegen = sub.add_parser("codegen", help="Generate C# (.NET), Java, or Go SDK wrappers from workflow YAML")
    p_codegen.add_argument("language", choices=["dotnet", "java", "go"], help="Target SDK language")
    p_codegen.add_argument("yaml", help="Path to workflow YAML file")

    # trace
    p_trace = sub.add_parser("trace", help="View a run trace in the terminal or browser")
    p_trace.add_argument("run_id", help="Run ID to inspect")
    p_trace.add_argument("--db", default="meshflow_runs.db")
    p_trace.add_argument("--json", dest="as_json", action="store_true", help="Output as JSON")
    p_trace.add_argument("--export", default="", metavar="FILE", help="Export trace to file")
    p_trace.add_argument("--format", default="terminal",
                         choices=["terminal", "langsmith", "json"],
                         dest="trace_format",
                         help="Output format: terminal (default), langsmith (LangSmith JSON), json (raw dict)")
    p_trace.add_argument("--browser", action="store_true",
                         help="Open trace in the visual browser UI instead of terminal")
    p_trace.add_argument("--port", type=int, default=7788,
                         help="Port for the trace server when using --browser (default 7788)")

    # trace-server
    p_trace_srv = sub.add_parser("trace-server", help="Start the visual trace server UI")
    p_trace_srv.add_argument("--db", default="meshflow_runs.db")
    p_trace_srv.add_argument("--port", type=int, default=7788)
    p_trace_srv.add_argument("--no-browser", dest="no_browser", action="store_true",
                             help="Don't auto-open browser")

    # doctor
    p_doctor = sub.add_parser("doctor", help="Run production readiness checks")
    p_doctor.add_argument("--db", default="meshflow_runs.db", help="Ledger path to validate")
    p_doctor.add_argument("--port", type=int, default=8000)
    p_doctor.add_argument("--json", dest="as_json", action="store_true")

    # env
    p_env = sub.add_parser("env", help="Generate .env file for production deployment")
    p_env.add_argument("--output", "-o", default="", metavar="FILE",
                       help="Write to FILE instead of stdout")
    p_env.add_argument("--overwrite", action="store_true")
    p_env.add_argument("--validate", metavar="FILE",
                       help="Validate an existing .env file")

    # deploy
    p_deploy = sub.add_parser("deploy", help="Build and run MeshFlow via Docker")
    p_deploy.add_argument("--tag", default="meshflow:latest", help="Docker image tag")
    p_deploy.add_argument("--port", type=int, default=8000, help="Host port")
    p_deploy.add_argument("--build-only", action="store_true", help="Build image but don't run")
    p_deploy.add_argument("--no-cache", action="store_true", help="Docker --no-cache")
    p_deploy.add_argument("--compose", action="store_true", help="Use docker compose instead")
    p_deploy.add_argument("--profile", default="", help="Docker Compose profile (e.g. postgres)")
    p_deploy.add_argument("--env-file", default=".env", help="Path to .env file")
    p_deploy.add_argument("--down", action="store_true", help="Stop and remove containers")
    p_deploy.add_argument("--status", action="store_true", help="Show container status")
    p_deploy.add_argument("--logs", action="store_true", help="Show container logs")

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
    p_eval.add_argument("--max-cost-delta", type=float, default=-1.0, help="Exit 1 if cost change vs baseline exceeds threshold")

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

    p_audit_verify = p_audit_sub.add_parser("verify-chain", help="Verify the tamper-evident hash chain for a run")
    p_audit_verify.add_argument("--run-id", required=True, dest="run_id", help="Run ID to verify")
    p_audit_verify.add_argument("--db", default="meshflow_runs.db", help="Ledger path")
    p_audit_verify.add_argument("--json", action="store_true", dest="as_json", help="Output result as JSON")

    # proxy server
    p_proxy = sub.add_parser("proxy", help="Start the MeshFlow HTTP proxy server (language-agnostic enforcement)")
    p_proxy.add_argument("--port", type=int, default=8080, help="Port to listen on (default 8080)")
    p_proxy.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1)")
    p_proxy.add_argument("--upstream", default="https://api.openai.com", help="Upstream API base URL")
    p_proxy.add_argument("--policy", default="", dest="policy_file", help="Policy YAML file for tool call rules")
    p_proxy.add_argument("--agent-id", default="http-proxy", dest="agent_id", help="Agent ID label in audit logs")

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

    p_wh_queue = p_webhooks_sub.add_parser("queue", help="Show pending delivery queue")
    p_wh_queue.add_argument("--db", default="meshflow_webhooks.db", help="Webhook queue SQLite path")
    p_wh_queue.add_argument("--limit", type=int, default=20, help="Max rows to show")
    p_wh_queue.add_argument("--json", action="store_true", dest="json_output", help="Output raw JSON")

    p_wh_dead = p_webhooks_sub.add_parser("dead", help="Show dead-letter deliveries")
    p_wh_dead.add_argument("--db", default="meshflow_webhooks.db", help="Webhook queue SQLite path")
    p_wh_dead.add_argument("--limit", type=int, default=20, help="Max rows to show")
    p_wh_dead.add_argument("--json", action="store_true", dest="json_output", help="Output raw JSON")

    p_wh_replay = p_webhooks_sub.add_parser("replay", help="Re-queue a dead or failed delivery")
    p_wh_replay.add_argument("delivery_id", help="Delivery ID to replay")
    p_wh_replay.add_argument("--db", default="meshflow_webhooks.db", help="Webhook queue SQLite path")

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

    # export-traces
    p_export = sub.add_parser("export-traces", help="Export agent traces as fine-tuning JSONL")
    p_export.add_argument("--db", default="meshflow_runs.db", help="Ledger SQLite path")
    p_export.add_argument("--output", "-o", default="traces.jsonl", help="Output JSONL file")
    p_export.add_argument(
        "--format", default="openai",
        choices=["openai", "anthropic", "generic", "sharegpt"],
        help="Output format (default: openai)",
    )
    p_export.add_argument("--min-confidence", type=float, default=0.0, dest="min_confidence")
    p_export.add_argument("--max-records", type=int, default=None, dest="max_records")
    p_export.add_argument("--agent", default="", dest="agent_name",
                          help="Filter to a specific agent name")
    p_export.add_argument("--no-dedup", action="store_false", dest="deduplicate",
                          help="Disable deduplication")
    p_export.add_argument("--stats", action="store_true", help="Print stats after export")

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

    # agent-serve
    p_aserve = sub.add_parser("agent-serve", help="Serve a single agent over A2A HTTP")
    p_aserve.add_argument("--agent", required=True, dest="agent_name", help="Agent name to serve")
    p_aserve.add_argument("--role", default="executor", help="Agent role (default: executor)")
    p_aserve.add_argument("--model", default="", help="LLM model override")
    p_aserve.add_argument("--host", default="0.0.0.0")
    p_aserve.add_argument("--port", type=int, default=8080)
    p_aserve.add_argument("--description", default="")

    # budget
    p_budget = sub.add_parser("budget", help="Manage per-agent cost and token budgets")
    p_budget_sub = p_budget.add_subparsers(dest="budget_cmd", required=True)

    p_bud_list = p_budget_sub.add_parser("list", help="List all budget accounts")
    p_bud_list.add_argument("--agent", default="", dest="agent_name")
    p_bud_list.add_argument("--db", default="meshflow_budgets.db")

    p_bud_status = p_budget_sub.add_parser("status", help="Show spend status for an account")
    p_bud_status.add_argument("account_id", help="Budget account ID")
    p_bud_status.add_argument("--db", default="meshflow_budgets.db")

    p_bud_set = p_budget_sub.add_parser("set", help="Create or update a budget account")
    p_bud_set.add_argument("account_id", help="Account ID (created if absent)")
    p_bud_set.add_argument("--agent", required=True, dest="agent_name")
    p_bud_set.add_argument("--period", default="daily",
                           choices=["daily", "weekly", "monthly", "total"])
    p_bud_set.add_argument("--limit-usd", type=float, default=0.0, dest="limit_usd")
    p_bud_set.add_argument("--limit-tokens", type=int, default=0, dest="limit_tokens")
    p_bud_set.add_argument("--name", default="")
    p_bud_set.add_argument("--db", default="meshflow_budgets.db")

    p_bud_reset = p_budget_sub.add_parser("reset", help="Zero out spend for the current window")
    p_bud_reset.add_argument("account_id")
    p_bud_reset.add_argument("--db", default="meshflow_budgets.db")

    p_bud_delete = p_budget_sub.add_parser("delete", help="Delete a budget account")
    p_bud_delete.add_argument("account_id")
    p_bud_delete.add_argument("--db", default="meshflow_budgets.db")

    # registry
    p_reg = sub.add_parser("registry", help="Manage the agent registry")
    p_reg_sub = p_reg.add_subparsers(dest="registry_cmd", required=True)

    p_reg_list = p_reg_sub.add_parser("list", help="List registered agents")
    p_reg_list.add_argument("--role", default="")
    p_reg_list.add_argument("--owner", default="")
    p_reg_list.add_argument("--tag", default="")
    p_reg_list.add_argument("--db", default="meshflow_registry.db")

    p_reg_search = p_reg_sub.add_parser("search", help="Search agents by keyword")
    p_reg_search.add_argument("query", help="Search query")
    p_reg_search.add_argument("--role", default="")
    p_reg_search.add_argument("--db", default="meshflow_registry.db")

    p_reg_get = p_reg_sub.add_parser("get", help="Show full manifest for an agent")
    p_reg_get.add_argument("name", help="Agent name")
    p_reg_get.add_argument("--db", default="meshflow_registry.db")

    p_reg_publish = p_reg_sub.add_parser("publish", help="Publish or update an agent manifest")
    p_reg_publish.add_argument("name", help="Agent name (slug)")
    p_reg_publish.add_argument("--role", default="executor")
    p_reg_publish.add_argument("--description", default="")
    p_reg_publish.add_argument("--tags", default="", help="Comma-separated tags")
    p_reg_publish.add_argument("--capabilities", default="", help="Comma-separated capabilities")
    p_reg_publish.add_argument("--version", default="1.0.0")
    p_reg_publish.add_argument("--owner", default="")
    p_reg_publish.add_argument("--url", default="")
    p_reg_publish.add_argument("--db", default="meshflow_registry.db")

    p_reg_unpublish = p_reg_sub.add_parser("unpublish", help="Remove an agent from the registry")
    p_reg_unpublish.add_argument("name", help="Agent name to remove")
    p_reg_unpublish.add_argument("--db", default="meshflow_registry.db")

    # schedule
    p_sched = sub.add_parser("schedule", help="Manage cron-scheduled agent tasks")
    p_sched_sub = p_sched.add_subparsers(dest="schedule_cmd", required=True)

    p_sched_list_cmd = p_sched_sub.add_parser("list", help="List all schedules")
    p_sched_list_cmd.add_argument("--agent", default="", dest="agent_name", help="Filter by agent name")
    p_sched_list_cmd.add_argument("--db", default="meshflow_schedules.db")

    p_sched_add_cmd = p_sched_sub.add_parser("add", help="Add a cron-scheduled task")
    p_sched_add_cmd.add_argument("--agent", required=True, dest="agent_name", help="Agent name to dispatch to")
    p_sched_add_cmd.add_argument("--cron", required=True, help="Cron expression, e.g. '0 9 * * 1-5'")
    p_sched_add_cmd.add_argument("--task", default="", dest="task_payload", help="Payload / prompt sent to agent")
    p_sched_add_cmd.add_argument("--name", default="", help="Human-friendly schedule name")
    p_sched_add_cmd.add_argument("--db", default="meshflow_schedules.db")

    p_sched_get_cmd = p_sched_sub.add_parser("get", help="Show a schedule by ID")
    p_sched_get_cmd.add_argument("schedule_id", help="Schedule ID")
    p_sched_get_cmd.add_argument("--db", default="meshflow_schedules.db")

    p_sched_rm_cmd = p_sched_sub.add_parser("remove", help="Remove a schedule")
    p_sched_rm_cmd.add_argument("schedule_id", help="Schedule ID")
    p_sched_rm_cmd.add_argument("--db", default="meshflow_schedules.db")

    p_sched_en_cmd = p_sched_sub.add_parser("enable", help="Enable a disabled schedule")
    p_sched_en_cmd.add_argument("schedule_id", help="Schedule ID")
    p_sched_en_cmd.add_argument("--db", default="meshflow_schedules.db")

    p_sched_dis_cmd = p_sched_sub.add_parser("disable", help="Disable a schedule without deleting it")
    p_sched_dis_cmd.add_argument("schedule_id", help="Schedule ID")
    p_sched_dis_cmd.add_argument("--db", default="meshflow_schedules.db")

    p_sched_runs_cmd = p_sched_sub.add_parser("runs", help="Show recent runs for a schedule")
    p_sched_runs_cmd.add_argument("schedule_id", help="Schedule ID")
    p_sched_runs_cmd.add_argument("--limit", type=int, default=20)
    p_sched_runs_cmd.add_argument("--db", default="meshflow_schedules.db")

    # ratelimit
    p_rl = sub.add_parser("ratelimit", help="Manage per-agent and per-team rate limit policies")
    p_rl_sub = p_rl.add_subparsers(dest="ratelimit_cmd", required=True)

    p_rl_list = p_rl_sub.add_parser("list", help="List all rate limit policies")
    p_rl_list.add_argument("--db", default="meshflow_ratelimits.db")

    p_rl_set = p_rl_sub.add_parser("set", help="Set a rate limit policy for an agent or team")
    p_rl_set.add_argument("key", help="Agent name, team slug, or '*' for global default")
    p_rl_set.add_argument("--max-requests", type=int, default=0, dest="max_requests",
                          help="Max requests per window (0 = unlimited)")
    p_rl_set.add_argument("--max-tokens", type=int, default=0, dest="max_tokens",
                          help="Max LLM tokens per window (0 = unlimited)")
    p_rl_set.add_argument("--window", type=float, default=60.0, dest="window_s",
                          help="Window duration in seconds (default: 60)")
    p_rl_set.add_argument("--warn-at", type=float, default=0.80, dest="warn_at",
                          help="Warn threshold as fraction of limit (default: 0.80)")

    p_rl_remove = p_rl_sub.add_parser("remove", help="Remove a rate limit policy")
    p_rl_remove.add_argument("key", help="Agent name, team slug, or '*'")

    p_rl_status = p_rl_sub.add_parser("status", help="Show current window usage for a key")
    p_rl_status.add_argument("key", help="Agent name or team slug")

    # circuit
    p_circ = sub.add_parser("circuit", help="Manage circuit breakers for resilient agent calls")
    p_circ_sub = p_circ.add_subparsers(dest="circuit_cmd", required=True)

    p_circ_list = p_circ_sub.add_parser("list", help="List all persisted circuit breakers")
    p_circ_list.add_argument("--db", default="meshflow_circuits.db")

    p_circ_status = p_circ_sub.add_parser("status", help="Show status of a circuit breaker")
    p_circ_status.add_argument("name", help="Circuit breaker name")
    p_circ_status.add_argument("--db", default="meshflow_circuits.db")

    p_circ_reset = p_circ_sub.add_parser("reset", help="Force a circuit breaker to CLOSED")
    p_circ_reset.add_argument("name", help="Circuit breaker name")
    p_circ_reset.add_argument("--db", default="meshflow_circuits.db")

    p_circ_trip = p_circ_sub.add_parser("trip", help="Force a circuit breaker to OPEN")
    p_circ_trip.add_argument("name", help="Circuit breaker name")
    p_circ_trip.add_argument("--db", default="meshflow_circuits.db")

    p_circ_remove = p_circ_sub.add_parser("remove", help="Delete a circuit breaker record")
    p_circ_remove.add_argument("name", help="Circuit breaker name")
    p_circ_remove.add_argument("--db", default="meshflow_circuits.db")

    # security
    p_sec = sub.add_parser("security", help="Security utilities — prompt injection scanning, PII detection")
    p_sec_sub = p_sec.add_subparsers(dest="security_cmd", required=True)

    p_sec_scan = p_sec_sub.add_parser("scan", help="Scan text for prompt injection attacks")
    p_sec_scan.add_argument("text", nargs="?", default=None, help="Text to scan (reads stdin if omitted)")
    p_sec_scan.add_argument("--threshold",       type=float, default=0.3,  help="Detection threshold (default 0.3)")
    p_sec_scan.add_argument("--block-threshold", type=float, default=0.6,  dest="block_threshold",
                            help="Block threshold (default 0.6)")
    p_sec_scan.add_argument("--categories", nargs="+", default=None,
                            help="Restrict to specific categories (default: all)")
    p_sec_scan.add_argument("--json", action="store_true", dest="json_output",
                            help="Output raw JSON result")

    p_sec_secrets = p_sec_sub.add_parser("secrets", help="Scan text for leaked credentials and secrets")
    p_sec_secrets.add_argument("text", nargs="?", default=None, help="Text to scan (reads stdin if omitted)")
    p_sec_secrets.add_argument("--categories", nargs="+", default=None,
                               help="Restrict to specific categories (default: all)")
    p_sec_secrets.add_argument("--min-confidence", type=float, default=0.70, dest="min_confidence",
                               help="Minimum pattern confidence to report (default: 0.70)")
    p_sec_secrets.add_argument("--redact", action="store_true",
                               help="Output redacted text instead of blocking")
    p_sec_secrets.add_argument("--json", action="store_true", dest="json_output",
                               help="Output raw JSON result")

    # memory
    p_mem = sub.add_parser("memory", help="Manage semantic memory store")
    p_mem_sub = p_mem.add_subparsers(dest="memory_cmd", required=True)

    p_mem_search = p_mem_sub.add_parser("search", help="Search memory by semantic similarity")
    p_mem_search.add_argument("query", help="Natural-language search query")
    p_mem_search.add_argument("--k",          type=int,   default=5,    help="Max results (default 5)")
    p_mem_search.add_argument("--min-score",  type=float, default=-1.0, dest="min_score",
                              help="Minimum cosine similarity (default -1 = no filter)")
    p_mem_search.add_argument("--provider",   default="auto",
                              choices=["auto", "hash"], help="Embedding provider (default auto)")
    p_mem_search.add_argument("--db",         default="meshflow_memory.db")
    p_mem_search.add_argument("--json",       action="store_true", dest="json_output")

    p_mem_store = p_mem_sub.add_parser("store", help="Store a text entry in semantic memory")
    p_mem_store.add_argument("key",  help="Unique key for this memory entry")
    p_mem_store.add_argument("text", help="Text to embed and store")
    p_mem_store.add_argument("--meta", default="{}", help="JSON metadata object (default '{}')")
    p_mem_store.add_argument("--provider", default="auto", choices=["auto", "hash"])
    p_mem_store.add_argument("--db", default="meshflow_memory.db")

    p_mem_get = p_mem_sub.add_parser("get", help="Retrieve a memory entry by exact key")
    p_mem_get.add_argument("key", help="Memory entry key")
    p_mem_get.add_argument("--db", default="meshflow_memory.db")

    p_mem_list = p_mem_sub.add_parser("list", help="List stored memory entries")
    p_mem_list.add_argument("--limit",  type=int, default=20)
    p_mem_list.add_argument("--offset", type=int, default=0)
    p_mem_list.add_argument("--db",     default="meshflow_memory.db")

    p_mem_delete = p_mem_sub.add_parser("delete", help="Delete a memory entry by key")
    p_mem_delete.add_argument("key", help="Memory entry key")
    p_mem_delete.add_argument("--db", default="meshflow_memory.db")

    p_mem_clear = p_mem_sub.add_parser("clear", help="Delete all memory entries")
    p_mem_clear.add_argument("--db",    default="meshflow_memory.db")
    p_mem_clear.add_argument("--yes",   action="store_true", help="Skip confirmation")

    p_mem_export = p_mem_sub.add_parser("export", help="Export agent memory snapshot to a JSON file")
    p_mem_export.add_argument("--agent", required=True, help="Agent name (session ID)")
    p_mem_export.add_argument("--output", default="", metavar="FILE",
                              help="Output JSON file (default: <agent>_memory.json)")
    p_mem_export.add_argument("--db", default="meshflow_memory.db")

    p_mem_import = p_mem_sub.add_parser("import", help="Restore agent memory from a JSON snapshot")
    p_mem_import.add_argument("file", help="JSON snapshot file to restore from")
    p_mem_import.add_argument("--agent", default="",
                              help="Override the agent name from the snapshot")
    p_mem_import.add_argument("--db", default="meshflow_memory.db")

    # identity
    p_id = sub.add_parser("identity", help="Agent identity registry and zero-trust token management")
    p_id_sub = p_id.add_subparsers(dest="identity_cmd", required=True)

    p_id_create = p_id_sub.add_parser("create", help="Register a new agent identity")
    p_id_create.add_argument("name", help="Agent name (must be unique)")
    p_id_create.add_argument("--capabilities", nargs="*", default=[], metavar="CAP")
    p_id_create.add_argument("--issuer", default="meshflow")
    p_id_create.add_argument("--db", default="meshflow_identity.db")

    p_id_list = p_id_sub.add_parser("list", help="List registered agent identities")
    p_id_list.add_argument("--active-only", action="store_true", dest="active_only")
    p_id_list.add_argument("--db", default="meshflow_identity.db")
    p_id_list.add_argument("--json", action="store_true", dest="json_output")

    p_id_get = p_id_sub.add_parser("get", help="Show identity by name")
    p_id_get.add_argument("name", help="Agent name")
    p_id_get.add_argument("--db", default="meshflow_identity.db")

    p_id_revoke = p_id_sub.add_parser("revoke", help="Revoke an agent identity")
    p_id_revoke.add_argument("agent_id", help="Agent ID to revoke")
    p_id_revoke.add_argument("--db", default="meshflow_identity.db")

    p_id_sign = p_id_sub.add_parser("sign", help="Issue a signed token for an agent")
    p_id_sign.add_argument("name", help="Agent name")
    p_id_sign.add_argument("--secret", required=True, help="HMAC signing secret")
    p_id_sign.add_argument("--ttl", type=float, default=3600.0, dest="ttl_s")
    p_id_sign.add_argument("--db", default="meshflow_identity.db")

    p_id_verify = p_id_sub.add_parser("verify", help="Verify a token string")
    p_id_verify.add_argument("token", help="Token string to verify")
    p_id_verify.add_argument("--secret", required=True, help="HMAC signing secret")

    # lineage
    p_lin = sub.add_parser("lineage", help="Data lineage graph — GDPR Article 30 provenance")
    p_lin_sub = p_lin.add_subparsers(dest="lineage_cmd", required=True)

    p_lin_show = p_lin_sub.add_parser("show", help="Show a lineage node by ID")
    p_lin_show.add_argument("node_id", help="Node ID")
    p_lin_show.add_argument("--db", default="meshflow_lineage.db")
    p_lin_show.add_argument("--json", action="store_true", dest="json_output")

    p_lin_trace = p_lin_sub.add_parser("trace", help="Trace upstream provenance for a node")
    p_lin_trace.add_argument("node_id", help="Node ID to trace from")
    p_lin_trace.add_argument("--db", default="meshflow_lineage.db")
    p_lin_trace.add_argument("--json", action="store_true", dest="json_output")

    p_lin_impact = p_lin_sub.add_parser("impact", help="Impact analysis — downstream of a node")
    p_lin_impact.add_argument("node_id", help="Node ID to analyse")
    p_lin_impact.add_argument("--db", default="meshflow_lineage.db")
    p_lin_impact.add_argument("--json", action="store_true", dest="json_output")

    p_lin_run = p_lin_sub.add_parser("run", help="List all lineage nodes for a run")
    p_lin_run.add_argument("run_id", help="Run ID")
    p_lin_run.add_argument("--db", default="meshflow_lineage.db")
    p_lin_run.add_argument("--json", action="store_true", dest="json_output")

    p_lin_delete = p_lin_sub.add_parser("delete", help="GDPR erasure — delete all nodes for a subject")
    p_lin_delete.add_argument("name", help="Data subject name")
    p_lin_delete.add_argument("--db", default="meshflow_lineage.db")
    p_lin_delete.add_argument("--yes", action="store_true", help="Skip confirmation")

    p_lin_stats = p_lin_sub.add_parser("stats", help="Show lineage graph statistics")
    p_lin_stats.add_argument("--db", default="meshflow_lineage.db")

    # locks
    p_locks = sub.add_parser("locks", help="Distributed lock management")
    p_locks_sub = p_locks.add_subparsers(dest="locks_cmd", required=True)

    p_locks_list = p_locks_sub.add_parser("list", help="List active locks")
    p_locks_list.add_argument("--db", default="meshflow_locks.db")
    p_locks_list.add_argument("--all", action="store_true", dest="show_all",
                               help="Include expired entries")
    p_locks_list.add_argument("--json", action="store_true", dest="json_output")

    p_locks_status = p_locks_sub.add_parser("status", help="Show lock status for a resource")
    p_locks_status.add_argument("resource_id", help="Resource ID to check")
    p_locks_status.add_argument("--db", default="meshflow_locks.db")

    p_locks_release = p_locks_sub.add_parser("release", help="Force-release a lock (admin)")
    p_locks_release.add_argument("resource_id", help="Resource ID to release")
    p_locks_release.add_argument("--db", default="meshflow_locks.db")

    p_locks_purge = p_locks_sub.add_parser("purge", help="Delete all expired lock entries")
    p_locks_purge.add_argument("--db", default="meshflow_locks.db")

    # alerts
    p_alerts = sub.add_parser("alerts", help="Alert engine — metric-threshold rules and fired alerts")
    p_alerts_sub = p_alerts.add_subparsers(dest="alerts_cmd", required=True)

    # alerts rules
    p_alrt_rules = p_alerts_sub.add_parser("rules", help="Manage alert rules")
    p_alrt_rules_sub = p_alrt_rules.add_subparsers(dest="rules_cmd", required=True)

    p_alrt_rules_list = p_alrt_rules_sub.add_parser("list", help="List alert rules")
    p_alrt_rules_list.add_argument("--agent", default="", dest="agent_name")
    p_alrt_rules_list.add_argument("--enabled-only", action="store_true", dest="enabled_only")
    p_alrt_rules_list.add_argument("--db", default="meshflow_alerts.db")
    p_alrt_rules_list.add_argument("--json", action="store_true", dest="json_output")

    p_alrt_rules_add = p_alrt_rules_sub.add_parser("add", help="Add an alert rule")
    p_alrt_rules_add.add_argument("name", help="Rule name")
    p_alrt_rules_add.add_argument("--agent", required=True, dest="agent_name")
    p_alrt_rules_add.add_argument("--metric", required=True)
    p_alrt_rules_add.add_argument("--operator", required=True,
                                   choices=["gt", "lt", "gte", "lte", "eq"])
    p_alrt_rules_add.add_argument("--threshold", required=True, type=float)
    p_alrt_rules_add.add_argument("--window", type=float, default=60.0, dest="window_s",
                                   help="Aggregation window in seconds (default 60)")
    p_alrt_rules_add.add_argument("--agg", default="mean",
                                   choices=["mean", "max", "min", "sum", "count"], dest="agg_fn")
    p_alrt_rules_add.add_argument("--webhook-url", default="", dest="webhook_url")
    p_alrt_rules_add.add_argument("--webhook-secret", default="", dest="webhook_secret")
    p_alrt_rules_add.add_argument("--db", default="meshflow_alerts.db")

    p_alrt_rules_remove = p_alrt_rules_sub.add_parser("remove", help="Delete an alert rule")
    p_alrt_rules_remove.add_argument("rule_id", help="Rule ID to delete")
    p_alrt_rules_remove.add_argument("--db", default="meshflow_alerts.db")

    p_alrt_rules_enable = p_alrt_rules_sub.add_parser("enable", help="Enable a disabled rule")
    p_alrt_rules_enable.add_argument("rule_id")
    p_alrt_rules_enable.add_argument("--db", default="meshflow_alerts.db")

    p_alrt_rules_disable = p_alrt_rules_sub.add_parser("disable", help="Disable a rule")
    p_alrt_rules_disable.add_argument("rule_id")
    p_alrt_rules_disable.add_argument("--db", default="meshflow_alerts.db")

    # alerts list / ack / status
    p_alrt_list = p_alerts_sub.add_parser("list", help="List fired alerts")
    p_alrt_list.add_argument("--status", default="", choices=["", "firing", "resolved", "acked"])
    p_alrt_list.add_argument("--agent", default="", dest="agent_name")
    p_alrt_list.add_argument("--limit", type=int, default=20)
    p_alrt_list.add_argument("--db", default="meshflow_alerts.db")
    p_alrt_list.add_argument("--json", action="store_true", dest="json_output")

    p_alrt_ack = p_alerts_sub.add_parser("ack", help="Acknowledge a firing alert")
    p_alrt_ack.add_argument("alert_id", help="Alert ID to acknowledge")
    p_alrt_ack.add_argument("--by", default="cli", dest="acked_by", help="Acknowledger identity")
    p_alrt_ack.add_argument("--db", default="meshflow_alerts.db")

    p_alrt_status = p_alerts_sub.add_parser("status", help="Show alert engine summary")
    p_alrt_status.add_argument("--db", default="meshflow_alerts.db")

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

    # canary
    p_can = sub.add_parser("canary", help="Canary agent router — progressive traffic splitting")
    p_can_sub = p_can.add_subparsers(dest="canary_cmd", required=True)

    p_can_create = p_can_sub.add_parser("create", help="Create a new canary experiment")
    p_can_create.add_argument("name", help="Experiment name (must be unique)")
    p_can_create.add_argument("--stable", required=True, dest="stable_agent", help="Stable agent name")
    p_can_create.add_argument("--canary", required=True, dest="canary_agent", help="Canary agent name")
    p_can_create.add_argument("--split", type=float, default=0.1, help="Canary traffic fraction (0.0–1.0, default 0.1)")
    p_can_create.add_argument("--min-requests", type=int, default=10, dest="min_requests")
    p_can_create.add_argument("--promote-threshold", type=float, default=0.95, dest="promote_threshold")
    p_can_create.add_argument("--rollback-threshold", type=float, default=0.80, dest="rollback_threshold")
    p_can_create.add_argument("--db", default="meshflow_canary.db")

    p_can_list = p_can_sub.add_parser("list", help="List canary experiments")
    p_can_list.add_argument("--status", default="", choices=["", "active", "promoted", "rolled_back", "paused"])
    p_can_list.add_argument("--db", default="meshflow_canary.db")
    p_can_list.add_argument("--json", action="store_true", dest="json_output")

    p_can_status = p_can_sub.add_parser("status", help="Show stats for a canary experiment")
    p_can_status.add_argument("name", help="Experiment name")
    p_can_status.add_argument("--db", default="meshflow_canary.db")

    p_can_promote = p_can_sub.add_parser("promote", help="Promote a canary to stable")
    p_can_promote.add_argument("name", help="Experiment name")
    p_can_promote.add_argument("--db", default="meshflow_canary.db")

    p_can_rollback = p_can_sub.add_parser("rollback", help="Roll back a canary experiment")
    p_can_rollback.add_argument("name", help="Experiment name")
    p_can_rollback.add_argument("--db", default="meshflow_canary.db")

    p_can_pause = p_can_sub.add_parser("pause", help="Pause a canary experiment")
    p_can_pause.add_argument("name", help="Experiment name")
    p_can_pause.add_argument("--db", default="meshflow_canary.db")

    # flags
    p_flags = sub.add_parser("flags", help="Feature flags — targeted rollouts for agent behaviour")
    p_flags_sub = p_flags.add_subparsers(dest="flags_cmd", required=True)

    p_fl_define = p_flags_sub.add_parser("define", help="Define a new feature flag")
    p_fl_define.add_argument("name", help="Flag name (must be unique)")
    p_fl_define.add_argument("--type", default="bool", choices=["bool", "string", "number"], dest="flag_type")
    p_fl_define.add_argument("--default", default="false", dest="default_value", help="Default value")
    p_fl_define.add_argument("--description", default="")
    p_fl_define.add_argument("--rollout", type=float, default=100.0, dest="rollout_pct",
                              help="Rollout percentage 0–100 (default 100)")
    p_fl_define.add_argument("--db", default="meshflow_flags.db")

    p_fl_list = p_flags_sub.add_parser("list", help="List feature flags")
    p_fl_list.add_argument("--enabled-only", action="store_true", dest="enabled_only")
    p_fl_list.add_argument("--db", default="meshflow_flags.db")
    p_fl_list.add_argument("--json", action="store_true", dest="json_output")

    p_fl_get = p_flags_sub.add_parser("get", help="Show a flag by name")
    p_fl_get.add_argument("name", help="Flag name")
    p_fl_get.add_argument("--db", default="meshflow_flags.db")

    p_fl_enable = p_flags_sub.add_parser("enable", help="Enable a flag")
    p_fl_enable.add_argument("name", help="Flag name")
    p_fl_enable.add_argument("--db", default="meshflow_flags.db")

    p_fl_disable = p_flags_sub.add_parser("disable", help="Disable a flag")
    p_fl_disable.add_argument("name", help="Flag name")
    p_fl_disable.add_argument("--db", default="meshflow_flags.db")

    p_fl_delete = p_flags_sub.add_parser("delete", help="Delete a flag and all its rules")
    p_fl_delete.add_argument("name", help="Flag name")
    p_fl_delete.add_argument("--db", default="meshflow_flags.db")

    p_fl_rule = p_flags_sub.add_parser("add-rule", help="Add a targeting rule to a flag")
    p_fl_rule.add_argument("name", help="Flag name")
    p_fl_rule.add_argument("--key", required=True, dest="condition_key", help="Context key to match")
    p_fl_rule.add_argument("--op", required=True, dest="condition_op",
                            choices=["eq", "neq", "in", "gt", "lt", "gte", "lte", "contains"])
    p_fl_rule.add_argument("--value", required=True, dest="condition_value", help="Value to compare against")
    p_fl_rule.add_argument("--return", required=True, dest="return_value", help="Value to return when rule matches")
    p_fl_rule.add_argument("--priority", type=int, default=0)
    p_fl_rule.add_argument("--db", default="meshflow_flags.db")

    p_fl_eval = p_flags_sub.add_parser("evaluate", help="Evaluate a flag for a context")
    p_fl_eval.add_argument("name", help="Flag name")
    p_fl_eval.add_argument("--context", default="{}", help="JSON context dict")
    p_fl_eval.add_argument("--db", default="meshflow_flags.db")

    # ── vault ─────────────────────────────────────────────────────────────────
    p_vault = sub.add_parser("vault", help="Secret vault — store/retrieve/rotate encrypted secrets")
    p_vault_sub = p_vault.add_subparsers(dest="vault_cmd", required=True)

    p_vlt_store = p_vault_sub.add_parser("store", help="Encrypt and store a secret")
    p_vlt_store.add_argument("name", help="Secret name (unique key)")
    p_vlt_store.add_argument("value", help="Plaintext secret value")
    p_vlt_store.add_argument("--category", default="generic")
    p_vlt_store.add_argument("--description", default="")
    p_vlt_store.add_argument("--passphrase", default="meshflow-vault")
    p_vlt_store.add_argument("--db", default="meshflow_vault.db")

    p_vlt_get = p_vault_sub.add_parser("retrieve", help="Decrypt and retrieve a secret")
    p_vlt_get.add_argument("name", help="Secret name")
    p_vlt_get.add_argument("--passphrase", default="meshflow-vault")
    p_vlt_get.add_argument("--db", default="meshflow_vault.db")

    p_vlt_rot = p_vault_sub.add_parser("rotate", help="Re-encrypt a secret with a new value")
    p_vlt_rot.add_argument("name", help="Secret name")
    p_vlt_rot.add_argument("new_value", help="New plaintext value")
    p_vlt_rot.add_argument("--passphrase", default="meshflow-vault")
    p_vlt_rot.add_argument("--db", default="meshflow_vault.db")

    p_vlt_del = p_vault_sub.add_parser("delete", help="Delete a secret")
    p_vlt_del.add_argument("name", help="Secret name")
    p_vlt_del.add_argument("--passphrase", default="meshflow-vault")
    p_vlt_del.add_argument("--db", default="meshflow_vault.db")

    p_vlt_list = p_vault_sub.add_parser("list", help="List secrets (metadata only, no values)")
    p_vlt_list.add_argument("--category", default="")
    p_vlt_list.add_argument("--db", default="meshflow_vault.db")
    p_vlt_list.add_argument("--passphrase", default="meshflow-vault")

    p_vlt_audit = p_vault_sub.add_parser("audit", help="Show vault audit log")
    p_vlt_audit.add_argument("--name", default="", help="Filter by secret name")
    p_vlt_audit.add_argument("--limit", type=int, default=20)
    p_vlt_audit.add_argument("--db", default="meshflow_vault.db")
    p_vlt_audit.add_argument("--passphrase", default="meshflow-vault")

    # ── tenant ────────────────────────────────────────────────────────────────
    p_tenant = sub.add_parser("tenant", help="Multi-tenant management")
    p_tenant_sub = p_tenant.add_subparsers(dest="tenant_cmd", required=True)

    p_ten_create = p_tenant_sub.add_parser("create", help="Create a new tenant")
    p_ten_create.add_argument("name", help="Display name")
    p_ten_create.add_argument("slug", help="URL-safe slug (unique)")
    p_ten_create.add_argument("--plan", default="free", choices=["free", "pro", "enterprise"])
    p_ten_create.add_argument("--db", default="meshflow_tenants.db")

    p_ten_list = p_tenant_sub.add_parser("list", help="List tenants")
    p_ten_list.add_argument("--status", default="", choices=["", "active", "suspended", "deleted"])
    p_ten_list.add_argument("--db", default="meshflow_tenants.db")

    p_ten_get = p_tenant_sub.add_parser("get", help="Get tenant by slug")
    p_ten_get.add_argument("slug", help="Tenant slug")
    p_ten_get.add_argument("--db", default="meshflow_tenants.db")

    p_ten_suspend = p_tenant_sub.add_parser("suspend", help="Suspend a tenant")
    p_ten_suspend.add_argument("slug", help="Tenant slug")
    p_ten_suspend.add_argument("--db", default="meshflow_tenants.db")

    p_ten_plan = p_tenant_sub.add_parser("plan", help="Change tenant plan")
    p_ten_plan.add_argument("slug", help="Tenant slug")
    p_ten_plan.add_argument("plan", choices=["free", "pro", "enterprise"])
    p_ten_plan.add_argument("--db", default="meshflow_tenants.db")

    # ── tracing ───────────────────────────────────────────────────────────────
    p_tracing = sub.add_parser("tracing", help="Distributed trace inspection")
    p_tracing_sub = p_tracing.add_subparsers(dest="tracing_cmd", required=True)

    p_tr_show = p_tracing_sub.add_parser("show", help="Show all spans for a trace ID")
    p_tr_show.add_argument("trace_id", help="Trace ID (hex)")
    p_tr_show.add_argument("--db", default="meshflow_traces.db")

    p_tr_run = p_tracing_sub.add_parser("run", help="Show spans for a run ID")
    p_tr_run.add_argument("run_id", help="Run ID")
    p_tr_run.add_argument("--db", default="meshflow_traces.db")

    p_tr_count = p_tracing_sub.add_parser("count", help="Count total spans stored")
    p_tr_count.add_argument("--db", default="meshflow_traces.db")

    # ── policy ────────────────────────────────────────────────────────────────
    p_policy = sub.add_parser("policy", help="Policy-as-code engine")
    p_policy_sub = p_policy.add_subparsers(dest="policy_cmd", required=True)

    p_pol_add = p_policy_sub.add_parser("add", help="Add a policy rule")
    p_pol_add.add_argument("name", help="Rule name (unique)")
    p_pol_add.add_argument("--action", required=True, choices=["allow", "deny", "log", "alert"])
    p_pol_add.add_argument("--framework", default="custom")
    p_pol_add.add_argument("--priority", type=int, default=0)
    p_pol_add.add_argument("--description", default="")
    p_pol_add.add_argument("--condition", action="append", dest="conditions", metavar="FIELD:OP:VALUE",
                            help="Condition in field:op:value format; repeatable")
    p_pol_add.add_argument("--db", default="meshflow_policy.db")

    p_pol_list = p_policy_sub.add_parser("list", help="List policy rules")
    p_pol_list.add_argument("--framework", default="")
    p_pol_list.add_argument("--enabled-only", action="store_true")
    p_pol_list.add_argument("--db", default="meshflow_policy.db")

    p_pol_enable = p_policy_sub.add_parser("enable", help="Enable a rule by name")
    p_pol_enable.add_argument("name", help="Rule name")
    p_pol_enable.add_argument("--db", default="meshflow_policy.db")

    p_pol_disable = p_policy_sub.add_parser("disable", help="Disable a rule by name")
    p_pol_disable.add_argument("name", help="Rule name")
    p_pol_disable.add_argument("--db", default="meshflow_policy.db")

    p_pol_eval = p_policy_sub.add_parser("evaluate", help="Evaluate context against policy")
    p_pol_eval.add_argument("--context", required=True, help="JSON context dict")
    p_pol_eval.add_argument("--framework", default="")
    p_pol_eval.add_argument("--db", default="meshflow_policy.db")

    # ── sla ───────────────────────────────────────────────────────────────────
    p_sla = sub.add_parser("sla", help="Agent SLA contracts and breach tracking")
    p_sla_sub = p_sla.add_subparsers(dest="sla_cmd", required=True)

    p_sla_define = p_sla_sub.add_parser("define", help="Define an SLA contract for an agent")
    p_sla_define.add_argument("agent_name", help="Agent name")
    p_sla_define.add_argument("--p50", type=float, required=True, metavar="MS")
    p_sla_define.add_argument("--p95", type=float, required=True, metavar="MS")
    p_sla_define.add_argument("--p99", type=float, required=True, metavar="MS")
    p_sla_define.add_argument("--error-rate", type=float, default=0.05)
    p_sla_define.add_argument("--window", type=float, default=3600.0, metavar="SECONDS")
    p_sla_define.add_argument("--db", default="meshflow_sla.db")

    p_sla_stats = p_sla_sub.add_parser("stats", help="Show latency stats for an agent")
    p_sla_stats.add_argument("agent_name", help="Agent name")
    p_sla_stats.add_argument("--window", type=float, default=3600.0, metavar="SECONDS")
    p_sla_stats.add_argument("--db", default="meshflow_sla.db")

    p_sla_breaches = p_sla_sub.add_parser("breaches", help="List recent SLA breaches")
    p_sla_breaches.add_argument("--agent", default="", help="Filter by agent name")
    p_sla_breaches.add_argument("--limit", type=int, default=20)
    p_sla_breaches.add_argument("--db", default="meshflow_sla.db")

    p_sla_list = p_sla_sub.add_parser("list", help="List all SLA contracts")
    p_sla_list.add_argument("--db", default="meshflow_sla.db")

    # ── snapshot ──────────────────────────────────────────────────────────────
    p_snapshot = sub.add_parser("snapshot", help="Compliance snapshot — ZIP export of all audit evidence")
    p_snapshot_sub = p_snapshot.add_subparsers(dest="snapshot_cmd", required=True)

    p_snap_export = p_snapshot_sub.add_parser("export", help="Export compliance snapshot to a ZIP file")
    p_snap_export.add_argument("--output", default="meshflow_snapshot.zip", metavar="FILE")
    p_snap_export.add_argument("--description", default="")
    p_snap_export.add_argument("--created-by", default="cli")
    p_snap_export.add_argument("--flags-db", default="meshflow_flags.db")
    p_snap_export.add_argument("--policy-db", default="meshflow_policy.db")
    p_snap_export.add_argument("--sla-db", default="meshflow_sla.db")
    p_snap_export.add_argument("--vault-db", default="meshflow_vault.db")
    p_snap_export.add_argument("--vault-passphrase", default="meshflow-vault")
    p_snap_export.add_argument("--tenant-db", default="meshflow_tenants.db")

    # ── dasc ──────────────────────────────────────────────────────────────────
    p_dasc = sub.add_parser("dasc", help="DASC-core risk governance — classify, ledger, taint")
    p_dasc_sub = p_dasc.add_subparsers(dest="dasc_cmd", required=True)

    p_dasc_classify = p_dasc_sub.add_parser("classify", help="Classify the risk tier of an intent")
    p_dasc_classify.add_argument("intent", help="Intent action string to classify")
    p_dasc_classify.add_argument("--db", default="meshflow_dasc.db")

    p_dasc_ledger = p_dasc_sub.add_parser("ledger", help="Show recent audit ledger entries")
    p_dasc_ledger.add_argument("--limit", type=int, default=20)
    p_dasc_ledger.add_argument("--db", default="meshflow_dasc.db")

    p_dasc_verify = p_dasc_sub.add_parser("verify", help="Verify integrity of the audit ledger hash chain")
    p_dasc_verify.add_argument("--db", default="meshflow_dasc.db")

    p_dasc_taint = p_dasc_sub.add_parser("taint", help="Mark an agent as tainted")
    p_dasc_taint.add_argument("agent_id", help="Agent ID to taint")
    p_dasc_taint.add_argument("--db", default="meshflow_dasc.db")

    # ── dashboard ─────────────────────────────────────────────────────────────
    p_dash = sub.add_parser("dashboard", help="Terminal cost/metrics dashboard (no Streamlit needed)")
    p_dash.add_argument("--db", default="meshflow_runs.db", help="Ledger SQLite path")
    p_dash.add_argument("--limit", type=int, default=20, help="Max runs to show")
    p_dash.add_argument("--refresh", type=float, default=0.0, metavar="SECONDS",
                        help="Auto-refresh interval in seconds (0 = one-shot)")

    # ── sweep ─────────────────────────────────────────────────────────────────
    p_sweep = sub.add_parser("sweep", help="Run a workflow across a parameter grid")
    p_sweep.add_argument("yaml", help="Workflow YAML path")
    p_sweep.add_argument("--task", default="", help="Base task string")
    p_sweep.add_argument("--models", nargs="+", default=[],
                         metavar="MODEL", help="Model list to sweep (e.g. claude-sonnet-4-6 claude-haiku-4-5-20251001)")
    p_sweep.add_argument("--concurrency", type=int, default=4)
    p_sweep.add_argument("--db", default="meshflow_sweep.db")

    # ── eval-feedback ─────────────────────────────────────────────────────────
    p_ef = sub.add_parser("eval-feedback", help="Show aggregated human feedback statistics")
    p_ef.add_argument("--db", default="meshflow_feedback.db", help="Feedback SQLite path")
    p_ef.add_argument("--agent", default="", help="Filter to a specific agent name")
    p_ef.add_argument("--run-id", default="", dest="run_id", help="Show stats for a single run_id")
    p_ef.add_argument("--export-jsonl", default="", dest="export_jsonl", metavar="PATH",
                      help="Export (prompt, output, correction) JSONL to PATH")
    p_ef.add_argument("--corrections-only", action="store_true", dest="corrections_only",
                      help="Include only records with a human correction")

    # ── worker ────────────────────────────────────────────────────────────────
    p_worker = sub.add_parser("worker", help="Distributed task execution workers")
    p_worker_sub = p_worker.add_subparsers(dest="worker_cmd", required=True)

    p_w_start = p_worker_sub.add_parser("start", help="Start a distributed worker process")
    p_w_start.add_argument("--queue", default="sqlite://meshflow_tasks.db",
                           help="Queue URL: sqlite://path.db or redis://host:port/db")
    p_w_start.add_argument("--concurrency", type=int, default=4,
                           help="Max parallel agent executions")
    p_w_start.add_argument("--poll", type=float, default=1.0, dest="poll_interval",
                           help="Poll interval in seconds when queue is idle")

    p_w_status = p_worker_sub.add_parser("status", help="Show task queue status")
    p_w_status.add_argument("--queue", default="sqlite://meshflow_tasks.db")
    p_w_status.add_argument("--limit", type=int, default=20)

    # ── templates ─────────────────────────────────────────────────────────────
    p_tmpl = sub.add_parser("templates", help="Agent template registry")
    p_tmpl_sub = p_tmpl.add_subparsers(dest="templates_cmd", required=True)

    p_tmpl_sub.add_parser("list", help="List all templates in the local registry")

    p_tmpl_pub = p_tmpl_sub.add_parser("publish", help="Publish a template YAML to the local registry")
    p_tmpl_pub.add_argument("yaml", help="Path to template YAML file")

    p_tmpl_pull = p_tmpl_sub.add_parser("pull", help="Retrieve a template by name")
    p_tmpl_pull.add_argument("name", help="Template name")

    p_tmpl_search = p_tmpl_sub.add_parser("search", help="BM25 search over template descriptions")
    p_tmpl_search.add_argument("query", help="Search query")
    p_tmpl_search.add_argument("--top", type=int, default=5, help="Max results")

    p_tmpl_delete = p_tmpl_sub.add_parser("delete", help="Remove a template from the local registry")
    p_tmpl_delete.add_argument("name", help="Template name to remove")

    p_tmpl_share = p_tmpl_sub.add_parser("share", help="Share a template to a remote HTTP marketplace registry")
    p_tmpl_share.add_argument("name", help="Template name to share")
    p_tmpl_share.add_argument(
        "--url",
        default="",
        help="Remote marketplace base URL (e.g. http://marketplace.meshflow.io). "
             "Omit to share locally only.",
    )

    p_tmpl_curated = p_tmpl_sub.add_parser("load-curated",
                                           help="Load all 20 curated specialist templates into local registry")
    p_tmpl_curated.add_argument("--dir", default="",
                                help="Registry directory (default: ~/.meshflow/templates/)")

    # ── marketplace ───────────────────────────────────────────────────────────
    p_mkt = sub.add_parser("marketplace", help="Manage the MeshFlow template marketplace")
    p_mkt_sub = p_mkt.add_subparsers(dest="marketplace_cmd", required=True)

    p_mkt_serve = p_mkt_sub.add_parser("serve", help="Start a local HTTP marketplace server")
    p_mkt_serve.add_argument("--port", type=int, default=9900, help="Port to listen on (default: 9900)")
    p_mkt_serve.add_argument("--host", default="127.0.0.1", help="Bind host")
    p_mkt_serve.add_argument("--dir", default="",
                             help="Registry directory for the marketplace (default: ~/.meshflow/marketplace/)")

    p_mkt_push = p_mkt_sub.add_parser("push", help="Push a local template to a remote marketplace")
    p_mkt_push.add_argument("name", help="Template name")
    p_mkt_push.add_argument("--url", required=True, help="Remote marketplace base URL")

    p_mkt_pull = p_mkt_sub.add_parser("pull", help="Pull a template from a remote marketplace")
    p_mkt_pull.add_argument("name", help="Template name")
    p_mkt_pull.add_argument("--url", required=True, help="Remote marketplace base URL")

    # ── lint ──────────────────────────────────────────────────────────────────
    p_lint = sub.add_parser("lint", help="Static validate a workflow YAML before running")
    p_lint.add_argument("yaml", help="Workflow YAML path")
    p_lint.add_argument("--strict", action="store_true",
                        help="Treat warnings as errors (exit 1 on any warning)")
    p_lint.add_argument("--json", dest="as_json", action="store_true",
                        help="Output issues as JSON array")

    # ── diff ──────────────────────────────────────────────────────────────────
    p_diff = sub.add_parser("diff", help="Compare two workflow YAML topologies")
    p_diff.add_argument("yaml_a", help="First workflow YAML")
    p_diff.add_argument("yaml_b", help="Second workflow YAML")
    p_diff.add_argument("--json", dest="as_json", action="store_true")

    # red-team
    p_rt = sub.add_parser("red-team", help="Adversarial red-team testing of an agent pipeline")
    p_rt.add_argument("--config", default="", help="Agent YAML config to probe")
    p_rt.add_argument("--categories", nargs="*",
                      choices=["prompt_injection", "indirect_injection", "privilege_escalation",
                               "data_exfiltration", "tool_poisoning", "context_manipulation"],
                      help="Limit to specific attack categories")
    p_rt.add_argument("--output", default="", help="Write JSON report to this file")
    p_rt.add_argument("--json", dest="as_json", action="store_true", help="Print JSON report")
    p_rt.add_argument("--fail-on-risk", choices=["high", "medium", "low"], default="high",
                      help="Exit non-zero if risk level meets or exceeds this threshold")

    # blue-green
    p_bg = sub.add_parser("blue-green", help="Blue/green zero-downtime agent deployments")
    p_bg_sub = p_bg.add_subparsers(dest="deploy_cmd", required=True)
    p_bg_promote = p_bg_sub.add_parser("promote", help="Promote a deployment slot to active")
    p_bg_promote.add_argument("slot", choices=["blue", "green"])
    p_bg_promote.add_argument("--steps", nargs="*", type=float, default=[0.1, 0.5, 1.0],
                               help="Traffic fractions at each promotion step (default: 0.1 0.5 1.0)")
    p_bg_sub.add_parser("rollback", help="Immediately roll back to previous slot")
    p_bg_sub.add_parser("status",   help="Show current deployment status")
    p_bg_register = p_bg_sub.add_parser("register", help="Register a deployment in a slot")
    p_bg_register.add_argument("slot", choices=["blue", "green"])
    p_bg_register.add_argument("--name",    required=True, help="Deployment name")
    p_bg_register.add_argument("--version", default="1.0.0", help="Version string")
    p_bg_register.add_argument("--config",  default="",     help="Path to agent YAML config")

    p_zt = sub.add_parser("zt-audit", help="Score your deployment against the Zero Trust for AI Agents framework")
    p_zt.add_argument("--tier", choices=["foundation", "enterprise", "advanced"],
                      default="enterprise", help="Target tier to score against (default: enterprise)")
    p_zt.add_argument("--regulation", default="", help="Regulation preset: hipaa, sox, gdpr, pci, nerc")
    p_zt.add_argument("--json", dest="as_json", action="store_true", help="Output as JSON")
    p_zt.add_argument("--fail-on-gaps", action="store_true",
                      help="Exit non-zero if any controls are missing for the target tier")

    # ── migrate ───────────────────────────────────────────────────────────────
    p_migrate = sub.add_parser(
        "migrate",
        help="Detect and convert LangGraph, CrewAI, and AutoGen projects to MeshFlow",
    )
    p_migrate_sub = p_migrate.add_subparsers(dest="migrate_cmd", required=True)

    p_mig_detect = p_migrate_sub.add_parser(
        "detect", help="Scan a directory and print a detection report"
    )
    p_mig_detect.add_argument("--path", default=".", metavar="PATH",
                              help="Directory to scan (default: .)")

    p_mig_plan = p_migrate_sub.add_parser(
        "plan", help="Print a migration plan with effort estimate"
    )
    p_mig_plan.add_argument("--path", default=".", metavar="PATH",
                            help="Directory to scan (default: .)")

    p_mig_apply = p_migrate_sub.add_parser(
        "apply", help="Apply zero-rewrite transformations to detected files"
    )
    p_mig_apply.add_argument("--path", default=".", metavar="PATH",
                             help="Directory to scan (default: .)")
    p_mig_apply.add_argument("--dry-run", action="store_true", dest="dry_run",
                             help="Print changes without writing files")

    # ── test ──────────────────────────────────────────────────────────────────
    p_test = sub.add_parser(
        "test",
        help="Property-based agent testing — run declarative quality/safety properties",
    )
    p_test.add_argument("--agent",  required=True, metavar="YAML_OR_MODULE",
                        help="Path to agent YAML or Python module:attribute")
    p_test.add_argument(
        "--properties", nargs="*", default=[],
        choices=[
            "cost_bounded", "output_determinism", "no_pii_leak",
            "blocks_injection", "respects_token_limit", "latency_sla",
            "non_empty_output",
        ],
        metavar="PROPERTY",
        help="Properties to test (omit for --all)",
    )
    p_test.add_argument("--all", dest="all_properties", action="store_true",
                        help="Run all built-in properties")
    p_test.add_argument("--domain", default="general",
                        choices=["legal", "medical", "finance", "code", "general"],
                        help="Scenario domain for test inputs (default: general)")
    p_test.add_argument("--n-trials", type=int, default=10, dest="n_trials",
                        help="Number of trials per property (default: 10)")
    p_test.add_argument("--max-usd", type=float, default=0.10, dest="max_usd",
                        help="Cost cap for cost_bounded property (default: 0.10)")
    p_test.add_argument("--max-tokens", type=int, default=4096, dest="max_tokens",
                        help="Token cap for respects_token_limit property (default: 4096)")
    p_test.add_argument("--max-ms", type=float, default=30000.0, dest="max_ms",
                        help="Latency cap in ms for latency_sla property (default: 30000)")
    p_test.add_argument("--fail-on-any", action="store_true", dest="fail_on_any",
                        help="Exit 1 if any property fails")
    p_test.add_argument("--output", default="", metavar="FILE",
                        help="Save JSON report to this file")

    return parser


def main() -> None:
    args = build_parser().parse_args()

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
        "trace-server": _cmd_trace_server,
        "doctor": _cmd_doctor,
        "env": _cmd_env,
        "deploy": _cmd_deploy,
        "conformance": _cmd_conformance,
        "schema": _cmd_schema,
        "serve": _cmd_serve,
        "dev": _cmd_dev,
        "studio": _cmd_studio,
        "codegen": _cmd_codegen,
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
        "export-traces": _cmd_export_traces,
        "agent-serve":   _cmd_agent_serve,
        "budget":        _cmd_budget,
        "registry":      _cmd_registry,
        "schedule":      _cmd_schedule,
        "ratelimit":     _cmd_ratelimit,
        "security":      _cmd_security,
        "circuit":       _cmd_circuit,
        "memory":        _cmd_memory,
        "alerts":        _cmd_alerts,
        "locks":         _cmd_locks,
        "lineage":       _cmd_lineage,
        "identity":      _cmd_identity,
        "canary":        _cmd_canary,
        "flags":         _cmd_flags,
        "vault":         _cmd_vault,
        "tenant":        _cmd_tenant,
        "tracing":       _cmd_tracing,
        "policy":        _cmd_policy,
        "sla":           _cmd_sla,
        "snapshot":      _cmd_snapshot,
        "dasc":          _cmd_dasc,
        "dashboard":     _cmd_dashboard,
        "lint":          _cmd_lint,
        "diff":          _cmd_diff,
        "sweep":         _cmd_sweep,
        "eval-feedback": _cmd_eval_feedback,
        "worker":        _cmd_worker,
        "templates":     _cmd_templates,
        "marketplace":   _cmd_marketplace,
        "zt-audit":      _cmd_zt_audit,
        "red-team":      _cmd_red_team,
        "blue-green":    _cmd_deploy,
        "migrate":       _cmd_migrate,
        "test":          _cmd_test,
        "proxy":         _cmd_proxy,
    }
    dispatch[args.cmd](args)


def mcp_stdio_main() -> None:
    """Entry point for `meshflow-mcp` console script and `uvx meshflow`.

    Starts the MeshFlow stdio MCP server directly — no subcommand needed.
    Designed for ``claude_desktop_config.json`` and ``uvx`` usage::

        # claude_desktop_config.json
        {
          "mcpServers": {
            "meshflow": {
              "command": "meshflow-mcp",
              "env": {"ANTHROPIC_API_KEY": "sk-ant-..."}
            }
          }
        }

        # uvx (no install required)
        uvx meshflow mcp-stdio
    """
    import sys as _sys
    _sys.argv = ["meshflow-mcp", "mcp-stdio"]
    main()


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


def _cmd_export_traces(args: argparse.Namespace) -> None:
    from meshflow.export import FinetuneExporter, ExportFormat

    exporter = FinetuneExporter(
        ledger_path=args.db,
        format=ExportFormat(args.format),
        min_confidence=args.min_confidence,
        max_records=args.max_records,
        agent_names=[args.agent_name] if args.agent_name else [],
        deduplicate=args.deduplicate,
    )
    count = exporter.export(args.output)
    print(f"\n  Exported {count} training records → {args.output}")
    print(f"  Format: {args.format}")
    if args.stats and count > 0:
        s = exporter.stats()
        print(f"  Agents: {', '.join(s['agents'])}")
        print(f"  Avg confidence: {s['avg_confidence']:.3f}")
        print(f"  Total tokens: {s['total_tokens']}")
    print()


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
        print("[meshflow] compliance guard active")
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

    # ── --diff: compare two runs ──────────────────────────────────────────────
    if getattr(args, "diff", ""):
        diff = await ledger.diff(args.run_id, args.diff)
        print(f"\n  Diff  {args.run_id[:24]}  ↔  {args.diff[:24]}")
        print(f"  {'─' * 58}")
        print(f"  Only in A : {', '.join(diff.only_in_a) or '—'}")
        print(f"  Only in B : {', '.join(diff.only_in_b) or '—'}")
        print(f"  Common    : {len(diff.common)} node(s)")
        print(f"  Changed   : {len(diff.changed)} node(s)")
        for c in diff.changed:
            print(f"    • {c['node_id']}: verdict {c['verdict_a']} → {c['verdict_b']}")
        print(f"  Cost Δ    : {diff.cost_delta_usd:+.6f} USD")
        print(f"  Token Δ   : {diff.token_delta:+d}")
        return

    # ── --fork-at: copy steps 0..N-1 to a new run ────────────────────────────
    if getattr(args, "fork_at", -1) >= 0:
        new_id = await ledger.fork(args.run_id, args.fork_at)
        print(f"[fork] new run_id={new_id!r} (copied {args.fork_at} step(s) from {args.run_id!r})")
        return

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
        print(f"  Paused at node '{paused_node}' — awaiting human approval.")
        print(f"     Run: meshflow approve {args.run_id} {paused_node}")
        print()

    # ── State injection: parse --inject KEY=VALUE pairs ──────────────────────
    context_patch: dict[str, str] = {}
    for pair in getattr(args, "inject", []):
        if "=" in pair:
            k, v = pair.split("=", 1)
            context_patch[k.strip()] = v.strip()
    if context_patch:
        print(f"\n  [state-inject] Injecting context: {context_patch}")

    # ── Branch & Compare mode ─────────────────────────────────────────────────
    if getattr(args, "branch_compare", False):
        from meshflow.core.branch_compare import BranchCompare, ForkConfig

        raw_forks = getattr(args, "forks", []) or []
        fork_cfgs: list[ForkConfig] = []
        for raw in raw_forks:
            label, _, opts_str = raw.partition(":")
            opts: dict[str, str] = {}
            for opt in opts_str.split(","):
                if "=" in opt:
                    ok, ov = opt.split("=", 1)
                    opts[ok.strip()] = ov.strip()
            fork_cfgs.append(ForkConfig(
                label=label or f"fork-{len(fork_cfgs)+1}",
                model_override=opts.get("model", ""),
                prompt_override=opts.get("prompt", "").replace("-", " "),
                context_patch=dict(context_patch),
                workflow_yaml=getattr(args, "rewind_yaml", ""),
            ))

        if not fork_cfgs:
            # Default: one fork with each model tier
            from meshflow.agents.model_router import _DEFAULT_TIERS
            for label, model in _DEFAULT_TIERS.items():
                fork_cfgs.append(ForkConfig(label=label, model_override=model))

        step = getattr(args, "compare_step", 1)
        print(f"\n  [branch-compare] Forking run at step {step} with {len(fork_cfgs)} variants...")

        bc = BranchCompare(ledger_db=args.db)
        try:
            result = await bc.compare(args.run_id, step, forks=fork_cfgs)
            print(f"\n  ┌{'─' * 70}┐")
            print(f"  │  Branch & Compare — {args.run_id[:48]:<48}  │")
            print(f"  │  Winner: {result.winner:<60}  │")
            print(f"  │  Fork point: step {result.fork_point:<51}  │")
            print(f"  ├{'─' * 70}┤")
            for fork in result.forks:
                status = "✓" if fork.completed else "✗"
                print(f"  │  {status} {fork.label:<20} conf={fork.confidence:.2f}  "
                      f"cost=${fork.total_cost_usd:.5f}  {fork.model_used:<25}  │")
            print(f"  └{'─' * 70}┘")
            if result.diff_summary and result.diff_summary != "(outputs identical)":
                print(f"\n  Diff (top-2 forks):\n{result.diff_summary[:600]}")
        except Exception as exc:
            print(f"  [branch-compare] ERROR: {exc}")
        return

    # ── Time-travel rewind ────────────────────────────────────────────────────
    if getattr(args, "rewind", 0):
        from meshflow.core.time_travel import RewindEngine

        engine = RewindEngine(args.db)
        print(f"\n  [rewind] Rewinding to step {args.rewind}...")
        if args.rewind_model:
            print(f"           model    → {args.rewind_model}")
        if args.rewind_prompt:
            print(f"           prompt   → {args.rewind_prompt[:60]}")
        if context_patch:
            print(f"           inject  → {context_patch}")
        print()
        if not args.rewind_yaml:
            print("  [rewind] ERROR: --yaml <path> is required for rewind.")
            print("           Example: meshflow replay <run_id> --rewind 3 --yaml mesh.yaml")
            return
        try:
            result = await engine.rewind(
                run_id=args.run_id,
                to_step=args.rewind,
                workflow_yaml=args.rewind_yaml,
                model_override=args.rewind_model,
                prompt_override=args.rewind_prompt,
                context_patch=context_patch or None,
            )
            status = "COMPLETED" if result.completed else "PARTIAL"
            print(f"  [rewind] {status}  run_id={result.rewind_run_id}")
            print(f"           steps={result.steps_replayed}  "
                  f"cost=${result.total_cost_usd:.5f}  "
                  f"tokens={result.total_tokens}")
            print(f"\n  Output:\n  {result.output[:400]}")
        except Exception as exc:
            print(f"  [rewind] ERROR: {exc}")

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
    if getattr(args, "browser", False):
        _cmd_trace_browser(args)
        return
    asyncio.run(_async_trace(args))


def _cmd_trace_browser(args: argparse.Namespace) -> None:
    """Open the run trace in the visual browser UI."""
    from meshflow.studio.trace_server import TraceServer
    import time

    port = getattr(args, "port", 7788)
    server = TraceServer(db=args.db, port=port)
    server.start(daemon=True)
    print(f"  Trace server started → {server.url}?run_id={args.run_id}")
    server.open_browser(args.run_id)
    print("  Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
        print("\n  Stopped.")


def _cmd_trace_server(args: argparse.Namespace) -> None:
    """Start the visual trace server UI."""
    from meshflow.studio.trace_server import TraceServer
    import time

    port = getattr(args, "port", 7788)
    server = TraceServer(db=args.db, port=port)
    server.start(daemon=True)
    print(f"  MeshFlow Trace Server → {server.url}")
    if not getattr(args, "no_browser", False):
        server.open_browser()
    print("  Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
        print("\n  Stopped.")


# ── doctor ────────────────────────────────────────────────────────────────────


def _cmd_doctor(args: argparse.Namespace) -> None:
    from meshflow.deploy.doctor import Doctor
    doc = Doctor(
        port=getattr(args, "port", 8000),
        db_path=getattr(args, "db", "meshflow_runs.db"),
    )
    report = doc.run()
    if getattr(args, "as_json", False):
        import json
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.summary())
    sys.exit(0 if report.ok else 1)


# ── env ───────────────────────────────────────────────────────────────────────


def _cmd_env(args: argparse.Namespace) -> None:
    from meshflow.deploy.env_generator import EnvGenerator

    validate_path = getattr(args, "validate", "")
    if validate_path:
        gen = EnvGenerator()
        issues = gen.validate(validate_path)
        if not issues:
            print(f"  ✓ {validate_path} is valid.")
        else:
            for issue in issues:
                print(f"  {issue}")
        sys.exit(0 if not any(i.severity == "error" for i in issues) else 1)

    gen = EnvGenerator()
    output = getattr(args, "output", "")
    if output:
        try:
            gen.write(output, overwrite=getattr(args, "overwrite", False))
            print(f"  Written → {output}")
        except FileExistsError as e:
            print(f"  Error: {e}")
            sys.exit(1)
    else:
        print(gen.render())


# ── deploy ────────────────────────────────────────────────────────────────────


def _cmd_deploy(args: argparse.Namespace) -> None:
    import json as _json
    from meshflow.deploy.deployer import DockerDeployer

    tag = getattr(args, "tag", "meshflow:latest")
    dep = DockerDeployer(tag=tag)

    if getattr(args, "status", False):
        name = "meshflow"
        st = dep.status(name)
        print(_json.dumps(st, indent=2))
        return

    if getattr(args, "logs", False):
        print(dep.logs())
        return

    if getattr(args, "down", False):
        if getattr(args, "compose", False):
            result = dep.compose_down()
        else:
            result = dep.stop()
        print(f"  {'ok' if result.ok else 'FAILED'}: {result.command}")
        if result.error:
            print(f"  {result.error}")
        sys.exit(0 if result.ok else 1)

    if getattr(args, "compose", False):
        profiles = [args.profile] if getattr(args, "profile", "") else None
        result = dep.compose_up(profiles=profiles, build=True)
        print(f"  {'ok' if result.ok else 'FAILED'}: docker compose up")
        if result.stdout:
            print(result.stdout[-500:])
        if result.error:
            print(result.error[-500:])
        sys.exit(0 if result.ok else 1)

    # Standard: build [+ run]
    print(f"  Building {tag}…")
    build_result = dep.build(no_cache=getattr(args, "no_cache", False))
    if not build_result.ok:
        print(f"  Build FAILED:\n{build_result.stderr[-800:]}")
        sys.exit(1)
    print(f"  Built in {build_result.duration_ms:.0f}ms")

    if getattr(args, "build_only", False):
        print(f"  Image ready: {tag}")
        return

    port = getattr(args, "port", 8000)
    env_file = getattr(args, "env_file", ".env")
    print(f"  Starting container on port {port}…")
    run_result = dep.run(port=port, env_file=env_file)
    if run_result.ok:
        print(f"  Container started: {run_result.container_id}")
        print(f"  Server → http://localhost:{port}/health/live")
    else:
        print(f"  Run FAILED:\n{run_result.stderr}")
        sys.exit(1)


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

    # ── New: structured trace formats ─────────────────────────────────────────
    fmt = getattr(args, "trace_format", "terminal")
    if fmt in ("langsmith",) or (args.as_json and fmt == "terminal"):
        from meshflow.observability.trace_viewer import TraceViewer
        viewer = TraceViewer(args.db)
        if fmt == "langsmith":
            payload = await viewer.export_langsmith_json(args.run_id)
        else:
            payload = json.dumps(
                {"run_id": args.run_id, "summary": summary, "steps": steps},
                indent=2,
            )
        if args.export:
            with open(args.export, "w") as fh:
                fh.write(payload)
            print(f"  Exported trace ({fmt}) → {args.export}")
        else:
            print(payload)
        return

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

    # ── OIDC / SSO ────────────────────────────────────────────────────────────
    # CLI flags take precedence over env vars; env vars are the fallback.
    oidc_issuer = getattr(args, "oidc_issuer", "") or os.environ.get("MESHFLOW_OIDC_ISSUER", "")
    if oidc_issuer:
        import os as _os
        # Propagate CLI flags back to env so OIDCConfig.from_env() picks them up
        _os.environ["MESHFLOW_OIDC_ISSUER"] = oidc_issuer
        if getattr(args, "oidc_audience", ""):
            _os.environ["MESHFLOW_OIDC_AUDIENCE"] = args.oidc_audience
        if getattr(args, "oidc_role_claim", ""):
            _os.environ["MESHFLOW_OIDC_ROLE_CLAIM"] = args.oidc_role_claim
        try:
            from meshflow.security.oidc import OIDCConfig, setup_oidc_middleware
            cfg = OIDCConfig.from_env()
            setup_oidc_middleware(cfg)
            print(f"  OIDC auth: enabled (issuer={cfg.issuer})")
        except Exception as exc:
            print(f"  Warning: OIDC setup failed: {exc}")

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


def _cmd_studio(args: argparse.Namespace) -> None:
    from meshflow.cli.studio import start_studio_server
    start_studio_server(host=args.host, port=args.port)


def _cmd_codegen(args: argparse.Namespace) -> None:
    from meshflow.core.codegen import SDKCodeGenerator
    import os as _os
    if not _os.path.exists(args.yaml):
        print(f"  [codegen] YAML file not found: {args.yaml}")
        sys.exit(1)

    gen = SDKCodeGenerator(args.yaml)
    if args.language == "dotnet":
        print(gen.generate_dotnet())
    elif args.language == "java":
        print(gen.generate_java())
    elif args.language == "go":
        print(gen.generate_go())


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
        import importlib.util
        import os

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

        # Check cost/token regression delta
        max_cost_delta = getattr(args, "max_cost_delta", -1.0)
        if max_cost_delta > 0.0 and old.total_tokens > 0:
            token_change = (new.total_tokens - old.total_tokens) / old.total_tokens
            print(f"  [eval] Token budget change: {token_change:+.1%} (threshold: {max_cost_delta:+.1%})")
            if token_change > max_cost_delta:
                print(f"  [eval] FAILED: Token increase {token_change:+.1%} exceeds maximum permitted threshold {max_cost_delta:+.1%}")
                sys.exit(1)

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
        print("\n  Available run IDs (use --run-id <id>):\n")
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
    elif cmd == "verify-chain":
        asyncio.run(_async_audit_verify_chain(args))


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


async def _async_audit_verify_chain(args: argparse.Namespace) -> None:
    """Verify the tamper-evident SHA-256 hash chain for a run.

    Exit code 0 = chain valid.  Exit code 1 = chain broken (records tampered).
    """
    from meshflow.core.ledger import ReplayLedger

    db = getattr(args, "db", "meshflow_runs.db")
    run_id = args.run_id.strip()
    as_json = getattr(args, "as_json", False)

    if not run_id:
        print("  --run-id is required for verify-chain")
        sys.exit(2)

    import os as _os
    if db != ":memory:" and not _os.path.exists(db):
        print(f"  No ledger found at '{db}'.")
        sys.exit(1)

    ledger = ReplayLedger(db)
    result = await ledger.verify_chain(run_id)

    if as_json:
        print(json.dumps(result, indent=2))
    else:
        valid = result.get("valid", False)
        steps = result.get("steps_verified", 0)
        errors = result.get("errors", [])
        if valid:
            print(f"  CHAIN VALID — {steps} step(s) verified for run '{run_id}'.")
        else:
            print(f"  CHAIN INVALID — {len(errors)} error(s) in run '{run_id}':")
            for e in errors:
                print(f"    {e}")

    if not result.get("valid", False):
        sys.exit(1)


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
        print("\n  Webhook registered!")
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

    elif args.webhooks_cmd == "queue":
        from meshflow.observability.webhook_queue import WebhookRetryQueue
        q = WebhookRetryQueue(args.db)
        deliveries = q.pending(limit=args.limit)
        counts = q.counts()
        if getattr(args, "json_output", False):
            print(json.dumps({"counts": counts, "deliveries": [d.to_dict() for d in deliveries]}, indent=2))
            return
        print(f"\n  Webhook delivery queue  (pending: {counts.get('pending', 0)}, "
              f"success: {counts.get('success', 0)}, dead: {counts.get('dead', 0)})\n")
        if not deliveries:
            print("  Queue is empty.")
            return
        print(f"  {'DELIVERY_ID':<36}  {'WEBHOOK_ID':<12}  {'ATTEMPT':<7}  {'NEXT_RETRY_AT':<20}  EVENT")
        print("  " + "─" * 100)
        for d in deliveries:
            import datetime
            ts = datetime.datetime.fromtimestamp(d.next_retry_at).strftime("%Y-%m-%d %H:%M:%S")
            print(f"  {d.delivery_id:<36}  {d.webhook_id[:12]:<12}  {d.attempt:<7}  {ts:<20}  {d.event_type}")
        print()

    elif args.webhooks_cmd == "dead":
        from meshflow.observability.webhook_queue import WebhookRetryQueue
        q = WebhookRetryQueue(args.db)
        deliveries = q.dead_letters(limit=args.limit)
        if getattr(args, "json_output", False):
            print(json.dumps([d.to_dict() for d in deliveries], indent=2))
            return
        if not deliveries:
            print("  No dead-letter deliveries.")
            return
        print(f"\n  Dead-letter deliveries ({len(deliveries)}):\n")
        print(f"  {'DELIVERY_ID':<36}  {'ATTEMPTS':<8}  {'LAST_ERROR'}")
        print("  " + "─" * 90)
        for d in deliveries:
            err = (d.last_error or "")[:50]
            print(f"  {d.delivery_id:<36}  {d.attempt:<8}  {err}")
        print()

    elif args.webhooks_cmd == "replay":
        from meshflow.observability.webhook_queue import WebhookRetryQueue
        q = WebhookRetryQueue(args.db)
        ok = q.replay(args.delivery_id)
        if ok:
            print(f"  Delivery {args.delivery_id} re-queued for immediate retry.")
        else:
            print(f"  No delivery found with ID {args.delivery_id}.")
            sys.exit(1)


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
        print("\n  API key created!")
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
            print("\n  Top costly nodes:")
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
        print("\n  Top costly nodes:")
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
        print("\n  Task enqueued!")
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

        print("\n  MeshFlow queue worker started")
        print(f"  Queue:       {db}")
        print(f"  Concurrency: {args.concurrency}")
        print(f"  Poll:        {args.poll_interval}s")
        print("  Press Ctrl-C to stop\n")
        await worker.run(stop_event=stop)
        await q.close()
        print("  Worker stopped.")


# ── agent-serve ───────────────────────────────────────────────────────────────


def _cmd_agent_serve(args: argparse.Namespace) -> None:
    """Serve a single named agent over the A2A HTTP protocol."""
    import signal
    import os
    import threading as _threading

    os.environ.setdefault("MESHFLOW_MOCK", "0")
    from meshflow.agents.builder import Agent
    from meshflow.a2a.server import A2AServer

    kwargs: dict = {"name": args.agent_name, "role": args.role}
    if getattr(args, "model", ""):
        kwargs["model"] = args.model

    agent = Agent(**kwargs)
    description = getattr(args, "description", "") or f"MeshFlow agent: {args.agent_name}"
    srv = A2AServer(agent, host=args.host, port=args.port, description=description)

    print("\n  MeshFlow agent-serve")
    print(f"  Agent:  {args.agent_name}  ({args.role})")
    print(f"  URL:    http://{args.host}:{args.port}")
    print("  A2A endpoints: /run  /tasks  /tasks/{id}  /tasks/{id}/stream")
    print("  Probes:        /health  /ready  /metrics")
    print("  Press Ctrl-C to stop\n")

    srv.start()
    stop = _threading.Event()

    def _on_signal(*_: object) -> None:
        print("\n  agent-serve: shutdown signal — stopping…")
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _on_signal)
        except (OSError, ValueError):
            pass

    stop.wait()
    srv.stop()
    print("  agent-serve: stopped.")


# ── budget ────────────────────────────────────────────────────────────────────


def _cmd_budget(args: argparse.Namespace) -> None:
    from meshflow.budget.store import BudgetAccount, BudgetStore

    db = getattr(args, "db", "meshflow_budgets.db")
    store = BudgetStore(db)

    if args.budget_cmd == "list":
        accounts = store.list(agent_name=getattr(args, "agent_name", ""))
        if not accounts:
            print("  No budget accounts found.")
            return
        print(f"\n  {'ACCOUNT ID':<20} {'AGENT':<20} {'PERIOD':<10} {'LIMIT USD':>10} {'LIMIT TOK':>12}")
        print("  " + "-" * 78)
        for a in accounts:
            print(f"  {a.account_id:<20} {a.agent_name:<20} {a.period:<10} "
                  f"${a.limit_usd:>9.2f} {a.limit_tokens:>12,}")

    elif args.budget_cmd == "status":
        s = store.summary(args.account_id)
        if "error" in s:
            print(f"  Error: {s['error']}")
            sys.exit(1)
        pct = s["percent_used"] * 100
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        print(f"\n  Budget: {s['name']}  ({s['account_id']})")
        print(f"  Agent:  {s['agent_name']}   Period: {s['period']} [{s['period_key']}]")
        if s["limit_usd"]:
            print(f"  USD:    ${s['spent_usd']:.4f} / ${s['limit_usd']:.4f}  "
                  f"(remaining: ${s['remaining_usd']:.4f})")
        else:
            print(f"  USD:    ${s['spent_usd']:.4f}  (no USD cap)")
        if s["limit_tokens"]:
            print(f"  Tokens: {s['spent_tokens']:,} / {s['limit_tokens']:,}")
        else:
            print(f"  Tokens: {s['spent_tokens']:,}  (no token cap)")
        print(f"  Calls:  {s['call_count']}")
        print(f"  [{bar}] {pct:.1f}%")
        status = "ALLOWED" if s["allowed"] else "BLOCKED"
        print(f"  Status: {status}\n")

    elif args.budget_cmd == "set":
        import time as _time
        existing = store.get(args.account_id)
        account = BudgetAccount(
            account_id=args.account_id,
            name=getattr(args, "name", "") or args.account_id,
            agent_name=args.agent_name,
            period=args.period,
            limit_usd=args.limit_usd,
            limit_tokens=args.limit_tokens,
            created_at=existing.created_at if existing else _time.time(),
        )
        store.create(account)
        print(f"  Budget account '{args.account_id}' saved.")
        print(f"  Agent: {args.agent_name}  Period: {args.period}  "
              f"Limit: ${args.limit_usd:.2f} / {args.limit_tokens:,} tokens")

    elif args.budget_cmd == "reset":
        store.reset_spend(args.account_id)
        print(f"  Spend reset for '{args.account_id}'.")

    elif args.budget_cmd == "delete":
        if store.delete(args.account_id):
            print(f"  Deleted '{args.account_id}'.")
        else:
            print(f"  Account '{args.account_id}' not found.")
            sys.exit(1)


# ── registry ──────────────────────────────────────────────────────────────────


def _cmd_registry(args: argparse.Namespace) -> None:
    import json as _json
    from meshflow.registry.core import AgentManifest, AgentRegistry

    db = getattr(args, "db", "meshflow_registry.db")
    reg = AgentRegistry(db)

    if args.registry_cmd == "list":
        agents = reg.list(
            role=getattr(args, "role", ""),
            owner=getattr(args, "owner", ""),
            tag=getattr(args, "tag", ""),
        )
        if not agents:
            print("  No agents registered.")
            return
        print(f"\n  {'NAME':<24} {'ROLE':<12} {'VERSION':<10} {'OWNER':<16} TAGS")
        print("  " + "-" * 80)
        for m in agents:
            tags = ", ".join(m.tags[:3]) + ("…" if len(m.tags) > 3 else "")
            print(f"  {m.name:<24} {m.role:<12} {m.version:<10} {m.owner:<16} {tags}")

    elif args.registry_cmd == "search":
        agents = reg.search(args.query, role=getattr(args, "role", ""))
        if not agents:
            print(f"  No agents matched '{args.query}'.")
            return
        print(f"\n  Results for '{args.query}':")
        for m in agents:
            print(f"  [{m.role}] {m.name}  v{m.version}")
            if m.description:
                print(f"    {m.description[:72]}")

    elif args.registry_cmd == "get":
        m = reg.get(args.name)
        if m is None:
            print(f"  Agent '{args.name}' not found.")
            sys.exit(1)
        print(_json.dumps(m.to_dict(), indent=2))

    elif args.registry_cmd == "publish":
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
        caps = [c.strip() for c in args.capabilities.split(",") if c.strip()]
        manifest = AgentManifest(
            name=args.name,
            role=args.role,
            description=args.description,
            tags=tags,
            capabilities=caps,
            version=args.version,
            owner=getattr(args, "owner", ""),
            url=getattr(args, "url", ""),
        )
        reg.publish(manifest)
        print(f"  Published '{args.name}'  v{args.version}  ({args.role})")

    elif args.registry_cmd == "unpublish":
        if reg.unpublish(args.name):
            print(f"  Unpublished '{args.name}'.")
        else:
            print(f"  Agent '{args.name}' not found.")
            sys.exit(1)


# ── schedule ──────────────────────────────────────────────────────────────────

def _cmd_schedule(args: argparse.Namespace) -> None:
    import json as _json
    from meshflow.scheduler.store import ScheduleStore, ScheduledTask
    from meshflow.scheduler.cron import CronExpression
    import time as _time

    store = ScheduleStore(args.db)

    if args.schedule_cmd == "list":
        tasks = store.list(agent_name=getattr(args, "agent_name", ""))
        if not tasks:
            print("  No schedules found.")
            return
        print(f"\n  {'ID':<14} {'NAME':<20} {'AGENT':<20} {'CRON':<18} {'ENABLED':<8} FIRES")
        print("  " + "-" * 90)
        for t in tasks:
            enabled = "yes" if t.enabled else "no"
            next_s  = _time.strftime("%Y-%m-%d %H:%M", _time.localtime(t.next_fire_at)) \
                      if t.next_fire_at else "—"
            print(f"  {t.schedule_id:<14} {t.name[:19]:<20} {t.agent_name[:19]:<20} "
                  f"{t.cron:<18} {enabled:<8} {next_s}")

    elif args.schedule_cmd == "add":
        # validate cron before saving
        try:
            expr = CronExpression(args.cron)
        except ValueError as exc:
            print(f"  Invalid cron expression: {exc}")
            sys.exit(1)
        task = ScheduledTask(
            name=args.name,
            agent_name=args.agent_name,
            cron=args.cron,
            task_payload=args.task_payload,
        )
        task.next_fire_at = expr.next_after(_time.time())
        store.add(task)
        nxt = _time.strftime("%Y-%m-%d %H:%M UTC", _time.gmtime(task.next_fire_at))
        print(f"  Schedule created: {task.schedule_id}")
        print(f"    agent   : {task.agent_name}")
        print(f"    cron    : {task.cron}")
        print(f"    next    : {nxt}")

    elif args.schedule_cmd == "get":
        task = store.get(args.schedule_id)
        if task is None:
            print(f"  Schedule '{args.schedule_id}' not found.")
            sys.exit(1)
        d = task.to_dict()
        d["metadata"] = task.metadata
        print(_json.dumps(d, indent=2, default=str))

    elif args.schedule_cmd == "remove":
        if store.delete(args.schedule_id):
            print(f"  Schedule '{args.schedule_id}' removed.")
        else:
            print(f"  Schedule '{args.schedule_id}' not found.")
            sys.exit(1)

    elif args.schedule_cmd == "enable":
        if store.enable(args.schedule_id, True):
            print(f"  Schedule '{args.schedule_id}' enabled.")
        else:
            print(f"  Schedule '{args.schedule_id}' not found.")
            sys.exit(1)

    elif args.schedule_cmd == "disable":
        if store.enable(args.schedule_id, False):
            print(f"  Schedule '{args.schedule_id}' disabled.")
        else:
            print(f"  Schedule '{args.schedule_id}' not found.")
            sys.exit(1)

    elif args.schedule_cmd == "runs":
        runs = store.runs(args.schedule_id, limit=args.limit)
        if not runs:
            print("  No runs found.")
            return
        print(f"\n  {'RUN ID':<14} {'FIRED AT':<22} {'STATUS':<12} TASK ID")
        print("  " + "-" * 70)
        for r in runs:
            fired = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(r.fired_at))
            print(f"  {r.run_id:<14} {fired:<22} {r.status:<12} {r.task_id or '—'}")


# ── ratelimit ─────────────────────────────────────────────────────────────────

def _cmd_ratelimit(args: argparse.Namespace) -> None:
    from meshflow.ratelimit.store_db import RateLimitPolicyDB
    from meshflow.ratelimit.window import RateLimitPolicy

    db = RateLimitPolicyDB(args.db)

    if args.ratelimit_cmd == "list":
        policies = db.list()
        if not policies:
            print("  No rate limit policies configured.")
            return
        print(f"\n  {'KEY':<28} {'MAX REQ':<10} {'MAX TOK':<12} {'WINDOW(s)':<12} WARN")
        print("  " + "-" * 68)
        for p in policies:
            req  = str(p["max_requests"]) if p["max_requests"] else "∞"
            tok  = str(p["max_tokens"])   if p["max_tokens"]   else "∞"
            warn = f"{int(p['warn_at'] * 100)}%"
            print(f"  {p['key']:<28} {req:<10} {tok:<12} {p['window_s']:<12} {warn}")

    elif args.ratelimit_cmd == "set":
        policy = RateLimitPolicy(
            max_requests=args.max_requests,
            max_tokens=args.max_tokens,
            window_s=args.window_s,
            warn_at=args.warn_at,
        )
        db.save(args.key, policy)
        req  = str(policy.max_requests) if policy.max_requests else "∞"
        tok  = str(policy.max_tokens)   if policy.max_tokens   else "∞"
        print(f"  Rate limit saved for '{args.key}'")
        print(f"    requests : {req} / {policy.window_s}s window")
        print(f"    tokens   : {tok} / {policy.window_s}s window")
        print(f"    warn at  : {int(policy.warn_at * 100)}%")

    elif args.ratelimit_cmd == "remove":
        if db.delete(args.key):
            print(f"  Rate limit for '{args.key}' removed.")
        else:
            print(f"  No policy found for '{args.key}'.")
            sys.exit(1)

    elif args.ratelimit_cmd == "status":
        policy = db.load(args.key)
        if policy is None:
            # fall back to wildcard
            policy = db.load("*")
        if policy is None:
            print(f"  No rate limit policy found for '{args.key}'.")
            return
        req  = str(policy.max_requests) if policy.max_requests else "∞"
        tok  = str(policy.max_tokens)   if policy.max_tokens   else "∞"
        print(f"\n  Policy for '{args.key}':")
        print(f"    max requests : {req} / {policy.window_s}s")
        print(f"    max tokens   : {tok} / {policy.window_s}s")
        print(f"    warn at      : {int(policy.warn_at * 100)}%")


# ── security ──────────────────────────────────────────────────────────────────

def _cmd_security(args: argparse.Namespace) -> None:
    from meshflow.security.injection import PromptInjectionDetector

    if args.security_cmd == "scan":
        import json as _json
        import sys as _sys

        if args.text is not None:
            text = args.text
        else:
            text = _sys.stdin.read()

        detector = PromptInjectionDetector(
            threshold=args.threshold,
            block_threshold=args.block_threshold,
            enabled_categories=args.categories,
        )
        result = detector.scan(text)

        if args.json_output:
            print(_json.dumps({
                "detected": result.detected,
                "blocked": result.blocked,
                "score": round(result.score, 4),
                "categories": result.categories,
                "matches": [
                    {
                        "category": m.category,
                        "pattern": m.pattern_name,
                        "text": m.matched_text,
                        "position": m.position,
                        "confidence": m.confidence,
                    }
                    for m in result.matches
                ],
            }, indent=2))
        else:
            status = "BLOCKED" if result.blocked else ("WARN" if result.detected else "CLEAN")
            colour = {"BLOCKED": "\033[31m", "WARN": "\033[33m", "CLEAN": "\033[32m"}[status]
            reset = "\033[0m"
            print(f"\n  Result  : {colour}{status}{reset}")
            print(f"  Score   : {result.score:.3f}  (threshold={args.threshold}, block={args.block_threshold})")
            if result.categories:
                print(f"  Categories: {', '.join(result.categories)}")
            if result.matches:
                print(f"\n  {'CATEGORY':<22} {'PATTERN':<28} {'CONF':>5}  MATCHED TEXT")
                print("  " + "-" * 82)
                for m in result.matches[:20]:
                    snippet = m.matched_text[:40].replace("\n", "↵")
                    print(f"  {m.category:<22} {m.pattern_name:<28} {m.confidence:>4.0%}  {snippet}")
                if len(result.matches) > 20:
                    print(f"  … and {len(result.matches) - 20} more matches")
            print()
            if result.blocked:
                _sys.exit(2)  # exit 2 = blocked (scriptable)
            elif result.detected:
                _sys.exit(1)  # exit 1 = suspicious

    elif args.security_cmd == "secrets":
        import json as _json
        import sys as _sys

        if args.text is not None:
            text = args.text
        else:
            text = _sys.stdin.read()

        from meshflow.security.secrets import SecretScanner

        scanner = SecretScanner(
            enabled_categories=args.categories,
            min_confidence=args.min_confidence,
            redact=args.redact,
        )
        result = scanner.scan(text)

        if args.json_output:
            print(_json.dumps({
                "found":      result.found,
                "categories": result.categories,
                "matches": [
                    {
                        "category":   m.category,
                        "pattern":    m.pattern_name,
                        "preview":    m.matched_text,
                        "confidence": m.confidence,
                        "position":   m.position,
                    }
                    for m in result.matches
                ],
                "redacted_text": result.redacted_text,
            }, indent=2))
        else:
            status = "CLEAN" if not result.found else "SECRETS FOUND"
            colour = "\033[32m" if not result.found else "\033[31m"
            reset  = "\033[0m"
            print(f"\n  Result  : {colour}{status}{reset}")
            if result.categories:
                print(f"  Categories: {', '.join(result.categories)}")
            if result.matches:
                print(f"\n  {'CATEGORY':<18} {'PATTERN':<28} {'CONF':>5}  PREVIEW")
                print("  " + "-" * 72)
                for m in result.matches[:20]:
                    print(f"  {m.category:<18} {m.pattern_name:<28} {m.confidence:>4.0%}  {m.matched_text}")
                if len(result.matches) > 20:
                    print(f"  … and {len(result.matches) - 20} more matches")
            if args.redact and result.redacted_text is not None:
                print(f"\n  Redacted output:\n  {result.redacted_text[:500]}")
            print()
            if result.found and not args.redact:
                _sys.exit(1)


# ── circuit ───────────────────────────────────────────────────────────────────

def _cmd_circuit(args: argparse.Namespace) -> None:
    import time as _time
    from meshflow.resilience.store import CircuitBreakerStore, CircuitBreakerRecord
    from meshflow.resilience.breaker import CircuitBreakerState

    store = CircuitBreakerStore(args.db)

    _STATE_COLOUR = {
        "closed":    "\033[32m",   # green
        "open":      "\033[31m",   # red
        "half_open": "\033[33m",   # yellow
    }
    _RESET = "\033[0m"

    def _coloured_state(state: str) -> str:
        return f"{_STATE_COLOUR.get(state, '')}{state.upper()}{_RESET}"

    if args.circuit_cmd == "list":
        records = store.list()
        if not records:
            print("  No circuit breakers recorded.")
            return
        print(f"\n  {'NAME':<28} {'STATE':<12} {'CALLS':>7} {'FAIL':>6} {'REJ':>6}")
        print("  " + "-" * 64)
        for r in records:
            print(
                f"  {r.name:<28} {_coloured_state(r.state.value):<12} "
                f"{r.total_calls:>7} {r.total_failures:>6} {r.total_rejected:>6}"
            )

    elif args.circuit_cmd == "status":
        r = store.load(args.name)
        if r is None:
            print(f"  No record for circuit '{args.name}'.")
            return
        print(f"\n  Circuit  : {r.name}")
        print(f"  State    : {_coloured_state(r.state.value)}")
        print(f"  Calls    : {r.total_calls}")
        print(f"  Failures : {r.total_failures}")
        print(f"  Successes: {r.total_successes}")
        print(f"  Rejected : {r.total_rejected}")
        if r.opened_at:
            import datetime
            opened = datetime.datetime.fromtimestamp(r.opened_at).isoformat()
            print(f"  Opened   : {opened}")

    elif args.circuit_cmd == "reset":
        r = store.load(args.name)
        if r is None:
            print(f"  No record for circuit '{args.name}'.")
            return
        r.state      = CircuitBreakerState.CLOSED
        r.opened_at  = None
        r.updated_at = _time.time()
        store.save(r)
        print(f"  Circuit '{args.name}' forced to CLOSED.")

    elif args.circuit_cmd == "trip":
        r = store.load(args.name)
        now = _time.time()
        if r is None:
            r = CircuitBreakerRecord(
                name=args.name,
                state=CircuitBreakerState.OPEN,
                opened_at=now,
                total_calls=0,
                total_failures=0,
                total_successes=0,
                total_rejected=0,
                updated_at=now,
            )
        else:
            r.state      = CircuitBreakerState.OPEN
            r.opened_at  = now
            r.updated_at = now
        store.save(r)
        print(f"  Circuit '{args.name}' forced to OPEN.")

    elif args.circuit_cmd == "remove":
        if store.delete(args.name):
            print(f"  Circuit '{args.name}' removed.")
        else:
            print(f"  No record for '{args.name}'.")
            import sys as _sys
            _sys.exit(1)


# ── memory ────────────────────────────────────────────────────────────────────

def _cmd_memory(args: argparse.Namespace) -> None:
    import json as _json
    import sys as _sys
    from meshflow.intelligence.semantic_memory import SemanticMemoryStore
    from meshflow.intelligence.embedding import HashEmbeddingProvider, get_embedding_provider

    provider_arg = getattr(args, "provider", "auto")
    provider = HashEmbeddingProvider() if provider_arg == "hash" else get_embedding_provider()
    store = SemanticMemoryStore(db_path=args.db, provider=provider)

    if args.memory_cmd == "search":
        results = store.search(args.query, k=args.k, min_score=args.min_score)
        if args.json_output:
            print(_json.dumps([
                {
                    "key":      r.key,
                    "text":     r.text,
                    "score":    round(r.score, 4),
                    "metadata": r.metadata,
                }
                for r in results
            ], indent=2))
        else:
            if not results:
                print("  No matching memories found.")
                return
            print(f"\n  {'SCORE':>6}  {'KEY':<28}  TEXT")
            print("  " + "-" * 70)
            for r in results:
                snippet = r.text[:50].replace("\n", " ")
                print(f"  {r.score:>6.3f}  {r.key:<28}  {snippet}")
            print()

    elif args.memory_cmd == "store":
        try:
            meta = _json.loads(args.meta)
        except _json.JSONDecodeError:
            print(f"  Error: --meta must be valid JSON. Got: {args.meta!r}")
            _sys.exit(1)
        entry = store.store(args.key, args.text, metadata=meta)
        print(f"  Stored '{entry.key}' ({len(entry.embedding)}-dim vector)")

    elif args.memory_cmd == "get":
        entry = store.get(args.key)
        if entry is None:
            print(f"  No memory entry found for key '{args.key}'.")
            _sys.exit(1)
        print(f"\n  Key      : {entry.key}")
        print(f"  Text     : {entry.text[:120]}")
        if entry.metadata:
            print(f"  Metadata : {_json.dumps(entry.metadata)}")
        import datetime
        print(f"  Stored   : {datetime.datetime.fromtimestamp(entry.stored_at).isoformat()}")

    elif args.memory_cmd == "list":
        entries = store.list(limit=args.limit, offset=args.offset)
        total   = store.count()
        if not entries:
            print("  Memory store is empty.")
            return
        print(f"\n  {'KEY':<28}  TEXT  ({total} total)")
        print("  " + "-" * 60)
        for e in entries:
            snippet = e.text[:40].replace("\n", " ")
            print(f"  {e.key:<28}  {snippet}")
        print()

    elif args.memory_cmd == "delete":
        if store.delete(args.key):
            print(f"  Deleted memory entry '{args.key}'.")
        else:
            print(f"  No entry found for key '{args.key}'.")
            _sys.exit(1)

    elif args.memory_cmd == "clear":
        if not args.yes:
            answer = input("  Delete ALL memory entries? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("  Aborted.")
                return
        count = store.clear()
        print(f"  Cleared {count} memory entries.")

    elif args.memory_cmd == "export":
        from meshflow.intelligence.memory_backends import SQLiteMemoryBackend
        import json as _json

        backend = SQLiteMemoryBackend(args.db)
        agent_name = args.agent
        snapshot_data = backend.load(agent_name)

        if snapshot_data is None:
            print(f"  No memory found for agent {agent_name!r} in {args.db}")
            sys.exit(1)

        output_file = args.output or f"{agent_name}_memory.json"
        with open(output_file, "w") as fh:
            _json.dump(snapshot_data, fh, indent=2)
        print(f"  Exported memory for {agent_name!r} → {output_file}")
        items = sum(len(snapshot_data.get(k, [])) for k in ("working", "episodic", "procedural"))
        print(f"  ({items} memory items)")

    elif args.memory_cmd == "import":
        from meshflow.intelligence.memory_backends import SQLiteMemoryBackend
        import json as _json

        with open(args.file) as fh:
            snapshot_data = _json.load(fh)

        agent_name = args.agent or snapshot_data.get("agent_id", "")
        if not agent_name:
            print("  ERROR: Cannot determine agent name. Use --agent <name>")
            sys.exit(1)

        backend = SQLiteMemoryBackend(args.db)
        backend.save(agent_name, snapshot_data)
        items = sum(len(snapshot_data.get(k, [])) for k in ("working", "episodic", "procedural"))
        print(f"  Imported {items} memory item(s) for {agent_name!r} → {args.db}")


# ── alerts ────────────────────────────────────────────────────────────────────


def _cmd_alerts(args: argparse.Namespace) -> None:
    from meshflow.alerting.rules import AlertRuleStore, AlertStore

    db = args.db

    if args.alerts_cmd == "rules":
        rule_store = AlertRuleStore(db)

        if args.rules_cmd == "list":
            rules = rule_store.list_rules(
                agent_name=getattr(args, "agent_name", ""),
                enabled_only=getattr(args, "enabled_only", False),
            )
            if getattr(args, "json_output", False):
                print(json.dumps([r.to_dict() for r in rules], indent=2))
                return
            if not rules:
                print("  No alert rules defined.")
                return
            print(f"\n  Alert rules ({len(rules)}):\n")
            print(f"  {'RULE_ID':<36}  {'NAME':<20}  {'AGENT':<18}  {'METRIC':<16}  {'OP':<4}  {'THRESH':>8}  EN")
            print("  " + "─" * 115)
            for r in rules:
                print(
                    f"  {r.rule_id:<36}  {r.name:<20}  {r.agent_name:<18}  "
                    f"{r.metric:<16}  {r.operator:<4}  {r.threshold:>8.3g}  {'✓' if r.enabled else '✗'}"
                )
            print()

        elif args.rules_cmd == "add":
            rule = rule_store.add(
                name=args.name,
                agent_name=args.agent_name,
                metric=args.metric,
                operator=args.operator,
                threshold=args.threshold,
                window_s=args.window_s,
                agg_fn=args.agg_fn,
                webhook_url=getattr(args, "webhook_url", ""),
                webhook_secret=getattr(args, "webhook_secret", ""),
            )
            print(f"\n  Alert rule '{rule.name}' created.")
            print(f"  ID:        {rule.rule_id}")
            print(f"  Condition: {rule.agent_name}.{rule.metric} {rule.operator} {rule.threshold}")
            print(f"  Window:    {rule.window_s}s  Agg: {rule.agg_fn}\n")

        elif args.rules_cmd == "remove":
            ok = rule_store.delete(args.rule_id)
            if ok:
                print(f"  Rule {args.rule_id} deleted.")
            else:
                print(f"  Rule {args.rule_id} not found.")
                sys.exit(1)

        elif args.rules_cmd == "enable":
            ok = rule_store.enable(args.rule_id)
            print(f"  Rule {args.rule_id} {'enabled' if ok else 'not found'}.")
            if not ok:
                sys.exit(1)

        elif args.rules_cmd == "disable":
            ok = rule_store.disable(args.rule_id)
            print(f"  Rule {args.rule_id} {'disabled' if ok else 'not found'}.")
            if not ok:
                sys.exit(1)

    elif args.alerts_cmd == "list":
        alert_store = AlertStore(db)
        alerts = alert_store.list_alerts(
            status=getattr(args, "status", ""),
            agent_name=getattr(args, "agent_name", ""),
            limit=getattr(args, "limit", 20),
        )
        if getattr(args, "json_output", False):
            print(json.dumps([a.to_dict() for a in alerts], indent=2))
            return
        if not alerts:
            print("  No alerts.")
            return
        print(f"\n  Alerts ({len(alerts)}):\n")
        print(f"  {'ALERT_ID':<36}  {'RULE':<20}  {'STATUS':<10}  {'VALUE':>8}  MESSAGE")
        print("  " + "─" * 100)
        for a in alerts:
            print(
                f"  {a.alert_id:<36}  {a.rule_name:<20}  {a.status:<10}  "
                f"{a.value:>8.3g}  {a.message[:50]}"
            )
        print()

    elif args.alerts_cmd == "ack":
        alert_store = AlertStore(db)
        ok = alert_store.ack(args.alert_id, acked_by=getattr(args, "acked_by", "cli"))
        if ok:
            print(f"  Alert {args.alert_id} acknowledged.")
        else:
            print(f"  Alert {args.alert_id} not found or already acked.")
            sys.exit(1)

    elif args.alerts_cmd == "status":
        rule_store = AlertRuleStore(db)
        alert_store = AlertStore(db)
        rules_count = rule_store.count()
        counts = alert_store.counts()
        print("\n  Alert engine status")
        print(f"  {'─' * 30}")
        print(f"  Rules defined:  {rules_count}")
        print(f"  Firing:         {counts.get('firing', 0)}")
        print(f"  Resolved:       {counts.get('resolved', 0)}")
        print(f"  Acknowledged:   {counts.get('acked', 0)}")
        print()


# ── locks ─────────────────────────────────────────────────────────────────────


def _cmd_locks(args: argparse.Namespace) -> None:
    from meshflow.locking.store import LockStore

    store = LockStore(args.db)

    if args.locks_cmd == "list":
        locks = store.list_locks(active_only=not getattr(args, "show_all", False))
        if getattr(args, "json_output", False):
            print(json.dumps([lk.to_dict() for lk in locks], indent=2))
            return
        if not locks:
            print("  No active locks.")
            return
        print(f"\n  Active locks ({len(locks)}):\n")
        print(f"  {'RESOURCE_ID':<30}  {'OWNER':<20}  {'TTL_S':>6}  REMAINING")
        print("  " + "─" * 75)
        for lk in locks:
            print(f"  {lk.resource_id:<30}  {lk.owner:<20}  {lk.ttl_s:>6.1f}  {lk.remaining_s:.1f}s")
        print()

    elif args.locks_cmd == "status":
        lk = store.get(args.resource_id)
        if lk is None:
            print(f"  '{args.resource_id}' is not locked.")
        else:
            print(f"\n  Lock: {args.resource_id}")
            print(f"  Owner:      {lk.owner}")
            print(f"  Lock ID:    {lk.lock_id}")
            print(f"  TTL:        {lk.ttl_s}s")
            print(f"  Remaining:  {lk.remaining_s:.1f}s\n")

    elif args.locks_cmd == "release":
        ok = store.force_release(args.resource_id)
        if ok:
            print(f"  Lock on '{args.resource_id}' force-released.")
        else:
            print(f"  No lock found for '{args.resource_id}'.")
            sys.exit(1)

    elif args.locks_cmd == "purge":
        n = store.purge_expired()
        print(f"  Purged {n} expired lock(s).")


# ── lineage ───────────────────────────────────────────────────────────────────


def _cmd_lineage(args: argparse.Namespace) -> None:
    from meshflow.lineage.graph import LineageGraph

    g = LineageGraph(args.db)

    if args.lineage_cmd == "show":
        node = g.get_node(args.node_id)
        if node is None:
            print(f"  Node '{args.node_id}' not found.")
            sys.exit(1)
        if getattr(args, "json_output", False):
            print(json.dumps(node.to_dict(), indent=2))
            return
        print(f"\n  Node: {node.node_id}")
        print(f"  Kind:  {node.kind}  Name: {node.name}")
        print(f"  Run:   {node.run_id}  Agent: {node.agent_name}\n")

    elif args.lineage_cmd == "trace":
        nodes = g.trace_upstream(args.node_id)
        if getattr(args, "json_output", False):
            print(json.dumps([n.to_dict() for n in nodes], indent=2))
            return
        if not nodes:
            print(f"  No upstream nodes for '{args.node_id}'.")
            return
        print(f"\n  Upstream lineage ({len(nodes)} nodes):\n")
        for n in nodes:
            print(f"  [{n.kind}] {n.name}  ({n.node_id[:8]}…)  agent={n.agent_name}")
        print()

    elif args.lineage_cmd == "impact":
        nodes = g.impact_analysis(args.node_id)
        if getattr(args, "json_output", False):
            print(json.dumps([n.to_dict() for n in nodes], indent=2))
            return
        if not nodes:
            print(f"  No downstream nodes for '{args.node_id}'.")
            return
        print(f"\n  Impact analysis ({len(nodes)} nodes):\n")
        for n in nodes:
            print(f"  [{n.kind}] {n.name}  ({n.node_id[:8]}…)  agent={n.agent_name}")
        print()

    elif args.lineage_cmd == "run":
        nodes = g.for_run(args.run_id)
        if getattr(args, "json_output", False):
            print(json.dumps([n.to_dict() for n in nodes], indent=2))
            return
        if not nodes:
            print(f"  No lineage data for run '{args.run_id}'.")
            return
        print(f"\n  Lineage for run {args.run_id} ({len(nodes)} nodes):\n")
        for n in nodes:
            print(f"  [{n.kind}] {n.name}  agent={n.agent_name}")
        print()

    elif args.lineage_cmd == "delete":
        if not getattr(args, "yes", False):
            answer = input(f"  Delete ALL lineage nodes for subject '{args.name}'? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("  Aborted.")
                return
        n = g.delete_subject(args.name)
        print(f"  Deleted {n} lineage node(s) for '{args.name}'.")

    elif args.lineage_cmd == "stats":
        print("\n  Lineage graph statistics")
        print(f"  {'─' * 30}")
        print(f"  Nodes: {g.node_count()}")
        print(f"  Edges: {g.edge_count()}")
        print()


# ── identity ──────────────────────────────────────────────────────────────────


def _cmd_identity(args: argparse.Namespace) -> None:
    from meshflow.identity.core import IdentityStore, sign_token, verify_token

    if args.identity_cmd == "create":
        store = IdentityStore(args.db)
        identity = store.register(
            name=args.name,
            capabilities=getattr(args, "capabilities", []),
            issuer=getattr(args, "issuer", "meshflow"),
        )
        print("\n  Agent identity registered.")
        print(f"  ID:           {identity.agent_id}")
        print(f"  Name:         {identity.name}")
        print(f"  Capabilities: {', '.join(identity.capabilities) or '(none)'}\n")

    elif args.identity_cmd == "list":
        store = IdentityStore(args.db)
        identities = store.list_identities(active_only=getattr(args, "active_only", False))
        if getattr(args, "json_output", False):
            print(json.dumps([i.to_dict() for i in identities], indent=2))
            return
        if not identities:
            print("  No identities registered.")
            return
        print(f"\n  Agent identities ({len(identities)}):\n")
        print(f"  {'AGENT_ID':<36}  {'NAME':<24}  {'REVOKED'}")
        print("  " + "─" * 72)
        for i in identities:
            print(f"  {i.agent_id:<36}  {i.name:<24}  {'yes' if i.revoked else 'no'}")
        print()

    elif args.identity_cmd == "get":
        store = IdentityStore(args.db)
        identity = store.get_by_name(args.name)
        if identity is None:
            print(f"  Identity '{args.name}' not found.")
            sys.exit(1)
        print(json.dumps(identity.to_dict(), indent=2))

    elif args.identity_cmd == "revoke":
        store = IdentityStore(args.db)
        ok = store.revoke(args.agent_id)
        if ok:
            print(f"  Identity {args.agent_id} revoked.")
        else:
            print(f"  Identity {args.agent_id} not found.")
            sys.exit(1)

    elif args.identity_cmd == "sign":
        store = IdentityStore(args.db)
        identity = store.get_by_name(args.name)
        if identity is None:
            print(f"  Identity '{args.name}' not found.")
            sys.exit(1)
        if identity.revoked:
            print(f"  Identity '{args.name}' is revoked — cannot issue token.")
            sys.exit(1)
        token = sign_token(identity, secret=args.secret, ttl_s=getattr(args, "ttl_s", 3600.0))
        print(token)

    elif args.identity_cmd == "verify":
        claims = verify_token(args.token, secret=args.secret)
        if claims is None:
            print("  Token is INVALID or EXPIRED.")
            sys.exit(1)
        print("\n  Token is VALID.")
        print(f"  Agent:  {claims.agent_name} ({claims.agent_id})")
        print(f"  Issuer: {claims.issuer}")
        print(f"  Caps:   {', '.join(claims.capabilities) or '(none)'}\n")


# ── canary ────────────────────────────────────────────────────────────────────


def _cmd_canary(args: argparse.Namespace) -> None:
    from meshflow.canary.router import CanaryStore, CanaryRouter

    db = getattr(args, "db", "meshflow_canary.db")

    if args.canary_cmd == "create":
        store = CanaryStore(db)
        exp = store.create_experiment(
            name=args.name,
            stable_agent=args.stable_agent,
            canary_agent=args.canary_agent,
            split=args.split,
            min_requests=args.min_requests,
            promote_threshold=args.promote_threshold,
            rollback_threshold=args.rollback_threshold,
        )
        print("\n  Canary experiment created.")
        print(f"  ID:         {exp.experiment_id}")
        print(f"  Name:       {exp.name}")
        print(f"  Stable:     {exp.stable_agent}")
        print(f"  Canary:     {exp.canary_agent}")
        print(f"  Split:      {exp.split:.1%}")
        print(f"  Promote @:  {exp.promote_threshold:.0%}  Rollback @: {exp.rollback_threshold:.0%}\n")

    elif args.canary_cmd == "list":
        store = CanaryStore(db)
        exps = store.list_experiments(status=getattr(args, "status", ""))
        if getattr(args, "json_output", False):
            print(json.dumps([e.to_dict() for e in exps], indent=2))
            return
        if not exps:
            print("  No canary experiments found.")
            return
        print(f"\n  Canary experiments ({len(exps)}):\n")
        print(f"  {'NAME':<24}  {'STATUS':<12}  {'STABLE':<18}  {'CANARY':<18}  SPLIT")
        print("  " + "─" * 85)
        for e in exps:
            print(
                f"  {e.name:<24}  {e.status:<12}  {e.stable_agent:<18}  "
                f"{e.canary_agent:<18}  {e.split:.0%}"
            )
        print()

    elif args.canary_cmd == "status":
        store = CanaryStore(db)
        exp = store.get_by_name(args.name)
        if exp is None:
            print(f"  Experiment '{args.name}' not found.")
            sys.exit(1)
        router = CanaryRouter(store)
        stats = router.stats(exp.experiment_id)
        stable = stats["stable"]
        canary = stats["canary"]
        print(f"\n  Canary experiment: {exp.name}")
        print(f"  Status:  {exp.status}  Split: {exp.split:.0%}")
        print(f"\n  {'COHORT':<8}  {'TOTAL':>6}  {'SUCCESS':>8}  {'ERROR':>6}  {'RATE':>6}  AVG_MS")
        print("  " + "─" * 52)
        for s in (stable, canary):
            rate = f"{s.success_rate:.1%}" if s.total else "—"
            print(
                f"  {s.cohort:<8}  {s.total:>6}  {s.successes:>8}  {s.errors:>6}  {rate:>6}  {s.avg_latency:.1f}"
            )
        promote_ready = router.should_promote(exp.experiment_id)
        rollback_ready = router.should_rollback(exp.experiment_id)
        print(f"\n  Promote ready: {'yes' if promote_ready else 'no'}")
        print(f"  Rollback ready: {'yes' if rollback_ready else 'no'}\n")

    elif args.canary_cmd == "promote":
        store = CanaryStore(db)
        exp = store.get_by_name(args.name)
        if exp is None:
            print(f"  Experiment '{args.name}' not found.")
            sys.exit(1)
        ok = CanaryRouter(store).promote(exp.experiment_id)
        if ok:
            print(f"  Experiment '{args.name}' promoted to stable.")
        else:
            print(f"  Could not promote '{args.name}'.")
            sys.exit(1)

    elif args.canary_cmd == "rollback":
        store = CanaryStore(db)
        exp = store.get_by_name(args.name)
        if exp is None:
            print(f"  Experiment '{args.name}' not found.")
            sys.exit(1)
        ok = CanaryRouter(store).rollback(exp.experiment_id)
        if ok:
            print(f"  Experiment '{args.name}' rolled back.")
        else:
            print(f"  Could not roll back '{args.name}'.")
            sys.exit(1)

    elif args.canary_cmd == "pause":
        store = CanaryStore(db)
        exp = store.get_by_name(args.name)
        if exp is None:
            print(f"  Experiment '{args.name}' not found.")
            sys.exit(1)
        ok = CanaryRouter(store).pause(exp.experiment_id)
        if ok:
            print(f"  Experiment '{args.name}' paused.")
        else:
            print(f"  Could not pause '{args.name}'.")
            sys.exit(1)


# ── flags ─────────────────────────────────────────────────────────────────────


def _cmd_flags(args: argparse.Namespace) -> None:
    import json as _json
    from meshflow.flags.store import FlagStore, FlagEvaluator

    db = getattr(args, "db", "meshflow_flags.db")

    def _parse_default(raw: str, flag_type: str):
        if flag_type == "bool":
            return raw.lower() not in ("false", "0", "no", "off", "")
        if flag_type == "number":
            try:
                return float(raw)
            except ValueError:
                return 0.0
        return raw

    if args.flags_cmd == "define":
        store = FlagStore(db)
        default = _parse_default(args.default_value, args.flag_type)
        flag = store.define(
            name=args.name,
            flag_type=args.flag_type,
            default_value=default,
            description=getattr(args, "description", ""),
            rollout_pct=getattr(args, "rollout_pct", 100.0),
        )
        print("\n  Feature flag defined.")
        print(f"  ID:          {flag.flag_id}")
        print(f"  Name:        {flag.name}")
        print(f"  Type:        {flag.flag_type}")
        print(f"  Default:     {flag.default_val}")
        print(f"  Rollout:     {flag.rollout_pct:.0f}%\n")

    elif args.flags_cmd == "list":
        store = FlagStore(db)
        flags = store.list_flags(enabled_only=getattr(args, "enabled_only", False))
        if getattr(args, "json_output", False):
            print(_json.dumps([f.to_dict() for f in flags], indent=2))
            return
        if not flags:
            print("  No feature flags defined.")
            return
        print(f"\n  Feature flags ({len(flags)}):\n")
        print(f"  {'NAME':<28}  {'TYPE':<8}  {'DEFAULT':<12}  {'ROLLOUT':>7}  EN  DESCRIPTION")
        print("  " + "─" * 80)
        for f in flags:
            en = "✓" if f.enabled else "✗"
            print(
                f"  {f.name:<28}  {f.flag_type:<8}  {str(f.default_val):<12}  "
                f"{f.rollout_pct:>6.0f}%  {en}   {f.description[:30]}"
            )
        print()

    elif args.flags_cmd == "get":
        store = FlagStore(db)
        flag = store.get_by_name(args.name)
        if flag is None:
            print(f"  Flag '{args.name}' not found.")
            sys.exit(1)
        rules = store.list_rules(flag.flag_id)
        print(_json.dumps({**flag.to_dict(), "rules": [r.to_dict() for r in rules]}, indent=2))

    elif args.flags_cmd == "enable":
        store = FlagStore(db)
        flag = store.get_by_name(args.name)
        if flag is None:
            print(f"  Flag '{args.name}' not found.")
            sys.exit(1)
        store.enable(flag.flag_id)
        print(f"  Flag '{args.name}' enabled.")

    elif args.flags_cmd == "disable":
        store = FlagStore(db)
        flag = store.get_by_name(args.name)
        if flag is None:
            print(f"  Flag '{args.name}' not found.")
            sys.exit(1)
        store.disable(flag.flag_id)
        print(f"  Flag '{args.name}' disabled.")

    elif args.flags_cmd == "delete":
        store = FlagStore(db)
        flag = store.get_by_name(args.name)
        if flag is None:
            print(f"  Flag '{args.name}' not found.")
            sys.exit(1)
        store.delete(flag.flag_id)
        print(f"  Flag '{args.name}' deleted.")

    elif args.flags_cmd == "add-rule":
        store = FlagStore(db)
        flag = store.get_by_name(args.name)
        if flag is None:
            print(f"  Flag '{args.name}' not found.")
            sys.exit(1)
        rule = store.add_rule(
            flag_id=flag.flag_id,
            condition_key=args.condition_key,
            condition_op=args.condition_op,
            condition_value=args.condition_value,
            return_value=args.return_value,
            priority=getattr(args, "priority", 0),
        )
        print(f"\n  Rule added to flag '{args.name}'.")
        print(f"  Rule ID:  {rule.rule_id}")
        print(f"  When {rule.condition_key} {rule.condition_op} {rule.condition_value!r} → {rule.return_value!r}\n")

    elif args.flags_cmd == "evaluate":
        store = FlagStore(db)
        try:
            ctx = _json.loads(getattr(args, "context", "{}"))
        except _json.JSONDecodeError:
            print("  Error: --context must be valid JSON.")
            sys.exit(1)
        evaluator = FlagEvaluator(store)
        try:
            value = evaluator.evaluate(args.name, ctx)
        except KeyError:
            print(f"  Flag '{args.name}' not found.")
            sys.exit(1)
        print(f"  {args.name} = {value!r}")


# ── _cmd_vault ────────────────────────────────────────────────────────────────

def _cmd_vault(args: argparse.Namespace) -> None:
    from meshflow.vault.store import VaultStore
    db = getattr(args, "db", "meshflow_vault.db")
    passphrase = getattr(args, "passphrase", "meshflow-vault")

    if args.vault_cmd == "store":
        store = VaultStore(db, passphrase=passphrase)
        secret = store.store(
            args.name, args.value,
            category=getattr(args, "category", "generic"),
            description=getattr(args, "description", ""),
        )
        print("\n  Secret stored.")
        print(f"  Name:     {secret.name}")
        print(f"  Category: {secret.category}")
        print(f"  ID:       {secret.secret_id}\n")

    elif args.vault_cmd == "retrieve":
        store = VaultStore(db, passphrase=passphrase)
        secret = store.retrieve(args.name)
        if secret is None:
            print(f"  Secret '{args.name}' not found.")
            sys.exit(1)
        print(f"\n  Name:  {secret.name}")
        print(f"  Value: {secret.value}")
        print(f"  Cat:   {secret.category}\n")

    elif args.vault_cmd == "rotate":
        store = VaultStore(db, passphrase=passphrase)
        ok = store.rotate(args.name, args.new_value)
        if ok:
            print(f"  Secret '{args.name}' rotated successfully.")
        else:
            print(f"  Secret '{args.name}' not found.")
            sys.exit(1)

    elif args.vault_cmd == "delete":
        store = VaultStore(db, passphrase=passphrase)
        ok = store.delete(args.name)
        if ok:
            print(f"  Secret '{args.name}' deleted.")
        else:
            print(f"  Secret '{args.name}' not found.")
            sys.exit(1)

    elif args.vault_cmd == "list":
        store = VaultStore(db, passphrase=passphrase)
        secrets = store.list_secrets(category=getattr(args, "category", ""))
        if not secrets:
            print("  No secrets found.")
            return
        print(f"\n  {'NAME':<30} {'CATEGORY':<15} {'DESCRIPTION'}")
        print(f"  {'-'*30} {'-'*15} {'-'*30}")
        for s in secrets:
            print(f"  {s['name']:<30} {s['category']:<15} {s.get('description','')}")
        print()

    elif args.vault_cmd == "audit":
        store = VaultStore(db, passphrase=passphrase)
        entries = store.audit_log(name=getattr(args, "name", ""), limit=args.limit)
        if not entries:
            print("  No audit entries found.")
            return
        print(f"\n  {'OP':<12} {'SECRET':<25} {'BY':<15} {'TS'}")
        print(f"  {'-'*12} {'-'*25} {'-'*15} {'-'*20}")
        import datetime
        for e in entries:
            ts = datetime.datetime.fromtimestamp(e.ts).strftime("%Y-%m-%d %H:%M:%S")
            print(f"  {e.operation:<12} {e.secret_name:<25} {e.accessed_by:<15} {ts}")
        print()


# ── _cmd_tenant ───────────────────────────────────────────────────────────────

def _cmd_tenant(args: argparse.Namespace) -> None:
    from meshflow.tenant.store import TenantStore
    db = getattr(args, "db", "meshflow_tenants.db")

    if args.tenant_cmd == "create":
        store = TenantStore(db)
        tenant = store.create(args.name, args.slug, plan=getattr(args, "plan", "free"))
        print("\n  Tenant created.")
        print(f"  Name: {tenant.name}  Slug: {tenant.slug}")
        print(f"  Plan: {tenant.plan}  ID: {tenant.tenant_id}\n")

    elif args.tenant_cmd == "list":
        store = TenantStore(db)
        tenants = store.list_tenants(status=getattr(args, "status", ""))
        if not tenants:
            print("  No tenants found.")
            return
        print(f"\n  {'NAME':<25} {'SLUG':<20} {'PLAN':<12} {'STATUS'}")
        print(f"  {'-'*25} {'-'*20} {'-'*12} {'-'*10}")
        for t in tenants:
            print(f"  {t.name:<25} {t.slug:<20} {t.plan:<12} {t.status}")
        print()

    elif args.tenant_cmd == "get":
        store = TenantStore(db)
        tenant = store.get_by_slug(args.slug)
        if tenant is None:
            print(f"  Tenant '{args.slug}' not found.")
            sys.exit(1)
        import json
        print(f"\n{json.dumps(tenant.to_dict(), indent=2)}\n")

    elif args.tenant_cmd == "suspend":
        store = TenantStore(db)
        tenant = store.get_by_slug(args.slug)
        if tenant is None:
            print(f"  Tenant '{args.slug}' not found.")
            sys.exit(1)
        store.update_status(tenant.tenant_id, "suspended")
        print(f"  Tenant '{args.slug}' suspended.")

    elif args.tenant_cmd == "plan":
        store = TenantStore(db)
        tenant = store.get_by_slug(args.slug)
        if tenant is None:
            print(f"  Tenant '{args.slug}' not found.")
            sys.exit(1)
        store.update_plan(tenant.tenant_id, args.plan)
        print(f"  Tenant '{args.slug}' plan updated to '{args.plan}'.")


# ── _cmd_tracing ──────────────────────────────────────────────────────────────

def _cmd_tracing(args: argparse.Namespace) -> None:
    from meshflow.tracing.context import TraceStore
    db = getattr(args, "db", "meshflow_traces.db")
    store = TraceStore(db)

    if args.tracing_cmd == "show":
        spans = store.get_trace(args.trace_id)
        if not spans:
            print(f"  No spans for trace '{args.trace_id}'.")
            return
        print(f"\n  Trace: {args.trace_id}  ({len(spans)} spans)")
        print(f"  {'SPAN':<18} {'PARENT':<18} {'NAME':<30} {'KIND':<10} {'STATUS':<8} {'MS'}")
        print(f"  {'-'*18} {'-'*18} {'-'*30} {'-'*10} {'-'*8} {'-'*8}")
        for s in spans:
            ms = f"{s.duration_ms:.1f}" if s.duration_ms is not None else "—"
            parent = (s.parent_id or "")[:16]
            print(f"  {s.span_id[:16]:<18} {parent:<18} {s.name[:28]:<30} {s.kind.value:<10} {s.status.value:<8} {ms}")
        print()

    elif args.tracing_cmd == "run":
        spans = store.get_for_run(args.run_id)
        if not spans:
            print(f"  No spans for run '{args.run_id}'.")
            return
        print(f"\n  Run: {args.run_id}  ({len(spans)} spans)")
        for s in spans:
            ms = f"{s.duration_ms:.1f}" if s.duration_ms is not None else "—"
            print(f"  {s.span_id[:12]}  {s.name:<35} {s.kind.value:<10} {ms}ms")
        print()

    elif args.tracing_cmd == "count":
        n = store.count()
        print(f"  Total spans: {n}")


# ── _cmd_policy ───────────────────────────────────────────────────────────────

def _cmd_policy(args: argparse.Namespace) -> None:
    import json as _json
    from meshflow.policy.engine import PolicyStore, PolicyEngine, PolicyAction
    db = getattr(args, "db", "meshflow_policy.db")

    if args.policy_cmd == "add":
        store = PolicyStore(db)
        engine = PolicyEngine(store, audit=False)
        raw_conditions = []
        for cond_str in (getattr(args, "conditions", None) or []):
            parts = cond_str.split(":", 2)
            if len(parts) != 3:
                print(f"  Error: condition must be FIELD:OP:VALUE — got '{cond_str}'")
                sys.exit(1)
            raw_conditions.append((parts[0], parts[1], parts[2]))
        rule = engine.add_rule(
            name=args.name,
            action=PolicyAction(args.action),
            conditions=raw_conditions,
            framework=getattr(args, "framework", "custom"),
            priority=getattr(args, "priority", 0),
            description=getattr(args, "description", ""),
        )
        print(f"\n  Policy rule '{rule.name}' added.")
        print(f"  Action:    {rule.action.value}")
        print(f"  Framework: {rule.framework}  Priority: {rule.priority}")
        print(f"  Conditions: {len(rule.conditions)}\n")

    elif args.policy_cmd == "list":
        store = PolicyStore(db)
        rules = store.list_rules(
            framework=getattr(args, "framework", ""),
            enabled_only=getattr(args, "enabled_only", False),
        )
        if not rules:
            print("  No policy rules found.")
            return
        print(f"\n  {'NAME':<30} {'ACTION':<8} {'FRAMEWORK':<12} {'PRI':>4} {'EN'}")
        print(f"  {'-'*30} {'-'*8} {'-'*12} {'-'*4} {'-'*3}")
        for r in rules:
            en = "Y" if r.enabled else "N"
            print(f"  {r.name:<30} {r.action.value:<8} {r.framework:<12} {r.priority:>4} {en}")
        print()

    elif args.policy_cmd in ("enable", "disable"):
        store = PolicyStore(db)
        rule = store.get_by_name(args.name)
        if rule is None:
            print(f"  Rule '{args.name}' not found.")
            sys.exit(1)
        if args.policy_cmd == "enable":
            store.enable_rule(rule.rule_id)
            print(f"  Rule '{args.name}' enabled.")
        else:
            store.disable_rule(rule.rule_id)
            print(f"  Rule '{args.name}' disabled.")

    elif args.policy_cmd == "evaluate":
        store = PolicyStore(db)
        engine = PolicyEngine(store)
        try:
            ctx = _json.loads(args.context)
        except _json.JSONDecodeError:
            print("  Error: --context must be valid JSON.")
            sys.exit(1)
        decision = engine.evaluate(ctx, framework=getattr(args, "framework", ""))
        print(f"\n  Action:    {decision.action.value}")
        print(f"  Rule:      {decision.rule_name or '(none)'}")
        print(f"  Reason:    {decision.reason}")
        print(f"  Allowed:   {decision.is_allowed}\n")


# ── _cmd_sla ──────────────────────────────────────────────────────────────────

def _cmd_sla(args: argparse.Namespace) -> None:
    from meshflow.sla.tracker import SLAStore, SLATracker
    db = getattr(args, "db", "meshflow_sla.db")

    if args.sla_cmd == "define":
        store = SLAStore(db)
        contract = store.define_contract(
            agent_name=args.agent_name,
            p50_ms=args.p50,
            p95_ms=args.p95,
            p99_ms=args.p99,
            error_rate=getattr(args, "error_rate", 0.05),
            window_s=getattr(args, "window", 3600.0),
        )
        print(f"\n  SLA contract defined for '{contract.agent_name}'.")
        print(f"  p50={contract.p50_ms}ms  p95={contract.p95_ms}ms  p99={contract.p99_ms}ms")
        print(f"  Error rate: {contract.error_rate*100:.1f}%  Window: {contract.window_s}s\n")

    elif args.sla_cmd == "stats":
        store = SLAStore(db)
        tracker = SLATracker(store)
        stats = tracker.stats(args.agent_name, window_s=getattr(args, "window", 3600.0))
        if stats.total == 0:
            print(f"  No observations for '{args.agent_name}'.")
            return
        print(f"\n  Agent: {stats.agent_name}  ({stats.total} observations)")
        print(f"  p50={stats.p50_ms:.1f}ms  p95={stats.p95_ms:.1f}ms  p99={stats.p99_ms:.1f}ms")
        print(f"  avg={stats.avg_ms:.1f}ms  error_rate={stats.error_rate*100:.2f}%\n")

    elif args.sla_cmd == "breaches":
        store = SLAStore(db)
        breaches = store.list_breaches(agent_name=getattr(args, "agent", ""), limit=args.limit)
        if not breaches:
            print("  No SLA breaches recorded.")
            return
        print(f"\n  {'AGENT':<25} {'TYPE':<12} {'OBSERVED':>10} {'THRESHOLD':>10}")
        print(f"  {'-'*25} {'-'*12} {'-'*10} {'-'*10}")
        for b in breaches:
            print(f"  {b.agent_name:<25} {b.breach_type:<12} {b.observed:>10.2f} {b.threshold:>10.2f}")
        print()

    elif args.sla_cmd == "list":
        store = SLAStore(db)
        contracts = store.list_contracts()
        if not contracts:
            print("  No SLA contracts defined.")
            return
        print(f"\n  {'AGENT':<25} {'P50':>8} {'P95':>8} {'P99':>8} {'ERR%':>6} {'EN'}")
        print(f"  {'-'*25} {'-'*8} {'-'*8} {'-'*8} {'-'*6} {'-'*3}")
        for c in contracts:
            en = "Y" if c.enabled else "N"
            print(f"  {c.agent_name:<25} {c.p50_ms:>8.1f} {c.p95_ms:>8.1f} {c.p99_ms:>8.1f} {c.error_rate*100:>5.1f}% {en}")
        print()


# ── _cmd_snapshot ─────────────────────────────────────────────────────────────

def _cmd_snapshot(args: argparse.Namespace) -> None:
    if args.snapshot_cmd == "export":
        from meshflow.snapshot.bundle import SnapshotExporter
        from meshflow.flags.store import FlagStore
        from meshflow.policy.engine import PolicyStore
        from meshflow.sla.tracker import SLAStore
        from meshflow.vault.store import VaultStore
        from meshflow.tenant.store import TenantStore

        exporter = SnapshotExporter(
            flag_store=FlagStore(getattr(args, "flags_db", "meshflow_flags.db")),
            policy_store=PolicyStore(getattr(args, "policy_db", "meshflow_policy.db")),
            sla_store=SLAStore(getattr(args, "sla_db", "meshflow_sla.db")),
            vault_store=VaultStore(
                getattr(args, "vault_db", "meshflow_vault.db"),
                passphrase=getattr(args, "vault_passphrase", "meshflow-vault"),
            ),
            tenant_store=TenantStore(getattr(args, "tenant_db", "meshflow_tenants.db")),
        )
        output = getattr(args, "output", "meshflow_snapshot.zip")
        bundle = exporter.export_to_file(
            output,
            created_by=getattr(args, "created_by", "cli"),
            description=getattr(args, "description", ""),
        )
        print("\n  Compliance snapshot exported.")
        print(f"  File:     {output}")
        print(f"  ID:       {bundle.manifest.snapshot_id}")
        print(f"  Sections: {len(bundle.sections)}")
        print(f"  Records:  {bundle.total_records()}\n")


# ── _cmd_dasc ─────────────────────────────────────────────────────────────────

def _cmd_dasc(args: argparse.Namespace) -> None:
    from meshflow.security.dasc_gate import AutoRiskClassifier, AuditLedger, TaintGraph
    from meshflow.core.schemas import RiskTier
    db = getattr(args, "db", "meshflow_dasc.db")

    if args.dasc_cmd == "classify":
        from meshflow.core.schemas import Intent, RiskTier
        classifier = AutoRiskClassifier()
        intent_obj = Intent(action=args.intent, payload={}, evidence=[], agent_id="cli", risk_tier=RiskTier.READ_ONLY)
        tier = classifier.classify(intent_obj)
        names = {1: "READ_ONLY", 2: "INTERNAL", 3: "EXTERNAL_IO", 4: "IRREVERSIBLE"}
        print(f"\n  Intent:    {args.intent}")
        print(f"  Risk tier: {int(tier)}  ({names.get(int(tier), '?')})\n")

    elif args.dasc_cmd == "ledger":
        ledger = AuditLedger(db)
        total = ledger.count()
        if total == 0:
            print("  Audit ledger is empty.")
            return
        rows = ledger._conn.execute(
            "SELECT agent_id, action, verdict, effective_tier FROM ledger ORDER BY rowid DESC LIMIT ?",
            (args.limit,),
        ).fetchall()
        print(f"\n  {'AGENT':<20} {'ACTION':<30} {'VERDICT':<10} {'TIER'}")
        print(f"  {'-'*20} {'-'*30} {'-'*10} {'-'*5}")
        for row in rows:
            print(f"  {row[0][:18]:<20} {row[1][:28]:<30} {row[2]:<10} {row[3]}")
        print()

    elif args.dasc_cmd == "verify":
        ledger = AuditLedger(db)
        ok = ledger.verify_chain()
        if ok:
            print(f"  Audit ledger chain is VALID ({ledger.count()} entries).")
        else:
            print("  INTEGRITY FAILURE: audit ledger chain is broken!")
            sys.exit(1)

    elif args.dasc_cmd == "taint":
        taint_graph = TaintGraph()
        taint_graph.mark_tainted(args.agent_id)
        print(f"  Agent '{args.agent_id}' marked as tainted.")


# ── eval-feedback ─────────────────────────────────────────────────────────────


def _cmd_eval_feedback(args: argparse.Namespace) -> None:
    from meshflow.eval.feedback import FeedbackCollector, FeedbackStore

    store = FeedbackStore(args.db)
    collector = FeedbackCollector(store)

    if args.run_id:
        summary = collector.summary(args.run_id)
        print(f"\n  Feedback summary for run {args.run_id!r}:")
        for k, v in summary.items():
            print(f"    {k}: {v}")
    else:
        summary = store.stats(agent_name=args.agent)
        print(f"\n  Feedback stats{f' (agent={args.agent!r})' if args.agent else ''}:")
        for k, v in summary.items():
            print(f"    {k}: {v}")

    if args.export_jsonl:
        pairs = collector.export_training_pairs(
            agent_name=args.agent,
            corrections_only=args.corrections_only,
        )
        with open(args.export_jsonl, "w") as fh:
            for pair in pairs:
                fh.write(json.dumps(pair) + "\n")
        print(f"\n  Exported {len(pairs)} training pair(s) → {args.export_jsonl}")


# ── worker ────────────────────────────────────────────────────────────────────


def _cmd_worker(args: argparse.Namespace) -> None:
    if args.worker_cmd == "start":
        asyncio.run(_async_worker_start(args))
    elif args.worker_cmd == "status":
        _worker_status(args)


async def _async_worker_start(args: argparse.Namespace) -> None:
    from meshflow.runtime.distributed import DistributedWorker

    worker = DistributedWorker(
        queue_url=args.queue,
        concurrency=args.concurrency,
        poll_interval=args.poll_interval,
    )
    print(f"  [worker] Starting with queue={args.queue!r}  concurrency={args.concurrency}  poll={args.poll_interval}s")
    print("  [worker] Press Ctrl-C to stop.")
    try:
        await worker.start()
    except KeyboardInterrupt:
        worker.stop()
        print("\n  [worker] Stopped.")


def _worker_status(args: argparse.Namespace) -> None:
    from meshflow.runtime.distributed import DistributedPool

    pool = DistributedPool(queue_url=args.queue)
    tasks = pool.list_tasks(limit=args.limit)
    pending = pool.pending_count()
    print(f"\n  Queue: {args.queue}  —  pending: {pending}  (showing last {args.limit})")
    if not tasks:
        print("  (no tasks)")
        return
    print(f"  {'task_id':<14} {'agent':<20} {'status':<10} task")
    print("  " + "-" * 70)
    for t in tasks:
        print(f"  {t.task_id[:12]:<14} {t.agent_name[:18]:<20} {t.status:<10} {t.task[:40]}")


# ── templates ─────────────────────────────────────────────────────────────────


def _cmd_templates(args: argparse.Namespace) -> None:
    import os
    from meshflow.registry.templates import AgentTemplate, TemplateRegistry

    reg = TemplateRegistry()

    if args.templates_cmd == "list":
        templates = reg.list()
        if not templates:
            print("  (no templates in local registry)")
            return
        print(f"\n  Local registry: {reg._dir}  ({len(templates)} template(s))")
        print(f"  {'name':<28} {'role':<14} {'version':<10} description")
        print("  " + "-" * 80)
        for t in templates:
            print(f"  {t.name[:26]:<28} {t.role[:12]:<14} {t.version:<10} {t.description[:40]}")

    elif args.templates_cmd == "publish":
        tmpl = AgentTemplate.from_yaml(args.yaml)
        path = reg.publish(tmpl)
        print(f"  Published template {tmpl.name!r} → {path}")

    elif args.templates_cmd == "pull":
        try:
            tmpl = reg.pull(args.name)
            print(tmpl.to_yaml())
        except KeyError as exc:
            print(f"  ERROR: {exc}")
            sys.exit(1)

    elif args.templates_cmd == "search":
        results = reg.search(args.query, top_k=args.top)
        if not results:
            print(f"  No templates matched {args.query!r}")
            return
        print(f"\n  Search results for {args.query!r}:")
        for t in results:
            print(f"  • {t.name} ({t.role}) — {t.description[:60]}")

    elif args.templates_cmd == "delete":
        removed = reg.delete(args.name)
        if removed:
            print(f"  Removed template {args.name!r}")
        else:
            print(f"  Template {args.name!r} not found in registry")
            sys.exit(1)

    elif args.templates_cmd == "share":
        try:
            tmpl = reg.pull(args.name)
            remote_url = getattr(args, "url", "")
            if remote_url:
                from meshflow.registry.templates import MarketplaceClient
                client = MarketplaceClient(remote_url)
                published_url = client.push(tmpl)
                print(f"  Published {tmpl.name!r} → {published_url}")
            else:
                shared_dir = os.path.expanduser("~/.meshflow/shared_templates")
                shared_reg = TemplateRegistry(registry_dir=shared_dir)
                shared_path = shared_reg.publish(tmpl)
                print(f"  Shared {tmpl.name!r} locally → {shared_path}")
                print("  Tip: use --url http://<host>:<port> to push to a remote marketplace.")
        except KeyError as exc:
            print(f"  ERROR: {exc}")
            sys.exit(1)

    elif args.templates_cmd == "load-curated":
        from meshflow.registry.curated_templates import load_curated_library, CURATED_TEMPLATES
        registry_dir = getattr(args, "dir", "") or None
        load_curated_library(registry_dir=registry_dir)
        print(f"\n  Loaded {len(CURATED_TEMPLATES)} curated templates into registry.")
        for t in CURATED_TEMPLATES:
            print(f"  • {t.name:<45} ({t.role})")


# ── marketplace ────────────────────────────────────────────────────────────────


def _cmd_marketplace(args: argparse.Namespace) -> None:
    """Handle `meshflow marketplace` subcommands."""
    import os as _os
    from meshflow.registry.templates import TemplateRegistry, MarketplaceClient
    from meshflow.registry.templates import MarketplaceServer

    if args.marketplace_cmd == "serve":
        registry_dir = getattr(args, "dir", "") or _os.path.expanduser("~/.meshflow/marketplace")
        server = MarketplaceServer(
            registry_dir=registry_dir,
            port=args.port,
            host=args.host,
        )
        server.start(daemon=False)  # blocking — serve until Ctrl-C
        print(f"\n  MeshFlow Marketplace running at http://{args.host}:{args.port}")
        print(f"  Registry: {registry_dir}")
        print("  Press Ctrl-C to stop.\n")
        try:
            import time as _time
            while True:
                _time.sleep(1)
        except KeyboardInterrupt:
            server.stop()
            print("\n  Marketplace stopped.")

    elif args.marketplace_cmd == "push":
        reg = TemplateRegistry()
        try:
            tmpl = reg.pull(args.name)
        except KeyError as exc:
            print(f"  ERROR: template {args.name!r} not found in local registry: {exc}")
            sys.exit(1)
        client = MarketplaceClient(args.url)
        url = client.push(tmpl)
        print(f"  Pushed {tmpl.name!r} → {url}")

    elif args.marketplace_cmd == "pull":
        client = MarketplaceClient(args.url)
        try:
            tmpl = client.pull(args.name)
        except RuntimeError as exc:
            print(f"  ERROR: {exc}")
            sys.exit(1)
        reg = TemplateRegistry()
        reg.publish(tmpl)
        print(f"  Pulled {tmpl.name!r} from {args.url} → local registry")


# ── dashboard ─────────────────────────────────────────────────────────────────


def _cmd_dashboard(args: argparse.Namespace) -> None:
    asyncio.run(_async_dashboard(args))


async def _async_dashboard(args: argparse.Namespace) -> None:
    from meshflow.cli.dashboard import TerminalDashboard

    dash = TerminalDashboard(ledger_db=args.db, limit=args.limit)
    if args.refresh > 0:
        print(f"  [dashboard] Live mode — refreshing every {args.refresh}s. Press Ctrl-C to stop.")
        await dash.watch(interval=args.refresh)
    else:
        await dash.render()


# ── sweep ─────────────────────────────────────────────────────────────────────


def _cmd_sweep(args: argparse.Namespace) -> None:
    asyncio.run(_async_sweep(args))


async def _async_sweep(args: argparse.Namespace) -> None:
    from meshflow.eval.sweep import SweepGrid, WorkflowSweep

    params: dict[str, list] = {}
    if args.task:
        params["task"] = [args.task]
    if args.models:
        params["model"] = list(args.models)

    if not params:
        print("  [sweep] Provide at least --task or --models to define a grid.")
        sys.exit(1)

    grid = SweepGrid(**params)
    print(f"  [sweep] Grid: {len(grid)} variant(s) — yaml={args.yaml!r}")

    sweep = WorkflowSweep(
        workflow_yaml=args.yaml,
        grid=grid,
        task=args.task or "Execute workflow",
        concurrency=args.concurrency,
        ledger_db=args.db,
    )

    def _progress(done: int, total: int) -> None:
        print(f"  [sweep] {done}/{total} variants complete", end="\r", flush=True)

    results = await sweep.run(progress_callback=_progress)
    print()
    print(results.comparison_table())
    print(f"\n  [sweep] Total wall time: {results.total_duration_s:.1f}s")


# ── lint ──────────────────────────────────────────────────────────────────────


def _cmd_lint(args: argparse.Namespace) -> None:
    from meshflow.core.lint import lint_workflow_yaml

    issues = lint_workflow_yaml(args.yaml)

    if args.as_json:
        print(json.dumps([
            {"severity": i.severity, "path": i.path, "message": i.message,
             "suggestion": i.suggestion}
            for i in issues
        ], indent=2))
    else:
        errors   = [i for i in issues if i.severity == "error"]
        warnings = [i for i in issues if i.severity == "warning"]
        infos    = [i for i in issues if i.severity == "info"]
        status = "PASS" if not errors else "FAIL"
        print(f"\n  [{status}] {args.yaml}")
        for issue in issues:
            print(str(issue))
        print(f"\n  {len(errors)} error(s), {len(warnings)} warning(s), {len(infos)} info(s)")

    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    if errors or (args.strict and warnings):
        sys.exit(1)


# ── diff ──────────────────────────────────────────────────────────────────────


def _cmd_diff(args: argparse.Namespace) -> None:
    from meshflow.core.diff import workflow_diff

    result = workflow_diff(args.yaml_a, args.yaml_b)

    if args.as_json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(result.summary())


# ── zt-audit ──────────────────────────────────────────────────────────────────

_ZT_PILLAR_CONTROLS: dict[str, list[str]] = {
    "Identity & Authentication": [
        "crypto_identity", "short_lived_tokens", "require_mtls", "hardware_bound",
    ],
    "Privilege Management": [
        "deny_by_default", "abac_context", "jit_privilege", "continuous_auth",
    ],
    "Resource Isolation": [
        "identity_isolation", "sandboxed_execution", "hardware_isolation",
    ],
    "Observability & Audit": [
        "action_logging", "immutable_logs", "otel_tracing", "siem_streaming", "full_provenance",
    ],
    "Behavioral Monitoring": [
        "behavior_baseline", "anomaly_detection", "auto_containment", "ml_behavioral",
    ],
    "Input / Output Controls": [
        "input_validation", "injection_detection", "spotlighting",
        "output_pii_filter", "hitl_high_risk",
    ],
    "Supply Chain": [
        "ai_bom", "dependency_audit", "supply_chain_verify",
    ],
    "Configuration Integrity": [
        "config_version_control", "config_signing", "immutable_infra",
    ],
    "Governance": [
        "policy_documentation", "formal_governance", "automated_compliance",
    ],
}


def _cmd_zt_audit(args: argparse.Namespace) -> None:
    """Score a MeshFlow deployment against the Zero Trust for AI Agents framework."""
    from meshflow.zero_trust.policy import ZeroTrustPolicy, ZeroTrustTier

    tier_map = {
        "foundation": ZeroTrustTier.FOUNDATION,
        "enterprise":  ZeroTrustTier.ENTERPRISE,
        "advanced":    ZeroTrustTier.ADVANCED,
    }

    if args.regulation:
        policy = ZeroTrustPolicy.for_regulation(args.regulation)
    else:
        policy = ZeroTrustPolicy.for_tier(tier_map[args.tier])

    enabled  = set(policy.controls_enabled())
    disabled = policy.controls_disabled()
    total    = len(enabled) + len(disabled)
    score    = int(100 * len(enabled) / max(total, 1))

    if args.as_json:
        print(json.dumps({
            "tier":             policy.tier.value,
            "regulation":       policy.regulation or None,
            "score_pct":        score,
            "controls_enabled": sorted(enabled),
            "controls_gap":     sorted(disabled),
            "pillars": {
                pillar: {
                    ctrl: ctrl in enabled
                    for ctrl in controls
                }
                for pillar, controls in _ZT_PILLAR_CONTROLS.items()
            },
        }, indent=2))
        if args.fail_on_gaps and disabled:
            sys.exit(1)
        return

    # Human-readable output
    tier_label = policy.tier.value.upper()
    reg_label  = f"  ({policy.regulation.upper()} preset)" if policy.regulation else ""
    bar        = "█" * (score // 5) + "░" * (20 - score // 5)
    verdict    = "PASS" if not disabled else "GAP"
    color_open  = "\033[92m" if verdict == "PASS" else "\033[93m"
    color_close = "\033[0m"

    print(f"\n  MeshFlow Zero Trust Audit — {tier_label}{reg_label}")
    print(f"  Score: {color_open}{score:3d}%  [{bar}]  {verdict}{color_close}")
    print(f"  {len(enabled)} / {total} controls active\n")

    # Build a set of what the TARGET tier expects (to distinguish tier-gaps vs out-of-scope)
    target_policy = ZeroTrustPolicy.for_tier(tier_map[args.tier]) if not args.regulation else policy
    target_enabled = set(target_policy.controls_enabled())

    for pillar, controls in _ZT_PILLAR_CONTROLS.items():
        active      = [c for c in controls if c in enabled]
        tier_gaps   = [c for c in controls if c not in enabled and c in target_enabled]
        out_of_scope = [c for c in controls if c not in enabled and c not in target_enabled]
        in_scope    = len(active) + len(tier_gaps)
        pillar_pct  = int(100 * len(active) / max(in_scope, 1)) if in_scope else 100
        status = "✅" if not tier_gaps else "⚠️ "
        print(f"  {status} {pillar}  ({pillar_pct}%)")
        for ctrl in active:
            print(f"      ✓  {ctrl.replace('_', ' ')}")
        for ctrl in tier_gaps:
            print(f"      ✗  {ctrl.replace('_', ' ')}  ← gap for {tier_label}")
        for ctrl in out_of_scope:
            print(f"      ·  {ctrl.replace('_', ' ')}  (higher tier)")

    if disabled:
        print(f"\n  {len(disabled)} control(s) not yet active for {tier_label} target:")
        for ctrl in sorted(disabled):
            print(f"    • {ctrl.replace('_', ' ')}")
        print(f"\n  Enable with: ZeroTrustPolicy.for_tier(ZeroTrustTier.{tier_label})")
    else:
        print(f"\n  All {tier_label} controls active — deployment meets {tier_label} Zero Trust standard.")

    if args.fail_on_gaps and disabled:
        sys.exit(1)


# ── red-team ──────────────────────────────────────────────────────────────────


def _cmd_red_team(args: argparse.Namespace) -> None:
    """Adversarial red-team testing of an agent pipeline."""
    import asyncio as _asyncio
    from meshflow.security.red_team import RedTeamSuite

    categories = args.categories or None

    # Build a minimal mock agent when no config is provided
    agent: object
    if args.config:
        try:
            from meshflow.core.config import load as _load_cfg
            _load_cfg(args.config)
            # Use a simple passthrough wrapper for probing
            class _WFAgent:
                name = args.config
                async def run(self, task: str) -> dict:
                    return {"result": f"[echo] {task[:100]}"}
            agent = _WFAgent()
        except Exception as e:
            print(f"  Could not load config {args.config!r}: {e}")
            print("  Running probes against built-in guardrails only.\n")
            agent = None
    else:
        agent = None

    suite = RedTeamSuite(categories=categories)
    print(f"  Running {len(suite._probes)} red-team probes"
          + (f" in categories: {', '.join(categories)}" if categories else "") + " …\n")

    report = _asyncio.run(suite.run_async(agent))

    if args.as_json or args.output:
        output_data = json.dumps(report.to_dict(), indent=2)
        if args.output:
            with open(args.output, "w") as fh:
                fh.write(output_data)
            print(f"  Report written to {args.output}")
        if args.as_json:
            print(output_data)
    else:
        print(report.summary())

    risk_order = {"low": 0, "medium": 1, "high": 2}
    threshold  = risk_order.get(args.fail_on_risk, 2)
    if risk_order.get(report.risk_level, 0) >= threshold:
        sys.exit(1)


# ── deploy ────────────────────────────────────────────────────────────────────


def _cmd_deploy(args: argparse.Namespace) -> None:
    """Blue/green zero-downtime agent deployments."""
    import asyncio as _asyncio
    from meshflow.deploy.blue_green import BlueGreenRouter, AgentDeployment, DeploymentStore

    store = DeploymentStore()
    router = BlueGreenRouter()

    # Restore any persisted state
    state = store.load()
    if state:
        for slot, dep_data in state.get("slots", {}).items():
            router.register(slot, AgentDeployment(
                name=dep_data.get("name", slot),
                version=dep_data.get("version", "?"),
                config_path=dep_data.get("config_path", ""),
            ))
        router._active_slot = state.get("active_slot", "blue")

    cmd = args.deploy_cmd

    if cmd == "register":
        dep = AgentDeployment(
            name=args.name,
            version=args.version,
            config_path=args.config,
        )
        router.register(args.slot, dep)
        store.save(router)
        print(f"  Registered {args.name} v{args.version} in slot '{args.slot}'")

    elif cmd == "status":
        status = router.status()
        print(f"\n  Active slot:  {status['active_slot']}")
        print(f"  Traffic split: {status['traffic_split']*100:.0f}% to standby\n")
        for slot, dep in status["slots"].items():
            marker = "▶ " if slot == status["active_slot"] else "  "
            print(f"  {marker}[{slot}]  {dep['name']} v{dep['version']}"
                  + (f"  ({dep['config_path']})" if dep['config_path'] else ""))

    elif cmd == "promote":
        if not router._slots:
            print("  No deployments registered. Run 'meshflow deploy register' first.")
            sys.exit(1)
        print(f"  Promoting '{args.slot}' with steps: "
              f"{' → '.join(f'{int(s*100)}%' for s in args.steps)} …\n")
        result = _asyncio.run(router.promote(args.slot, steps=args.steps))
        store.save(router)
        if result.success:
            print(f"  ✅ Promotion complete — active slot: '{result.active_slot}'")
        else:
            print(f"  ❌ Promotion failed — rolled back to '{result.active_slot}'")
            print(f"     Reason: {result.rollback_reason}")
            sys.exit(1)

    elif cmd == "rollback":
        prev = router._active_slot
        new_active = router.rollback()
        store.save(router)
        print(f"  Rolled back: '{prev}' → '{new_active}'")


# ── migrate ───────────────────────────────────────────────────────────────────


def _cmd_migrate(args: argparse.Namespace) -> None:
    """Detect, plan, and apply migration from LangGraph / CrewAI / AutoGen to MeshFlow."""
    from meshflow.migration.detector import ProjectDetector
    from meshflow.migration.transformer import CodeTransformer

    cmd = args.migrate_cmd

    if cmd == "detect":
        _migrate_detect(args, ProjectDetector)

    elif cmd == "plan":
        _migrate_plan(args, ProjectDetector)

    elif cmd == "apply":
        _migrate_apply(args, ProjectDetector, CodeTransformer)


def _migrate_detect(
    args: argparse.Namespace,
    ProjectDetector: Any,  # type: ignore[valid-type]
) -> None:
    detector = ProjectDetector(args.path)
    result = detector.detect()

    fw_str = ", ".join(result.frameworks) if result.frameworks else "none detected"
    print()
    print(f"  MeshFlow Migration — Detection Report")
    print(f"  {'─' * 50}")
    print(f"  Path        : {args.path}")
    print(f"  Frameworks  : {fw_str}")
    print(f"  Python files: {result.file_count}")
    print(f"  Agents found: {result.agent_count}")
    print(f"  Tools found : {result.tool_count}")
    print(f"  Complexity  : {result.complexity}")
    print(f"  Mig. path   : {result.migration_path}")
    print(f"  Est. effort : {result.estimated_effort}")
    print()

    if not result.frameworks:
        print("  No supported frameworks detected.")
        print("  Supported: langgraph, crewai, autogen, openai-agents")
    else:
        print(f"  Next step: meshflow migrate plan --path {args.path}")
    print()


def _migrate_plan(
    args: argparse.Namespace,
    ProjectDetector: Any,  # type: ignore[valid-type]
) -> None:
    detector = ProjectDetector(args.path)
    result = detector.detect()

    fw_str = ", ".join(result.frameworks) if result.frameworks else "none"
    print()
    print(f"  MeshFlow Migration Plan")
    print(f"  {'─' * 50}")
    print(f"  Detected    : {fw_str}")
    print(f"  Complexity  : {result.complexity}")
    print(f"  Path        : {result.migration_path}")
    print(f"  Effort      : {result.estimated_effort}")
    print()

    if result.migration_path == "zero_rewrite":
        print("  Strategy: ZERO REWRITE")
        print("  ─────────────────────────────────────────────")
        print("  MeshFlow ships a StateGraph-compatible shim.")
        print("  1. Run:  meshflow migrate apply --path .")
        print("     Replaces `from langgraph.graph import StateGraph`")
        print("     with     `from meshflow import StateGraph`")
        print("  2. Add governance at the entry point:")
        print("     from meshflow.governance import govern")
        print("     @govern()")
        print("  3. Optionally set a cost cap:")
        print("     policy = Policy(budget_usd=1.00)")
        print()

    elif result.migration_path == "wrapper":
        fw = result.frameworks[0] if result.frameworks else "external"
        fn_map = {
            "langgraph": "from_langgraph(runnable)",
            "crewai":    "from_crewai(crew_agent)",
            "autogen":   "from_autogen(autogen_agent)",
            "openai-agents": "from_callable(fn)",
        }
        fn_call = fn_map.get(fw, "from_callable(fn)")
        print("  Strategy: WRAPPER")
        print("  ─────────────────────────────────────────────")
        print(f"  Wrap your existing {fw} agent in one line:")
        print(f"    from meshflow.agents.adapters import {fn_call.split('(')[0]}")
        print(f"    mf_agent = {fn_call}")
        print()
        print("  Then add governance:")
        print("    from meshflow.governance import govern")
        print("    @govern()")
        print()
        print("  Run: meshflow migrate apply --path . to apply import rewrites.")
        print()

    else:  # native
        print("  Strategy: NATIVE REWRITE")
        print("  ─────────────────────────────────────────────")
        print("  Multi-framework or complex project detected.")
        print("  Recommended steps:")
        print("  1. Read:  docs/migration/ for per-framework guides")
        print("  2. Replace agent definitions with MeshFlow BaseAgent subclasses")
        print("  3. Wire workflows through StepRuntime or Workflow.run()")
        print("  4. Add Policy(budget_usd=...) and @govern() at entry points")
        print()
        print("  Guides:")
        print("    docs/migration/autogen-to-meshflow.md")
        print("    docs/migration/flowise-to-meshflow.md")
        print()

    print(f"  Apply zero-rewrite transforms: meshflow migrate apply --path {args.path}")
    print(f"  Preview only:                  meshflow migrate apply --path {args.path} --dry-run")
    print()


def _migrate_apply(
    args: argparse.Namespace,
    ProjectDetector: Any,  # type: ignore[valid-type]
    CodeTransformer: Any,  # type: ignore[valid-type]
) -> None:

    detector = ProjectDetector(args.path)
    result = detector.detect()

    if not result.frameworks:
        print("\n  No supported frameworks detected. Nothing to apply.\n")
        return

    transformer = CodeTransformer()
    changed_files = 0
    total_changes = 0

    mode = "DRY RUN — " if args.dry_run else ""
    print()
    print(f"  MeshFlow Migration Apply  [{mode}path={args.path}]")
    print(f"  {'─' * 50}")

    for fpath in result.scanned_files:
        try:
            tr = transformer.transform(fpath)
        except Exception as exc:
            print(f"  ! Could not parse {fpath}: {exc}")
            continue

        if not tr.has_changes():
            continue

        changed_files += 1
        total_changes += len(tr.suggested_changes)

        print(f"\n  {fpath}")
        for change in tr.suggested_changes:
            loc = f"line {change.line_number}" if change.line_number else "top of file"
            print(f"    [{change.change_type}] {loc}")
            print(f"      {change.description}")
            if change.original:
                print(f"      - {change.original.strip()}")
            print(f"      + {change.replacement.strip()}")

        if not args.dry_run:
            try:
                tr.apply(dry_run=False)
                print(f"    Written.")
            except Exception as exc:
                print(f"    ! Write failed: {exc}")

    print()
    if changed_files == 0:
        print("  No changes needed — project may already be migrated.")
    elif args.dry_run:
        print(f"  DRY RUN: {total_changes} change(s) across {changed_files} file(s) — nothing written.")
        print(f"  Re-run without --dry-run to apply.")
    else:
        print(f"  Applied {total_changes} change(s) across {changed_files} file(s).")
    print()


# ── test ──────────────────────────────────────────────────────────────────────


def _cmd_test(args: argparse.Namespace) -> None:
    """Run property-based agent tests from the CLI."""
    from meshflow.testing.property_tests import AgentPropertyTest, PropertyTestSuite
    from meshflow.testing.scenario_gen import ScenarioGenerator

    # ── Resolve which properties to run ──────────────────────────────────────
    all_prop_names = [
        "cost_bounded", "output_determinism", "no_pii_leak",
        "blocks_injection", "respects_token_limit", "latency_sla",
        "non_empty_output",
    ]
    requested: list[str] = []
    if getattr(args, "all_properties", False):
        requested = list(all_prop_names)
    elif args.properties:
        requested = list(args.properties)
    else:
        # default: run the safety-critical subset
        requested = ["no_pii_leak", "blocks_injection", "non_empty_output"]

    _prop_factories: dict[str, Any] = {
        "cost_bounded":          lambda: AgentPropertyTest.cost_bounded(args.max_usd),
        "output_determinism":    lambda: AgentPropertyTest.output_determinism(),
        "no_pii_leak":           lambda: AgentPropertyTest.no_pii_leak(),
        "blocks_injection":      lambda: AgentPropertyTest.blocks_injection(),
        "respects_token_limit":  lambda: AgentPropertyTest.respects_token_limit(args.max_tokens),
        "latency_sla":           lambda: AgentPropertyTest.latency_sla(args.max_ms),
        "non_empty_output":      lambda: AgentPropertyTest.non_empty_output(),
    }

    suite = PropertyTestSuite()
    for name in requested:
        suite.add(_prop_factories[name]())

    # ── Build a sandbox agent for YAML-specified agents ───────────────────────
    agent = _resolve_test_agent(args.agent)

    # ── Generate domain inputs ────────────────────────────────────────────────
    gen = ScenarioGenerator()
    inputs = gen.for_domain(args.domain)
    # Include adversarial payloads so injection-related properties have data
    inputs = inputs + gen.adversarial()

    # ── Run ───────────────────────────────────────────────────────────────────
    print(f"\n  MeshFlow Property Test — agent: {args.agent}")
    print(f"  Properties : {', '.join(requested)}")
    print(f"  Domain     : {args.domain}  |  Trials: {args.n_trials}")
    print()

    report = suite.run(agent, inputs=inputs, n_trials=args.n_trials)
    print(report.summary())

    # ── Optional JSON output ──────────────────────────────────────────────────
    if args.output:
        import json as _json
        from pathlib import Path as _Path

        out_path = _Path(args.output)
        out_path.write_text(_json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        print(f"  Report saved to {out_path}\n")

    # ── Exit code ─────────────────────────────────────────────────────────────
    if args.fail_on_any and report.properties_failed > 0:
        sys.exit(1)


def _resolve_test_agent(agent_spec: str) -> Any:
    """Return an agent-like object for the given spec string.

    Supports:
    - "path/to/agent.yaml"  → SandboxProvider echo agent
    - "module:attribute"    → imports module and returns attribute
    - bare name             → returns a named echo agent
    """
    import importlib

    if ":" in agent_spec and not agent_spec.endswith(".yaml"):
        module_path, attr = agent_spec.rsplit(":", 1)
        mod = importlib.import_module(module_path)
        return getattr(mod, attr)

    # For YAML paths or bare names we return a zero-dependency echo agent
    # so the CLI can be used without a real API key.
    agent_name = agent_spec.replace("/", "_").replace(".", "_")

    class _EchoTestAgent:
        name = agent_name

        async def run(self, task: str, _context: dict | None = None) -> dict[str, Any]:
            return {
                "result":            f"[echo] {task[:120]}",
                "cost_usd":          0.0,
                "tokens":            len(task.split()),
                "stated_confidence": 0.9,
            }

    return _EchoTestAgent()


# ── proxy server ──────────────────────────────────────────────────────────────


def _cmd_proxy(args: argparse.Namespace) -> None:
    """Start the MeshFlow HTTP proxy server.

    Routes all traffic to --upstream (default https://api.openai.com).
    Intercepts POST /v1/chat/completions to enforce tool call policy.

    Examples::

        # No policy — audit logging only
        meshflow proxy --port 8080

        # With policy YAML
        meshflow proxy --port 8080 --policy policy.yaml

        # Custom upstream (Azure OpenAI, custom base URL, etc.)
        meshflow proxy --port 8080 --upstream https://my.azure.openai.azure.com

        # Then point any client to the proxy
        OPENAI_BASE_URL=http://localhost:8080/v1 python my_app.py
    """
    from meshflow.proxy.http_server import MeshFlowHTTPProxy

    port = args.port
    host = args.host
    upstream = args.upstream
    policy_file = getattr(args, "policy_file", "").strip()
    agent_id = getattr(args, "agent_id", "http-proxy")

    interceptor = None
    if policy_file:
        import os as _os
        if not _os.path.exists(policy_file):
            print(f"  Policy file not found: {policy_file}")
            sys.exit(1)
        try:
            with open(policy_file) as fh:
                policy_yaml = fh.read()
            from meshflow.policy.engine import PolicyStore, PolicyEngine as _RE
            from meshflow.policy.engine import PolicyLoader
            from meshflow.core.tool_intercept import PolicyToolCallInterceptor
            store = PolicyStore()
            PolicyLoader.from_yaml(policy_yaml, store=store)
            interceptor = PolicyToolCallInterceptor(_RE(store))
            print(f"  Policy loaded: {policy_file}")
        except Exception as e:
            print(f"  Failed to load policy: {e}")
            sys.exit(1)
    else:
        # Default: audit-only interceptor (no rules = allow everything, log all)
        from meshflow.policy.engine import PolicyStore, PolicyEngine as _RE
        from meshflow.core.tool_intercept import PolicyToolCallInterceptor
        store = PolicyStore()
        interceptor = PolicyToolCallInterceptor(_RE(store, audit=False))

    proxy = MeshFlowHTTPProxy(
        port=port,
        host=host,
        upstream=upstream,
        interceptor=interceptor,
        agent_id=agent_id,
    )

    print(f"\n  MeshFlow Proxy listening on http://{host}:{port}/v1")
    print(f"  Upstream: {upstream}")
    print(f"  Policy: {policy_file or '(none — audit logging only)'}")
    print(f"  Press Ctrl+C to stop.\n")
    print(f"  Set in your client:")
    print(f"    OPENAI_BASE_URL=http://{host}:{port}/v1")
    print(f"    OPENAI_API_BASE=http://{host}:{port}/v1   # LangChain\n")

    proxy.serve_forever()
