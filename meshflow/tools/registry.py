"""Tool registry and @tool decorator for MeshFlow agents.

Usage:
    from meshflow.tools import tool, global_registry

    @tool(name="web_search", description="Search the web", risk=RiskTier.EXTERNAL_IO)
    async def web_search(query: str, max_results: int = 5) -> str:
        return f"Results for: {query}"

    # Tools auto-register; look them up by name
    t = global_registry.get("web_search")
    result = await t.call(query="prompt caching")

    # Generate LLM-ready tool schema
    schema = t.to_anthropic_schema()  # {"name": ..., "description": ..., "input_schema": {...}}
    schema = t.to_openai_schema()     # {"type": "function", "function": {...}}
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, get_args, get_origin

from meshflow.core.schemas import RiskTier


# ── Python type → JSON Schema mapping ────────────────────────────────────────

def _py_type_to_json_schema(annotation: Any) -> dict[str, Any]:
    """Convert a Python type annotation to a JSON Schema fragment.

    Handles: str, int, float, bool, list, dict, Optional[X], list[X], None.
    Falls back to {} (any type) for unknown/complex annotations.
    """
    if annotation is inspect.Parameter.empty or annotation is None:
        return {}

    origin = get_origin(annotation)
    args = get_args(annotation)

    # Optional[X] → Union[X, None]
    if origin is type(None):
        return {"type": "null"}

    import types as _types
    # Union / Optional
    if origin is _types.UnionType or str(origin) in ("<class 'typing.Union'>", "typing.Union"):
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            inner = _py_type_to_json_schema(non_none[0])
            inner["nullable"] = True
            return inner
        return {"oneOf": [_py_type_to_json_schema(a) for a in non_none]}

    # Generic list / List[X]
    if origin is list:
        schema: dict[str, Any] = {"type": "array"}
        if args:
            schema["items"] = _py_type_to_json_schema(args[0])
        return schema

    # Generic dict / Dict[K, V]
    if origin is dict:
        return {"type": "object"}

    # Bare primitives
    _MAP: dict[Any, str] = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        bytes: "string",
    }
    if annotation in _MAP:
        return {"type": _MAP[annotation]}

    # Fallback — accept any type
    return {}


def _build_input_schema(fn: Callable[..., Any]) -> dict[str, Any]:
    """Build a JSON Schema 'object' for all parameters of *fn*."""
    sig = inspect.signature(fn)
    hints: dict[str, Any] = {}
    try:
        import typing
        hints = typing.get_type_hints(fn)
    except Exception:
        pass

    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue
        annotation = hints.get(param_name, param.annotation)
        prop = _py_type_to_json_schema(annotation)

        # Grab inline description from default docstring-style comments if available
        doc = inspect.getdoc(fn) or ""
        if f"{param_name}:" in doc:
            for line in doc.splitlines():
                stripped = line.strip()
                if stripped.startswith(f"{param_name}:"):
                    prop["description"] = stripped[len(param_name) + 1:].strip()
                    break

        properties[param_name] = prop
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


@dataclass
class Tool:
    """A governed, discoverable tool that agents can call.

    Parameters
    ----------
    name:        Unique tool name (used as the lookup key).
    description: Human-readable description for discovery.
    fn:          The underlying callable (sync or async).
    risk:        Risk tier — governs whether HITL approval is needed.
    tags:        Optional tags for discovery filtering (e.g. ["search", "web"]).
    """

    name: str
    description: str
    fn: Callable[..., Any]
    risk: RiskTier = RiskTier.READ_ONLY
    tags: list[str] = field(default_factory=list)

    async def call(self, **kwargs: Any) -> Any:
        """Invoke the tool, handling sync/async transparently."""
        if inspect.iscoroutinefunction(self.fn):
            return await self.fn(**kwargs)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.fn(**kwargs))

    # ── Schema generation ─────────────────────────────────────────────────────

    def input_schema(self) -> dict[str, Any]:
        """Return a JSON Schema object for this tool's parameters."""
        return _build_input_schema(self.fn)

    def to_anthropic_schema(self) -> dict[str, Any]:
        """Return the Anthropic tool-calling schema format.

        Compatible with ``anthropic.AsyncAnthropic().messages.create(tools=[...])``::

            {"name": "web_search", "description": "...", "input_schema": {...}}
        """
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema(),
        }

    def to_openai_schema(self) -> dict[str, Any]:
        """Return the OpenAI / compatible tool-calling schema format.

        Compatible with ``openai.AsyncOpenAI().chat.completions.create(tools=[...])``::

            {"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema(),
            },
        }

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation of this tool (catalog format)."""
        return {
            "name": self.name,
            "description": self.description,
            "risk": self.risk.name,
            "tags": self.tags,
            "input_schema": self.input_schema(),
        }


class ToolRegistry:
    """Central catalog for discovering and retrieving tools.

    Agents resolve tool names to Tool objects through this registry.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, t: Tool) -> None:
        self._tools[t.name] = t

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not found in registry. Available: {list(self._tools)}")
        return self._tools[name]

    def search(self, query: str = "", tags: list[str] | None = None) -> list[Tool]:
        """Find tools by keyword and/or tags."""
        results = list(self._tools.values())
        if query:
            q = query.lower()
            results = [t for t in results if q in t.name.lower() or q in t.description.lower()]
        if tags:
            tag_set = set(tags)
            results = [t for t in results if tag_set & set(t.tags)]
        return results

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def catalog(self) -> list[dict[str, Any]]:
        """Return a JSON-serialisable catalog for display or export."""
        return [t.to_dict() for t in self._tools.values()]

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)


global_registry = ToolRegistry()


def tool(
    name: str | None = None,
    description: str = "",
    risk: RiskTier = RiskTier.READ_ONLY,
    tags: list[str] | None = None,
    registry: ToolRegistry | None = None,
) -> Callable[[Callable[..., Any]], Tool]:
    """Decorator to define and register a governed tool.

    @tool(name="web_search", description="Search the web", risk=RiskTier.EXTERNAL_IO)
    async def web_search(query: str) -> str:
        ...
    """

    def decorator(fn: Callable[..., Any]) -> Tool:
        t = Tool(
            name=name or fn.__name__,
            description=description or (fn.__doc__ or "").strip().split("\n")[0],
            fn=fn,
            risk=risk,
            tags=tags or [],
        )
        reg = global_registry if registry is None else registry
        reg.register(t)
        return t

    return decorator
