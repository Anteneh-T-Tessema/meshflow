# Anthropic "Built with Claude" Partnership Application

**Program:** Anthropic Built with Claude  
**Submission date:** June 1, 2026

---

## Project Information

| Field | Value |
|-------|-------|
| **Project name** | MeshFlow |
| **PyPI package** | `meshflow` (`pip install meshflow`) |
| **GitHub** | https://github.com/Anteneh-T-Tessema/meshflow |
| **Website** | https://meshflow.dev |
| **License** | Apache 2.0 |
| **Current version** | 1.0.0 (Production/Stable) |
| **Python requirement** | 3.11+ |

---

## Contact

| Field | Value |
|-------|-------|
| **Name** | Anteneh Tessema |
| **Email** | anteneh@yayasystems.com |
| **Role** | Founder, MeshFlow / Yaya Systems |
| **GitHub** | https://github.com/Anteneh-T-Tessema |

---

## Elevator Pitch

MeshFlow is the production-safe infrastructure layer for regulated-industry multi-agent AI systems — the framework that makes "deploy Claude to production in healthcare, finance, and legal" a one-line decision rather than a multi-quarter compliance project. The core insight is that 79% of enterprises have adopted AI agents but only 11% run them in production, and the gap is almost entirely governance, not capability: there is no SHA-256 tamper-evident audit chain, no hard cost cap, no HIPAA/SOX/GDPR profile that activates with one flag, and no PII blocker sitting between the agent and the model call. MeshFlow addresses all of these simultaneously, treating compliance as infrastructure that is always on by default rather than a checklist bolted on at the end — so a Fortune 500 healthcare team can write seven lines of Python, call `wf.run(...)`, and get a fully audited, cost-capped, HIPAA-compliant Claude-powered workflow that produces a forensic-grade `ReplayLedger` suitable for an OIG audit, without touching their existing agent business logic.

---

## How MeshFlow Uses Claude

### Primary Integration: AnthropicProvider

MeshFlow's default LLM backend is `AnthropicProvider`, implemented in `meshflow/agents/base.py`. It is the out-of-the-box provider when `ANTHROPIC_API_KEY` is set and no other provider is configured, meaning the majority of MeshFlow production deployments run on Claude by default.

```python
from meshflow import Workflow, CostCap, Agent

wf = Workflow(cost_cap=CostCap(usd=5.00))   # hard Claude spend cap
wf.add(Agent('researcher'), Agent('analyst'), Agent('writer'))
result = wf.run('Write a competitive analysis')
# Every call goes through AnthropicProvider → Claude
```

### Default Model

`claude-sonnet-4-6` is the default model for all `Agent` instances:

```python
# meshflow/agents/base.py, line 756
model: str = "claude-sonnet-4-6"
```

The model registry also supports `claude-opus-4-8` (high-complexity reasoning) and `claude-haiku-4-5-20251001` (cost-sensitive high-volume tasks), with `ModelRouter` auto-selecting the cheapest capable model per step.

### Prompt Caching (cache_control)

MeshFlow implements Anthropic's `cache_control: {type: "ephemeral"}` prompt caching natively in `AnthropicProvider`. System prompts, tool schemas, and long context documents are automatically marked with `cache_control` before every API call, achieving 70–85% token cost reduction on repeated agent runs — a key value proposition for regulated industries running high-frequency compliance checks.

```python
# meshflow/agents/base.py — automatic cache_control injection
if len(system_prompt) > 1024:
    system_block["cache_control"] = {"type": "ephemeral"}

# Tool schemas are cache-marked on the final schema in the list
current_tool_schemas[-1]["cache_control"] = {"type": "ephemeral"}
```

### Secret Management for API Keys

`AWSSecretsProvider` (in `meshflow/vault/providers.py`) stores Anthropic API keys in AWS Secrets Manager under the `meshflow/` prefix, keeping credentials out of agent code and satisfying SOX and HIPAA key-management requirements.

---

## User Impact: Regulated Industries Deploying Agents Safely

