# Agent Template Marketplace

The marketplace lets you publish, discover, and deploy pre-built agent templates with one command.

## Use a curated template

```python
from meshflow import template_by_name, templates_by_tag, load_curated_library, CURATED_TEMPLATES

# Load all 20 curated templates
load_curated_library()

# Find by name
template = template_by_name("research-pipeline")

# Find by tag
rag_templates = templates_by_tag("rag")
compliance_templates = templates_by_tag("hipaa")

# Instantiate
agent = template.to_agent()
crew = template.to_crew()
```

## CLI

```bash
meshflow templates list                          # show all curated templates
meshflow templates list --tag hipaa              # filter by tag
meshflow templates pull research-pipeline        # download and scaffold
meshflow templates push my-template.yaml         # publish to marketplace server
```

## AgentTemplate format

```python
from meshflow import AgentTemplate, TemplateRegistry

template = AgentTemplate(
    name="hipaa-research-pipeline",
    description="HIPAA-compliant research agent with PHI masking",
    tags=["hipaa", "research", "rag"],
    version="1.0.0",
    author="your-org",
    config={
        "role": "researcher",
        "compliance": "hipaa",
        "knowledge": ["docs/"],
        "guardrails": ["PIIBlockGuardrail"],
    },
)

registry = TemplateRegistry()
registry.register(template)
```

## MarketplaceClient

```python
from meshflow import MarketplaceClient

client = MarketplaceClient(base_url="https://marketplace.meshflow.dev")

# Search
results = await client.search("hipaa research")
for r in results:
    print(r.name, r.downloads, r.rating)

# Pull
template = await client.pull("hipaa-research-pipeline")
agent = template.to_agent()

# Push your own
await client.push(my_template, api_key="mf-pub-...")
```

## MarketplaceServer (self-hosted)

```python
from meshflow import MarketplaceServer

server = MarketplaceServer(registry=registry, port=4567)
server.start()
# → GET /templates, GET /templates/{name}, POST /templates
```
