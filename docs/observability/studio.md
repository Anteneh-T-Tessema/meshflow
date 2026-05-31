# Visual Trace Studio

`TraceServer` serves a browser-based UI for inspecting workflow traces, execution graphs, and RAG pipelines.

## Start the studio

```bash
meshflow trace-server
# → http://localhost:8765
```

Or from Python:

```python
from meshflow import TraceServer

server = TraceServer(ledger=ledger, port=8765)
server.start()   # non-blocking daemon thread
```

## Pages

### Trace viewer (`/`)
- Step-by-step execution timeline for any run ID
- Per-step: verdict, cost, tokens, carbon, duration, block reason
- Hash-chain integrity indicator
- Search and filter by node, verdict, or timestamp

### Graph view (`/graph`)
- Visual node-and-edge DAG of the workflow
- Color-coded by verdict (green=approved, red=blocked, yellow=paused)
- Hover for node stats; click to expand step details
- Mermaid and DOT export buttons

### RAG builder (`/rag`)
- Interactive knowledge source configuration
- Test retrieval queries against live VectorStore
- Token budget visualization

## Navigation

All three pages share a top navigation bar. The active page is highlighted. Deep-link any page via URL.

## CLI trace viewer (text mode)

```bash
meshflow trace <run_id>          # terminal trace timeline
meshflow trace <run_id> --json   # JSON output
meshflow trace <run_id> --open   # open browser to trace-server
```
