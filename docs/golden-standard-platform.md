# MeshFlow Golden Standard Platform Plan

Status: draft
Date: 2026-05-22

## Thesis

MeshFlow should become the control plane standard for multi-agent systems: a layer
above LangGraph, CrewAI, AutoGen, OpenAI Agents SDK, MCP tools, A2A agents, and
custom services. The product promise is simple:

> Build agents anywhere. Govern, orchestrate, evaluate, replay, and certify them
> through MeshFlow.

MeshFlow should not compete with the best agent frameworks at their strongest
layer. It should absorb their strengths and solve the weaknesses that stop
multi-agent systems from becoming reliable enterprise infrastructure.

## What to Borrow

| Platform | Strength to adopt | Weakness MeshFlow should solve |
| --- | --- | --- |
| LangGraph | Explicit graph state, conditional edges, durable execution, human review points | Framework-specific graphs, governance and cross-framework audit are not the default contract |
| CrewAI | Role/task ergonomics, simple mental model for teams, quick prototyping | Less precise low-level state control and weaker standardization for replay, policy, identity, and audit |
| AutoGen | Conversational agents, dynamic collaboration, human-agent interaction, benchmark culture | Conversation flows can be hard to constrain, inspect, replay, and certify across frameworks |
| OpenAI Agents SDK | Lightweight code-first orchestration, manager and decentralized handoff patterns, guardrail-first product guidance | Provider-specific runtime assumptions; not a neutral standard over every framework and protocol |
| Google A2A | Cross-vendor agent discovery and communication | Needs local policy, identity, consent, audit, and runtime enforcement around the protocol |
| Anthropic MCP | Tool/context standardization | Tool access is not enough; agent-to-agent orchestration still needs governance and replay |
| IBM ACP/BeeAI | Framework-independent agent messaging and async-first collaboration | Needs a broader execution kernel, conformance suite, and enterprise controls |

## Golden Standard Principles

1. One governed path for every step.
   Every node, no matter its source framework, must pass through the same
   runtime lifecycle: identity, policy, guardrails, risk gate, execution,
   telemetry, ledger, uncertainty scoring, and audit.

2. Separate orchestration from governance.
   LangGraph-style DAGs, CrewAI-style roles, AutoGen-style conversations, and
   OpenAI-style handoffs are orchestration choices. MeshFlow should make them
   all governable through one contract.

3. Make state explicit and portable.
   Node input and output must be schema-valid, serializable, replayable, and
   convertible between framework-native state shapes.

4. Treat protocols as first-class surfaces.
   MeshFlow should support MCP for tools, A2A/ACP for agent communication, HTTP
   for language neutrality, and internal Python APIs for local development.

5. Prefer controlled autonomy.
   Dynamic planning and decentralized handoff are valuable, but every delegation,
   tool call, and irreversible action needs capability scope, budget scope, and
   audit scope.

6. Certification is the moat.
   MeshFlow should ship conformance levels, golden traces, benchmark suites, and
   reproducible evals. "Works with MeshFlow" should mean something measurable.

7. Observability is a product feature, not debug plumbing.
   Every run should emit OpenTelemetry spans, structured events, cost, token,
   carbon, risk, tool, and approval records.

8. Durable replay is mandatory.
   Production agents need restart-safe checkpoints, deterministic replay where
   possible, immutable run archives, and human-decision provenance.

## Research Scan

As of 2026-05-22, the most relevant industry work points to the same direction:
multi-agent systems work best when orchestration is modular, observable,
bounded, and evaluated under realistic side effects.

| Source | Why it matters for MeshFlow |
| --- | --- |
| Microsoft AutoGen, "Enabling Next-Gen LLM Applications via Multi-Agent Conversation" | Establishes flexible multi-agent conversation as a core abstraction and validates human/tool/LLM mixed workflows. |
| Microsoft Magentic-One | Shows a lead orchestrator plus specialized agents can recover from errors and solve diverse long-horizon tasks. |
| Microsoft AutoGen Studio and Magentic-UI | Emphasize debugging, human-in-the-loop control, and developer tooling as part of the orchestration surface. |
| Microsoft Triangle | Shows multi-role agents plus negotiation can improve real production incident triage and reduce time-to-engage. |
| Microsoft UFO2 and Windows Agent Arena | Reinforces the need for realistic environments, OS/tool side effects, and scalable benchmarks. |
| Anthropic multi-agent research system | Shows breadth-first decomposition with a lead agent and subagents can outperform single-agent execution on broad research tasks. |
| Anthropic "Building Effective AI Agents" | Recommends scaling from simple agents to multi-agent systems only when complexity demands it, with clear guardrails. |
| OpenAI "A practical guide to building agents" and Agents SDK work | Distinguishes manager patterns from decentralized handoffs and treats guardrails, tools, and evals as first-class. |
| Google Agent2Agent protocol | Pushes agent interoperability toward a vendor-neutral communication layer. MeshFlow should govern A2A, not ignore it. |
| IBM Agent Communication Protocol and BeeAI | Confirms the market direction toward framework-independent, async agent messaging. |
| Google Research ATLAS | Demonstrates constraints-aware multi-agent planning for real-world travel tasks, useful for MeshFlow's policy and constraint language. |
| Recent multi-agent orchestration benchmark papers | Reinforce that voting, consensus, and collaboration can outperform single-agent baselines, but can introduce herding, premature consensus, and identity effects. |

Key sources:

