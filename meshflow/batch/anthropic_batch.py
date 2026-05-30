"""Anthropic Message Batches API — 50% cost discount on queued workloads.

Closes the token-optimization Batch Processing gap from Section 3.2 of the
Competitive Intelligence document. The Anthropic Batch API
(``/v1/messages/batches``) gives a 50% cost discount for workloads that can
tolerate up to 24-hour processing time.

Suitable for:
- Nightly eval runs (grade 1000s of agent outputs offline)
- Cost regression CI gates (run baseline evals at half price)
- Bulk document processing (analyse 500 contracts, not one at a time)
- Prompt A/B testing against large datasets

Usage::

    from meshflow.batch.anthropic_batch import AnthropicBatchClient, BatchRequest

    client = AnthropicBatchClient()

    # Submit a batch
    requests = [
        BatchRequest(custom_id=f"task-{i}", prompt=task, model="claude-haiku-4-5-20251001")
        for i, task in enumerate(my_tasks)
    ]
    batch_id = await client.submit(requests)
    print(f"Submitted batch {batch_id} — processing up to 24h")

    # Poll for completion (or use wait())
    results = await client.wait(batch_id, poll_interval=30)
    for r in results:
        print(r.custom_id, r.output[:80])

    # One-shot: submit + wait
    results = await client.run(requests)

Integration with TaskQueue (Sprint 22)::

    from meshflow.batch.anthropic_batch import batch_agent_tasks

    # Run a list of agent tasks as a batch (50% cheaper, async 24h window)
    outputs = await batch_agent_tasks(
        tasks=["Audit this contract", "Review this code", "Summarise this doc"],
        agent_model="claude-haiku-4-5-20251001",
        system_prompt="You are a thorough analyst.",
    )
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


# ── Data structures ───────────────────────────────────────────────────────────


@dataclass
class BatchRequest:
    """A single request in an Anthropic batch.

    Parameters
    ----------
    custom_id:      Stable identifier — returned in results for correlation.
    prompt:         User message text.
    model:          Anthropic model to use (default: ``claude-haiku-4-5-20251001``).
    system:         System prompt (optional).
    max_tokens:     Maximum output tokens (default: 1024).
    """

    custom_id: str
    prompt: str
    model: str = "claude-haiku-4-5-20251001"
    system: str = ""
    max_tokens: int = 1024

    def to_api_request(self) -> dict[str, Any]:
        """Convert to Anthropic batch API request format."""
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": self.prompt}],
        }
        if self.system:
            params["system"] = self.system
        return {
            "custom_id": self.custom_id,
            "params": params,
        }


@dataclass
class BatchResult:
    """Result of a single request from a completed batch.

    Attributes
    ----------
    custom_id:  Matches the ``BatchRequest.custom_id`` submitted.
    output:     Text output from the model (empty string if the request errored).
    tokens:     Total tokens used (input + output).
    cost_usd:   Estimated USD cost (50% discount applied automatically by Anthropic).
    error:      Error message if the individual request failed.
    """

    custom_id: str
    output: str
    tokens: int = 0
    cost_usd: float = 0.0
    error: str = ""

    @property
    def succeeded(self) -> bool:
        return not bool(self.error)

    def to_dict(self) -> dict[str, Any]:
        return {
            "custom_id": self.custom_id,
            "output": self.output[:500],
            "tokens": self.tokens,
            "cost_usd": round(self.cost_usd, 6),
            "error": self.error,
        }


@dataclass
class BatchJob:
    """Metadata for a submitted Anthropic batch job.

    Attributes
    ----------
    batch_id:       Anthropic-assigned batch identifier.
    status:         ``"in_progress"`` | ``"ended"`` | ``"canceling"`` | ``"canceled"``.
    request_counts: Dict with ``processing``, ``succeeded``, ``errored``, ``canceled``, ``expired``.
    created_at:     Unix timestamp when the batch was submitted.
    """

    batch_id: str
    status: str = "in_progress"
    request_counts: dict[str, int] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    results_url: str = ""

    @property
    def is_complete(self) -> bool:
        return self.status in ("ended", "canceled")

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "status": self.status,
            "request_counts": self.request_counts,
            "created_at": self.created_at,
        }


# ── AnthropicBatchClient ──────────────────────────────────────────────────────


class AnthropicBatchClient:
    """Client for the Anthropic Message Batches API.

    Provides a 50% cost discount on workloads that tolerate async processing
    (up to 24 hours).  Requires ``pip install anthropic``.

    Parameters
    ----------
    api_key:
        Anthropic API key.  Defaults to ``ANTHROPIC_API_KEY`` env var.
    max_retries:
        Number of retries on transient errors during polling (default: 3).
    """

    def __init__(self, api_key: str = "", *, max_retries: int = 3) -> None:
        self._api_key = api_key
        self._max_retries = max_retries

    def _client(self) -> Any:
        try:
            import anthropic  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "AnthropicBatchClient requires anthropic: pip install anthropic"
            ) from exc
        import os
        key = self._api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        return anthropic.AsyncAnthropic(api_key=key or None)

    async def submit(self, requests: list[BatchRequest]) -> str:
        """Submit a list of requests as an Anthropic batch job.

        Parameters
        ----------
        requests:   List of ``BatchRequest`` objects to submit.

        Returns
        -------
        The Anthropic batch ID string (use with ``status()`` and ``results()``).
        """
        if not requests:
            raise ValueError("At least one BatchRequest is required")

        client = self._client()
        api_requests = [r.to_api_request() for r in requests]
        response = await client.messages.batches.create(requests=api_requests)
        return response.id

    async def status(self, batch_id: str) -> BatchJob:
        """Poll the status of a batch job.

        Parameters
        ----------
        batch_id:   The batch ID returned by ``submit()``.

        Returns
        -------
        ``BatchJob`` with current status and request counts.
        """
        client = self._client()
        resp = await client.messages.batches.retrieve(batch_id)
        counts = {}
        if hasattr(resp, "request_counts") and resp.request_counts:
            rc = resp.request_counts
            for attr in ("processing", "succeeded", "errored", "canceled", "expired"):
                counts[attr] = getattr(rc, attr, 0)
        return BatchJob(
            batch_id=batch_id,
            status=getattr(resp, "processing_status", "in_progress"),
            request_counts=counts,
            results_url=getattr(resp, "results_url", "") or "",
        )

    async def results(self, batch_id: str) -> list[BatchResult]:
        """Fetch results from a completed batch job.

        Parameters
        ----------
        batch_id:   The batch ID. The batch must be in ``ended`` status.

        Returns
        -------
        List of ``BatchResult`` objects, one per original ``BatchRequest``.
        """
        client = self._client()
        batch_results: list[BatchResult] = []

        async for result in await client.messages.batches.results(batch_id):
            custom_id = getattr(result, "custom_id", "")
            result_type = getattr(result, "result", None)

            if result_type is None:
                batch_results.append(BatchResult(custom_id=custom_id, output="",
                                                   error="No result object"))
                continue

            result_kind = getattr(result_type, "type", "error")
            if result_kind == "succeeded":
                msg = getattr(result_type, "message", None)
                content = ""
                in_tok = out_tok = 0
                if msg:
                    blocks = getattr(msg, "content", [])
                    content = "".join(
                        getattr(b, "text", "") for b in blocks if hasattr(b, "text")
                    )
                    usage = getattr(msg, "usage", None)
                    if usage:
                        in_tok = getattr(usage, "input_tokens", 0)
                        out_tok = getattr(usage, "output_tokens", 0)
                batch_results.append(BatchResult(
                    custom_id=custom_id,
                    output=content,
                    tokens=in_tok + out_tok,
                    cost_usd=0.0,  # Anthropic bills automatically at 50% discount
                ))
            else:
                error_msg = str(getattr(result_type, "error", result_kind))
                batch_results.append(BatchResult(custom_id=custom_id, output="", error=error_msg))

        return batch_results

    async def wait(
        self,
        batch_id: str,
        *,
        poll_interval: float = 30.0,
        timeout: float = 86400.0,
    ) -> list[BatchResult]:
        """Poll until the batch is complete, then return all results.

        Parameters
        ----------
        batch_id:       The batch ID returned by ``submit()``.
        poll_interval:  Seconds between status checks (default: 30).
        timeout:        Maximum wait time in seconds (default: 24 hours).

        Returns
        -------
        List of ``BatchResult`` objects.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = await self.status(batch_id)
            if job.is_complete:
                return await self.results(batch_id)
            await asyncio.sleep(poll_interval)
        raise TimeoutError(f"Batch {batch_id} did not complete within {timeout}s")

    async def run(
        self,
        requests: list[BatchRequest],
        *,
        poll_interval: float = 30.0,
        timeout: float = 86400.0,
    ) -> list[BatchResult]:
        """Submit a batch and wait for completion in one call.

        Parameters
        ----------
        requests:       List of ``BatchRequest`` objects.
        poll_interval:  Seconds between status checks (default: 30).
        timeout:        Maximum wait time in seconds (default: 24 hours).

        Returns
        -------
        List of ``BatchResult`` objects.
        """
        batch_id = await self.submit(requests)
        return await self.wait(batch_id, poll_interval=poll_interval, timeout=timeout)

    async def cancel(self, batch_id: str) -> bool:
        """Cancel a batch job that is still in progress.

        Returns True if the cancellation was accepted.
        """
        client = self._client()
        try:
            resp = await client.messages.batches.cancel(batch_id)
            return getattr(resp, "processing_status", "") == "canceling"
        except Exception:
            return False


