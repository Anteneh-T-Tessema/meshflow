# Show HN: MeshFlow — production-safe multi-agent orchestration (pip install meshflow)

**HN title:** Show HN: MeshFlow – 7 lines to a production-safe multi-agent workflow (pip install meshflow)

---

Hi HN,

79% of enterprises have adopted AI agents. Only 11% run them in production.

That 68-point gap is what we built MeshFlow to close.

Every framework makes agents easy to prototype. None of them make agents safe to ship. Compliance, audit trails, cost governance, and crash recovery are all afterthoughts you bolt on later — usually after something goes wrong in production. MeshFlow treats them as infrastructure: always on, zero configuration, built in from line one.

```python
from meshflow import Workflow, CostCap, Agent

wf = Workflow(cost_cap=CostCap(usd=5.00))
wf.add(Agent('researcher'), Agent('analyst'), Agent('writer'))
result = wf.run('Write a competitive analysis of our market')

# Compliant. Durable. Audited. Cost-capped. Done.
```

```bash
pip install meshflow
```

No API key required for your first run:

```bash
python -c "
import os; os.environ['MESHFLOW_MOCK'] = '1'
from meshflow import Workflow, CostCap, Agent
wf = Workflow(cost_cap=CostCap(usd=5.00), mode='sandbox')
wf.add(Agent('researcher'), Agent('analyst'))
result = wf.run('What makes agents fail in production?')
print(result.summary())
"
```

---

**What every run gets — zero configuration:**

- **SHA-256 tamper-evident audit chain.** Every step is cryptographically linked to the previous one. Modify a log entry and the chain breaks. This is the artifact HIPAA §164.312 and SOC 2 CC6.1 actually want.
- **Hard cost cap.** `CostCap(usd=5.00)` stops execution before it hits the limit — not after. No more weekend surprise bills.
- **Durable execution.** Crash recovery via SQLite/Redis/Postgres/S3 checkpoints. Same `run_id` on restart = resume from last checkpoint.
- **Subprocess sandbox.** Code execution (python_repl) runs in a memory-capped (256 MB), network-blocked subprocess. No sandbox escapes, no RCE.
- **Compliance profiles.** `compliance_profile("hipaa")` or `"sox"`, `"gdpr"`, `"pci"`, `"nerc"` — one line, all rules enforced.
- **70–85% token cost reduction.** Prompt caching (`cache_control` on every system prompt), ModelRouter (routes cheap tasks to nano models), ContextCompactor (sliding window summarization). Combined, this typically pays back implementation cost in the first week of production use.

---

**The Stripe parallel**

Stripe didn't win by being better at accepting payments. They won by making payment infrastructure something any developer could trust with production money — PCI compliance built in, idempotent by design, audit trail on every transaction.

MeshFlow is the same bet for agents. Not a better framework. Infrastructure that any engineer can trust with production workloads. The positioning is not "better than LangGraph" — it is "the layer LangGraph runs on when it needs to ship."

---

**Comparison**

|  | MeshFlow | LangGraph | CrewAI | AutoGen |
|--|--|--|--|--|
| SHA-256 audit chain | ✅ | ✗ | ✗ | ✗ |
| HIPAA/SOX/GDPR built-in | ✅ | ✗ | ✗ | ✗ |
| Hard cost cap | ✅ | ✗ | ✗ | ✗ |
| Subprocess sandbox | ✅ | ✗ | ✗ | ✅ (Docker only) |
| 70–85% token reduction | ✅ | ✗ | ✗ | ✗ |
| Durable execution | ✅ | ✅ | ✗ | ✗ |
| Policy-as-code engine | ✅ | ✗ | ✗ | ✗ |
| Secret vault | ✅ | ✗ | ✗ | ✗ |

---

**What else is in the box**

- LangGraph-compatible typed `StateGraph` with `interrupt()` / `Command` HITL
- CrewAI-compatible `Crew`, `Task`, `Process` primitives
- AutoGen-compatible `GroupChat` with auto speaker selection
- `govern(your_existing_app)` — wrap any LangGraph/CrewAI/AutoGen app with one line
- A2A (Agent-to-Agent) HTTP protocol with `/.well-known/agent-card` discovery
- MCP server auto-generation from any workflow
- TypeScript client SDK with WebCrypto webhook signature verification
- `meshflow serve` → FastAPI REST + SSE + WebSocket server, `/health/live` + `/health/ready` for k8s
- 85-page documentation site, 4,379 passing tests

---

**What we'd love feedback on**

1. The 7-line API — does it feel right? Is there anything in those 7 lines that would make you not want to use it?
2. The governance defaults — too much? Not enough? What would you add?
3. The token optimization layer — have you measured this in your own production deployments?

Apache 2.0. Self-hostable. No platform tax.

GitHub: https://github.com/Anteneh-T-Tessema/meshflow
Docs: https://meshflow.dev
PyPI: https://pypi.org/project/meshflow/
