# Migrating from Flowise to MeshFlow

**CVE-2025-59528 (CVSS 10.0) has left 12,000 Flowise instances under active exploitation as of 2026.** If you're running Flowise in production, this guide shows you how to migrate your workflows to MeshFlow — a security-first, compliance-ready alternative that gives you more capability, not less.

---

## Why migrate now

| | Flowise | MeshFlow |
|--|--|--|
| CVE-2025-59528 (RCE, CVSS 10.0) | Unpatched on 12K instances | Not affected — no legacy parser |
| SOC 2 / HIPAA compliance | ✗ | ✅ built-in |
| Tamper-evident audit chain | ✗ | ✅ SHA-256 hash chain |
| Cost caps | ✗ | ✅ `CostCap(usd=5.00)` |
| Durable execution (crash recovery) | ✗ | ✅ SQLite/Redis/Postgres/S3 |
| Policy-as-code | ✗ | ✅ YAML policy engine |
| Secret vault | ✗ | ✅ Fernet AES + PBKDF2 |
| Python SDK | ✗ (visual only) | ✅ full Python + YAML |
| Open-source license | Apache 2.0 | Apache 2.0 |

---

## Install MeshFlow

```bash
pip install meshflow
# No API key required for your first run — use sandbox mode
```

---

## Concept mapping

| Flowise | MeshFlow | Notes |
|---------|----------|-------|
| Flow / Chatflow | `Workflow` | Synchronous, cost-capped |
| Agent | `Agent` | Declarative dataclass |
| Tool | `@tool` decorator | With risk tier |
| LLM node | `Agent(model=...)` | Auto-detects provider |
| Memory | `Agent(memory=True)` | 4-tier: Working/Episodic/Semantic/Procedural |
| Document Loader | `KnowledgeSource` | Auto-RAG at every step |
| Vector Store | `VectorStore` | Zero-dep TF-IDF or sentence-transformers |
| Chain | `Team` or `StateGraph` | Multiple agents, typed edges |
| API endpoint | `meshflow serve` | REST + SSE + WebSocket |

---

## Migration patterns

### Simple chatflow → Workflow

**Flowise (visual):** LLM node → Memory node → Response

**MeshFlow:**
```python
from meshflow import Workflow, CostCap, Agent

wf = Workflow(cost_cap=CostCap(usd=1.00))
wf.add(Agent('assistant', memory=True))
result = wf.run('What are the benefits of RAG?')
print(result.output)
```

---

### Custom tool → @tool

**Flowise:** Custom Tool node with JavaScript function

**MeshFlow:**
```python
from meshflow import Agent, tool, RiskTier

@tool(name="search_crm", description="Search the CRM system", risk=RiskTier.EXTERNAL_IO)
async def search_crm(customer_id: str) -> str:
    # your real implementation
    return await crm_api.get_customer(customer_id)

agent = Agent(name="support", role="executor", tools=[search_crm])
result = await agent.run(f"Look up customer {customer_id} and summarize their account")
```

---

### Document Q&A → Agent with knowledge

**Flowise:** PDF Loader → Vector Store → Conversational Retrieval Chain

**MeshFlow:**
```python
from meshflow import Agent, KnowledgeSource, VectorStore

# Index your documents once
vs = VectorStore.from_directory("./documents/")

agent = Agent(
    name="doc-qa",
    role="researcher",
    knowledge=[KnowledgeSource(source=vs, top_k=5, max_chars=4096)],
    memory=True,
)

# Q&A loop
result = await agent.run("What does section 3.2 say about data retention?")
print(result["result"])
```

---

### Multi-agent conversation → GroupChat

**Flowise:** Multi-Agent Supervisor flow

