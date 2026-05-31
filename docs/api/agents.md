# meshflow.agents — Agent API Reference

Key classes and functions in the agents subsystem.

## Agent

```python
@dataclass
class Agent:
    name: str
    role: str | AgentRole = AgentRole.EXECUTOR
    model: str = ""                    # empty → auto-detect from env
    llm: Any = None                    # LLM instance or any LLMProvider
    tools: list[Any] = []
    skills: list[str] = []             # built-in skill names
    mcps: list[Any] = []               # MCP server URLs or StdioServerParams
    input_guardrails: list[Any] = []
    output_guardrails: list[Any] = []
    knowledge: list[Any] = []          # str | VectorStore | KnowledgeSource
    memory: bool = False
    memory_backend: Any = None
    memory_session_id: str = ""
    cache: Any = None                  # LLMCache | True | False
    healing: Any = None                # HealingPolicy
    teachable: bool = False
    handoffs: list[Any] = []
    delegates: list[Agent] = []
    system_prompt: str = ""
    risk: RiskTier = RiskTier.READ_ONLY
    policy: Policy | str | None = None
    model_router: Any = None           # ModelRouter — auto-selects model tier
    context_pruner: Any = None         # SlidingWindowPruner | SummaryPruner
    provider: Any = None              # low-level escape hatch
```

### Methods

```python
await agent.run(task, context=None)           # → dict with "result", "tokens", "cost_usd"
await agent.run_typed(task, OutputModel)      # → parsed Pydantic model
await agent.run_structured(task, schema)      # → StructuredOutputResult
await agent.run_multimodal(task, inputs)      # → dict (with image/doc/audio inputs)
await agent.run_with_handoffs(task)           # → HandoffResult
await agent.run_with_healing(task, policy)    # → HealingResult
async for chunk in agent.stream(task):        # → AsyncIterator[StreamChunk]
```

## Built-in Agent Library

```python
from meshflow import agents

researcher  = agents.ResearchAgent()
coder       = agents.CoderAgent()
critic      = agents.CriticAgent()
planner     = agents.PlannerAgent()
executor    = agents.ExecutorAgent()
guardian    = agents.GuardianAgent()
summarizer  = agents.SummarizerAgent()
```

## ReActAgent

```python
from meshflow import ReActAgent, ReActResult, ThoughtStep

agent = ReActAgent(name="react", tools=[web_search, calculator])
result: ReActResult = await agent.run("What is the population of France × 2?")

for step in result.thought_steps:
    print(step.thought, step.action, step.observation)
```

## CriticAgent

```python
from meshflow import CriticAgent, CriticResult

critic = CriticAgent(
    base_agent=my_agent,
    stop_on_confidence=0.92,
    max_rounds=3,
)
result: CriticResult = await critic.run("Write a sorting algorithm")
```

## AgentSession

```python
from meshflow.agents.session import AgentSession
from meshflow import SlidingWindowPruner

session = AgentSession(
    agent,
    max_history_turns=20,
    context_pruner=SlidingWindowPruner(max_messages=10),
)
r = await session.chat("Hello")
r = await session.chat("Follow up question")
print(session.history)       # list[Turn]
print(session.total_tokens)
```

## ModelRouter

```python
from meshflow import ModelRouter, RouterConfig, RoutingDecision

router = ModelRouter()
decision: RoutingDecision = router.route("classify this text", tools=[])
# decision.tier   → "nano" | "small" | "medium" | "large"
# decision.model  → resolved model string
# decision.rationale → why this tier was chosen

# Wire into Agent:
agent = Agent(name="smart", role="executor", model_router=router)
```

## AgentPool

```python
from meshflow import AgentPool, PoolStats, register_pool

pool = AgentPool(agents=[agent1, agent2, agent3])
register_pool("workers", pool)

result = await pool.run("task text")
stats: PoolStats = pool.stats()
```

## Supervisor

```python
from meshflow import Supervisor, SupervisorResult

sup = Supervisor(
    orchestrator=planner,
    workers=[researcher, coder, reviewer],
)
result: SupervisorResult = await sup.run("Build a REST API")
```

## AdversarialTeam

```python
from meshflow import AdversarialTeam, AdversarialResult

team = AdversarialTeam(
    proposer=Agent(name="p", role="executor"),
    attacker=Agent(name="a", role="critic"),
    judge=Agent(name="j", role="guardian"),
)
result: AdversarialResult = await team.run("Is GPT-4 safe for medical advice?")
```
