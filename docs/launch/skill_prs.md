# SKILL.md PR Submissions

Submit to both repos on launch day at 10 AM PT.

---

## PR 1: anthropics/skills

**Repo:** https://github.com/anthropics/skills (or the official catalog repo)
**Branch name:** `add-meshflow-skill`
**File to add:** `meshflow/SKILL.md` (copy of our root SKILL.md)

**PR Title:**
```
Add meshflow skill — production-safe multi-agent orchestration
```

**PR Description:**
```markdown
## MeshFlow — production-safe multi-agent orchestration

This PR adds the MeshFlow skill to the official catalog.

### What it does

MeshFlow is the infrastructure layer for production agent deployments —
HIPAA/SOX/GDPR compliance, SHA-256 audit chain, hard cost caps, and
70-85% token savings built in by default.

The SKILL.md auto-invokes when a Claude Code user mentions:
- "build an agent", "multi-agent", "orchestrate agents"
- "compliance", "HIPAA", "SOC2", "GDPR"
- "cost cap", "token budget", "guardrails"
- "durable workflow", "human in the loop", "HITL"
- "LangGraph", "CrewAI", "AutoGen" (wraps all three)

### What users get

When this skill is active, Claude Code automatically:
1. Uses the 7-line `Workflow` API for any agent pipeline
2. Adds compliance profiles for regulated industry requests
3. Recommends `mode='sandbox'` for testing (zero token spend)
4. Applies cost caps and guardrails by default
5. Suggests the right patterns: Team vs StateGraph vs Crew vs GroupChat

### Install

```bash
pip install meshflow
```

### Links

- GitHub: https://github.com/Anteneh-T-Tessema/meshflow
- PyPI: https://pypi.org/project/meshflow/
- Docs: https://meshflow.dev

### Why this belongs in the official catalog

MeshFlow is the only agent framework built from the ground up for regulated
industries. It's the answer to the most common enterprise blocker: "we can't
ship agents to production because we can't prove what they did." The SKILL.md
makes Claude Code automatically production-safe for any agent task.
```

---

## PR 2: openai/skills (or openai-skills catalog)

**Repo:** https://github.com/openai/skills (or equivalent Codex skills repo)
**Branch name:** `add-meshflow-skill`
**File to add:** `meshflow/SKILL.md`

**PR Title:**
```
Add meshflow skill — governed multi-agent workflows with cost caps and compliance
```

**PR Description:**
```markdown
## MeshFlow skill for Codex / OpenAI coding tools

This PR adds MeshFlow to the official skill catalog.

### The problem it solves

79% of enterprises have adopted AI agents. Only 11% run them in production.
The gap: no framework provides compliance, audit trails, and cost governance
out of the box. MeshFlow does.

### What the skill does

When active in Codex or any OpenAI-compatible tool, this skill:

- Auto-generates production-safe agent code (not just prototype code)
- Always includes cost caps (`CostCap`) for any agent workflow
- Applies compliance profiles when user mentions HIPAA, SOX, GDPR, etc.
- Uses sandbox mode (`mode='sandbox'`) for testing — zero API calls
- Provides correct patterns for LangGraph-compatible, CrewAI-compatible,
  and AutoGen-compatible workflows

### Trigger phrases

The description field matches:
- "build an agent", "multi-agent", "orchestrate agents", "agent team"
- "compliance", "HIPAA", "SOC2", "GDPR", "PCI", "regulated"
- "cost cap", "token budget", "token optimization"
- "guardrails", "audit trail", "governance"
- "durable workflow", "crash recovery", "checkpoint"
- "LangGraph", "CrewAI", "AutoGen" (governance wrapper for all three)

### Quick demo

```python
from meshflow import Workflow, CostCap, Agent

wf = Workflow(cost_cap=CostCap(usd=5.00))
wf.add(Agent('researcher'), Agent('analyst'), Agent('writer'))
result = wf.run('Write a competitive analysis')
```

```bash
pip install meshflow
```

- GitHub: https://github.com/Anteneh-T-Tessema/meshflow
- Docs: https://meshflow.dev
- PyPI: https://pypi.org/project/meshflow/
```

---

## Skill marketplace submissions (same day)

After the GitHub PRs, submit to the three skill marketplaces:

### SkillsMP (skillsmp.com)
- Category: Developer Tools → AI/ML → Agent Frameworks
- Description: Use the "Short Description" from product_hunt.md (< 260 chars)
- Link: https://github.com/Anteneh-T-Tessema/meshflow
- Install: `pip install meshflow`

### Skills.sh
- Follow their submission form
- Use the SKILL.md file directly

### ClawHub
- Category: Production AI → Governance
- Tagline: "The Stripe of agent infrastructure"
