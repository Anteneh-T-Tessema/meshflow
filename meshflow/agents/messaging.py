"""Agent-to-agent MessageBus — typed, async pub/sub for governed agent communication.

In-process usage (default InMemoryBusBackend):
    bus = MessageBus()
    bus.subscribe("agent-b", handle)
    await bus.send(Message(sender_id="agent-a", receiver_id="agent-b", content="Hello"))

Cross-process via WebSocket (WebSocketBusBackend):
    backend = WebSocketBusBackend("ws://localhost:8000/ws/bus")
    bus = MessageBus(backend=backend)
    await bus.connect()               # open the WebSocket connection
    bus.subscribe("agent-b", handle)
    await bus.send(...)
    await bus.disconnect()

The server-side WebSocket hub is at GET /ws/bus.  Every message sent over the
bus is JSON-serialised and fan-out delivered to all other connected clients,
enabling agents in different processes (or machines) to communicate as if they
share the same in-process bus.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol, runtime_checkable

from meshflow.core.schemas import Message


MessageHandler = Callable[[Message], Awaitable[None]]


@dataclass
class RoutedMessage:
    """A message with delivery metadata."""

    message: Message
    delivered_to: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── Backend protocol ──────────────────────────────────────────────────────────


@runtime_checkable
class BusBackend(Protocol):
    """Protocol for pluggable MessageBus backends."""

    async def publish(self, message_dict: dict[str, Any]) -> None:
        """Publish a serialised message to all remote subscribers."""
        ...

    def incoming(self) -> AsyncIterator[dict[str, Any]]:
        """Async iterator of messages received from remote peers."""
        ...

    async def connect(self) -> None:
        """Open the backend connection (no-op for in-memory)."""
        ...

    async def disconnect(self) -> None:
        """Close the backend connection (no-op for in-memory)."""
        ...


class InMemoryBusBackend:
    """Default no-op backend — all delivery is handled locally by MessageBus."""

    async def publish(self, message_dict: dict[str, Any]) -> None:
        pass

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def incoming(self) -> AsyncIterator[dict[str, Any]]:  # type: ignore[override]
        # In-memory backend never delivers remote messages — use an empty generator.
        return
        yield  # noqa: unreachable — makes this an async generator


class WebSocketBusBackend:
    """Cross-process bus backend via WebSocket.

    Connects to the MeshFlow server's ``GET /ws/bus`` hub.  Messages sent via
    ``publish()`` are forwarded to the hub, which fans them out to every other
    connected client.  Incoming messages are delivered through ``incoming()``,
    which the owning ``MessageBus`` drains in a background task.

    Requires aiohttp: pip install aiohttp
    """

    def __init__(self, url: str, api_key: str = "", reconnect: bool = True) -> None:
        self._url = url
        self._api_key = api_key
        self._reconnect = reconnect
        self._ws: Any = None
        self._session: Any = None
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._connected = asyncio.Event()
        self._closed = False
        self._recv_task: asyncio.Task[None] | None = None

    async def connect(self) -> None:
        try:
            import aiohttp
        except ImportError as exc:
            raise RuntimeError(
                "WebSocketBusBackend requires aiohttp. pip install aiohttp"
            ) from exc

        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(self._url, headers=headers)
        self._connected.set()
        self._recv_task = asyncio.create_task(self._receive_loop())

    async def disconnect(self) -> None:
        self._closed = True
        if self._ws is not None:
            await self._ws.close()
        if self._session is not None:
            await self._session.close()
        if self._recv_task is not None:
            self._recv_task.cancel()

    async def publish(self, message_dict: dict[str, Any]) -> None:
        if self._ws is None or self._ws.closed:
            raise RuntimeError("WebSocketBusBackend not connected — call connect() first")
        await self._ws.send_str(json.dumps(message_dict))

    async def _receive_loop(self) -> None:
        try:
            import aiohttp

            assert self._ws is not None
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        await self._queue.put(data)
                    except json.JSONDecodeError:
                        pass
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        except asyncio.CancelledError:
            pass

    async def incoming(self) -> AsyncIterator[dict[str, Any]]:  # type: ignore[override]
        while not self._closed:
            try:
                msg = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                yield msg
            except asyncio.TimeoutError:
                continue


# ── Redis bus backend ─────────────────────────────────────────────────────────


class RedisBusBackend:
    """Production-grade cross-process bus backend via Redis pub/sub.

    Requires redis-py with asyncio support: pip install redis[asyncio]

    Usage::

        backend = RedisBusBackend("redis://localhost:6379", channel="meshflow:bus")
        bus = MessageBus(backend=backend)
        await bus.connect()
        ...
        await bus.disconnect()
    """

    def __init__(
        self,
        url: str = "redis://localhost:6379",
        channel: str = "meshflow:bus",
        db: int = 0,
    ) -> None:
        self._url = url
        self._channel = channel
        self._db = db
        self._client: Any = None
        self._pubsub: Any = None
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._closed = False
        self._recv_task: asyncio.Task[None] | None = None

    async def connect(self) -> None:
        try:
            import redis.asyncio as aioredis
        except ImportError as exc:
            raise RuntimeError(
                "RedisBusBackend requires redis[asyncio]. "
                "pip install 'redis[asyncio]'"
            ) from exc
        self._client = aioredis.from_url(self._url, db=self._db, decode_responses=True)
        self._pubsub = self._client.pubsub()
        await self._pubsub.subscribe(self._channel)
        self._recv_task = asyncio.create_task(self._receive_loop())

    async def disconnect(self) -> None:
        self._closed = True
        if self._pubsub is not None:
            await self._pubsub.unsubscribe(self._channel)
            await self._pubsub.close()
        if self._client is not None:
            await self._client.aclose()
        if self._recv_task is not None:
            self._recv_task.cancel()

    async def publish(self, message_dict: dict[str, Any]) -> None:
        if self._client is None:
            raise RuntimeError("RedisBusBackend not connected — call connect() first")
        await self._client.publish(self._channel, json.dumps(message_dict))

    async def _receive_loop(self) -> None:
        try:
            async for msg in self._pubsub.listen():
                if self._closed:
                    break
                if msg.get("type") == "message":
                    try:
                        data = json.loads(msg["data"])
                        await self._queue.put(data)
                    except (json.JSONDecodeError, KeyError):
                        pass
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def incoming(self) -> AsyncIterator[dict[str, Any]]:  # type: ignore[override]
        while not self._closed:
            try:
                msg = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                yield msg
            except asyncio.TimeoutError:
                continue


# ── MessageBus ────────────────────────────────────────────────────────────────


class MessageBus:
    """Async pub/sub bus for agent-to-agent communication.

    Default backend is in-memory (single-process).  Pass a
    ``WebSocketBusBackend`` for cross-process messaging through the
    MeshFlow server hub (``GET /ws/bus``).

    Each agent subscribes with its agent_id. Messages are routed by receiver_id.
    Use receiver_id="*" to broadcast to all subscribers.
    """

    def __init__(self, backend: BusBackend | None = None) -> None:
        self._subscribers: dict[str, list[MessageHandler]] = defaultdict(list)
        self._history: list[RoutedMessage] = []
        self._backend: BusBackend = backend or InMemoryBusBackend()
        self._remote_task: asyncio.Task[None] | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Open the backend and start draining remote messages."""
        await self._backend.connect()
        if not isinstance(self._backend, InMemoryBusBackend):
            self._remote_task = asyncio.create_task(self._drain_remote())

    async def disconnect(self) -> None:
        """Close the backend connection."""
        if self._remote_task is not None:
            self._remote_task.cancel()
        await self._backend.disconnect()

    async def _drain_remote(self) -> None:
        """Background task: receive messages from the remote backend and deliver locally."""
        try:
            async for msg_dict in self._backend.incoming():
                try:
                    msg = Message(**msg_dict)
                    await self._deliver_locally(msg, RoutedMessage(message=msg))
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass

    # ── Subscription ──────────────────────────────────────────────────────────

    def subscribe(self, agent_id: str, handler: MessageHandler) -> None:
        """Register a handler for messages addressed to agent_id."""
        self._subscribers[agent_id].append(handler)

    def unsubscribe(self, agent_id: str) -> None:
        self._subscribers.pop(agent_id, None)

    # ── Sending ───────────────────────────────────────────────────────────────

    async def send(self, message: Message) -> RoutedMessage:
        """Send a message to a specific agent or broadcast if receiver_id is '*'."""
        if not message.trace_id:
            object.__setattr__(message, "trace_id", str(uuid.uuid4()))

        routed = RoutedMessage(message=message)

        # Local delivery
        await self._deliver_locally(message, routed)

        # Remote delivery (no-op for InMemoryBusBackend)
        try:
            await self._backend.publish(_message_to_dict(message))
        except Exception as exc:
            routed.failed.append(f"remote:{exc}")

        self._history.append(routed)
        return routed

    async def broadcast(self, message: Message) -> RoutedMessage:
        """Send to all subscribers regardless of receiver_id."""
        object.__setattr__(message, "receiver_id", "*")
        return await self.send(message)

    async def _deliver_locally(self, message: Message, routed: RoutedMessage) -> None:
        if message.receiver_id == "*":
            targets = list(self._subscribers.keys())
        else:
            targets = [message.receiver_id] if message.receiver_id in self._subscribers else []

        await asyncio.gather(
            *[
                self._deliver(handler, message, agent_id, routed)
                for agent_id in targets
                for handler in self._subscribers[agent_id]
            ],
            return_exceptions=True,
        )

    async def _deliver(
        self,
        handler: MessageHandler,
        message: Message,
        agent_id: str,
        routed: RoutedMessage,
    ) -> None:
        try:
            await handler(message)
            routed.delivered_to.append(agent_id)
        except Exception as exc:
            routed.failed.append(f"{agent_id}:{exc}")

    # ── Introspection ─────────────────────────────────────────────────────────

    def history(self, limit: int = 50) -> list[RoutedMessage]:
        return self._history[-limit:]

    def conversation(self, agent_a: str, agent_b: str) -> list[Message]:
        """Return all messages exchanged between two agents."""
        return [
            rm.message
            for rm in self._history
            if {rm.message.sender_id, rm.message.receiver_id} >= {agent_a, agent_b}
        ]

    def clear_history(self) -> None:
        self._history.clear()


# ── helpers ───────────────────────────────────────────────────────────────────


def _message_to_dict(msg: Message) -> dict[str, Any]:
    return {
        "sender_id": msg.sender_id,
        "receiver_id": msg.receiver_id,
        "content": msg.content,
        "trace_id": msg.trace_id,
        "metadata": msg.metadata if hasattr(msg, "metadata") else {},
    }
