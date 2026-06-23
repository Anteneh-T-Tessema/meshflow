import asyncio
import os
import pytest
import shutil
import tempfile
from meshflow.core.ledger import ReplayLedger
from meshflow.core.runtime import StepRecord
from meshflow.core.policy_loader import WasmPolicyEngine
from meshflow.intelligence.collusion import SteganographicChannelDetector

@pytest.mark.asyncio
async def test_concurrent_writes_and_merkle_verification():
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_remediation.db")
    
    try:
        ledger = ReplayLedger(db_path, enable_batching=True)
        run_id = "test-run-concurrent"
        
        async def write_record(idx: int):
            record = StepRecord(
                run_id=run_id,
                step_id=f"step-{idx}",
                node_id="test-node",
                node_kind="native",
                input_task=f"Task {idx}",
                output_content=f"This is output content from concurrent task {idx}",
                verdict="commit",
                blocked=False,
                block_reason="",
                uncertainty=0.1,
                cost_usd=0.001,
                tokens_used=100,
                carbon_gco2=0.01,
                duration_ms=5.0,
                timestamp="2026-06-23T06:51:44-04:00",
                prev_hash="",
                metadata={"custom_idx": idx},
            )
            await ledger.write(record)

        # Gather concurrent writes
        await asyncio.gather(*(write_record(i) for i in range(50)))
        
        # Close the ledger, which waits for background worker to flush
        await ledger.aclose()
        
        # Verify the ledger database directly
        verifier = ReplayLedger(db_path, enable_batching=False)
        result = await verifier.verify_chain(run_id)
        
        assert result["valid"] is True
        assert result["steps_verified"] == 50
        assert len(result["errors"]) == 0
        
        # Check that records have merkle_proof in metadata
        records = await verifier.get_run(run_id)
        assert len(records) == 50
        for r in records:
            meta = r.get("metadata", {})
            assert "merkle_proof" in meta
            proof_data = meta["merkle_proof"]
            assert "proof" in proof_data
            assert "index" in proof_data
            assert "batch_root" in proof_data
            assert "master_root" in proof_data
            
    finally:
        shutil.rmtree(temp_dir)


def test_steganographic_audit_suppression_structured_json_code():
    detector = SteganographicChannelDetector()
    
    # 1. Natural Language outputs that correlate in length and word choice (Schelling words)
    # This should trigger an alert under normal correlation circumstances
    for i in range(10):
        text_a = "always " * (i + 5)
        text_b = "always " * (i + 5)
        detector.record_output("agent-a", text_a, role="natural_language")
        detector.record_output("agent-b", text_b, role="natural_language")
        
    alerts = detector.analyse([("agent-a", "agent-b")])
    assert alerts[0].is_alert is True

    # 2. Structured JSON / code inputs.
    detector_suppressed = SteganographicChannelDetector()
    for i in range(10):
        json_a = f'{{"status": "success", "step_id": "{i}", "values": [1, 2, 3]}}'
        json_b = f'{{"status": "success", "step_id": "{i}", "values": [1, 2, 3]}}'
        detector_suppressed.record_output("agent-a", json_a, role="code")
        detector_suppressed.record_output("agent-b", json_b, role="code")
        
    alerts_suppressed = detector_suppressed.analyse([("agent-a", "agent-b")])
    assert alerts_suppressed[0].is_alert is False


def test_cryptographic_key_exchange_skipped():
    detector = SteganographicChannelDetector()
    
    for i in range(10):
        key_a = f"-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAv{i}-----END PUBLIC KEY-----"
        key_b = f"-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAv{i}-----END PUBLIC KEY-----"
        
        detector.record_output("agent-a", key_a, role="cryptography_exchange")
        detector.record_output("agent-b", key_b, role="cryptography_exchange")
        
    alerts = detector.analyse([("agent-a", "agent-b")])
    assert alerts[0].is_alert is False


def test_wasm_policy_loader_fallback():
    engine = WasmPolicyEngine("nonexistent_policy_engine.wasm")
    assert engine.has_wasm is False
    res = engine.evaluate_compliance("hipaa", "minimum_necessary", {"test": "data"})
    assert res["status"] == "fallback"
    assert "Wasm" in res["reason"]
