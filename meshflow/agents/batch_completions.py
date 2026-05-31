"""Anthropic Message Batches API integration — send N prompts in one API call.

Closes the batch-processing gap: instead of making N sequential `complete()`
calls, submit all prompts in a single batch request.  Anthropic processes them
asynchronously at 50 % of the per-token price.

Use-cases
---------
- Eval suites that score many outputs at once
- Nightly report generation across many accounts
- Parameter sweep variants (pairs with WorkflowSweep)
- Any non-real-time multi-agent workload

Usage::

    from meshflow.agents.batch_completions import BatchCompletion, BatchCompletionRequest

    batch = BatchCompletion()

    # Build requests
    reqs = [
        BatchCompletionRequest(
            custom_id=f"q{i}",
            model="claude-haiku-4-5-20251001",
            messages=[{"role": "user", "content": question}],
            system="You are a helpful assistant.",
            max_tokens=256,
        )
        for i, question in enumerate(questions)
    ]

    # Submit and wait for all results
    results = await batch.run(reqs)
    for r in results:
        if r.success:
            print(r.custom_id, r.content)
        else:
            print(r.custom_id, "FAILED:", r.error)

    # Or use the high-level helper
    from meshflow.agents.batch_completions import batch_complete
    results = await batch_complete(prompts=questions, model="claude-haiku-4-5-20251001")
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any


# ── Request / Result types ────────────────────────────────────────────────────

@dataclass
class BatchCompletionRequest:
    """One prompt in a batch request."""

    messages: list[dict[str, Any]]
    model: str = "claude-haiku-4-5-20251001"
    system: str = ""
    max_tokens: int = 1024
    custom_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_anthropic_params(self) -> dict[str, Any]:
        """Serialise to the Anthropic batch `params` block."""
        body: dict[str, Any] = {
            "model": self.model,
            "messages": self.messages,
            "max_tokens": self.max_tokens,
        }
        if self.system:
            body["system"] = self.system
        return {"custom_id": self.custom_id, "params": body}


@dataclass
class BatchCompletionResult:
    """Result for one item in a completed batch."""

    custom_id: str
    success: bool
    content: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


# ── BatchCompletion ───────────────────────────────────────────────────────────

class BatchCompletion:
    """Client for the Anthropic Message Batches API.

    Parameters
    ----------
    api_key:        Anthropic API key.  Defaults to ``ANTHROPIC_API_KEY`` env var.
    poll_interval:  Seconds between status checks while the batch processes.
    timeout:        Total seconds to wait before giving up.
    fallback_sequential:
        When True (default), fall back to sequential ``complete()`` calls if
        the batches API is unavailable or the SDK version doesn't support it.
    """

    # Anthropic pricing (USD per 1M tokens) with 50% batch discount applied
    _PRICING: dict[str, tuple[float, float]] = {
        "claude-opus-4-8":            (7.50, 37.50),
        "claude-opus-4-7":            (7.50, 37.50),
        "claude-sonnet-4-6":          (1.50,  7.50),
        "claude-haiku-4-5-20251001":  (0.40,  2.00),
        "claude-haiku-3-5":           (0.40,  2.00),
    }

    def __init__(
        self,
        api_key: str = "",
        *,
        poll_interval: float = 5.0,
        timeout: float = 3600.0,
        fallback_sequential: bool = True,
    ) -> None:
        import os
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._poll = poll_interval
        self._timeout = timeout
        self._fallback = fallback_sequential

    # ── Core API ──────────────────────────────────────────────────────────────

    async def run(
        self, requests: list[BatchCompletionRequest]
    ) -> list[BatchCompletionResult]:
        """Submit all *requests* as one batch and wait for completion.

        Returns results in the same order as *requests*.
        """
        if not requests:
            return []

        try:
            return await self._run_batch(requests)
        except Exception:
            if self._fallback:
                return await self._run_sequential(requests)
            raise

    async def _run_batch(
        self, requests: list[BatchCompletionRequest]
    ) -> list[BatchCompletionResult]:
        """Use the Anthropic batches API."""
        try:
            import anthropic
        except ImportError:
            raise ImportError("anthropic SDK required: pip install anthropic")

        client = anthropic.AsyncAnthropic(api_key=self._api_key or None)

        # Submit batch
        batch_params = [r.to_anthropic_params() for r in requests]
        try:
            batch = await client.messages.batches.create(requests=batch_params)
        except AttributeError:
            raise RuntimeError(
                "Anthropic SDK version does not support batches API. "
                "Upgrade: pip install --upgrade anthropic"
            )

        # Poll until complete
        import time
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            status = await client.messages.batches.retrieve(batch.id)
            if status.processing_status == "ended":
                break
            await asyncio.sleep(self._poll)
        else:
            raise TimeoutError(
                f"Batch {batch.id} did not complete within {self._timeout}s"
            )

        # Collect results
        results_by_id: dict[str, BatchCompletionResult] = {}
        async for result in await client.messages.batches.results(batch.id):
            custom_id = result.custom_id
            if result.result.type == "succeeded":
                msg = result.result.message
                content = "".join(
                    b.text for b in msg.content
                    if hasattr(b, "text")
                )
                in_tok  = msg.usage.input_tokens
                out_tok = msg.usage.output_tokens
                model   = msg.model or requests[0].model
                price   = self._PRICING.get(model, (1.50, 7.50))
                cost    = (in_tok * price[0] + out_tok * price[1]) / 1_000_000
                results_by_id[custom_id] = BatchCompletionResult(
                    custom_id=custom_id,
                    success=True,
                    content=content,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    cost_usd=cost,
                )
            else:
                err = str(getattr(result.result, "error", result.result.type))
                results_by_id[custom_id] = BatchCompletionResult(
                    custom_id=custom_id,
                    success=False,
                    error=err,
                )

        # Return in request order
        return [
            results_by_id.get(
                r.custom_id,
                BatchCompletionResult(
                    custom_id=r.custom_id, success=False,
                    error="result not found in batch output"
                ),
            )
            for r in requests
        ]

    async def _run_sequential(
        self, requests: list[BatchCompletionRequest]
    ) -> list[BatchCompletionResult]:
        """Fallback: sequential complete() calls."""
        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=self._api_key or None)
        except ImportError:
            return [
                BatchCompletionResult(
                    custom_id=r.custom_id, success=False,
                    error="anthropic SDK not installed"
                )
                for r in requests
            ]

        results = []
        for req in requests:
            try:
                body: dict[str, Any] = {
                    "model": req.model,
                    "messages": req.messages,
                    "max_tokens": req.max_tokens,
                }
                if req.system:
                    body["system"] = req.system
                resp = await client.messages.create(**body)
                content = "".join(
                    b.text for b in resp.content if hasattr(b, "text")
                )
                price = self._PRICING.get(req.model, (1.50, 7.50))
                cost = (
                    resp.usage.input_tokens * price[0]
                    + resp.usage.output_tokens * price[1]
                ) / 1_000_000
                results.append(BatchCompletionResult(
                    custom_id=req.custom_id,
                    success=True,
                    content=content,
                    input_tokens=resp.usage.input_tokens,
                    output_tokens=resp.usage.output_tokens,
                    cost_usd=cost,
                ))
            except Exception as exc:
                results.append(BatchCompletionResult(
                    custom_id=req.custom_id, success=False, error=str(exc)
                ))
        return results


# ── High-level helper ─────────────────────────────────────────────────────────

async def batch_complete(
    prompts: list[str],
    *,
    model: str = "claude-haiku-4-5-20251001",
    system: str = "",
    max_tokens: int = 1024,
    api_key: str = "",
    poll_interval: float = 5.0,
    timeout: float = 3600.0,
) -> list[BatchCompletionResult]:
    """High-level helper: send a list of prompt strings as a single batch.

    Returns results in the same order as *prompts*.

    Usage::

        results = await batch_complete(
            prompts=["Summarise X", "Summarise Y", "Summarise Z"],
            model="claude-haiku-4-5-20251001",
            system="Be concise.",
        )
    """
    requests = [
        BatchCompletionRequest(
            messages=[{"role": "user", "content": p}],
            model=model,
            system=system,
            max_tokens=max_tokens,
        )
        for p in prompts
    ]
    client = BatchCompletion(
        api_key=api_key, poll_interval=poll_interval, timeout=timeout
    )
    return await client.run(requests)


__all__ = ["BatchCompletion", "BatchCompletionRequest", "BatchCompletionResult", "batch_complete"]