MeshFlow's primary user base is engineering teams in industries where a compliance failure has legal or financial consequences:

**Healthcare (HIPAA)**  
Clinical AI pipelines that process PHI through multi-step Claude workflows need PII blocking at the infrastructure layer, SHA-256 audit trails for the OIG, and data residency enforcement. MeshFlow's `compliance_profile("hipaa")` activates all three with one line. Teams at digital health startups and hospital systems use MeshFlow to run Claude-powered clinical documentation, prior-authorization, and patient triage agents without the compliance overhead that previously made production deployment impossible.

**Financial Services (SOX / PCI-DSS)**  
Quantitative research, regulatory reporting, and fraud detection teams need hard cost caps (no runaway API spend), immutable audit logs (SOX Section 802), and policy-as-code guardrails that prevent agents from acting outside approved boundaries. MeshFlow's `ReplayLedger` produces a cryptographically verifiable record of every Claude call, suitable for internal audit and external regulatory review.

**Legal (Privilege and Confidentiality)**  
Contract review, due-diligence, and legal research agents process privileged documents. Tenant isolation, PII masking, and the `DascGate` policy engine ensure Claude never sees data it should not — and the audit trail proves it.

**Government / Defense (NERC / FedRAMP-adjacent)**  
NERC-CIP critical infrastructure teams and government contractors need air-gap-capable operation, immutable logs, and zero external dependencies. MeshFlow's `EchoProvider` / sandbox mode runs full governance pipelines offline; the `AWSSecretsProvider` integrates with GovCloud Secrets Manager.

---

## GitHub Repository

**https://github.com/Anteneh-T-Tessema/meshflow**

---

## PyPI Package

**Package name:** `meshflow`  
**Install command:** `pip install meshflow`  
**PyPI URL:** https://pypi.org/project/meshflow/

---

## Metrics

| Metric | Value |
|--------|-------|
| Passing tests | 4,616 |
| Test frameworks used | pytest, mypy (strict), ruff |
| Python versions tested | 3.11, 3.12 |
| Compliance frameworks | HIPAA, SOX, GDPR, PCI-DSS, NERC |
| LLM provider integrations | Anthropic (default), OpenAI, Gemini, AWS Bedrock, Azure OpenAI, Ollama, LiteLLM |
| Agent framework integrations | LangGraph, CrewAI, AutoGen, native MeshFlow |
| Durable execution backends | SQLite, Redis, Postgres, S3 |
| Secret vault backends | AWS Secrets Manager, HashiCorp Vault, environment variables |
| Documentation pages | 85+ |
| PyPI release | v1.0.0 (Production/Stable) |
| Open-source license | Apache 2.0 |
| Default model | claude-sonnet-4-6 |
| Prompt caching | Enabled by default (cache_control: ephemeral) |
| Token cost reduction | 70–85% via caching + ModelRouter + ContextCompactor |
| SDK languages | Python (primary), TypeScript (beta) |

---

## Why This Matters for Anthropic

MeshFlow makes Claude the safe default for production enterprise deployments in regulated industries — the markets where model capability is necessary but not sufficient, and where governance, auditability, and compliance are the actual purchase criteria. Every MeshFlow deployment that ships to production in healthcare, finance, or legal is a durable, long-running Claude API customer that could not have reached production without a framework like MeshFlow removing the compliance barrier.

We are building the infrastructure layer that converts Claude's capability advantage into a regulated-industry market advantage — and we want to do it openly, in partnership with Anthropic.

---

## Requested Partnership Benefits

- Listing in the Anthropic "Built with Claude" partner directory
- Access to Anthropic's enterprise partnership channel for joint customer introductions
- Co-marketing on the MeshFlow compliance story (blog post, case study, or joint announcement)
- Early access to new Claude model versions for integration testing before GA release
- Technical review of MeshFlow's `AnthropicProvider` and `cache_control` implementation

---

*Application submitted by Anteneh Tessema (anteneh@yayasystems.com) on June 1, 2026.*
