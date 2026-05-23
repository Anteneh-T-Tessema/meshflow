"""MeshFlow Python client SDK.

Talks to a running MeshFlow server over HTTP — no local imports of the core
library required. Designed to mirror the TypeScript SDK surface exactly so
teams can switch languages without relearning the API.

Usage::

    from meshflow.client import MeshFlowClient

    client = MeshFlowClient("http://localhost:8000", api_key="my-key")

    # Run a task and wait for the result
    result = await client.run("Summarise the quarterly report")
    print(result.status, result.total_cost_usd)

    # Stream token-by-token
    async for event in client.stream("Analyse this contract"):
        if event.kind == "token_delta":
            print(event.text, end="", flush=True)

    # Inspect the audit trail
    trace = await client.get_trace(result.run_id)

    # Human-in-the-loop approval
    await client.approve_hitl(result.run_id, reviewer_id="alice", notes="LGTM")

Synchronous wrapper::

    from meshflow.client import MeshFlowClient
    client = MeshFlowClient.sync("http://localhost:8000", api_key="my-key")
    result = client.run_sync("Summarise the report")
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, cast


# ── Data models ───────────────────────────────────────────────────────────────


@dataclass
class PolicyConfig:
    mode: str = "standard"
    budget_usd: float | None = None
    budget_tokens: int | None = None
    timeout_s: float | None = None
    max_steps: int | None = None
    deterministic_gate: bool | None = None
    enable_guardian: bool | None = None
    enable_collusion_audit: bool | None = None
    enable_uncertainty: bool | None = None
    enable_environmental: bool | None = None
    carbon_budget_g: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class RunResult:
    run_id: str
    status: str
    output: Any
    total_cost_usd: float
    total_tokens: int
    total_carbon_g: float
    duration_s: float
    ledger_entries: int
    trace_id: str
    checkpoints: list[str]
    error: str
    collusion_alerts: int

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunResult:
        return cls(
            run_id=d.get("run_id", ""),
            status=d.get("status", "unknown"),
            output=d.get("output"),
            total_cost_usd=float(d.get("total_cost_usd", 0.0)),
            total_tokens=int(d.get("total_tokens", 0)),
            total_carbon_g=float(d.get("total_carbon_g", 0.0)),
            duration_s=float(d.get("duration_s", 0.0)),
            ledger_entries=int(d.get("ledger_entries", 0)),
            trace_id=d.get("trace_id", ""),
            checkpoints=d.get("checkpoints", []),
            error=d.get("error", ""),
            collusion_alerts=int(d.get("collusion_alerts", 0)),
        )


@dataclass
class MeshEvent:
    kind: str
    agent_id: str = ""
    role: str = ""
    step: int = 0
    uncertainty: float = 0.0
    cost_usd: float = 0.0
    tokens: int = 0
    blocked_by: str = ""
    output: str = ""
    text: str = ""  # populated when kind == "token_delta"
    step_id: str = ""
    run_id: str = ""
    error: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MeshEvent:
        return cls(
            kind=d.get("kind", ""),
            agent_id=d.get("agent_id", ""),
            role=d.get("role", ""),
            step=int(d.get("step", 0)),
            uncertainty=float(d.get("uncertainty", 0.0)),
            cost_usd=float(d.get("cost_usd", 0.0)),
            tokens=int(d.get("tokens", 0)),
            blocked_by=d.get("blocked_by", ""),
            output=d.get("output", ""),
            text=d.get("text", ""),
            step_id=d.get("step_id", ""),
            run_id=d.get("run_id", ""),
            error=d.get("error", ""),
            raw=d,
        )


@dataclass
class StepRecord:
    step_id: str
    run_id: str
    node_id: str
    node_kind: str
    input_task: str
    output_content: str
    verdict: str
    blocked: bool
    block_reason: str
    uncertainty: float
    cost_usd: float
    tokens_used: int
    carbon_gco2: float
    duration_ms: float
    timestamp: str
    prev_hash: str
    entry_hash: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StepRecord:
        return cls(
            step_id=d.get("step_id", ""),
            run_id=d.get("run_id", ""),
            node_id=d.get("node_id", ""),
            node_kind=d.get("node_kind", ""),
            input_task=d.get("input_task", ""),
            output_content=d.get("output_content", ""),
            verdict=d.get("verdict", ""),
            blocked=bool(d.get("blocked", False)),
            block_reason=d.get("block_reason", ""),
            uncertainty=float(d.get("uncertainty", 0.0)),
            cost_usd=float(d.get("cost_usd", 0.0)),
            tokens_used=int(d.get("tokens_used", 0)),
            carbon_gco2=float(d.get("carbon_gco2", 0.0)),
            duration_ms=float(d.get("duration_ms", 0.0)),
            timestamp=d.get("timestamp", ""),
            prev_hash=d.get("prev_hash", ""),
            entry_hash=d.get("entry_hash", ""),
        )


@dataclass
class TraceSummary:
    steps: int
    nodes: list[str]
    total_cost_usd: float
    total_tokens: int
    total_carbon_gco2: float
    blocked_steps: int
    verdicts: list[str]
    start: str
    end: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TraceSummary:
        ts = d.get("timestamps", {})
        return cls(
            steps=int(d.get("steps", 0)),
            nodes=d.get("nodes", []),
            total_cost_usd=float(d.get("total_cost_usd", 0.0)),
            total_tokens=int(d.get("total_tokens", 0)),
            total_carbon_gco2=float(d.get("total_carbon_gco2", 0.0)),
            blocked_steps=int(d.get("blocked_steps", 0)),
            verdicts=d.get("verdicts", []),
            start=ts.get("start", ""),
            end=ts.get("end", ""),
        )


@dataclass
class Trace:
    run_id: str
    summary: TraceSummary
    steps: list[StepRecord]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Trace:
        return cls(
            run_id=d.get("run_id", ""),
            summary=TraceSummary.from_dict(d.get("summary", {})),
            steps=[StepRecord.from_dict(s) for s in d.get("steps", [])],
        )


@dataclass
class PausedRun:
    run_id: str
    paused_at: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PausedRun:
        return cls(run_id=d.get("run_id", ""), paused_at=d.get("paused_at", ""))


@dataclass
class HealthResponse:
    ok: bool
    version: str
    uptime_s: float
    db: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> HealthResponse:
        return cls(
            ok=bool(d.get("ok", False)),
            version=d.get("version", ""),
            uptime_s=float(d.get("uptime_s", 0.0)),
            db=d.get("db", ""),
        )


# ── Async client ──────────────────────────────────────────────────────────────


class MeshFlowClient:
    """Async HTTP client for the MeshFlow server API.

    Parameters
    ----------
    base_url:
        URL of the running MeshFlow server, e.g. ``"http://localhost:8000"``.
    api_key:
        Bearer token set via ``MESHFLOW_API_KEYS`` on the server.
        Pass an empty string for dev-mode servers with no auth.
    default_policy:
        Default policy config merged with per-call overrides.
    timeout:
        Default request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        default_policy: PolicyConfig | None = None,
        timeout: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._default_policy = default_policy or PolicyConfig()
        self._timeout = timeout
        self._session: Any = None  # aiohttp.ClientSession, lazy

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    async def _ensure_session(self) -> Any:
        if self._session is None or self._session.closed:
            import aiohttp

            self._session = aiohttp.ClientSession(
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            )
        return self._session

    async def close(self) -> None:
        """Close the underlying HTTP session. Call when done to avoid resource leaks."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> MeshFlowClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    def _merged_policy(self, override: PolicyConfig | None) -> dict[str, Any]:
        base = self._default_policy.to_dict()
        if override:
            base.update(override.to_dict())
        return base

    async def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> Any:
        session = await self._ensure_session()
        url = f"{self._base_url}{path}"
        kwargs: dict[str, Any] = {}
        if body is not None:
            kwargs["json"] = body
        async with session.request(method, url, **kwargs) as resp:
            if resp.status >= 400:
                text = await resp.text()
                raise MeshFlowError(resp.status, text)
            return await resp.json()

    # ── Health ────────────────────────────────────────────────────────────────

    async def health(self) -> HealthResponse:
        """Check server health. Does not require authentication."""
        session = await self._ensure_session()
        async with session.get(f"{self._base_url}/health") as resp:
            return HealthResponse.from_dict(await resp.json())

    # ── Run ───────────────────────────────────────────────────────────────────

    async def run(
        self,
        task: str,
        policy: PolicyConfig | None = None,
        context: dict[str, Any] | None = None,
    ) -> RunResult:
        """Execute a task and wait for completion."""
        data = await self._request(
            "POST",
            "/run",
            {
                "task": task,
                "policy": self._merged_policy(policy),
                "context": context or {},
            },
        )
        return RunResult.from_dict(data)

    # ── Stream ────────────────────────────────────────────────────────────────

    async def stream(
        self,
        task: str,
        policy: PolicyConfig | None = None,
        context: dict[str, Any] | None = None,
    ) -> AsyncIterator[MeshEvent]:
        """Stream events as they arrive. NDJSON over chunked HTTP.

        Example::

            async for event in client.stream("Analyse this contract"):
                if event.kind == "token_delta":
                    print(event.text, end="", flush=True)
                elif event.kind == "step_end":
                    print(f"\\n[step done  cost=${event.cost_usd:.4f}]")
        """
        session = await self._ensure_session()
        url = f"{self._base_url}/stream"
        body = {
            "task": task,
            "policy": self._merged_policy(policy),
            "context": context or {},
        }
        async with session.post(url, json=body) as resp:
            if resp.status >= 400:
                text = await resp.text()
                raise MeshFlowError(resp.status, text)

            buffer = ""
            async for chunk in resp.content.iter_chunked(4096):
                buffer += chunk.decode("utf-8", errors="replace")
                lines = buffer.split("\n")
                buffer = lines.pop()
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield MeshEvent.from_dict(json.loads(line))
                    except json.JSONDecodeError:
                        pass

            if buffer.strip():
                try:
                    yield MeshEvent.from_dict(json.loads(buffer))
                except json.JSONDecodeError:
                    pass

    # ── Traces ────────────────────────────────────────────────────────────────

    async def get_trace(self, run_id: str) -> Trace:
        """Fetch the full audit trace for a run."""
        data = await self._request("GET", f"/traces/{run_id}")
        return Trace.from_dict(data)

    async def list_runs(self) -> list[str]:
        """List all run IDs in the ledger."""
        data = await self._request("GET", "/traces")
        return cast(list[str], data.get("runs", []))

    # ── HITL ──────────────────────────────────────────────────────────────────

    async def list_pending_hitl(self) -> list[PausedRun]:
        """List runs currently paused for human approval."""
        data = await self._request("GET", "/hitl/pending")
        return [PausedRun.from_dict(r) for r in data.get("paused_runs", [])]

    async def approve_hitl(
        self,
        run_id: str,
        reviewer_id: str = "",
        notes: str = "",
    ) -> None:
        """Approve a paused run so it can continue."""
        await self._request(
            "POST",
            f"/hitl/{run_id}/approve",
            {
                "reviewer_id": reviewer_id,
                "notes": notes,
            },
        )

    async def reject_hitl(
        self,
        run_id: str,
        reviewer_id: str = "",
        notes: str = "",
    ) -> None:
        """Reject a paused run — sets confidence=0.0 on resume."""
        await self._request(
            "POST",
            f"/hitl/{run_id}/reject",
            {
                "reviewer_id": reviewer_id,
                "notes": notes,
            },
        )

    # ── Metrics ───────────────────────────────────────────────────────────────

    async def metrics(self) -> str:
        """Fetch Prometheus-format metrics text from /metrics."""
        session = await self._ensure_session()
        async with session.get(f"{self._base_url}/metrics") as resp:
            return cast(str, await resp.text())

    # ── Sync wrapper ──────────────────────────────────────────────────────────

    @classmethod
    def sync(
        cls,
        base_url: str,
        api_key: str = "",
        default_policy: PolicyConfig | None = None,
        timeout: float = 120.0,
    ) -> _SyncClient:
        """Return a synchronous wrapper around this async client.

        Use when you cannot ``await`` — e.g. in Jupyter ``%run`` or scripts.

        Example::

            client = MeshFlowClient.sync("http://localhost:8000", api_key="k")
            result = client.run_sync("Summarise the report")
        """
        return _SyncClient(cls(base_url, api_key, default_policy, timeout))


class _SyncClient:
    """Synchronous wrapper around MeshFlowClient.

    Creates its own event loop. Do not use from inside an already-running loop
    (use the async client directly in that case).
    """

    def __init__(self, async_client: MeshFlowClient) -> None:
        self._client = async_client

    def _run(self, coro: Any) -> Any:
        return asyncio.run(coro)

    def health(self) -> HealthResponse:
        return cast(HealthResponse, self._run(self._client.health()))

    def run_sync(
        self,
        task: str,
        policy: PolicyConfig | None = None,
        context: dict[str, Any] | None = None,
    ) -> RunResult:
        return cast(RunResult, self._run(self._client.run(task, policy, context)))

    def get_trace(self, run_id: str) -> Trace:
        return cast(Trace, self._run(self._client.get_trace(run_id)))

    def list_runs(self) -> list[str]:
        return cast(list[str], self._run(self._client.list_runs()))

    def list_pending_hitl(self) -> list[PausedRun]:
        return cast(list[PausedRun], self._run(self._client.list_pending_hitl()))

    def approve_hitl(self, run_id: str, reviewer_id: str = "", notes: str = "") -> None:
        self._run(self._client.approve_hitl(run_id, reviewer_id, notes))

    def reject_hitl(self, run_id: str, reviewer_id: str = "", notes: str = "") -> None:
        self._run(self._client.reject_hitl(run_id, reviewer_id, notes))

    def metrics(self) -> str:
        return cast(str, self._run(self._client.metrics()))

    def close(self) -> None:
        self._run(self._client.close())

    def __enter__(self) -> _SyncClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


# ── Exceptions ────────────────────────────────────────────────────────────────


class MeshFlowError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"MeshFlow API error {status}: {message}")
        self.status = status
        self.message = message
