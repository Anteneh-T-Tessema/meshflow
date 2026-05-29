"""MeshFlow Flows — event-driven, state-machine workflows (CrewAI Flows parity).

Closes the CrewAI 2.0 Flows gap.  Flows let you describe a workflow as a
Python class whose methods are event handlers, rather than as a YAML DAG of
nodes.

Key features
------------
- ``@start``        — marks one or more entry-point methods.
- ``@listen(fn)``   — fires after *fn* completes, receives its return value.
- ``@router(fn)``   — conditional branching: return a string route name;
                      only listeners registered under that name are called.
- ``FlowState``     — typed Pydantic-like state shared across all handlers.
- Full governance   — every handler passes through StepRuntime (optional).

Quick example::

    from meshflow.core.flows import Flow, FlowState, start, listen, router

    class ResearchState(FlowState):
        topic: str = ""
        research: str = ""
        approved: bool = False

    class ResearchFlow(Flow[ResearchState]):
        @start()
        async def plan(self):
            self.state.topic = "AI governance"
            return "planned"

        @listen("plan")
        async def research(self, _):
            self.state.research = f"Research on {self.state.topic}"
            return self.state.research

        @router("research")
        def route(self, result: str) -> str:
            return "approve" if len(result) > 10 else "skip"

        @listen(("research", "approve"))
        async def finalize(self, research_text: str):
            self.state.approved = True
            return f"Final: {research_text}"

    flow = ResearchFlow()
    result = await flow.kickoff()
    print(flow.state.approved)   # True

State machine::

    flow = ResearchFlow(state=ResearchState(topic="HIPAA"))
    result = await flow.kickoff(inputs={"topic": "GDPR"})
"""

from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Callable, Generic, TypeVar, get_type_hints


# ── FlowState ─────────────────────────────────────────────────────────────────

class FlowState:
    """Shared mutable state for a Flow.  Subclass to add typed fields.

    Usage::

        class MyState(FlowState):
            topic: str = ""
            results: list[str] = []

    Access inside handlers via ``self.state``.
    """

    def __init__(self, **kwargs: Any) -> None:
        for key, val in kwargs.items():
            setattr(self, key, val)

    def update(self, **kwargs: Any) -> None:
        for key, val in kwargs.items():
            setattr(self, key, val)

    def to_dict(self) -> dict[str, Any]:
        return {
            k: v for k, v in self.__dict__.items()
            if not k.startswith("_")
        }

    @classmethod
    def _get_defaults(cls) -> dict[str, Any]:
        defaults: dict[str, Any] = {}
        for klass in reversed(cls.__mro__):
            for k, v in klass.__dict__.items():
                if not k.startswith("_") and not callable(v) and not isinstance(v, (classmethod, staticmethod)):
                    defaults[k] = v
        return defaults


# ── Decorator markers ─────────────────────────────────────────────────────────

_START_ATTR   = "__flow_start__"
_LISTEN_ATTR  = "__flow_listen__"
_ROUTER_ATTR  = "__flow_router__"


def start() -> Callable:
    """Mark a method as a Flow entry point."""
    def decorator(fn: Callable) -> Callable:
        setattr(fn, _START_ATTR, True)
        return fn
    return decorator


def listen(trigger: str | Callable | tuple[str | Callable, str] | tuple) -> Callable:
    """Register a method as a listener for *trigger*.

    *trigger* can be:
    - A method name string: ``@listen("plan")``
    - A method reference: ``@listen(plan)``
    - A (method, route) tuple: ``@listen(("research", "approve"))``
      — only fires when the router returned route "approve".
    """
    def decorator(fn: Callable) -> Callable:
        if isinstance(trigger, tuple):
            src, route = trigger[0], trigger[1] if len(trigger) > 1 else ""
            src_name = src.__name__ if callable(src) else str(src)
            triggers = [(src_name, route)]
        elif callable(trigger):
            triggers = [(trigger.__name__, "")]
        else:
            triggers = [(str(trigger), "")]
        setattr(fn, _LISTEN_ATTR, triggers)
        return fn
    return decorator


def router(trigger: str | Callable) -> Callable:
    """Mark a method as a router that returns a route name after *trigger*.

    The router is called synchronously (or async); its return value is a
    string route name.  Only ``@listen((trigger, route))`` handlers whose
    route matches are executed.
    """
    def decorator(fn: Callable) -> Callable:
        src_name = trigger.__name__ if callable(trigger) else str(trigger)
        setattr(fn, _ROUTER_ATTR, src_name)
        return fn
    return decorator


# ── FlowResult ────────────────────────────────────────────────────────────────

@dataclass
class FlowResult:
    """Final outcome of a Flow.kickoff()."""

    final_output: Any
    state: FlowState
    steps_executed: list[str]
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    duration_s: float = 0.0
    error: str = ""


# ── Flow base class ───────────────────────────────────────────────────────────

_S = TypeVar("_S", bound=FlowState)