**MeshFlow:**
```python
from meshflow import Agent, GroupChat, GroupChatManager

analyst  = Agent(name="analyst",  role="researcher")
planner  = Agent(name="planner",  role="planner")
executor = Agent(name="executor", role="executor")

chat = GroupChat(
    agents=[analyst, planner, executor],
    max_turns=10,
    speaker_selection="auto",
)
manager = GroupChatManager(chat)
result = await manager.run("Plan and execute a competitive analysis of our top 3 rivals")
print(result.final_message)
```

---

### API endpoint → meshflow serve

**Flowise:** Built-in Prediction API at `/api/v1/prediction/{chatflowId}`

**MeshFlow:**
```bash
meshflow serve --host 0.0.0.0 --port 8000
```

Endpoints:
- `POST /runs` — submit a workflow run
- `GET /runs/{run_id}` — get results
- `GET /events` — SSE stream of all events
- `GET /health/ready` — readiness probe

Python client:
```python
from meshflow import MeshFlowClient

client = MeshFlowClient("http://localhost:8000", api_key="mf-...")
result = client.run_agent("assistant", "Hello, what can you help me with?")
print(result.output)
```

TypeScript client:
```typescript
import { MeshFlowClient } from "@meshflow/client";
const client = new MeshFlowClient({ baseUrl: "http://localhost:8000", apiKey: "mf-..." });
const result = await client.runAgent("assistant", "Hello");
```

---

## Security hardening (closes CVE-2025-59528)

MeshFlow's execution engine is not affected by CVE-2025-59528 because it does not use Flowise's parser or runtime. Additionally, MeshFlow adds security layers Flowise never had:

### 1. Prompt injection detection (automatic)

```python
from meshflow import Agent, PromptInjectionGuardrail

agent = Agent(
    name="safe-agent",
    role="executor",
    input_guardrails=[PromptInjectionGuardrail()],
)
```

### 2. Code execution sandboxing

```python
from meshflow import Agent

agent = Agent(
    name="coder",
    role="executor",
    tools=["python_repl"],  # memory-limited (256MB), network-blocked subprocess
)
```

### 3. Secret vault

```python
from meshflow import VaultStore

vault = VaultStore("vault.db", master_password=os.environ["VAULT_KEY"])
vault.store("openai_api_key", os.environ["OPENAI_API_KEY"])

# Retrieve at runtime — never in logs, never in audit output
key = vault.retrieve("openai_api_key").value
```

### 4. Tamper-evident audit chain

Every step is written to a SHA-256 hash chain. Any tampering with the audit log is detectable:

```bash
meshflow audit export <run_id> --format json
# Includes prev_hash and entry_hash for every step — mathematically verifiable
```

### 5. Policy-as-code

```yaml
# policies/production.yaml
rules:
  - name: block-unauthenticated-tool-calls
    conditions:
      - field: agent_token
        op: not_exists
    action: DENY
    reason: "All production agents must have signed identity tokens"
```

```bash
meshflow serve --policy-file policies/production.yaml
```

---

## Migration checklist

- [ ] Identify all Flowise chatflows and map them to MeshFlow patterns (see table above)
- [ ] `pip install meshflow`
- [ ] Run `meshflow doctor` to validate your environment
- [ ] Set `MESHFLOW_MOCK=1` and test each migrated workflow in sandbox mode
- [ ] Configure `VaultStore` for all secrets (remove from environment variables)
- [ ] Add `PIIBlockGuardrail` + `PromptInjectionGuardrail` to all user-facing agents
- [ ] Set compliance profile: `compliance_profile("hipaa")` or `"gdpr"` as appropriate
- [ ] Deploy with `meshflow serve` and configure `/health/ready` for your load balancer
- [ ] Run `meshflow snapshot export` to generate your first compliance artifact
- [ ] Uninstall Flowise and patch or decommission unpatched instances immediately

---

## Getting help

- **Docs:** https://meshflow.dev/docs
- **GitHub:** https://github.com/meshflow-ai/meshflow
- **Discord:** https://discord.gg/meshflow
- **CLI help:** `meshflow --help`
- **Environment check:** `meshflow doctor`
