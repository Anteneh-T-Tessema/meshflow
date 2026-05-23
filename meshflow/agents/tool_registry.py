"""ToolRegistry — governed tool registration with permissions and audit trail.

All tools used by MeshFlow agents should be registered here so that:
  - Permission tiers are enforced before a call is dispatched.
  - Every invocation is logged with agent_id, args, and outcome.
  - The registry is queryable: list tools, filter by tier, inspect schemas.

Usage::

    from meshflow.agents.tool_registry import ToolRegistry, ToolPermission

    registry = ToolRegistry()

    @registry.register("web_search", permissions=[ToolPermission.NETWORK])
    async def web_search(query: str) -> str:
        ...

    @registry.register("read_file", permissions=[ToolPermission.FILESYSTEM_READ])
    def read_file(path: str) -> str:
        ...

    # Dispatch with audit logging
    result = await registry.call("web_search", agent_id="researcher", args={"query": "HIPAA §164"})

    # Inspect
    registry.list_tools()
    registry.audit_log(agent_id="researcher")
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class ToolPermission(str, Enum):
    READ_ONLY      = "read_only"       # safe read operations
    FILESYSTEM_READ = "filesystem_read" # read local files
    FILESYSTEM_WRITE = "filesystem_write" # write / delete files
    NETWORK        = "network"          # outbound HTTP / DNS
    CODE_EXEC      = "code_exec"        # run shell / subprocess
    DATABASE_READ  = "database_read"    # query databases (SELECT)
    DATABASE_WRITE = "database_write"   # mutate databases (INSERT/UPDATE/DELETE)
    EXTERNAL_API   = "external_api"     # call third-party services


@dataclass
class ToolRecord:
    name: str
    fn: Callable[..., Any]
    permissions: list[ToolPermission]
    description: str
    schema: dict[str, Any]
    registered_at: float = field(default_factory=time.time)


@dataclass
class AuditEntry:
    tool_name: str
    agent_id: str
    args: dict[str, Any]
    outcome: str  # "success" | "error" | "permission_denied"
    error: str
    duration_ms: float
    timestamp: float = field(default_factory=time.time)


class PermissionDeniedError(Exception):
    pass


class ToolNotFoundError(KeyError):
    pass


def _build_schema(fn: Callable[..., Any]) -> dict[str, Any]:
    """Build a minimal JSON-schema-like dict from a function's type hints."""
    sig = inspect.signature(fn)
    props: dict[str, Any] = {}
    required: list[str] = []
    hints = fn.__annotations__ if hasattr(fn, "__annotations__") else {}

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue
        py_type = hints.get(param_name, Any)
        json_type = _py_to_json_type(py_type)
        props[param_name] = {"type": json_type}
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    return {
        "type": "object",
        "properties": props,
        "required": required,
    }


def _py_to_json_type(py_type: Any) -> str:
    mapping = {str: "string", int: "integer", float: "number", bool: "boolean"}
    return mapping.get(py_type, "string")


