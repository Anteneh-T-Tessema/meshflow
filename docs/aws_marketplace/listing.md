# MeshFlow — AWS Marketplace Product Listing

---

## Product Title

**MeshFlow — Production-Safe Multi-Agent Orchestration (HIPAA/SOX/GDPR)**

---

## Short Description (250 characters)

The production-safe infrastructure layer for multi-agent AI systems. HIPAA/SOX/GDPR/PCI built-in, SHA-256 tamper-evident audit chain, hard cost caps, 70–85% token savings. 7 lines to ship safely.

---

## Long Description (2000 characters)

MeshFlow is the production-safe infrastructure layer for multi-agent AI systems. While every other framework makes agents easy to prototype, MeshFlow makes them safe to ship — with compliance, governance, and auditability built in as infrastructure, not bolted on as afterthoughts.

**The problem MeshFlow solves:** 79% of enterprises have adopted AI agents, but only 11% run them in production. The gap exists because frameworks treat HIPAA/SOX/GDPR compliance, cost governance, and tamper-evident audit trails as optional extras. MeshFlow treats them as zero-configuration defaults that are always on.

**What every MeshFlow run gets automatically:**

- **SHA-256 tamper-evident audit chain** — every agent step is cryptographically hashed and chained, producing a replay-capable, forensic-grade ledger that satisfies HIPAA §164.312(b) and SOX Section 802 requirements.
- **Hard cost caps** — a `CostCap(usd=5.00)` guardrail hard-stops runaway LLM spend at the infrastructure layer, before your cloud bill escalates.
- **HIPAA/SOX/GDPR/PCI/NERC compliance profiles** — one line activates PII blocking, data residency enforcement, consent tracking, and regulatory logging for the relevant regime.
- **Durable execution** — SQLite, Redis, Postgres, and S3 checkpoint backends give you crash recovery and exactly-once execution semantics across all agent steps.
- **70–85% token cost reduction** — Anthropic prompt caching (`cache_control`), a `ModelRouter` that auto-routes to the cheapest capable model, and a `ContextCompactor` that prunes redundant context before every call.
- **Policy-as-code engine** — DascGate policies enforce security boundaries (PII, toxicity, JSON schema, confidence thresholds) at the step level without modifying agent code.
- **Secret vault** — integrated `VaultStore` with AWS Secrets Manager, HashiCorp Vault, and environment variable backends keeps API keys out of agent code.
- **Sandbox/echo mode** — full workflow execution with zero real token spend, enabling CI/CD pipeline testing at no cost.

MeshFlow wraps LangGraph, CrewAI, and AutoGen — all three execution backends are supported — so existing agent code migrates without rewriting business logic.

**Ideal for:** healthcare AI, financial services automation, legal document processing, government/defense AI, and any regulated-industry team that cannot afford a compliance incident in production.

`pip install meshflow` · Apache 2.0 · Python 3.11+

---

## Highlights

- **Compliance-first by default** — HIPAA, SOX, GDPR, PCI-DSS, and NERC profiles activate with a single line; PII blocking, audit trails, and data residency enforcement are infrastructure, not afterthoughts.
- **SHA-256 tamper-evident audit ledger** — every agent step is cryptographically chained and replay-capable, producing forensic-grade evidence that satisfies regulatory audit requirements out of the box.
- **70–85% LLM cost reduction** — Anthropic prompt caching, intelligent model routing, and context compaction reduce token spend while maintaining full governance and auditability on every call.

---

## Support Information

- **Documentation:** https://meshflow.dev/docs
- **GitHub:** https://github.com/Anteneh-T-Tessema/meshflow
- **Issue Tracker:** https://github.com/Anteneh-T-Tessema/meshflow/issues
- **Community Discussions:** https://github.com/Anteneh-T-Tessema/meshflow/discussions
- **Email Support:** support@meshflow.dev
- **Security Disclosures:** security@meshflow.dev (see SECURITY.md)
- **License:** Apache 2.0 — free to use in commercial production deployments

---

## Pricing Model

### BYOL (Bring Your Own License)

MeshFlow is open-source (Apache 2.0). Customers who bring their own installation (via `pip install meshflow` or self-hosted container) pay only the underlying EC2/infrastructure costs. No per-seat or per-call fees from MeshFlow.

You supply your own Anthropic, OpenAI, or Bedrock API keys. MeshFlow's cost-cap guardrails (`CostCap`) enforce per-run and per-day token spend limits against those keys.

### Hourly Software Fee (SaaS / AMI deployment)

For customers deploying via the MeshFlow AMI on AWS Marketplace:

| Instance Type | vCPU | RAM  | Hourly Software Fee |
|---------------|------|------|---------------------|
| t3.medium     | 2    | 4 GB | $0.05 / hr          |
| t3.large      | 2    | 8 GB | $0.09 / hr          |
| m6i.xlarge    | 4    | 16 GB| $0.18 / hr          |
| m6i.2xlarge   | 8    | 32 GB| $0.32 / hr          |

Infrastructure (EC2, EBS, data transfer) is billed separately at standard AWS rates.

---

## Categories

- Machine Learning
- Developer Tools
- Governance & Compliance
