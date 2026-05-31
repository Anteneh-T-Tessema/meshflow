# Pre-built Agents

MeshFlow ships 20 specialist agents in `meshflow.agents` — drop-in, zero-config, production-ready.

```python
from meshflow import agents

researcher = agents.ResearchAgent()
coder      = agents.CoderAgent()
critic     = agents.CriticAgent()
```

All agents accept `model=`, `policy=`, and `tools=` overrides.

---

## ResearchAgent

Deep research with source attribution and explicit uncertainty flagging. Memory enabled by default.

```python
from meshflow import agents

researcher = agents.ResearchAgent(model="claude-sonnet-4-6")
result = await researcher.run("Explain transformer attention mechanisms")
print(result["result"])
```

**Defaults:** `name="researcher"`, `role=researcher`, `memory=True`

Output format: `Summary / Details / Sources / Caveats` sections, with `CONFIDENCE:0.XX` on the last line.

---

## CoderAgent

Writes clean, production-ready, type-hinted code. Explicit about edge cases and external dependencies.

```python
coder = agents.CoderAgent(language="TypeScript")
result = await coder.run("Write a binary search tree with insert and search")
```

**Defaults:** `name="coder"`, `role=executor`, `language="Python"`, `risk=INTERNAL`

---

## ReviewerAgent

Reviews code for correctness, security, performance, and style. Returns structured JSON.

```python
reviewer = agents.ReviewerAgent()
result = await reviewer.run(f"Review this code:\n{code}")
# result["result"] is JSON: verdict, score, security_issues, performance_issues, strengths, suggested_fixes
```

**Defaults:** `name="reviewer"`, `role=critic`

Blocks immediately on: SQL injection, XSS, hardcoded secrets, unsafe eval, command injection.

---

## CriticAgent

Evaluates output quality, reasoning, and completeness against the original task.

```python
critic = agents.CriticAgent()
result = await critic.run(f"Original task: {task}\n\nOutput to evaluate:\n{output}")
# Returns JSON: score, passed, issues, strengths, missing, verdict, revision_prompt
```

**Defaults:** `name="critic"`, `role=critic`

---

## PlannerAgent

Decomposes complex tasks into ordered, executable steps with success criteria and token estimates.

```python
planner = agents.PlannerAgent()
result = await planner.run("Build a microservices payment system")
# Returns JSON: goal, steps[{id, role, task, depends_on, success_criteria}], risks, confidence
```

**Defaults:** `name="planner"`, `role=planner`

---

## AnalystAgent

Interprets numerical data, identifies trends, detects anomalies.

```python
analyst = agents.AnalystAgent()
result = await analyst.run(f"Analyse this CSV data:\n{csv_text}")
# Output: Executive Summary → Key Findings → Supporting Analysis → Recommendations → Data Quality Notes
```

**Defaults:** `name="analyst"`, `role=researcher`

---

## WriterAgent

Creates well-structured written content. Style is configurable.

```python
writer = agents.WriterAgent(style="technical")
result = await writer.run("Write a blog post on prompt caching")
```

**Defaults:** `name="writer"`, `role=executor`, `style="professional"`

---

## SummarizerAgent

Condenses long content into concise summaries. Uses Haiku for speed and cost efficiency.

```python
summarizer = agents.SummarizerAgent(max_words=150)
result = await summarizer.run(long_document)
```

**Defaults:** `name="summarizer"`, `role=executor`, `model="claude-haiku-4-5-20251001"`, `max_words=200`

---

## ExtractorAgent

Extracts structured data from unstructured text. Returns only valid JSON.

```python
from meshflow import agents

extractor = agents.ExtractorAgent(schema={
    "company": "str",
    "revenue": "float",
    "date": "ISO 8601 date",
})
result = await extractor.run("Apple reported $94.9B in revenue for Q1 2025...")
```

**Defaults:** `name="extractor"`, `model="claude-haiku-4-5-20251001"`

Returns `null` for missing fields — never guesses.

---

## ClassifierAgent

Classifies input into predefined categories.

```python
classifier = agents.ClassifierAgent(categories=["bug", "feature", "docs", "question"])
result = await classifier.run("The login button doesn't work on mobile Safari")
# Returns JSON: {"label": "bug", "confidence": 0.97, "reasoning": "..."}
```