- https://arxiv.org/abs/2308.08155
- https://arxiv.org/abs/2411.04468
- https://www.microsoft.com/en-us/research/project/autogen/publications/
- https://www.microsoft.com/en-us/research/publication/triangle-empowering-incident-triage-with-multi-llm-agents/
- https://www.microsoft.com/en-us/research/publication/ufo2-the-desktop-agentos/
- https://www.anthropic.com/engineering/multi-agent-research-system
- https://resources.anthropic.com/hubfs/Building%20Effective%20AI%20Agents-%20Architecture%20Patterns%20and%20Implementation%20Frameworks.pdf
- https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/
- https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/
- https://research.ibm.com/projects/agent-communication-protocol
- https://research.google/pubs/atlas-constraints-aware-multi-agent-collaboration-for-real-world-travel-planning/
- https://arxiv.org/abs/2509.23537

## Target Architecture

MeshFlow should evolve into seven planes:

1. Agent Contract Plane
   Universal `MeshNode`, typed `NodeInput`, typed `NodeOutput`, schemas,
   capability declarations, risk profile, tool scope, and framework metadata.

2. Orchestration Plane
   Supports static DAGs, dynamic manager agents, decentralized handoffs,
   fan-out/fan-in, consensus/voting, critique loops, retry/replan, and
   cancellation.

3. Governance Plane
   Policy-as-code, budget controls, HITL, data classification, tool risk tiers,
   consent, identity, revocation, prompt-injection defense, and irreversible
   action approval.

4. Runtime Plane
   Durable queue-backed execution, idempotency keys, checkpointing, retries,
   parallel branch scheduling, resource quotas, sandboxed tools, and worker
   isolation.

5. Protocol Plane
   MCP gateway for tools, A2A bridge for cross-vendor agents, ACP bridge for
   async messaging, HTTP runtime for language-neutral execution, and SDK adapters
   for LangGraph, CrewAI, AutoGen, OpenAI Agents SDK, BeeAI, and custom code.

6. Observability and Audit Plane
   OpenTelemetry spans, structured event stream, replay ledger, signed archive,
   cost/token/carbon accounting, data lineage, and run provenance.

7. Evaluation and Certification Plane
   Conformance suite, golden traces, deterministic fixture agents, side-effect
   isolation, regression dashboards, and domain benchmark packs.

## Conformance Levels

| Level | Name | Requirement |
| --- | --- | --- |
| L0 | Runnable | Node executes and returns a valid `NodeOutput`. |
| L1 | Reliable | Exceptions, timeouts, cancellation, and retries are handled without crashing the runtime. |
| L2 | Governed | Identity, policy, risk, budget, and guardrails execute for every step. |
| L3 | Auditable | Ledger, telemetry, lineage, and replay export are complete. |
| L4 | Durable | Distributed checkpoints, resume, idempotency, and side-effect tracking are verified. |
| L5 | Certified | Adapter passes benchmark pack, golden traces, security tests, and reproducibility checks. |

## Roadmap

### Phase 1: Standardize the Core

- Freeze `MeshNode`, `NodeInput`, `NodeOutput`, `RuntimeOutcome`, and ledger
  schemas as public contracts.
- Add JSON Schema export for every public contract.
- Add adapter contract tests for LangGraph, CrewAI, AutoGen, Python, HTTP, and MCP.
- Clean lint/typecheck debt so the project can enforce CI gates.

### Phase 2: Orchestrator V2

- Add a unified scheduler that supports DAG, manager, handoff, consensus, and
  critique-loop patterns.
- Add dynamic replanning with bounded iteration limits and visible plan diffs.
- Add blackboard/context-bus semantics with typed merge strategies.
- Add cancellation, branch-level timeout, branch retry, and fan-in conflict rules.

### Phase 3: Protocol Interop

- Add MCP client/server mode around `MCPGateway`.
- Add A2A agent-card import/export and task messaging bridge.
- Add ACP bridge for async agent communication.
- Add protocol-level policy checks before message forwarding.

### Phase 4: Enterprise Runtime

- Add Postgres-first distributed run store.
- Add queue-backed workers with idempotency keys.
- Add sandbox profiles for tools and code execution.
- Add secrets isolation and per-agent credential scopes.
- Add immutable S3/archive backend as a release-grade feature.

### Phase 5: Eval and Certification

- Expand `meshflow conformance` from L0-L3 to L0-L5.
- Add golden traces for known workflows.
- Add benchmark packs: research, customer support, incident triage, coding, data
  analysis, and tool-side-effect tasks.
- Add decision-quality metrics: correctness, specificity, validity, confidence
  calibration, cost, latency, and policy compliance.

### Phase 6: Developer Product

- Add `meshflow init`, `meshflow inspect`, `meshflow replay --timeline`, and
  `meshflow eval`.
- Add a local web UI for run inspection, replay, topology, cost, and approvals.
- Add plugin registry metadata for adapters and certified nodes.

## Immediate Engineering Priorities

1. Pay down quality gate debt: make `make check` pass using repo-local tooling.
2. Split current staged and unstaged changes into coherent commits.
3. Promote the current conformance suite into a public standard document.
4. Add schema validation to workflow YAML and node input/output boundaries.
5. Add adapter fixture tests that simulate framework behavior without requiring
   heavyweight optional dependencies.
6. Add an ADR for protocol strategy: MCP for tools, A2A/ACP for agent-to-agent
   messages, MeshFlow runtime for governance.

## Product Positioning

MeshFlow should be described as:

> The open control plane for governed multi-agent orchestration.

Short form:

> LangGraph gives you graphs. CrewAI gives you crews. AutoGen gives you
> conversations. MeshFlow gives you the standard runtime, governance, audit,
> replay, and certification layer above all of them.

