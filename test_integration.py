import os
from meshflow.cloud.client import MeshFlowCloud, get_cloud_client
from meshflow.agents.model_router import RouterConfig
from meshflow.zero_trust.orchestrator import ZeroTrustOrchestrator

def main():
    print("Testing MeshFlow SDK Integration...")
    
    # Enable the cloud client but point to local dev
    os.environ["MESHFLOW_CLOUD_URL"] = "http://localhost:3000"
    os.environ["MESHFLOW_API_KEY"] = "test_key"
    
    client = get_cloud_client()
    print("Cloud client base URL:", client.base_url)
    
    # Try fetching (this will fail if the server isn't running, but the code shouldn't crash)
    print("Attempting to hydrate RouterConfig from cloud...")
    config = RouterConfig.from_cloud(client)
    print("RouterConfig tiers:", config.tiers)
    
    print("Attempting to hydrate ZeroTrustOrchestrator from cloud...")
    zt = ZeroTrustOrchestrator.from_cloud(client)
    print("ZeroTrustOrchestrator policy tier:", zt._policy.tier)
    
    print("Integration test complete.")

if __name__ == "__main__":
    main()
