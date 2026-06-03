"""Structured streaming v2 — typed Pydantic model streaming.

Upgrades the existing PartialStructuredOutput to emit real Pydantic instances
(with defaults for missing fields) rather than plain dicts, and adds SSE/NDJSON
serialisation helpers.

Usage::

    from pydantic import BaseModel
    from meshflow.streaming.structured_v2 import stream_model, collect_model

    class Report(BaseModel):
        title: str = ""
        summary: str = ""
        key_points: list[str] = []

    # As an async generator — each yield is a partially-filled Report instance
    async for partial in stream_model(token_gen, Report):
        print(partial.title)          # "" until that field arrives
        if partial.__stream_complete__:
            print("done:", partial.summary)

    # Convenience: block until final
    report = await collect_model(token_gen, Report)

FastAPI SSE usage::

    from meshflow.streaming.structured_v2 import stream_to_sse
    from starlette.responses import StreamingResponse

    @app.get("/stream")
    async def endpoint(task: str):
        async def _gen():
            async for line in stream_to_sse(wf.astream_structured(task, Report)):
                yield line
        return StreamingResponse(_gen(), media_type="text/event-stream")
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, AsyncIterator, Generic, Type, TypeVar

T = TypeVar("T")


# ── TypedStreamChunk ──────────────────────────────────────────────────────────


@dataclass
class TypedStreamChunk(Generic[T]):
    """One emission from the typed structured stream.

    ``partial`` is a fully constructed model instance (using defaults for any
    fields not yet parsed).  ``complete`` is True on the final chunk where all
    required fields have been filled.
    """

    partial: T
    complete: bool
    raw_so_far: str = ""
    token: str = ""

    def to_dict(self) -> dict[str, Any]:
        raw = self.partial
        try:
            data = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
        except Exception:
            data = {}
        return {
            "partial": data,
            "complete": self.complete,
            "token": self.token,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


# ── Core typed streaming ───────────────────────────────────────────────────────


def _partial_model(schema: Type[T], partial_dict: dict[str, Any]) -> T:
    """Construct *schema* from *partial_dict*, filling missing fields with defaults.

    For Pydantic v2 models this uses ``model_construct`` which skips validation
    and allows partial construction.  Falls back to ``__init__`` for v1.
    """
    if hasattr(schema, "model_construct"):
        # Pydantic v2
        return schema.model_construct(**partial_dict)  # type: ignore[return-value]
    if hasattr(schema, "construct"):
        # Pydantic v1
        return schema.construct(**partial_dict)  # type: ignore[return-value]
    # Plain dataclass / TypedDict / namedtuple fallback
    try:
        return schema(**partial_dict)  # type: ignore[return-value]
    except Exception:
        return schema()  # type: ignore[return-value]


async def stream_model(
    token_gen: AsyncIterator[str],
    schema: Type[T],
    *,
    emit_on_every_token: bool = False,
) -> AsyncIterator[TypedStreamChunk[T]]:
    """Yield :class:`TypedStreamChunk[T]` objects as tokens accumulate.

    Each chunk carries a *partial* instance of *schema* — Pydantic fields not
    yet parsed are set to their declared defaults (or ``None`` when no default
    exists).

    Parameters
    ----------
    token_gen:
        Async token generator (strings) — e.g. from ``workflow.astream()``.
    schema:
        A Pydantic ``BaseModel`` subclass.  Must have default values for all
        fields to support partial construction.
    emit_on_every_token:
        When *True*, emit a chunk on every token even if the partial JSON has
        not changed.  Useful for progress indicators.
    """
    from meshflow.streaming.partial_output import PartialStructuredOutput

    pso = PartialStructuredOutput(schema=schema, emit_on_every_token=emit_on_every_token)
    last_partial: dict[str, Any] = {}

    async for chunk in pso.stream(token_gen):
        partial_dict = chunk.partial or last_partial
        last_partial = partial_dict
        partial_instance = _partial_model(schema, partial_dict)
        yield TypedStreamChunk(
            partial=partial_instance,
            complete=chunk.complete,
            raw_so_far=chunk.raw_so_far,
            token=chunk.token,
        )


async def collect_model(
    token_gen: AsyncIterator[str],
    schema: Type[T],
) -> T | None:
    """Consume *token_gen* and return the fully validated final *schema* instance.

    Returns *None* if the stream produced no valid JSON.
    """
    from meshflow.streaming.partial_output import PartialStructuredOutput

    pso = PartialStructuredOutput(schema=schema)
    async for chunk in pso.stream(token_gen):
        if chunk.complete:
            if chunk.validated is not None:
                return chunk.validated  # type: ignore[return-value]
    return None


# ── SSE / NDJSON helpers ──────────────────────────────────────────────────────


async def stream_to_sse(
    chunk_gen: "AsyncIterator[Any]",
    event: str = "chunk",
) -> AsyncIterator[str]:
    """Convert a chunk generator to Server-Sent Events format.

    Accepts either :class:`TypedStreamChunk` or the legacy
    :class:`~meshflow.streaming.partial_output.PartialOutputChunk`.

    Each SSE line looks like::

        data: {"partial": {...}, "complete": false, "token": " the"}

        data: {"partial": {...}, "complete": true, "token": ""}

    Parameters
    ----------
    chunk_gen:
        An async generator yielding TypedStreamChunk or PartialOutputChunk.
    event:
        SSE event name (default ``"chunk"``).
    """
    async for chunk in chunk_gen:
        if hasattr(chunk, "to_json"):
            payload = chunk.to_json()
        elif hasattr(chunk, "to_dict"):
            payload = json.dumps(chunk.to_dict())
        else:
            payload = json.dumps(str(chunk))

        yield f"event: {event}\ndata: {payload}\n\n"

        if getattr(chunk, "complete", False):
            yield "event: done\ndata: {}\n\n"
            return


async def stream_to_ndjson(
    chunk_gen: "AsyncIterator[Any]",
) -> AsyncIterator[str]:
    """Convert a chunk generator to newline-delimited JSON (NDJSON).

    One JSON object per line, terminated by ``\\n``.  Suitable for
    ``media_type="application/x-ndjson"`` streaming responses.
    """
    async for chunk in chunk_gen:
        if hasattr(chunk, "to_json"):
            payload = chunk.to_json()
        elif hasattr(chunk, "to_dict"):
            payload = json.dumps(chunk.to_dict())
        else:
            payload = json.dumps(str(chunk))

        yield payload + "\n"


# ── Workflow.astream_model integration ────────────────────────────────────────


def _install_on_workflow() -> None:
    """Monkey-patch Workflow with ``astream_model`` and ``stream_model_sync``
    so these methods are available without changing the core workflow module.
    """
    try:
        from meshflow.core.workflow import Workflow
    except ImportError:
        return

    if hasattr(Workflow, "astream_model"):
        return  # already installed

    async def _astream_model(
        self: Any,
        task: str,
        schema: Type[Any],
        *,
        emit_on_every_token: bool = False,
    ) -> AsyncIterator[TypedStreamChunk[Any]]:
        """Typed structured streaming — yields :class:`TypedStreamChunk[T]`.

        Unlike :meth:`astream_structured` (which yields raw
        ``PartialOutputChunk`` dicts), this method yields real *schema*
        instances so you get IDE auto-complete and type safety.

        Example::

            class Report(BaseModel):
                title: str = ""
                score: float = 0.0

            async for chunk in wf.astream_model("Analyse this.", Report):
                print(chunk.partial.title)   # "" → "Revenue" as tokens arrive
                if chunk.complete:
                    print(chunk.partial.score)
        """
        async def _tok_gen() -> AsyncIterator[str]:
            async for sc in self.astream(task):
                if sc.is_token and sc.content:
                    yield sc.content

        async for typed_chunk in stream_model(
            _tok_gen(), schema, emit_on_every_token=emit_on_every_token
        ):
            yield typed_chunk

    def _stream_model_sync(
        self: Any,
        task: str,
        schema: Type[Any],
    ) -> "Any":
        """Synchronous typed structured streaming — use in a for-loop.

        Example::

            for chunk in wf.stream_model_sync("Analyse this.", Report):
                if chunk.complete:
                    print(chunk.partial.title)
        """
        from meshflow.integrations._utils import run_sync

        async def _run() -> list[TypedStreamChunk[Any]]:
            chunks: list[TypedStreamChunk[Any]] = []
            async for c in _astream_model(self, task, schema):
                chunks.append(c)
            return chunks

        return iter(run_sync(_run()))

    Workflow.astream_model = _astream_model  # type: ignore[attr-defined]
    Workflow.stream_model_sync = _stream_model_sync  # type: ignore[attr-defined]


# Install at import time
_install_on_workflow()
