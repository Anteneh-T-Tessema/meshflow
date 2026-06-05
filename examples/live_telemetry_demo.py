import requests
import json
import uuid
import time
import random

# Configure the local MeshFlow dashboard ingestion endpoint
INGEST_URL = "http://localhost:3000/api/ingest/run"
API_KEY = "demo_key_123" # In a real scenario, this is the MESHFLOW_API_KEY from the dashboard

def simulate_agent_run():
    print("🚀 Initializing MeshFlow Agent Swarm Simulation...")
    time.sleep(1)
    
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    print(f"📡 Generating Run ID: {run_id}")
    
    # Simulate some work
    print("🧠 Orchestrator Agent planning tasks...")
    time.sleep(1.5)
    print("🕸️ Data Extractor Agent scraping content...")
    time.sleep(2)
    print("⚖️ Legal Reviewer Agent checking for compliance...")
    time.sleep(1.5)
    
    # Generate realistic telemetry metrics
    tokens = random.randint(50000, 200000)
    cost = tokens * 0.00001
    
    payload = {
        "run_id": run_id,
        "workflow_name": "Legal_Review_Swarm",
        "agent_count": 3,
        "total_cost_usd": round(cost, 4),
        "total_tokens": tokens,
        "cache_hit_rate": round(random.uniform(0.1, 0.4), 2),
        "policy": "strict_zero_trust",
        "compliance": "HIPAA_CLEAN",
        "status": "success",
        "duration_ms": random.randint(4000, 8000),
        "violations": 0
    }
    
    print("\n📤 Sending Telemetry Payload to Next.js Dashboard...")
    print(json.dumps(payload, indent=2))
    
    headers = {
        "Content-Type": "application/json",
        "x-meshflow-key": API_KEY
    }
    
    try:
        response = requests.post(INGEST_URL, json=payload, headers=headers)
        if response.status_code == 200:
            print(f"✅ Success! Data ingested. View it at http://localhost:3000/dashboard")
        else:
            print(f"❌ Failed. Status: {response.status_code}, Msg: {response.text}")
    except requests.exceptions.ConnectionError:
        print("⚠️ Failed to connect. Make sure the Next.js server is running on localhost:3000")

if __name__ == "__main__":
    simulate_agent_run()
