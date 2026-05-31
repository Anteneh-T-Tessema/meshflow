# Reddit Launch Templates

This kit contains tailormade templates for three key subreddits: r/MachineLearning, r/Python, and r/LocalLLaMA.

---

## 1. Subreddit: r/MachineLearning
**Title**: [D] MeshFlow: An open-source orchestrator for governed, cost-optimized multi-agent workflows (Apache-2.0)

**Body**:
Hey ML community,

We’ve just open-sourced **MeshFlow**, a code-first, framework-agnostic runtime designed for governing and optimizing multi-agent systems in production. 

Most agent frameworks focus on rapid prototyping, but ML and platform engineering teams usually run into hard bottlenecks around LLM cost scaling, evaluation alignment, and execution safety. MeshFlow tackles these from a runtime/infrastructure perspective.

Here are the key ML and system features:

* **Task-Based Model Routing**: Before an agent executes a node, MeshFlow runs an evaluation on task complexity, routing the execution to one of four model tiers (`nano`, `small`, `medium`, `large`). This cuts overall API costs by 50-60% by utilizing smaller local models (e.g. LLaMA-3-8B) for standard formatting or extraction and reservation of frontier models (e.g. Claude Opus) for high-complexity reasoning.
* **Context Compactor & Summary Pruning Middleware**: Implements sliding window summarization and context deduplication across parallel agent teams to limit prompt length growth.
* **System Prompt Caching**: Native injection of Anthropic `cache_control` tags when system prompts exceed 1024 tokens.
* **Cost Regression Evaluation Gate**: Integrates with CI pipelines to evaluate agent changes against a golden scenario baseline, throwing failures if code updates introduce token cost regressions.
* **Resilient State Persistence**: Multi-backend state serialization (Redis, PostgreSQL, S3) that preserves checkpoint frames and allows resuming paused workflows.

Here is the basic API contract:

```python
from meshflow import Workflow, Agent, CostCap

wf = Workflow(cost_cap=CostCap(usd=5.00))
wf.add(Agent('researcher'), Agent('critic'), Agent('writer'))
result = wf.run('Compile comparative literature review of LLM reasoning pathways')
print(result)
```

We'd love to discuss:
1. How do you handle token budget enforcement and model routing in your agent loops?
2. What evaluation pipelines do you use to detect cost or performance regression in production?

GitHub: https://github.com/Anteneh-T-Tessema/meshflow

---

## 2. Subreddit: r/Python
**Title**: MeshFlow — A code-first framework to make AI agents safe to run in production

**Body**:
Hi pythonistas,

I want to share **MeshFlow**, a new open-source Python library (Apache-2.0) that we built to solve a problem every developer faces when taking AI agents from prototype to production: **safety and reliability**.

Many existing agent frameworks rely heavily on global state variables, complex event loops, and heavy abstractions. We wanted to build something that feels like native Python, integrates with standard tooling (MyPy, Ruff, Pytest), and runs reliably.

### The 7-Line API Contract
We promise a friction-free experience. You can execute governed multi-agent steps in exactly 7 lines of code:

```python
from meshflow import Workflow, Agent, CostCap

wf = Workflow(cost_cap=CostCap(usd=1.50))
wf.add(Agent('researcher'), Agent('critic'), Agent('writer'))
result = wf.run('Generate a Python code review report')
print(result)
```

### Under the Hood
1. **Isolated Code Execution**: Unlike raw `exec()` tools, MeshFlow's `CodeInterpreter` runs code inside an isolated subprocess with strict memory limit controls (`resource.setrlimit`) and complete outbound network blocks.
2. **Synchronous Wrappers**: We provide synchronous entry points (`Workflow.run()`) that wrap asynchronous step executors. You get the simplicity of sync code without losing concurrency or SSE streaming under the hood.
3. **Offline Sandbox Mode**: Setting `mode="sandbox"` propagates a ContextVar that overrides LLM providers with a local `SandboxProvider`. This runs full agent simulations with zero token cost, using mocked, schema-compliant responses.
4. **Tamper-Evident Ledger**: Every step creates a signed audit record with SHA-256 hash chaining, giving you a mathematically verifiable audit trail for compliance (SOC 2, GDPR).

Check out the code, run the tests, and let us know what you think of the design:

```bash
pip install meshflow
meshflow studio --port 7788
```

GitHub: https://github.com/Anteneh-T-Tessema/meshflow

---

## 3. Subreddit: r/LocalLLaMA
**Title**: MeshFlow: Open-source agent runtime with automatic model routing, local tool execution sandboxes, and zero-spend sandbox mode

**Body**:
Hey LocalLLaMA community,

If you are running agent loops locally or with private endpoints, you know how easily agents can get stuck in loops, consume resources, or breakout of text-generation boundaries. 

We built **MeshFlow**, a 100% self-hosted, Apache-2.0 agent orchestrator that helps you manage local model execution, enforce budget caps, and run tool code safely.

### Why LocalLLaMA developers might find it useful:

* **Zero-Spend Sandbox Mode**: You can test workflow topologies and loops offline using `Workflow(mode="sandbox")`. It uses a schema-aware Mock LLM responder (`SandboxProvider`) to simulate runs with zero real token cost and logs all steps to a local database.
* **Outbound-Blocked Subprocess Python Sandbox**: If you let your agent write and execute Python code, MeshFlow runs it in a sandboxed subprocess. Outbound network traffic is blocked, timeouts are enforced, and memory is capped.
* **Local Model Tiering (Model Routing)**: Save frontier API token costs by classification. Route formatting, parsing, and basic retrieval steps to a local, low-latency LLaMA-3-8B model, and only call GPT-4o/Claude Sonnet when the router detects a complex task.
* **Persistent SQLite/Redis Ledgers**: All trace records, checkpoints, and time-travel steps are written locally to SQLite or Redis. You own 100% of your data and can inspect execution steps in our local visual studio (`meshflow studio`).

You can get it running with:
```bash
pip install meshflow
meshflow init local-gov
```

Let us know if you have ideas on expanding local execution security or integrating with tools like Ollama/vLLM!

GitHub: https://github.com/Anteneh-T-Tessema/meshflow