class ToolRegistry:
    """Central registry for governed tool dispatch.

    Parameters
    ----------
    allowed_permissions:
        If set, only tools whose permissions are a subset of this list can be
        called. Useful for agent-level sandboxing.
    """

    def __init__(
        self,
        allowed_permissions: list[ToolPermission] | None = None,
    ) -> None:
        self._tools: dict[str, ToolRecord] = {}
        self._audit: list[AuditEntry] = []
        self._allowed = set(allowed_permissions) if allowed_permissions else None

    # ── Registration ──────────────────────────────────────────────────────────

    def register(
        self,
        name: str,
        permissions: list[ToolPermission] | None = None,
        description: str = "",
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator that registers a function as a governed tool."""

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            schema = _build_schema(fn)
            self._tools[name] = ToolRecord(
                name=name,
                fn=fn,
                permissions=permissions or [ToolPermission.READ_ONLY],
                description=description or (fn.__doc__ or "").strip()[:200],
                schema=schema,
            )
            return fn

        return decorator

    def register_tool(
        self,
        name: str,
        fn: Callable[..., Any],
        permissions: list[ToolPermission] | None = None,
        description: str = "",
    ) -> None:
        """Imperative registration (no decorator syntax)."""
        schema = _build_schema(fn)
        self._tools[name] = ToolRecord(
            name=name,
            fn=fn,
            permissions=permissions or [ToolPermission.READ_ONLY],
            description=description or (fn.__doc__ or "").strip()[:200],
            schema=schema,
        )

    # ── Dispatch ──────────────────────────────────────────────────────────────

    async def call(
        self,
        tool_name: str,
        agent_id: str = "unknown",
        args: dict[str, Any] | None = None,
        check_permissions: list[ToolPermission] | None = None,
    ) -> Any:
        """Dispatch a tool call through the governance layer.

        Parameters
        ----------
        tool_name:
            Name of the registered tool.
        agent_id:
            Identifier of the calling agent (used in audit log).
        args:
            Keyword arguments passed to the tool function.
        check_permissions:
            Additional permission check — the caller must hold all of these.
        """
        if tool_name not in self._tools:
            raise ToolNotFoundError(f"Tool '{tool_name}' is not registered.")

        record = self._tools[tool_name]
        _args = args or {}
        start = time.monotonic()

        # Permission check
        denied = self._check_permissions(record, check_permissions)
        if denied:
            entry = AuditEntry(
                tool_name=tool_name,
                agent_id=agent_id,
                args=_args,
                outcome="permission_denied",
                error=denied,
                duration_ms=0.0,
            )
            self._audit.append(entry)
            raise PermissionDeniedError(denied)

        # Execute
        try:
            fn = record.fn
            if inspect.iscoroutinefunction(fn):
                result = await fn(**_args)
            else:
                result = fn(**_args)
            duration_ms = (time.monotonic() - start) * 1000
            self._audit.append(AuditEntry(
                tool_name=tool_name,
                agent_id=agent_id,
                args=_args,
                outcome="success",
                error="",
                duration_ms=duration_ms,
            ))
            return result
        except PermissionDeniedError:
            raise
        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            self._audit.append(AuditEntry(
                tool_name=tool_name,
                agent_id=agent_id,
                args=_args,
                outcome="error",
                error=str(exc),
                duration_ms=duration_ms,
            ))
            raise

    def _check_permissions(
        self, record: ToolRecord, extra: list[ToolPermission] | None
    ) -> str:
        """Return an error message if permissions are violated, else empty string."""
        if self._allowed is not None:
            denied = [p for p in record.permissions if p not in self._allowed]
            if denied:
                return (
                    f"Tool '{record.name}' requires permissions "
                    f"{[p.value for p in denied]} not granted to this registry."
                )
        if extra:
            missing = [p for p in extra if p not in record.permissions]
            if missing:
                return (
                    f"Tool '{record.name}' does not declare "
                    f"required permissions {[p.value for p in missing]}."
                )
        return ""

    # ── Introspection ─────────────────────────────────────────────────────────

    def list_tools(
        self, permission: ToolPermission | None = None
    ) -> list[dict[str, Any]]:
        """Return tool metadata, optionally filtered by a required permission."""
        records = self._tools.values()
        if permission:
            records = [r for r in records if permission in r.permissions]  # type: ignore[assignment]
        return [
            {
                "name": r.name,
                "permissions": [p.value for p in r.permissions],
                "description": r.description,
                "schema": r.schema,
            }
            for r in records
        ]

    def audit_log(
        self,
        agent_id: str | None = None,
        tool_name: str | None = None,
        last_n: int = 100,
    ) -> list[AuditEntry]:
        """Return recent audit entries, optionally filtered."""
        entries = self._audit
        if agent_id:
            entries = [e for e in entries if e.agent_id == agent_id]
        if tool_name:
            entries = [e for e in entries if e.tool_name == tool_name]
        return entries[-last_n:]

    def get_schema(self, tool_name: str) -> dict[str, Any]:
        """Return the JSON schema for a tool's arguments."""
        if tool_name not in self._tools:
            raise ToolNotFoundError(f"Tool '{tool_name}' is not registered.")
        return self._tools[tool_name].schema

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools


# ── Global default registry ───────────────────────────────────────────────────

default_registry = ToolRegistry()


GovernedToolRegistry = ToolRegistry  # public alias used in top-level exports

__all__ = [
    "ToolRegistry",
    "GovernedToolRegistry",
    "ToolPermission",
    "ToolRecord",
    "AuditEntry",
    "PermissionDeniedError",
    "ToolNotFoundError",
    "default_registry",
]
