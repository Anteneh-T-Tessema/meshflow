# Fine-Tuning Data Export

`FinetuneExporter` converts governed run history into fine-tuning datasets for Anthropic, OpenAI, or custom formats.

## Export a run

```python
from meshflow import FinetuneExporter, ExportFormat, TraceRecord, ExportFilter

exporter = FinetuneExporter(ledger=ledger)

# Export all approved steps as Anthropic JSONL
await exporter.export(
    format=ExportFormat.ANTHROPIC,
    output_path="finetune_data.jsonl",
    filter=ExportFilter(
        verdict="approved",         # only successful steps
        min_confidence=0.85,        # high-confidence outputs only
        exclude_blocked=True,
    ),
)
```

## Export formats

| Format | Description |
|--------|-------------|
| `ExportFormat.ANTHROPIC` | Anthropic fine-tuning JSONL (`messages` format) |
| `ExportFormat.OPENAI` | OpenAI fine-tuning JSONL (`messages` format) |
| `ExportFormat.JSONL` | Generic JSONL — one TraceRecord per line |
| `ExportFormat.CSV` | CSV with all fields |

## ExportFilter options

```python
ExportFilter(
    verdict="approved",           # "approved" | "rejected" | None (all)
    min_confidence=0.0,           # minimum stated_confidence
    max_cost_usd=None,            # exclude expensive steps
    run_ids=["run-abc", ...],     # specific runs only
    node_ids=["summarize", ...],  # specific nodes only
    exclude_blocked=True,         # drop blocked steps
    date_from="2026-01-01",       # ISO date filter
    date_to="2026-05-31",
)
```

## TraceRecord fields

```python
record: TraceRecord
record.run_id
record.node_id
record.input_task        # what the agent was asked
record.output_content    # what the agent produced
record.verdict           # "approved" | "rejected"
record.confidence        # 0–1
record.cost_usd
record.tokens_used
record.timestamp
```

## Iterate without writing

```python
async for record in exporter.iter_records(filter=my_filter):
    # process each record
    print(record.input_task[:80])
```
