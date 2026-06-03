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

## Next — v1.4 (June–July 2026)

**Theme: Multi-language ecosystem + Enterprise auth + Observability**

- ✅ Go SDK — `go get meshflow.dev/go-sdk` (in progress)
- ✅ OIDC/SSO middleware — Okta, Auth0, Azure AD, Google Workspace (in progress)
- ✅ Show HN launch — `docs/launch/show_hn.md` ready to post (v1.10.0 cascade router story)
- ✅ Product Hunt launch — `docs/launch/product_hunt.md` ready to post (v1.10.0)
- ✅ `RedisMemoryBackend` — TTL, key prefix, multi-tenant; `pip install redis`
- ✅ `FileMemoryBackend` — zero-dep JSON files, atomic writes, path-traversal-safe
- [ ] Discord community launch — `docs/community/discord_setup.md` ready

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
