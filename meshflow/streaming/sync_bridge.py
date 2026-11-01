"""Close-aware bridge from an async stream to a synchronous iterator."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Iterator
from contextlib import aclosing
import queue
import threading
from typing import Any


def sync_stream(
    factory: Callable[[], AsyncGenerator[Any, None]],
    error_handler: Callable[[Exception], Any] | None = None,
) -> Iterator[Any]:
    """Cancel and join the producer when a synchronous consumer closes early."""
    output: queue.Queue[Any] = queue.Queue(maxsize=128)
    stopped = threading.Event()
    ready = threading.Event()
    state: dict[str, Any] = {}
    sentinel = object()

    def put(value: Any) -> bool:
        while not stopped.is_set():
            try:
                output.put(value, timeout=0.05)
                return True
            except queue.Full:
                continue
        return False

    async def produce() -> None:
        try:
            async with aclosing(factory()) as iterator:
                async for value in iterator:
                    if not put(value):
                        break
        except Exception as exc:
            put(error_handler(exc) if error_handler else exc)
        finally:
            put(sentinel)

    def run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        task = loop.create_task(produce())
        state.update(loop=loop, task=task)
        ready.set()
        try:
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            pass
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    worker = threading.Thread(target=run, daemon=True, name="meshflow-stream")
    worker.start()
    try:
        while True:
            value = output.get()
            if value is sentinel:
                break
            if isinstance(value, Exception):
                raise value
            yield value
    finally:
        stopped.set()
        if ready.wait(timeout=5) and worker.is_alive():
            try:
                state["loop"].call_soon_threadsafe(state["task"].cancel)
            except RuntimeError:
                pass  # The worker completed between the liveness check and cancellation.
        worker.join(timeout=5)
