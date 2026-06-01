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
- [ ] SKILL.md submissions to anthropics/skills and openai/skills official catalogs
- [ ] Discord community launch

---

## Near — v1.2 (July–August 2026)

**Theme: Enterprise readiness**

- ✅ Visual workflow builder — no-code DAG editor for non-technical stakeholders
- ✅ No-code RAG pipeline configurator
- [ ] SOC 2 Type II audit track begins (6-month program)
- [ ] AWS Marketplace listing (`pip install meshflow` from Marketplace)
- [ ] Anthropic "Built with Claude" partnership application
- ✅ `meshflow Cloud` beta — managed token optimization dashboard (deployed to Vercel)

---

## Later — v1.3+ (Q3–Q4 2026)

**Theme: Market leadership**

- [ ] SOC 2 Type II report issued and published
- [ ] MeshFlow Cloud GA — ModelRouter analytics, cost regression CI gate, cross-session memory analytics
- [ ] MeshFlow Sessions — annual developer event
- [ ] Gartner / Forrester analyst briefings
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
