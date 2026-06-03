"""LangGraph-compatible Functional API — @task and @entrypoint decorators.

The Functional API lets you write governed, checkpointed workflows as plain
Python async functions instead of building a StateGraph manually.

Usage::

    from meshflow.core.functional import task, entrypoint
    from meshflow.core.state import MemorySaver

    @task
    async def fetch_data(url: str) -> str:
        return f"data from {url}"

    @task
    async def summarise(data: str) -> str:
        return f"summary of {data}"

    checkpointer = MemorySaver()

    @entrypoint(checkpointer=checkpointer)
    async def pipeline(inputs: dict) -> dict:
        raw   = await fetch_data(inputs["url"])
        brief = await summarise(raw)
        return {"result": brief}

    result = await pipeline.invoke({"url": "https://example.com"})
    # result == {"result": "summary of data from https://example.com"}

    # With HITL (interrupt / resume):
    from meshflow.core.state import interrupt, Command
    @entrypoint(checkpointer=checkpointer)
    async def review_pipeline(inputs: dict) -> dict:
        draft = await draft_task(inputs["topic"])
        feedback = interrupt("Please review: " + draft)
        return {"final": draft + "\\n\\nFeedback: " + str(feedback)}

    # First invocation pauses at interrupt():
    result = await review_pipeline.invoke({"topic": "AI"}, config={"thread_id": "t1"})
    # result.interrupted == True

    # Resume:
    result = await review_pipeline.invoke(
        None,
        config={"thread_id": "t1"},
        command=Command(resume="LGTM"),
    )
"""

from __future__ import annotations

import asyncio
import functools
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable


# ── Return type from entrypoint.invoke() ──────────────────────────────────────

@dataclass
class EntrypointResult:
    """Result returned by :meth:`Entrypoint.invoke`.

    Attributes
    ----------
    value:
        The return value of the entrypoint function, or ``None`` if interrupted.
    interrupted:
        ``True`` when execution paused at an ``interrupt()`` call.
    interrupt_value:
        The value passed to ``interrupt()`` (prompt for the human reviewer).
    thread_id:
        The thread ID used for this invocation (useful for resume calls).
    """

    value: Any = None
    interrupted: bool = False
    interrupt_value: Any = None
    thread_id: str = ""

    def __bool__(self) -> bool:
        return not self.interrupted


# ── @task ─────────────────────────────────────────────────────────────────────

