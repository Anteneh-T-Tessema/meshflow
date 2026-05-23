"""Shared pytest fixtures for MeshFlow test suite.

Live fixtures (server_url, live_client) are only active when ANTHROPIC_API_KEY
is set and the test is marked with @pytest.mark.live — they are no-ops otherwise.
"""
from __future__ import annotations

import asyncio
import os
import socket
import threading
from typing import Any

import pytest


# ── Marker registration ───────────────────────────────────────────────────────
# (also declared in pyproject.toml [tool.pytest.ini_options] markers)


def pytest_configure(config: Any) -> None:
    config.addinivalue_line(
        "markers",
        "live: tests that call real LLM APIs — skipped unless ANTHROPIC_API_KEY is set",
    )


# ── In-process ledger fixture ─────────────────────────────────────────────────


@pytest.fixture
def in_memory_ledger():
    """A fresh in-memory ReplayLedger for each test."""
    from meshflow.core.ledger import ReplayLedger
    return ReplayLedger(":memory:")


@pytest.fixture
def shared_ledger(tmp_path):
    """A file-backed ReplayLedger in a temp directory — survives within a test."""
    from meshflow.core.ledger import ReplayLedger
    return ReplayLedger(str(tmp_path / "test_runs.db"))


# ── Policy fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def dev_policy():
    from meshflow.core.schemas import policy_for_mode
    return policy_for_mode("dev")


@pytest.fixture
def standard_policy():
    from meshflow.core.schemas import policy_for_mode
    return policy_for_mode("standard")


@pytest.fixture
def regulated_policy():
    from meshflow.core.schemas import policy_for_mode
    return policy_for_mode("regulated", budget_usd=1.0)


# ── Live server fixture (only used by test_live.py) ──────────────────────────

def _find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(port: int, ready: threading.Event) -> None:
    """Boot a real aiohttp MeshFlow server in a daemon thread.

    serve() is a synchronous blocking function that calls asyncio.run() internally.
    We patch it to signal ready before entering the forever-loop.
    """
    import asyncio
    from aiohttp import web
    from meshflow.runtime.server import _build_app  # type: ignore[attr-defined]

    api_keys_str = os.environ.get("MESHFLOW_API_KEYS", "")
    api_keys: set[str] = {k for k in api_keys_str.split(",") if k}

    async def _run() -> None:
        app = await _build_app(api_keys, ":memory:")
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", port)
        await site.start()
        ready.set()
        await asyncio.Event().wait()  # run forever (daemon thread exits with process)

    asyncio.run(_run())


@pytest.fixture(scope="session")
def live_server_url():
    """Start a real MeshFlow server for the session; yield its base URL.

    Only active when ANTHROPIC_API_KEY is present — otherwise skips the test.
    The server runs in a daemon thread and dies when the test process exits.
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set — live server not started")

    port = _find_free_port()
    ready = threading.Event()
    t = threading.Thread(target=_start_server, args=(port, ready), daemon=True)
    t.start()

    if not ready.wait(timeout=10):
        pytest.skip("MeshFlow server did not start in 10s")

    return f"http://127.0.0.1:{port}"


@pytest.fixture(scope="session")
def live_client(live_server_url: str):
    """Async MeshFlowClient connected to the live session server."""
    from meshflow.client import MeshFlowClient

    async def _make():
        return MeshFlowClient(live_server_url, api_key="")

    client = asyncio.get_event_loop().run_until_complete(_make())
    yield client
    asyncio.get_event_loop().run_until_complete(client.close())


# ── Step record factory ───────────────────────────────────────────────────────


@pytest.fixture
def make_step_record():
    """Factory that creates valid StepRecord instances for ledger tests."""
    import datetime
    import uuid
    from meshflow.core.ledger import StepRecord

    def _make(
        run_id: str = "",
        node_id: str = "test-node",
        output: str = "test output",
        cost: float = 0.001,
        tokens: int = 10,
        blocked: bool = False,
        **kwargs: Any,
    ) -> StepRecord:
        return StepRecord(
            run_id=run_id or str(uuid.uuid4()),
            step_id=str(uuid.uuid4()),
            node_id=node_id,
            node_kind="python",
            input_task="test task",
            output_content=output,
            verdict="commit",
            blocked=blocked,
            block_reason="" if not blocked else "policy",
            uncertainty=0.1,
            cost_usd=cost,
            tokens_used=tokens,
            carbon_gco2=0.0001,
            duration_ms=5.0,
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            **kwargs,
        )

    return _make
