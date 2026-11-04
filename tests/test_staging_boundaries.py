"""Local HTTP readiness checks for authentication, tenant scope and concurrency."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from meshflow.core.ledger import ReplayLedger
from meshflow.runtime.server import _build_app
from meshflow.security.api_keys import KeyStore


def test_serve_cli_starts_without_a_policy_file(monkeypatch):
    from meshflow.cli.main import _cmd_serve, build_parser
    from meshflow.runtime import server

    calls = []
    monkeypatch.delenv("MESHFLOW_OIDC_ISSUER", raising=False)
    monkeypatch.setattr(server, "serve", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(server, "_load_api_keys", lambda: set())
    _cmd_serve(build_parser().parse_args(["serve", "--port", "8123"]))
    assert len(calls) == 1
    assert calls[0]["port"] == 8123
    assert calls[0]["policy_file"] == ""


@pytest.mark.asyncio
async def test_http_auth_tenant_scope_and_concurrent_reads(tmp_path, make_step_record):
    path = str(tmp_path / "staging.db")
    keys = KeyStore(path)
    first_id, first_key = keys.create("first", role="viewer", tenant_id="tenant-a")
    _, second_key = keys.create("second", role="viewer", tenant_id="tenant-b")
    for tenant, run in (("tenant-a", "run-a"), ("tenant-b", "run-b")):
        ledger = ReplayLedger(path, tenant_id=tenant, enable_batching=False)
        await ledger.write(make_step_record(run_id=run, output=f"private {tenant}"))
        await ledger.aclose()
    async with TestClient(TestServer(await _build_app(set(), path))) as client:
        headers = {"Authorization": f"Bearer {first_key}"}
        assert (await client.get("/traces/run-a")).status == 401
        assert (
            await client.get("/traces/run-a", headers={"Authorization": "Bearer invalid"})
        ).status == 401
        own = await client.get("/traces/run-a", headers=headers)
        assert own.status == 200
        assert "private tenant-a" in await own.text()
        assert (await client.get("/traces/run-b", headers=headers)).status == 404
        spoofed = {"Authorization": f"Bearer {second_key}", "X-Tenant-ID": "tenant-a"}
        assert (await client.get("/traces/run-a", headers=spoofed)).status == 404
        responses = await asyncio.gather(
            *[client.get("/traces/run-a", headers=headers) for _ in range(32)]
        )
        assert all(response.status == 200 for response in responses)
        for response in responses:
            assert "private tenant-b" not in await response.text()
        assert (
            await client.post("/keys", headers=headers, json={"name": "escalated", "role": "admin"})
        ).status == 403
        keys.revoke(first_id)
        assert (await client.get("/traces/run-a", headers=headers)).status == 401


@pytest.mark.asyncio
async def test_oidc_validation_failure_does_not_fall_back_to_static_key(tmp_path, monkeypatch):
    from meshflow.security import oidc

    def unavailable():
        raise RuntimeError("identity validator unavailable")

    monkeypatch.setattr(oidc, "get_oidc_middleware", unavailable)
    async with TestClient(
        TestServer(await _build_app({"static-key"}, str(tmp_path / "oidc.db")))
    ) as client:
        response = await client.get("/traces", headers={"Authorization": "Bearer static-key"})
        assert response.status == 401


@pytest.mark.asyncio
async def test_tenant_checkpoint_and_destructive_operations_are_scoped(tmp_path, make_step_record):
    path = str(tmp_path / "scoped.db")
    owner = ReplayLedger(path, tenant_id="owner", enable_batching=False)
    other = ReplayLedger(path, tenant_id="other", enable_batching=False)
    try:
        await owner.write(make_step_record(run_id="private", output="private data"))
        await owner.save_checkpoint("private", {"state": "private state"})
        assert await other.get_run("private") == []
        assert await other.load_checkpoint_data("private") is None
        assert await other.list_paused_runs() == []
        with pytest.raises(PermissionError):
            await other.save_checkpoint("private", {"state": "overwritten"})
        assert await other.anonymize_run("private") == 0
        assert await other.delete_run("private") == 0
        await other.delete_checkpoint("private")
        with pytest.raises(PermissionError):
            await other.delete_tenant("owner")
        assert (await owner.load_checkpoint_data("private"))["state"] == "private state"
        assert (await owner.get_run("private"))[0]["output_content"] == "private data"
    finally:
        await owner.aclose()
        await other.aclose()


@pytest.mark.asyncio
async def test_http_run_persists_trace_under_authenticated_tenant(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHFLOW_MOCK", "1")
    path = str(tmp_path / "roundtrip.db")
    keys = KeyStore(path)
    _, key = keys.create("operator", role="operator", tenant_id="tenant-a")
    _, other_key = keys.create("other", role="viewer", tenant_id="tenant-b")
    async with TestClient(TestServer(await _build_app(set(), path))) as client:
        headers = {"Authorization": f"Bearer {key}"}
        response = await client.post(
            "/run",
            headers=headers,
            json={
                "task": "Summarize quarterly revenue",
                "policy": {"mode": "dev", "enable_uncertainty": False, "deterministic_gate": False},
                "context": {"_tenant_id": "tenant-b"},
            },
        )
        assert response.status == 200, await response.text()
        result = await response.json()
        run_id = result["run_id"]
        own = await client.get(f"/traces/{run_id}", headers=headers)
        assert own.status == 200
        trace = await own.json()
        assert trace["steps"]
        assert all(step["tenant_id"] == "tenant-a" for step in trace["steps"])
        assert (
            await client.get(f"/traces/{run_id}", headers={"Authorization": f"Bearer {other_key}"})
        ).status == 404


@pytest.mark.asyncio
async def test_viewer_cannot_start_completion_or_stream(tmp_path):
    path = str(tmp_path / "viewer.db")
    _, key = KeyStore(path).create("viewer", role="viewer", tenant_id="tenant-a")
    async with TestClient(TestServer(await _build_app(set(), path))) as client:
        for route in ("/run", "/stream"):
            response = await client.post(
                route,
                headers={"Authorization": f"Bearer {key}"},
                json={"task": "Summarize revenue"},
            )
            assert response.status == 403