**Defaults:** `name="classifier"`, `model="claude-haiku-4-5-20251001"`, `categories=["positive", "negative", "neutral"]`

---

## ValidatorAgent

Validates output against a set of rules.

```python
validator = agents.ValidatorAgent(rules=[
    "Response is factually accurate",
    "Response is under 500 words",
    "No PII is present",
])
result = await validator.run(agent_output)
# Returns JSON: valid, rules_passed, rules_failed, details
```

**Defaults:** `name="validator"`, `model="claude-haiku-4-5-20251001"`

---

## TranslatorAgent

Translates text while preserving meaning and formatting.

```python
translator = agents.TranslatorAgent(target_language="Spanish", preserve_formatting=True)
result = await translator.run(english_text)
```

**Defaults:** `name="translator"`, `model="claude-haiku-4-5-20251001"`, `target_language="English"`

---

## SQLAgent

Generates safe, optimized SQL from natural language. Never produces unparameterized queries.

```python
sql_agent = agents.SQLAgent(
    dialect="PostgreSQL",
    schema="users(id, email, created_at), orders(id, user_id, total)",
)
result = await sql_agent.run("Find the top 10 users by total order value in the last 30 days")
# Returns JSON: sql, params, risk, explanation, requires_review
```

**Defaults:** `name="sql_agent"`, `model="claude-sonnet-4-6"`, `risk=EXTERNAL_IO`

Flags any data-modifying query as `requires_review=true`.

---

## APIAgent

Composes REST API calls from task descriptions.

```python
api_agent = agents.APIAgent(tools=[my_http_tool])
result = await api_agent.run("Get the current weather for San Francisco")
# Returns JSON: method, url, headers, body, expected_status, risk
```

**Defaults:** `name="api_agent"`, `role=executor`, `risk=EXTERNAL_IO`

---

## DebugAgent

Root-cause analysis and fix generation for bugs and errors.

```python
debugger = agents.DebugAgent()
result = await debugger.run(f"Error: {traceback}\n\nCode:\n{code}")
# Returns JSON: root_cause, confidence, fix, explanation, prevention
```

**Defaults:** `name="debugger"`, `role=executor`

---

## AuditorAgent

Compliance auditor for SOC 2, HIPAA, GDPR, and other regulatory frameworks.

```python
auditor = agents.AuditorAgent(framework="HIPAA")
result = await auditor.run(f"Review this data handling policy:\n{policy_text}")
# Returns JSON: compliant, findings[{control, severity, description, remediation}], summary
```

**Defaults:** `name="auditor"`, `role=critic`, `framework="SOC 2"`, `policy="regulated"`

---

## ReporterAgent

Compiles raw data and analysis into a professional, structured report.

```python
reporter = agents.ReporterAgent(format="markdown")
result = await reporter.run(f"Data: {analysis}\nContext: {context}")
```

**Defaults:** `name="reporter"`, `role=executor`, `format="markdown"`

---

## TeacherAgent

Explains concepts clearly, calibrated to the audience. Ends with comprehension-check questions.

```python
teacher = agents.TeacherAgent(audience="junior developer")
result = await teacher.run("Explain database indexing")
```

**Defaults:** `name="teacher"`, `role=researcher`, `audience="intermediate developer"`

---

## NegotiatorAgent

Strategy, talking points, and compromise positions using the Harvard Principled Negotiation framework.

```python
negotiator = agents.NegotiatorAgent()
result = await negotiator.run("We need to negotiate a software license renewal...")
```

---

## OrchestratorAgent

High-level coordinator that routes tasks and synthesises results. Uses Opus by default.

```python
orchestrator = agents.OrchestratorAgent()
result = await orchestrator.run("Coordinate: research X, code Y, review Z")
```

**Defaults:** `name="orchestrator"`, `model="claude-opus-4-7"`, `role=orchestrator`

---

## GuardianAgent

Safety and policy enforcement layer. Blocks actions violating privacy, ethics, or policy.

```python
guardian = agents.GuardianAgent()
result = await guardian.run(f"Proposed action: {action_description}")
# Returns JSON: {"verdict": "allow"|"block"|"escalate", "reason": "...", "risk_tier": 1-4}
```

**Defaults:** `name="guardian"`, `model="claude-haiku-4-5-20251001"`, `policy="regulated"`

Use as an output guardrail in a team's supervised pattern, or call directly before any sensitive action.