class Task:
    """A decorated async function that can be called within an @entrypoint.

    Tasks are retried automatically on transient errors (up to ``max_retries``
    times with exponential back-off) and their outputs are cached within a
    single entrypoint invocation.
    """

    def __init__(self, fn: Callable, *, max_retries: int = 0) -> None:
        self._fn = fn
        self._max_retries = max_retries
        functools.update_wrapper(self, fn)

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                if inspect.iscoroutinefunction(self._fn):
                    return await self._fn(*args, **kwargs)
                return self._fn(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    await asyncio.sleep(0.1 * (2 ** attempt))
        if last_exc is not None:
            raise last_exc
        return None  # unreachable

    # LangGraph-compat convenience
    async def invoke(self, *args: Any, **kwargs: Any) -> Any:
        return await self(*args, **kwargs)

    def __repr__(self) -> str:
        return f"Task({self._fn.__name__!r})"


def task(fn: Callable | None = None, *, max_retries: int = 0) -> Any:
    """Decorator that marks an async function as a retryable workflow task.

    Can be used bare or with keyword arguments::

        @task
        async def my_task(x: str) -> str: ...

        @task(max_retries=3)
        async def flaky_task(x: str) -> str: ...
    """
    if fn is not None:
        # @task (no parens)
        return Task(fn, max_retries=0)

    # @task(max_retries=N) — return a decorator
    def _decorator(f: Callable) -> Task:
        return Task(f, max_retries=max_retries)

    return _decorator


# ── @entrypoint ───────────────────────────────────────────────────────────────

class Entrypoint:
    """A checkpointed, interruptible workflow entry point.

    Produced by the :func:`entrypoint` decorator.  Supports:

    * ``await ep.invoke(inputs)`` — run the workflow synchronously.
    * ``await ep.ainvoke(inputs)`` — async alias for invoke.
    * ``ep.invoke_sync(inputs)`` — synchronous wrapper (for non-async callers).
    * HITL via ``interrupt()`` + ``Command(resume=...)``.
    * Checkpointing via any ``MemorySaver`` / ``SqliteSaver`` compatible object.
    """

    def __init__(
        self,
        fn: Callable,
        *,
        checkpointer: Any = None,
    ) -> None:
        self._fn = fn
        self._checkpointer = checkpointer
        functools.update_wrapper(self, fn)

    async def invoke(
        self,
        inputs: Any,
        *,
        config: dict[str, Any] | None = None,
        command: Any = None,
    ) -> EntrypointResult:
        """Run the workflow and return an :class:`EntrypointResult`.

        Parameters
        ----------
        inputs:
            Input dict (or any value) for the first invocation.  Pass ``None``
            when resuming a paused thread.
        config:
            Dict with optional ``{"thread_id": "..."}`` for checkpointing.
        command:
            A ``Command(resume=value)`` to resume an interrupted thread.
        """
        from meshflow.core.state import Interrupt, Command as _Command

        thread_id: str = (config or {}).get("thread_id", "")

        # Resolve inputs — resume uses persisted inputs
        if command is not None and isinstance(command, _Command) and command.resume is not None:
            if thread_id and self._checkpointer is not None:
                persisted = self._checkpointer.get(thread_id) or {}
                inputs = persisted.get("__inputs__", inputs)
            resume_value = command.resume
        else:
            resume_value = None

        # Persist inputs for potential future resumes
        if thread_id and self._checkpointer is not None:
            existing = self._checkpointer.get(thread_id) or {}
            if inputs is not None:
                existing["__inputs__"] = inputs
            self._checkpointer.put(thread_id, existing)

        try:
            if inspect.iscoroutinefunction(self._fn):
                result = await self._fn(inputs)
            else:
                result = self._fn(inputs)
            # Clear checkpoint on successful completion
            if thread_id and self._checkpointer is not None:
                self._checkpointer.delete(thread_id)
            return EntrypointResult(value=result, thread_id=thread_id)
        except Interrupt as exc:
            if thread_id and self._checkpointer is not None:
                state = self._checkpointer.get(thread_id) or {}
                state["__interrupted__"] = True
                state["__interrupt_value__"] = str(exc.value)
                self._checkpointer.put(thread_id, state)
            return EntrypointResult(
                interrupted=True,
                interrupt_value=exc.value,
                thread_id=thread_id,
            )

    # Alias
    ainvoke = invoke

    def invoke_sync(self, inputs: Any, **kwargs: Any) -> EntrypointResult:
        """Synchronous wrapper around :meth:`invoke`."""
        from meshflow.integrations._utils import run_sync
        return run_sync(self.invoke(inputs, **kwargs))

    def __repr__(self) -> str:
        return f"Entrypoint({self._fn.__name__!r})"


def entrypoint(
    fn: Callable | None = None,
    *,
    checkpointer: Any = None,
) -> Any:
    """Decorator that wraps an async function as a checkpointed entrypoint.

    Can be used bare or with keyword arguments::

        @entrypoint
        async def my_flow(inputs: dict) -> dict: ...

        @entrypoint(checkpointer=MemorySaver())
        async def stateful_flow(inputs: dict) -> dict: ...
    """
    if fn is not None:
        # @entrypoint (no parens)
        return Entrypoint(fn, checkpointer=None)

    def _decorator(f: Callable) -> Entrypoint:
        return Entrypoint(f, checkpointer=checkpointer)

    return _decorator
