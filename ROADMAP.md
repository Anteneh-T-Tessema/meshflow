# MeshFlow Roadmap

This is the public roadmap. It is a living document — priorities shift based on community feedback and market signals. The most important source for roadmap input is [GitHub Discussions → Roadmap](https://github.com/Anteneh-T-Tessema/meshflow/discussions/categories/roadmap).

---

## Now — v1.0 (shipped May 2026)

- ✅ 4,405 passing tests, Production/Stable classifier
- ✅ Full LangGraph/CrewAI/AutoGen feature parity
- ✅ SHA-256 tamper-evident audit chain
- ✅ HIPAA/SOX/GDPR/PCI/NERC compliance profiles
- ✅ Subprocess sandbox for code execution
- ✅ DurableWorkflowExecutor (SQLite/Redis/Postgres/S3)
- ✅ Token optimization layer (cache_control + ModelRouter + ContextCompactor)
- ✅ ReplayLedger interactive API (diff/fork/load_state)
- ✅ 85-page documentation site
- ✅ PyPI publish: `pip install meshflow`
- ✅ SKILL.md for 32 AI coding tools

---

## Next — v1.1 (June 2026)

**Theme: Community flywheel**

- ✅ Public agent template gallery (20 curated templates, fork counts, deploy buttons)
- ✅ `meshflow init` CLI scaffolder — production-ready project in 60 seconds
- ✅ Agent Teams config for Claude Code (pre-built `.claude/agents/` folder)
- ✅ Flowise migration guide published (CVE-2025-59528 competitive window)
- ✅ SKILL.md submissions to anthropic/skills and openai/skills official catalogs (`docs/submissions/`)
- ✅ Discord setup guide and launch checklist in docs/community/

---

## Near — v1.2 (July–August 2026)

**Theme: Enterprise readiness**

- ✅ Visual workflow builder — no-code DAG editor for non-technical stakeholders
- ✅ No-code RAG pipeline configurator
- ✅ SOC 2 Type II audit track began June 2026 — controls mapping and evidence templates in `docs/compliance/`
- ✅ AWS Marketplace listing artifacts in docs/aws_marketplace/
- ✅ Partnership application drafted in docs/partnerships/
- ✅ `meshflow Cloud` beta — managed token optimization dashboard (deployed to Vercel)

---

## Shipped — v1.1 / v1.2 / v1.3 (June 2026)

**Theme: Zero Trust + Enterprise ops**

- ✅ Zero Trust for AI Agents — Foundation/Enterprise/Advanced tiers, SpotlightingGuardrail, JIT privileges, AI-BOM, ContinuousAuth (v1.1)
- ✅ `MESHFLOW_ZT_TIER` / `MESHFLOW_ZT_REGULATION` env vars — ops-friendly tier config (v1.2)
- ✅ GitHub Actions ZT gate — reusable composite action with PR step summary (v1.2)
- ✅ MeshFlow Cloud GA — ZT posture telemetry, `/api/zt-status`, Cloud dashboard page (v1.2)
- ✅ SIEM streaming — Splunk HEC, Datadog Logs, generic HTTP (v1.3)
- ✅ Red-team testing — 22 probes, 6 OWASP categories, `meshflow red-team` CLI (v1.3)
- ✅ Blue/green deployments — `BlueGreenRouter`, `meshflow blue-green` CLI (v1.3)
- ✅ GitHub Releases for all three versions with full changelogs (v1.3)

---

## Shipped — v1.9.x (June 2026)

- ✅ **Extended thinking** — `Agent(thinking=True, thinking_budget=N)` for Claude; thinking tokens tracked in StepRecord and RunResult
- ✅ **Always-on prompt caching** — cache_control inserted automatically for Anthropic system prompts ≥ 1 024 chars
- ✅ **Cache hit-rate metrics** — `RunResult.cache_read_tokens` / `cache_creation_tokens`; cloud reporter wired to real data
- ✅ **Mixed-model pipelines** — `ModelTierRouter` + `ModelTier`; `model_is_local()` utility; zero cost for local calls; `RunResult.agent_costs` + `cloud_agents`
- ✅ **CI green on Python 3.11 + 3.12** — 4,778 tests passing; all CI gates green
- ✅ **PyPI 1.9.3 + GHCR image** — `pip install meshflow` and `ghcr.io/anteneh-t-tessema/meshflow-mcp:1.9.3`
- ✅ **Workflow cost estimation** — `Workflow.estimate_cost(task)` dry-run before spending tokens
- ✅ **`meshflow cost-report`** — per-agent cost table in CLI; estimation mode + ledger mode
- ✅ **User-configurable `is_local`** — `ModelTier(is_local=True/False)` for custom Ollama models, LiteLLM proxies, corporate fine-tunes
- ✅ **`ModelRegistry` + `DEFAULT_REGISTRY`** — register model metadata once (locality, cost rates, quality, latency); used by `estimate_cost`, `AdaptiveModelTierRouter`, and routing report
- ✅ **`TaskScorer`** — 5-factor composite routing score (length + question density + conjunction density + technical terms + tool pressure × task-type multiplier); replaces raw char count
- ✅ **`AdaptiveModelTierRouter`** — self-improving router: routes by composite score, epsilon-greedy exploration with annealing, auto-adapts thresholds every N runs from CONFIDENCE feedback; `explain()`, `stats()`, `report()`
- ✅ **`CascadeRouter`** — FrugalGPT pattern: start cheap, escalate to next tier on low CONFIDENCE; `Agent(cascade_threshold=0.65)` triggers automatic retry within `step()`
- ✅ **Router persistence** — `router.save(path)` / `AdaptiveModelTierRouter.load(path)` (JSON); `router.to_yaml(path)` / `AdaptiveModelTierRouter.from_yaml(path)` for version-controlled config; `RouterOutcomeStore.export_csv(path)`
- ✅ **`meshflow routing-report`** — CLI: tier distribution, cost savings vs. always-large, adaptation history; `--state`, `--export`, `--json` flags
- ✅ **4,963 tests passing** — 81–85 sprints; CI green on Python 3.11 + 3.12

## Shipped — v1.15.0 (June 2026)

**Theme: Deep guardrails + audit integrity + collusion hardening**

- ✅ **Guardrail engine integration** — `LangGraphGuardCallback`, `CrewAIGuardCallback`, `_register_autogen_guard` auto-injected when Guardian is present in runtime context
- ✅ **`PromptSafetyCache`** — thread-safe LRU cache (1,000 entries) for `InjectionScanner.scan()`, eliminates redundant regex scans
- ✅ **Merkle tree audit chain** — `_build_merkle_tree()` / `_verify_merkle_proof()` for O(log n) per-entry verification
- ✅ **Batch ledger writes** — `write_batch()` on SQLite and Postgres backends; `ReplayLedger(enable_batching=True)` with async flush worker
- ✅ **Collusion detection v2** — Shannon entropy profiling, bigram perplexity tracker, role-aware sensitivity factors
- ✅ **Wasm policy engine** — `WasmPolicyEngine` loads OPA/Rego or Rust bytecode; supports wasmtime + wasmer; graceful fallback
- ✅ **Cloud GET methods** — `get_policy()`, `get_model_routers()`, `ZeroTrustOrchestrator.from_cloud()`
- ✅ **Auto skill detection** — `detect_skills(text)` infers relevant built-in skills from task descriptions
- ✅ **`EvalSuite.from_dataset_hub()`** — pull eval datasets from MeshFlow Cloud
- ✅ **5,888 tests passing** — CI green

---

## Shipped — v1.14.0 (June 2026)

**Theme: Cloud platform SDK parity — every dashboard feature backed by an SDK call**

- ✅ **PromptHub** — `PromptHub.get/push/list` — pull versioned prompts at runtime with 60s TTL cache; `POST /api/ingest/prompts` with API-key auth
- ✅ **DatasetHub** — `DatasetHub.push/pull/list/delete` — eval dataset management SDK; `GET/POST/DELETE /api/ingest/datasets`
- ✅ **CloudAgentRegistry** — `CloudAgentRegistry.register/record_run/list/get` — `GET/POST /api/ingest/agents`; auto-bump run counter from `instrument(register_agents=True)`
- ✅ **`instrument()` fixed + span telemetry** — was broken (wrong API); now injects duck-typed queue into `WorkflowEventBus._queues`; sends per-step trace spans to `/api/ingest/spans` on WORKFLOW_COMPLETE
- ✅ **`MeshFlowCloud.report_spans()`** — batch span ingest; also async variant `areport_spans()`
- ✅ **TypeScript SDK v1.14.0** — 14 new cloud ingest methods: `reportRun`, `reportEval`, `reportMcpCall`, `reportWorkerJob`, `reportSpans`, `promptGet/Push/List`, `datasetPush/Pull/List/Delete`, `registerAgent/recordAgentRun/listAgents/getAgent`
- ✅ **Go SDK v1.14.0** — same 14 methods; new `CloudSpanInput`, `CloudEvalInput`, `CloudDatasetRow`, `CloudAgentDefinition` types; `cloudDo()` helper with `x-meshflow-key` auth
- ✅ **Rust SDK v1.14.0** — same 14 methods added in `client.rs`; new types in `types.rs`
- ✅ **5,816 tests passing** — 49 new tests for all cloud SDK surfaces; CI green on Python 3.11 + 3.12

---

## Shipped — v1.10–v1.13.0 (June 2026)

**Theme: Claude ecosystem parity + Forensic audit + Competitive positioning**

- ✅ **AdvisorAgent + AdvisorRouter** — Anthropic advisor-tool pattern; high-intelligence advisor + cost-efficient executor; complexity-based routing
- ✅ **BudgetConfig / ThinkingBudget / EffortBudget** — fine-grained token and effort budgets enforced in StepRuntime; `BudgetViolation` on cap breach
- ✅ **DynamicWorkflow + DynamicCoordinator** — runtime agent spawning; coordinator watches step output and spawns specialists on keyword match
- ✅ **ContextCompactor** — Claude-native, sliding-window, and summary compaction strategies; `compact()` returns `(messages, CompactionStats)`
- ✅ **Tool streaming** — `ToolStreamEvent` hierarchy in `meshflow.streaming.tool_stream`; `stream_tool_calls()` async generator; SSE helpers
- ✅ **meshflow-forensic** — standalone pip package (`pip install meshflow-forensic`); `DascGate`, `AuditLedger`, `ForensicReport`, `EUAIActChecker`, `TaintGraph`; zero runtime deps
- ✅ **SOC2Checker** — programmatic SOC 2 Type II controls check; 18 controls across all 5 TSC categories; `to_json()` + `print_summary()`
- ✅ **CostRegressionGate** — `CostRegressionError` raised in CI when per-run cost exceeds baseline; `record()` + `check()` API
- ✅ **Competitive benchmarks** — `benchmarks/competitive_bench.py`; MeshFlow vs LangGraph / CrewAI / AutoGen on RPS, latency, governance overhead
- ✅ **AutoGen 0.4+ parity** — `AssistantAgent`, `UserProxyAgent`, `SocietyOfMind`, `MagenticOne`, `AgentRuntime`, topic pub/sub, termination conditions
- ✅ **OpenAI Agents SDK parity** — `Agent`, `Runner`, `handoff`, `AgentHooks`, `guardrails`, `FunctionTool`, `as_tool()`
- ✅ **5,711 tests passing** — CI green on Python 3.11 + 3.12
- ✅ **Multi-platform publish** — PyPI `meshflow==1.13.0`, PyPI `meshflow-forensic==1.0.0`, npm `meshflow-sdk@1.13.0`, GHCR `meshflow-mcp:1.13.0`, Go `sdks/go/v1.13.0`

---

## Next — v1.4 (June–July 2026)

**Theme: Launch + ecosystem growth**

- [ ] Show HN post — `docs/launch/show_hn.md` ready
- [ ] Product Hunt post — `docs/launch/product_hunt.md` ready
- [ ] Discord server live — `docs/community/discord_setup.md` + launch checklist ready
- [ ] Show HN post — `docs/launch/show_hn.md` ready
- [ ] Product Hunt post — `docs/launch/product_hunt.md` ready
- [ ] Discord server live
- [ ] Smithery listing — `smithery.yaml` at v1.14.0, submit at smithery.ai
- [ ] Rust SDK on crates.io — `CARGO_REGISTRY_TOKEN` needed; v1.14.0 ready to publish
- [ ] npm publish — `meshflow-sdk@1.14.0` TypeScript SDK ready
- ✅ Go SDK v1.14.0 — `go get github.com/Anteneh-T-Tessema/meshflow/sdks/go@v1.14.0`
- ✅ `RedisMemoryBackend` — TTL, key prefix, multi-tenant
- ✅ `FileMemoryBackend` — zero-dep JSON files, atomic writes

---

## Later — v1.5+ (Q3–Q4 2026)

**Theme: Market leadership**

- [ ] SOC 2 Type II report issued and published
- ✅ MeshFlow Cloud GA — ModelRouter analytics, cost regression CI gate, cross-session memory analytics
- [ ] MeshFlow Sessions — annual developer event
- ✅ Analyst briefing kit in docs/analyst_briefing/
- [ ] Fundraising process (if required)
- [ ] deepset (Haystack) co-marketing partnership
- ✅ Confidence threshold early exit (`stop_on_confidence` on Team and Crew)
- ✅ Parallel context dedup across crew agents

---

## Not planned

Items we have deliberately chosen not to build:

- **A visual-first, no-code-primary product.** MeshFlow is code-first. The visual builder is an addition, not the foundation.
- **Hosted model serving.** We are framework infrastructure, not a model provider.
- **A fork of LangGraph or CrewAI.** We wrap them. We don't replace them.

If you disagree with any of these decisions, the right place to argue the case is [GitHub Discussions → RFCs](https://github.com/Anteneh-T-Tessema/meshflow/discussions/categories/rfcs).

---

## How to influence the roadmap

1. **Vote on existing discussions.** Thumbs-up on the issues and RFCs that matter most to you.
2. **File a compliance gap.** If a regulatory requirement isn't covered, it goes to the top of the queue.
3. **Submit an RFC.** Major new features need an RFC before implementation begins.
4. **Tell us your production scenario.** The most persuasive argument for prioritization is "this is blocking us from shipping to production."

The roadmap is driven by one question: **what does it take for a Fortune 500 engineering team to trust MeshFlow with their production agent workloads?**
