import time
import requests

API_URL = "http://localhost:3000/api/ingest"
API_KEY = "mf-test-key-1234"

def push_run():
    run_id = f"run_{int(time.time())}"
    res = requests.post(f"{API_URL}/run", headers={"x-meshflow-key": API_KEY}, json={
        "run_id": run_id,
        "workflow_name": "Test Workflow",
        "agent_count": 2,
        "total_cost_usd": 0.052,
        "total_tokens": 1500,
        "duration_ms": 1200,
        "status": "completed"
    })
    print("Run ingestion:", res.status_code, res.text)
    
    # Push spans
    res = requests.post(f"{API_URL}/spans", headers={"x-meshflow-key": API_KEY}, json={
        "spans": [
            {
                "run_id": run_id,
                "agent_name": "researcher",
                "span_type": "llm_call",
                "name": "generate_query",
                "input_text": "Find latest news",
                "output_text": "Here is the news...",
                "started_at": "2026-06-06T12:00:00Z",
                "duration_ms": 800,
                "input_tokens": 500,
                "output_tokens": 1000,
                "cost_usd": 0.05,
                "status": "ok"
            },
            {
                "run_id": run_id,
                "agent_name": "researcher",
                "span_type": "tool_call",
                "name": "search_web",
                "started_at": "2026-06-06T12:00:01Z",
                "duration_ms": 400,
                "cost_usd": 0.002,
                "status": "ok"
            }
        ]
    })
    print("Spans ingestion:", res.status_code, res.text)

if __name__ == "__main__":
    push_run()