# ── Convenience helpers ───────────────────────────────────────────────────────


async def batch_agent_tasks(
    tasks: list[str],
    *,
    agent_model: str = "claude-haiku-4-5-20251001",
    system_prompt: str = "You are a helpful assistant.",
    max_tokens: int = 1024,
    api_key: str = "",
    poll_interval: float = 30.0,
) -> list[BatchResult]:
    """Submit a list of agent task strings as an Anthropic batch (50% cheaper).

    This is the high-level helper — for fine-grained control use
    ``AnthropicBatchClient`` directly.

    Parameters
    ----------
    tasks:          List of task description strings.
    agent_model:    Model to use for all tasks.
    system_prompt:  System prompt for all tasks.
    max_tokens:     Max output tokens per task.
    api_key:        Anthropic API key (defaults to env var).
    poll_interval:  Polling interval in seconds.

    Returns
    -------
    List of ``BatchResult`` (same length as ``tasks``, same order).

    Example
    -------
    ::

        results = await batch_agent_tasks(
            ["Summarise doc1.pdf", "Summarise doc2.pdf"],
            agent_model="claude-haiku-4-5-20251001",
        )
        for r in results:
            print(r.custom_id, r.output[:80])
    """
    client = AnthropicBatchClient(api_key=api_key)
    requests = [
        BatchRequest(
            custom_id=f"task-{i}-{uuid.uuid4().hex[:6]}",
            prompt=task,
            model=agent_model,
            system=system_prompt,
            max_tokens=max_tokens,
        )
        for i, task in enumerate(tasks)
    ]
    return await client.run(requests, poll_interval=poll_interval)


__all__ = [
    "AnthropicBatchClient",
    "BatchRequest",
    "BatchResult",
    "BatchJob",
    "batch_agent_tasks",
]
