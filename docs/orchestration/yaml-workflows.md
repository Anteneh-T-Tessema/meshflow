# YAML Workflows

`WorkflowDefinition` is a governed, graph-topological workflow that can be loaded from YAML, executed with full audit trails, and round-tripped back to YAML for version control.

```python
from meshflow.core.workflow import WorkflowDefinition
from meshflow.core.mesh import Mesh

wf     = WorkflowDefinition.from_yaml("pipeline.yaml")
mesh   = Mesh()
result = await mesh.run_workflow(wf, task="Summarise Q3 earnings")
print(result.output)
```

## `from_yaml(path, node_registry=None)`

Loads a `WorkflowDefinition` from a YAML file. The file's SHA-256 is stored on `wf.yaml_sha256` for exact-replay version pinning.

```python
registry = {
    "crews.market_research": my_crewai_crew,
    "graphs.fact_check":     my_langgraph_graph,
    "agents.custom_fn":      my_python_callable,
}
wf = WorkflowDefinition.from_yaml("mesh.yaml", node_registry=registry)
```

## Full YAML Schema

```yaml
name: research_pipeline        # workflow name (required)
version: "2"                   # arbitrary version string

metadata:                      # free-form user metadata
  owner: platform-team
  ticket: PLAT-1234

policy:
  mode: standard               # dev | standard | regulated | legal-critical | hipaa
  budget_usd: 2.00             # hard spend cap; aborts if exceeded
  budget_tokens: 500000
  max_steps: 30
  timeout_s: 300
  enable_guardian: true        # prompt-injection scanner on every node
  enable_collusion_audit: true
  enable_uncertainty: true
  human_approval_tier: irreversible  # none | read_only | internal | external_io | irreversible
  max_forecast_usd: 1.50       # pre-run cost gate; 0 = disabled
  max_replans: 3               # dynamic replanning limit

nodes:
  planner:
    kind: native               # native | python | crewai | langgraph | autogen | human | http | subgraph
    role: planner              # planner | researcher | executor | critic
    model: claude-sonnet-4-6   # override model tier
    risk: read_only            # read_only | internal | external_io | irreversible
    timeout_s: 60
    retry_on_fail: true
    max_retries: 2
    output_schema:             # JSON schema for structured output validation
      type: object
      required: [plan]
      properties:
        plan: {type: string}

  researcher:
    kind: python
    ref: agents.research_fn    # resolved via node_registry

  fact_check:
    kind: langgraph
    ref: graphs.fact_check

  approval:
    kind: human                # always pauses for human input

  publisher:
    kind: native
    role: executor

edges:
  - planner -> researcher                  # shorthand
  - researcher -> fact_check
  - from: fact_check                       # long form — supports conditions
    to: approval
    condition: "confidence < 0.8"          # Python expression; available: output, content, confidence
  - from: fact_check
    to: publisher
    condition: "confidence >= 0.8"

loop_edges:                    # back-edges for iterative refinement
  - from: fact_check
    to: researcher
    condition: "confidence < 0.5"
    max_iterations: 5

entry: planner                 # explicit entry node (default: first node declared)
terminal:                      # nodes that end the workflow
  - publisher

compliance:                    # optional real-time compliance enforcement
  frameworks: [hipaa, sox]
  block_on_violation: true

context_bus:                   # fan-in merge strategies for parallel branches
  merge_strategies:
    summary: append            # overwrite | append | select_highest_confidence | logical_and | logical_or
```

## `to_yaml(path=None)`

Round-trip export — works on any `WorkflowDefinition` whether built from YAML or the Python API.

```python
wf = WorkflowDefinition.from_yaml("pipeline.yaml")
# ... modify nodes or edges ...
yaml_str = wf.to_yaml()                  # in-memory string
wf.to_yaml("pipeline_v2.yaml")           # write to disk
```

## CLI

```bash
# Run a workflow YAML against a task
meshflow run pipeline.yaml --task "Summarise Q3 earnings"

# Diff two YAML versions
meshflow diff pipeline_v1.yaml pipeline_v2.yaml
```

## Crew YAML (`kind: crew`)

Wrap a CrewAI crew in the MeshFlow governance plane by referencing it via `node_registry`:

```yaml
nodes:
  market_research:
    kind: crewai
    ref: crews.market_research   # resolved via node_registry at load time
    risk: external_io
```

```python
import my_crews
wf = WorkflowDefinition.from_yaml("crew_pipeline.yaml", {
    "crews.market_research": my_crews.market_research_crew,
})
```

## `@workflow` Decorator

Makes any factory function portable and CI-diffable:

```python
from meshflow.core.workflow_decorator import workflow
from meshflow.core.workflow import WorkflowDefinition
from meshflow.core.node import MeshNode, NodeKind

@workflow
def research_pipeline():
    wf = WorkflowDefinition(name="research", version="2")
    wf.add_node(MeshNode(id="planner",    kind=NodeKind.NATIVE))
    wf.add_node(MeshNode(id="researcher", kind=NodeKind.NATIVE))
    wf.add_node(MeshNode(id="writer",     kind=NodeKind.NATIVE))
    wf.add_edge("planner", "researcher")
    wf.add_edge("researcher", "writer")
    wf.set_terminal("writer")
    return wf

# Export to YAML (versionable in git)
research_pipeline.to_yaml("pipelines/research.yaml")

# Round-trip load
wf = research_pipeline.load("pipelines/research.yaml")

# CI diff between versions
diff = research_pipeline.diff("pipelines/v1/research.yaml", "pipelines/v2/research.yaml")
print(diff.summary())
if diff.has_breaking_changes:
    raise SystemExit("Breaking pipeline changes detected")

# Get the live WorkflowDefinition
wf = research_pipeline()        # or research_pipeline.build()
```

### `WorkflowProxy` Methods

| Method | Description |
|--------|-------------|
| `to_yaml(path=None)` | Export YAML string; optionally write to file |
| `load(path, node_registry=None)` | Load `WorkflowDefinition` from YAML |
| `diff(path_a, path_b)` | Compare two YAML versions; returns `DiffResult` |
| `schema()` | Return node topology as JSON-serialisable dict |
| `build()` | Materialise the `WorkflowDefinition` (alias for calling the proxy) |

## Fan-Out / Fan-In Example

Parallel branches with automatic join:

```yaml
name: parallel_research
version: "1"

policy:
  budget_usd: 3.00
  max_steps: 20

nodes:
  planner:      {kind: native, role: planner}
  branch_eu:    {kind: python, ref: agents.eu_research}
  branch_us:    {kind: python, ref: agents.us_research}
  branch_apac:  {kind: python, ref: agents.apac_research}
  synthesizer:  {kind: native, role: executor}

edges:
  - planner -> branch_eu
  - planner -> branch_us
  - planner -> branch_apac
  - branch_eu   -> synthesizer
  - branch_us   -> synthesizer
  - branch_apac -> synthesizer

context_bus:
  merge_strategies:
    findings: append   # all three branches' findings are concatenated

terminal:
  - synthesizer
```

`branch_eu`, `branch_us`, and `branch_apac` run concurrently via `asyncio.gather`. `synthesizer` runs after all three complete.
