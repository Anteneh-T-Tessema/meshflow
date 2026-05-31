# CLI Reference

```bash
meshflow <command> [options]
```

## Workflow

| Command | Description |
|---------|-------------|
| `meshflow run <yaml>` | Run a workflow YAML to completion |
| `meshflow stream <yaml>` | Stream governed events from a workflow |
| `meshflow describe <yaml>` | Print workflow topology (nodes, edges, compliance) |
| `meshflow lint <yaml>` | Static-validate a workflow YAML before running |
| `meshflow diff <yaml_a> <yaml_b>` | Compare two workflow YAML topologies |
| `meshflow resume <run_id>` | Resume a paused workflow |
| `meshflow sweep <yaml>` | Run a workflow across a parameter grid |

```bash
meshflow run workflow.yaml --input "Summarize AI safety research"
meshflow run workflow.yaml --kind crew   # force crew dispatch
meshflow lint workflow.yaml
```

## Agents & Sessions

| Command | Description |
|---------|-------------|
| `meshflow init` | Scaffold a new governed agent project |
| `meshflow new agent <name>` | Generate a new agent file |
| `meshflow new team <name>` | Generate a new team file |
| `meshflow agent-serve <yaml>` | Serve a single agent over A2A HTTP |

## Observability

| Command | Description |
|---------|-------------|
| `meshflow logs [--db]` | Show recent run history |
| `meshflow runs [--db]` | List recent run IDs (alias for logs) |
| `meshflow watch <run_id>` | Tail live events for a workflow run |
| `meshflow replay <run_id>` | Step-through debugger for a past run |
| `meshflow replay <run_id> --diff <run_b>` | Diff two runs |
| `meshflow replay <run_id> --fork-at <N>` | Fork run at step N |
| `meshflow replay <run_id> --rewind <N>` | Re-run from step N |
| `meshflow replay <run_id> --branch-compare` | Fork with multiple configs |
| `meshflow trace <run_id>` | View run trace in terminal |
| `meshflow trace-server` | Start visual trace studio UI |
| `meshflow graph <run_id>` | Export execution graph (Mermaid/DOT) |

## Evaluation

| Command | Description |
|---------|-------------|
| `meshflow eval run <yaml>` | Run eval suite against an agent |
| `meshflow eval run <yaml> --save-baseline <file>` | Save baseline |
| `meshflow eval run <yaml> --compare-baseline <file> --fail-on-regression` | CI gate |
| `meshflow eval-history [--suite <name>]` | List stored eval results |
| `meshflow eval-diff <baseline_a> <baseline_b>` | Compare two baselines |
| `meshflow eval-feedback` | Show aggregated human feedback statistics |

## Governance

| Command | Description |
|---------|-------------|
| `meshflow compliance report` | Generate a compliance report |
| `meshflow compliance schedule add/list/run/remove` | Scheduled reports |
| `meshflow audit export <run_id>` | Export audit trail as CSV/JSON |
| `meshflow snapshot export [--out <file>]` | ZIP compliance artifact bundle |
| `meshflow policy add/list/enable/disable/evaluate` | Policy-as-code management |
| `meshflow sla define/stats/breaches/list` | SLA contract management |
| `meshflow dasc classify/ledger/verify/taint` | DASC risk governance |

## Security

| Command | Description |
|---------|-------------|
| `meshflow vault store/retrieve/rotate/delete/list/audit` | Secret vault management |
| `meshflow keys generate/list/revoke` | API key management |
| `meshflow identity register/list/revoke` | Agent identity management |
| `meshflow security scan <text>` | Scan text for PII/injection/secrets |

## Tenants & Multi-tenancy

| Command | Description |
|---------|-------------|
| `meshflow tenant create/list/get/suspend/plan` | Tenant management |
| `meshflow lineage show <run_id>` | Data lineage graph |

## Infrastructure

| Command | Description |
|---------|-------------|
| `meshflow serve` | Start HTTP API server |
| `meshflow dev` | Start server in dev mode (colored output) |
| `meshflow doctor` | Pre-deploy environment health check |
| `meshflow env <yaml>` | Generate .env file for deployment |
| `meshflow deploy` | Build and run via Docker |
| `meshflow dashboard` | Terminal cost/metrics dashboard |
| `meshflow bench` | Run performance benchmarks |
| `meshflow worker start` | Start distributed task worker |

## Configuration & Integrations

| Command | Description |
|---------|-------------|
| `meshflow schema` | Print public JSON Schema contracts |
| `meshflow codegen <yaml>` | Generate Go/Java/C# SDK from workflow |
| `meshflow mcp serve <yaml>` | Serve workflow tools as MCP server |
| `meshflow plugins list/verify/info` | Plugin management |
| `meshflow templates list/pull/push` | Agent template registry |
| `meshflow marketplace` | Template marketplace |

## Resilience & Rate Limiting

| Command | Description |
|---------|-------------|
| `meshflow ratelimit add/list/remove` | Rate limit policy management |
| `meshflow circuit list/reset/stats` | Circuit breaker management |
| `meshflow circuit breaker show <model>` | Show breaker state |
| `meshflow canary add/list/stats` | Canary router management |
| `meshflow flags add/list/enable/disable/evaluate` | Feature flags |

## Scheduling & Queues

| Command | Description |
|---------|-------------|
| `meshflow schedule add/list/run/remove` | Cron-scheduled tasks |
| `meshflow queue stats/list/retry/purge` | Background task queue |
| `meshflow webhooks add/list/remove/replay` | Webhook management |
| `meshflow alerts add/list/remove/fire` | Alert engine management |

## Budget & Costs

| Command | Description |
|---------|-------------|
| `meshflow budget set/status/reset` | Per-agent cost budgets |
| `meshflow analytics [--metric full\|cost\|latency]` | Run analytics |
| `meshflow export-traces [--format openai\|anthropic\|jsonl]` | Fine-tuning export |

## Tracing & Memory

| Command | Description |
|---------|-------------|
| `meshflow tracing show/run/count` | Distributed trace inspection |
| `meshflow memory list/search/clear` | Semantic memory management |

## Global flags

| Flag | Description |
|------|-------------|
| `--db <path>` | Ledger database path (default: `meshflow_runs.db`) |
| `--tenant <id>` | Active tenant ID |
| `--json` | Output as JSON |
| `--help` | Show help for any command |