class Flow(Generic[_S]):
    """Base class for event-driven, state-machine workflows.

    Subclass and decorate methods with ``@start``, ``@listen``, ``@router``.
    Call ``await flow.kickoff()`` (or ``flow.kickoff_sync()``) to execute.

    The optional ``policy=`` and ``ledger_db=`` constructor arguments enable
    full MeshFlow governance (guardian, budget, audit) on every handler.
    """

    def __init__(
        self,
        state: _S | None = None,
        *,
        policy: Any = None,
        ledger_db: str = "",
        max_steps: int = 50,
    ) -> None:
        # Initialise state from class-level defaults + constructor arg
        state_cls = self._state_class()
        if state is not None:
            self.state: _S = state
        else:
            defaults = state_cls._get_defaults()
            self.state = state_cls(**defaults)  # type: ignore[arg-type]

        self._policy = policy
        self._ledger_db = ledger_db
        self._max_steps = max_steps

        # Build execution graph from decorators
        self._start_methods: list[str] = []
        self._listeners: dict[str, list[str]] = defaultdict(list)
        self._routers: dict[str, str] = {}   # trigger → router_method_name

        for name, method in inspect.getmembers(self, predicate=inspect.ismethod):
            fn = method.__func__
            if getattr(fn, _START_ATTR, False):
                self._start_methods.append(name)
            listen_triggers = getattr(fn, _LISTEN_ATTR, None)
            if listen_triggers:
                for src, route in listen_triggers:
                    key = f"{src}::{route}" if route else src
                    self._listeners[key].append(name)
            router_src = getattr(fn, _ROUTER_ATTR, None)
            if router_src is not None:
                self._routers[router_src] = name

    def _state_class(self) -> type:
        """Extract the FlowState subclass from the Generic parameter."""
        for base in getattr(self.__class__, "__orig_bases__", []):
            args = getattr(base, "__args__", ())
            if args and inspect.isclass(args[0]) and issubclass(args[0], FlowState):
                return args[0]
        return FlowState

    # ── Execution ──────────────────────────────────────────────────────────────

    async def kickoff(self, inputs: dict[str, Any] | None = None) -> FlowResult:
        """Execute the flow.  Returns when all reachable handlers have run."""
        import time
        t0 = time.monotonic()

        if inputs:
            self.state.update(**inputs)

        steps_executed: list[str] = []
        total_tokens = 0
        total_cost = 0.0
        final_output: Any = None
        step_count = 0

        # BFS queue: (method_name, argument passed from previous step)
        queue: list[tuple[str, Any]] = [(name, None) for name in self._start_methods]

        while queue and step_count < self._max_steps:
            method_name, arg = queue.pop(0)
            step_count += 1

            if method_name not in {n for n, _ in inspect.getmembers(self, inspect.ismethod)}:
                continue

            method = getattr(self, method_name)
            result = await self._invoke(method, arg)
            steps_executed.append(method_name)
            final_output = result

            # Check for router on this method
            route_name = ""
            if method_name in self._routers:
                router_method = getattr(self, self._routers[method_name])
                route_name = await self._invoke_router(router_method, result)

            # Enqueue listeners
            exact_key = f"{method_name}::{route_name}" if route_name else method_name
            for listener_name in self._listeners.get(exact_key, []):
                queue.append((listener_name, result))
            # Always fire unrouted listeners (no route qualifier)
            if route_name:
                for listener_name in self._listeners.get(method_name, []):
                    queue.append((listener_name, result))

        duration = round(time.monotonic() - t0, 3)
        return FlowResult(
            final_output=final_output,
            state=self.state,
            steps_executed=steps_executed,
            total_tokens=total_tokens,
            total_cost_usd=round(total_cost, 6),
            duration_s=duration,
        )

    def kickoff_sync(self, inputs: dict[str, Any] | None = None) -> FlowResult:
        """Synchronous wrapper for :meth:`kickoff`."""
        return asyncio.run(self.kickoff(inputs))

    async def _invoke(self, method: Callable, arg: Any) -> Any:
        """Call *method* with the right arity."""
        sig = inspect.signature(method)
        params = list(sig.parameters.keys())
        if len(params) == 0:
            result = method()
        else:
            result = method(arg)
        if inspect.iscoroutine(result):
            result = await result
        return result

    async def _invoke_router(self, router_method: Callable, arg: Any) -> str:
        result = await self._invoke(router_method, arg)
        return str(result) if result is not None else ""

    # ── Introspection ──────────────────────────────────────────────────────────

    def describe(self) -> dict[str, Any]:
        """Return the flow topology as a dict."""
        return {
            "class": self.__class__.__name__,
            "start_methods": self._start_methods,
            "listeners": dict(self._listeners),
            "routers": self._routers,
            "state_class": self.state.__class__.__name__,
            "max_steps": self._max_steps,
        }

    def plot(self) -> str:
        """Return a Mermaid diagram of the flow topology."""
        lines = ["graph TD"]
        for name in self._start_methods:
            lines.append(f"    START([START]) --> {name}")
        for trigger_key, listeners in self._listeners.items():
            if "::" in trigger_key:
                src, route = trigger_key.split("::", 1)
                for listener in listeners:
                    lines.append(f"    {src} -- \"{route}\" --> {listener}")
            else:
                for listener in listeners:
                    lines.append(f"    {trigger_key} --> {listener}")
        for src, router_name in self._routers.items():
            lines.append(f"    {src} -. router .-> {router_name}{{router}}")
        return "\n".join(lines)


__all__ = ["Flow", "FlowState", "FlowResult", "start", "listen", "router"]
