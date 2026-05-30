"""Base agent and role-specific agents — AutoGen DNA with HITL support."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, cast, runtime_checkable

from meshflow.core.schemas import (
    AgentRole,
    AgentState,
    Evidence,
    Intent,
    Message,
    Policy,
    RiskTier,
    TokenChunk,
)

# ── Pricing registry (Claude 4.x rates, May 2025) ─────────────────────────────
# Key: substring that appears in model name. Match order matters — more specific first.
# Tuple: (input_usd_per_1k_tokens, output_usd_per_1k_tokens)
_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-7": (0.015, 0.075),
    "claude-opus-4-5": (0.015, 0.075),
    "claude-sonnet-4-6": (0.003, 0.015),
    "claude-sonnet-4-5": (0.003, 0.015),
    "claude-haiku-4-5": (0.0008, 0.004),
    "opus": (0.015, 0.075),
    "sonnet": (0.003, 0.015),
    "haiku": (0.0008, 0.004),
}


# Publicly mutable so callers can update rates without a code change:
#   from meshflow.agents.base import update_pricing
#   update_pricing("my-model", input_per_1k=0.002, output_per_1k=0.008)
def update_pricing(model_key: str, input_per_1k: float, output_per_1k: float) -> None:
    """Register or update the per-token price for a model key."""
    _PRICING[model_key] = (input_per_1k, output_per_1k)


def _cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    model_lower = model.lower()
    for key, (in_rate, out_rate) in _PRICING.items():
        if key in model_lower:
            return (input_tokens / 1000) * in_rate + (output_tokens / 1000) * out_rate
    return (input_tokens / 1000) * 0.003 + (output_tokens / 1000) * 0.015


# ── LLM provider abstraction ──────────────────────────────────────────────────


@runtime_checkable
class LLMProvider(Protocol):
    """Plug-in interface for any LLM backend.

    Implement this to add GPT-4, Gemini, a local model, or a stub for tests.
    Inject via ``AgentConfig(provider=MyProvider())``.
    """

    async def complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        response_format: str | None = None,
    ) -> tuple[str, int, float]:
        """Return (content, total_tokens, cost_usd).

        Parameters
        ----------
        response_format:
            Optional output format hint.  Pass ``"json"`` to request a
            JSON-only response.  Providers that support native JSON mode
            (e.g. OpenAI) enable it; others prepend a system-prompt directive.
        """
        ...

    async def complete_with_tools(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        tool_schemas: list[dict[str, Any]],
        tool_fns: dict[str, Any],
    ) -> tuple[str, int, float]:
        """Run the tool-dispatch loop. Return (content, total_tokens, cost_usd)."""
        ...

    def stream_complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        agent_id: str,
        step_id: str,
        run_id: str,
    ) -> AsyncIterator[TokenChunk]:
        """Yield TokenChunk objects as the LLM produces tokens."""
        ...


def _require_anthropic() -> Any:
    """Lazy-import the anthropic SDK; raises ImportError with install hint if missing."""
    try:
        import anthropic
        return anthropic
    except ImportError as exc:
        raise ImportError(
            "The default AnthropicProvider requires the anthropic SDK. "
            "Install it with: pip install anthropic"
        ) from exc


class AnthropicProvider(LLMProvider):
    """Default provider — uses the Anthropic Python SDK with real tool dispatch."""

    def __init__(self) -> None:
        anthropic = _require_anthropic()
        self._client = anthropic.AsyncAnthropic()

    async def complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        response_format: str | None = None,
    ) -> tuple[str, int, float]:
        if response_format == "json":
            system = (system + "\n\nRespond with valid JSON only. No prose before or after the JSON.").strip()

        system_param: Any = system
        extra_headers = None

        from meshflow.optimization.tracker import active_tracker
        tracker = active_tracker.get()
        if tracker is not None and system:
            system_param = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"}
                }
            ]
            extra_headers = {"anthropic-beta": "prompt-caching-2024-07-31"}

        response = await self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_param,
            messages=cast(Any, messages),
            extra_headers=extra_headers,
        )
        first = cast(Any, response.content[0]) if response.content else None
        content = str(getattr(first, "text", "")) if first else ""
        cost = _cost_usd(model, response.usage.input_tokens, response.usage.output_tokens)
        total = response.usage.input_tokens + response.usage.output_tokens
        return content, total, cost

    async def complete_with_tools(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        tool_schemas: list[dict[str, Any]],
        tool_fns: dict[str, Any],
    ) -> tuple[str, int, float]:
        msgs: list[Any] = list(messages)
        total_in = total_out = 0
        max_rounds = 10

        system_param: Any = system
        extra_headers = None
        current_tool_schemas = tool_schemas

        from meshflow.optimization.tracker import active_tracker
        tracker = active_tracker.get()
        if tracker is not None:
            extra_headers = {"anthropic-beta": "prompt-caching-2024-07-31"}
            if system:
                system_param = [
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"}
                    }
                ]
            if tool_schemas:
                current_tool_schemas = [dict(t) for t in tool_schemas]
                current_tool_schemas[-1]["cache_control"] = {"type": "ephemeral"}

        for _ in range(max_rounds):
            response = await self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_param,
                messages=cast(Any, msgs),
                tools=cast(Any, current_tool_schemas),
                extra_headers=extra_headers,
            )
            total_in += response.usage.input_tokens
            total_out += response.usage.output_tokens

            # Cast the entire content list to Any so mypy doesn't complain about
            # union-attr on the specific block subtypes we've already filtered.
            content_any: list[Any] = cast(Any, response.content)
            tool_uses: list[Any] = [b for b in content_any if getattr(b, "type", "") == "tool_use"]
            if not tool_uses or response.stop_reason == "end_turn":
                text_blocks: list[Any] = [
                    b for b in content_any if getattr(b, "type", "") == "text"
                ]
                content = str(getattr(text_blocks[0], "text", "")) if text_blocks else ""
                return content, total_in + total_out, _cost_usd(model, total_in, total_out)

            # Append assistant turn with tool calls
            msgs.append({"role": "assistant", "content": content_any})

            # Execute all tool calls in parallel
            async def _call_tool(tu: Any) -> dict[str, Any]:
                fn = tool_fns.get(tu.name)
                if fn is None:
                    result_text = f"Tool '{tu.name}' is not available."
                else:
                    try:
                        kwargs = dict(tu.input) if tu.input else {}
                        if asyncio.iscoroutinefunction(fn):
                            result_text = str(await fn(**kwargs))
                        else:
                            loop = asyncio.get_event_loop()
                            result_text = str(
                                await loop.run_in_executor(None, lambda: fn(**kwargs))
                            )
                    except Exception as exc:
                        result_text = f"Tool error: {exc}"
                return {"type": "tool_result", "tool_use_id": tu.id, "content": result_text}

            tool_results: list[dict[str, Any]] = list(
                await asyncio.gather(*[_call_tool(tu) for tu in tool_uses])
            )
            msgs.append({"role": "user", "content": cast(Any, tool_results)})

        return (
            "[max tool rounds reached]",
            total_in + total_out,
            _cost_usd(model, total_in, total_out),
        )

    async def stream_complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        agent_id: str,
        step_id: str,
        run_id: str,
    ) -> AsyncIterator[TokenChunk]:
        async with self._client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=cast(Any, messages),
        ) as stream:
            async for text in stream.text_stream:
                yield TokenChunk(text=text, agent_id=agent_id, step_id=step_id, run_id=run_id)


class OpenAICompatibleProvider(LLMProvider):
    """Provider for any OpenAI-compatible endpoint (OpenAI, Ollama, Groq, etc.).

    Requires ``openai`` package: pip install openai
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: str = "",
        base_url: str = "",
        input_rate: float = 0.005,
        output_rate: float = 0.015,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._in_rate = input_rate
        self._out_rate = output_rate

    def _client(self) -> Any:
        try:
            import openai
        except ImportError as exc:
            raise ImportError(
                "OpenAICompatibleProvider requires openai: pip install openai"
            ) from exc
        kwargs: dict[str, Any] = {}
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._base_url:
            kwargs["base_url"] = self._base_url
        return openai.AsyncOpenAI(**kwargs)

    async def complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        response_format: str | None = None,
    ) -> tuple[str, int, float]:
        client = self._client()
        oai_messages = [{"role": "system", "content": system}, *messages]
        extra: dict[str, Any] = {}
        if response_format == "json":
            extra["response_format"] = {"type": "json_object"}
        response = await client.chat.completions.create(
            model=model or self._model,
            messages=cast(Any, oai_messages),
            max_tokens=max_tokens,
            **extra,
        )
        content = response.choices[0].message.content or ""
        in_tok = response.usage.prompt_tokens if response.usage else 0
        out_tok = response.usage.completion_tokens if response.usage else 0
        cost = (in_tok / 1000) * self._in_rate + (out_tok / 1000) * self._out_rate
        return content, in_tok + out_tok, cost

    async def complete_with_tools(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        tool_schemas: list[dict[str, Any]],
        tool_fns: dict[str, Any],
    ) -> tuple[str, int, float]:
        client = self._client()
        oai_messages: list[Any] = [{"role": "system", "content": system}, *messages]
        # Convert Anthropic-style schemas to OpenAI function schemas
        oai_tools = [_anthropic_to_oai_tool(s) for s in tool_schemas]
        total_in = total_out = 0
        max_rounds = 10

        for _ in range(max_rounds):
            response = await client.chat.completions.create(
                model=model or self._model,
                messages=cast(Any, oai_messages),
                max_tokens=max_tokens,
                tools=cast(Any, oai_tools),
            )
            msg = response.choices[0].message
            total_in += response.usage.prompt_tokens if response.usage else 0
            total_out += response.usage.completion_tokens if response.usage else 0

            if not msg.tool_calls:
                content = msg.content or ""
                cost = (total_in / 1000) * self._in_rate + (total_out / 1000) * self._out_rate
                return content, total_in + total_out, cost

            oai_messages.append(msg)
            for tc in msg.tool_calls:
                fn = tool_fns.get(tc.function.name)
                if fn is None:
                    result_text = f"Tool '{tc.function.name}' not available."
                else:
                    try:
                        kwargs = json.loads(tc.function.arguments or "{}")
                        if asyncio.iscoroutinefunction(fn):
                            result_text = str(await fn(**kwargs))
                        else:
                            loop = asyncio.get_event_loop()
                            result_text = str(
                                await loop.run_in_executor(None, lambda: fn(**kwargs))
                            )
                    except Exception as exc:
                        result_text = f"Tool error: {exc}"
                oai_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_text,
                    }
                )

        cost = (total_in / 1000) * self._in_rate + (total_out / 1000) * self._out_rate
        return "[max tool rounds reached]", total_in + total_out, cost

    async def stream_complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        agent_id: str,
        step_id: str,
        run_id: str,
    ) -> AsyncIterator[TokenChunk]:
        client = self._client()
        oai_messages = [{"role": "system", "content": system}, *messages]
        stream = await client.chat.completions.create(
            model=model or self._model,
            messages=cast(Any, oai_messages),
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield TokenChunk(
                    text=delta.content, agent_id=agent_id, step_id=step_id, run_id=run_id
                )


class EchoProvider(LLMProvider):
    """Zero-dependency provider for offline use, tests, and demos.

    Requires no API key and makes no network calls.  Returns a canned response
    that echoes the last user message prefixed with ``[echo]``.

    Use it directly::

        agent = Agent(name="a", role="executor", provider=EchoProvider())

    Or enable globally with the ``MESHFLOW_MOCK=1`` environment variable —
    MeshFlow will automatically use EchoProvider for every agent that does not
    have an explicit provider set.

    This is how LangGraph, CrewAI, and AutoGen let you test orchestration
    logic without paying for API calls.
    """

    def __init__(self, response: str = "") -> None:
        # ``response`` overrides the echo behaviour with a fixed reply — useful
        # for tests that need predictable, deterministic output.
        self._fixed = response

    async def complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        response_format: str | None = None,
    ) -> tuple[str, int, float]:
        if self._fixed:
            return self._fixed, len(self._fixed.split()), 0.0
        last = messages[-1].get("content", "") if messages else "Hello"
        if isinstance(last, list):
            last = " ".join(
                p.get("text", "") for p in last if isinstance(p, dict)
            )
        if response_format == "json":
            import json as _json
            reply = _json.dumps({"echo": last})
        else:
            reply = f"[echo] {last}"
        return reply, len(reply.split()), 0.0

    async def complete_with_tools(
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        tool_schemas: list[dict[str, Any]],
        tool_fns: dict[str, Any],
    ) -> tuple[str, int, float]:
        return await self.complete(model, messages, system, max_tokens)

    async def stream_complete(  # type: ignore[override]
        self,
        model: str,
        messages: list[dict[str, Any]],
        system: str,
        max_tokens: int,
        agent_id: str,
        step_id: str,
        run_id: str,
    ) -> AsyncIterator[TokenChunk]:
        content, _, _ = await self.complete(model, messages, system, max_tokens)
        for word in content.split():
            yield TokenChunk(text=word + " ", agent_id=agent_id, step_id=step_id, run_id=run_id)


def _default_provider() -> LLMProvider:
    """Auto-detect and return the best available LLM provider.

    Checks (in order): MESHFLOW_MOCK → MESHFLOW_PROVIDER → ANTHROPIC_API_KEY
    → OPENAI_API_KEY → GOOGLE_API_KEY → AWS credentials → Azure → Ollama
    → LITELLM_MODEL → EchoProvider fallback.

    See meshflow.agents.providers.auto_detect_provider for full details.
    """
    from meshflow.agents.providers import auto_detect_provider
    return auto_detect_provider()


def _anthropic_to_oai_tool(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": schema["name"],
            "description": schema.get("description", ""),
            "parameters": schema.get("input_schema", {"type": "object", "properties": {}}),
        },
    }


# ── Tool schema helpers ───────────────────────────────────────────────────────


def _ann_to_json_schema(ann: Any, description: str = "") -> dict[str, Any]:
    """Convert a Python type annotation to a JSON Schema dict.

    Handles: str, int, float, bool, list[X], dict[str, X],
    Optional[X] (→ anyOf with null), Literal["a","b"] (→ enum),
    Annotated[X, "description"] (→ uses string metadata as description),
    Pydantic BaseModel (→ model_json_schema()).
    """
    import typing

    origin = getattr(ann, "__origin__", None)
    args = getattr(ann, "__args__", ())

    # Annotated[X, "description string"]
    if origin is getattr(typing, "Annotated", None) or str(origin) == "typing.Annotated":
        base = args[0] if args else str
        desc = next((a for a in args[1:] if isinstance(a, str)), description)
        schema = _ann_to_json_schema(base, desc)
        if desc:
            schema["description"] = desc
        return schema

    # Optional[X] == Union[X, None]
    if origin is getattr(typing, "Union", None):
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            inner = _ann_to_json_schema(non_none[0], description)
            result: dict[str, Any] = {"anyOf": [inner, {"type": "null"}]}
            if description:
                result["description"] = description
            return result

    # Literal["a", "b"]
    if origin is getattr(typing, "Literal", None):
        result = {"enum": list(args)}
        if description:
            result["description"] = description
        return result

    # list[X]
    if origin is list:
        item_schema = _ann_to_json_schema(args[0]) if args else {"type": "string"}
        result = {"type": "array", "items": item_schema}
        if description:
            result["description"] = description
        return result

    # dict[str, X]
    if origin is dict:
        result = {"type": "object"}
        if description:
            result["description"] = description
        return result

    # Pydantic BaseModel
    try:
        from pydantic import BaseModel

        if isinstance(ann, type) and issubclass(ann, BaseModel):
            schema = ann.model_json_schema()
            if description:
                schema["description"] = description
            return schema
    except ImportError:
        pass

    # Primitive types
    _PRIM_MAP = {str: "string", int: "integer", float: "number", bool: "boolean", bytes: "string"}
    json_type = _PRIM_MAP.get(ann, "string")
    result = {"type": json_type}
    if description:
        result["description"] = description
    return result


def _build_tool_schema(tool: Any) -> dict[str, Any]:
    """Build an Anthropic-compatible tool schema from a MeshFlow Tool object.

    Supports: raw input_schema dict, Pydantic BaseModel first param, and
    full type annotation inference (Optional, List, Literal, Annotated, etc.).
    """
    # Escape hatch: caller provided a complete schema
    if getattr(tool, "input_schema", None) is not None:
        return {
            "name": str(getattr(tool, "name", "tool")),
            "description": str(getattr(tool, "description", "")),
            "input_schema": tool.input_schema,
        }

    props: dict[str, Any] = {}
    required: list[str] = []

    fn = getattr(tool, "fn", None)
    if fn is not None:
        try:
            sig = inspect.signature(fn)
            hints = {}
            try:
                import typing

                hints = typing.get_type_hints(fn, include_extras=True)
            except Exception:
                pass

            for pname, param in sig.parameters.items():
                if pname in ("self", "cls") or param.kind in (
                    inspect.Parameter.VAR_KEYWORD,
                    inspect.Parameter.VAR_POSITIONAL,
                ):
                    continue
                ann = hints.get(pname, param.annotation)
                if ann is inspect.Parameter.empty:
                    ann = str
                desc = pname.replace("_", " ")
                props[pname] = _ann_to_json_schema(ann, desc)
                if param.default is inspect.Parameter.empty:
                    required.append(pname)
        except (ValueError, TypeError):
            pass

    if not props:
        props = {"input": {"type": "string", "description": "Input to the tool"}}
        required = ["input"]

    return {
        "name": str(getattr(tool, "name", "tool")),
        "description": str(getattr(tool, "description", "")),
        "input_schema": {"type": "object", "properties": props, "required": required},
    }


# ── Confidence extraction ─────────────────────────────────────────────────────

_CONFIDENCE_SUFFIX = (
    "\n\nOn the very last line of your response, write your confidence as: "
    "CONFIDENCE:0.XX  (a number 0.00–1.00, e.g. CONFIDENCE:0.82)"
)


def _extract_confidence(content: str) -> tuple[float, str]:
    """Parse 'CONFIDENCE:0.XX' from the last lines; return (confidence, clean_content)."""
    lines = content.strip().split("\n")
    for i in range(len(lines) - 1, max(len(lines) - 5, -1), -1):
        m = re.search(r"CONFIDENCE:\s*([0-9](?:\.[0-9]+)?)", lines[i], re.IGNORECASE)
        if m:
            confidence = min(1.0, max(0.0, float(m.group(1))))
            clean = "\n".join(lines[:i] + lines[i + 1 :]).strip()
            return confidence, clean
    return 0.80, content  # default when model doesn't emit the tag


def _parse_json_retry(raw: str) -> dict[str, Any] | None:
    """Try json.loads, then regex-extract the first {...} block."""
    try:
        return cast(dict[str, Any], json.loads(raw))
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return cast(dict[str, Any], json.loads(m.group()))
            except json.JSONDecodeError:
                pass
    return None


# ── Agent config ──────────────────────────────────────────────────────────────


@dataclass
class AgentConfig:
    agent_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    role: AgentRole = AgentRole.EXECUTOR
    model: str = "claude-sonnet-4-6"
    system_prompt: str = ""
    tools: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    max_tokens: int = 4096
    temperature: float = 0.7
    provider: LLMProvider | None = None  # None → AnthropicProvider()


# ── Base agent ────────────────────────────────────────────────────────────────


class BaseAgent:
    """Base agent — wraps an LLM call with MeshFlow protocol.

    All agents:
    - Emit typed Messages (never raw strings)
    - Declare Intents before executing side-effects
    - Report uncertainty as part of every output
    - Maintain an AgentState that the graph can checkpoint
    """

    def __init__(self, config: AgentConfig, policy: Policy) -> None:
        self.config = config
        self.policy = policy
        if config.provider is not None:
            self._provider: LLMProvider = config.provider
        else:
            # Infer provider from model name first (CrewAI pattern),
            # fall back to environment-based auto-detection.
            from meshflow.agents.providers import model_to_provider
            self._provider = model_to_provider(config.model) if config.model else _default_provider()
        self._state = AgentState(
            agent_id=config.agent_id,
            role=config.role,
        )
        self._call_count = 0
        self._total_tokens = 0
        self._total_cost = 0.0

    @property
    def agent_id(self) -> str:
        return self.config.agent_id

    @property
    def role(self) -> AgentRole:
        return self.config.role

    @property
    def state(self) -> AgentState:
        return self._state

    # ── Core LLM calls ───────────────────────────────────────────────────────

    async def think(
        self,
        messages: list[dict[str, str]],
        system: str | None = None,
    ) -> tuple[str, int, float]:
        """Single LLM call — returns (content, tokens, cost_usd)."""
        from meshflow.optimization.tracker import active_tracker
        tracker = active_tracker.get()

        model = self.config.model
        if tracker is not None and tracker.should_degrade():
            model = tracker.fallback_model

        # ── Health auto-recovery: swap to healthiest fallback if primary is degraded ──
        try:
            from meshflow.agents.health import get_health_tracker
            _health = get_health_tracker()
            if _health.is_degraded(model):
                from meshflow.agents.router import ProviderRouter
                _router = ProviderRouter()
                _router.set_fallback_chain(
                    "claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"
                )
                _role = getattr(self.config, "role", None)
                _role_str = _role.value if hasattr(_role, "value") else str(_role or "executor")
                _, model = _router.route_with_health(_role_str, tracker=_health)
        except Exception:
            pass  # best-effort; never blocks execution

        sys = system or self.config.system_prompt
        msgs = messages
        if tracker is not None:
            sys, msgs = tracker.compress_prompt(sys, msgs)

        from meshflow.resilience.rate_limit import with_rate_limit_retry, get_default_policy
        content, tokens, cost = await with_rate_limit_retry(
            self._provider.complete,
            policy=get_default_policy(),
            model=model,
            messages=cast(Any, msgs),
            system=sys,
            max_tokens=self.config.max_tokens,
        )
        self._call_count += 1
        self._total_tokens += tokens
        self._total_cost += cost
        self._state.token_count += tokens
        self._state.cost_usd += cost

        if tracker is not None:
            tracker.add_usage(tokens, cost)

        return content, tokens, cost

    async def think_with_tools(
        self,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
        tool_fns: dict[str, Any],
        system: str | None = None,
    ) -> tuple[str, int, float]:
        """LLM call with real tool dispatch loop — returns (content, tokens, cost_usd)."""
        from meshflow.optimization.tracker import active_tracker
        tracker = active_tracker.get()

        model = self.config.model
        if tracker is not None and tracker.should_degrade():
            model = tracker.fallback_model

        sys = system or self.config.system_prompt
        msgs = messages
        if tracker is not None:
            sys, msgs = tracker.compress_prompt(sys, msgs)

        content, tokens, cost = await self._provider.complete_with_tools(
            model=model,
            messages=cast(Any, msgs),
            system=sys,
            max_tokens=self.config.max_tokens,
            tool_schemas=tool_schemas,
            tool_fns=tool_fns,
        )
        self._call_count += 1
        self._total_tokens += tokens
        self._total_cost += cost
        self._state.token_count += tokens
        self._state.cost_usd += cost

        if tracker is not None:
            tracker.add_usage(tokens, cost)

        return content, tokens, cost

    # ── Protocol helpers ──────────────────────────────────────────────────────

    def make_message(self, content: str, receiver_id: str, trace_id: str = "") -> Message:
        return Message(
            sender_id=self.agent_id,
            receiver_id=receiver_id,
            content=content,
            role=self.config.role.value,
            trace_id=trace_id or str(uuid.uuid4()),
        )

    def make_intent(
        self,
        action: str,
        payload: dict[str, Any],
        evidence: list[Evidence] | None = None,
        risk_tier: RiskTier = RiskTier.READ_ONLY,
    ) -> Intent:
        return Intent(
            action=action,
            payload=payload,
            evidence=evidence or [],
            agent_id=self.agent_id,
            agent_did=self._state.did,
            risk_tier=risk_tier,
        )

    # ── Step — override in subclasses ─────────────────────────────────────────

    async def step(self, task: str, context: dict[str, Any]) -> dict[str, Any]:
        """Execute one agent step. Override in subclasses."""
        raise NotImplementedError


# ── Role-specific agents ──────────────────────────────────────────────────────


class PlannerAgent(BaseAgent):
    """Decomposes tasks into a structured plan for other agents."""

    SYSTEM = (
        "You are a Planner agent. Decompose the user's task into clear, ordered steps. "
        "Each step must specify: which role executes it, what inputs it needs, and what it must produce.\n"
        "Output valid JSON ONLY in this format:\n"
        '{"steps": [{"role": "...", "input": "...", "expected_output": "..."}], '
        '"confidence": 0.85}\n'
        'Include a "confidence" field (0.00–1.00) reflecting your certainty the plan is correct.'
    )

    async def step(self, task: str, context: dict[str, Any]) -> dict[str, Any]:
        messages = [{"role": "user", "content": f"Task: {task}\nContext: {context}"}]
        content, tokens, cost = await self.think(messages, self.SYSTEM)

        plan = _parse_json_retry(content)
        if plan is None:
            # Retry: ask the model to fix its JSON
            fix_messages = [
                {"role": "user", "content": f"Task: {task}\nContext: {context}"},
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": "Your response was not valid JSON. Return ONLY the JSON, nothing else.",
                },
            ]
            content2, t2, c2 = await self.think(fix_messages, self.SYSTEM)
            tokens += t2
            cost += c2
            plan = _parse_json_retry(content2)

        if plan is None:
            plan = {
                "steps": [{"role": "executor", "input": task, "expected_output": "result"}],
                "confidence": 0.5,
            }

        confidence = float(plan.get("confidence", 0.80))

        return {
            "plan": plan,
            "planner_id": self.agent_id,
            "tokens": tokens,
            "cost_usd": cost,
            "stated_confidence": confidence,
        }


class ResearcherAgent(BaseAgent):
    """Gathers and synthesises information for a given query."""

    SYSTEM = (
        "You are a Researcher agent. Given a research question, provide a "
        "thorough, factual answer with source attribution. Flag any uncertainty. "
        "Be explicit about what you do not know. Output as structured text." + _CONFIDENCE_SUFFIX
    )

    async def step(self, task: str, context: dict[str, Any]) -> dict[str, Any]:
        plan_step = context.get("current_step", {})
        query = plan_step.get("input", task)
        messages = [{"role": "user", "content": f"Research question: {query}"}]
        content, tokens, cost = await self.think(messages, self.SYSTEM)
        confidence, content = _extract_confidence(content)

        return {
            "research": content,
            "researcher_id": self.agent_id,
            "tokens": tokens,
            "cost_usd": cost,
            "stated_confidence": confidence,
            "evidence": [Evidence(content=content, source="llm_synthesis", trust_level="internal")],
        }


class ExecutorAgent(BaseAgent):
    """Executes concrete actions based on plan and research."""

    SYSTEM = (
        "You are an Executor agent. You receive a plan step and research context. "
        "Execute the step precisely. If you need to write code, write complete, "
        "runnable code. If you need to take an action, describe it precisely and "
        "declare it as an Intent before proceeding. Do not take irreversible actions "
        "without explicit instruction." + _CONFIDENCE_SUFFIX
    )

    async def step(self, task: str, context: dict[str, Any]) -> dict[str, Any]:
        research = context.get("research", "")
        plan_step = context.get("current_step", {})
        messages = [
            {
                "role": "user",
                "content": (
                    f"Plan step: {plan_step}\nResearch context: {research[:2000]}\nTask: {task}"
                ),
            }
        ]
        content, tokens, cost = await self.think(messages, self.SYSTEM)
        confidence, content = _extract_confidence(content)

        return {
            "execution_result": content,
            "executor_id": self.agent_id,
            "tokens": tokens,
            "cost_usd": cost,
            "stated_confidence": confidence,
        }


class CriticAgent(BaseAgent):
    """Independent critic — evaluates outputs before handoff.

    Dual-judge pattern: one Critic looks for failures, one for successes.
    A meta-arbitrator (the orchestrator) settles disagreements.
    """

    SYSTEM_FAILURE = (
        "You are a Critic agent looking for FAILURES. Given an output, "
        "identify all errors, omissions, hallucinations, and weak reasoning. "
        "Be adversarial. Score from 0–10 (10 = completely wrong). "
        'Output JSON ONLY: {"failure_score": N, "issues": [...]}'
    )

    SYSTEM_SUCCESS = (
        "You are a Critic agent looking for SUCCESSES. Given an output, "
        "identify what is correct, well-reasoned, and complete. "
        "Score from 0–10 (10 = perfect). "
        'Output JSON ONLY: {"success_score": N, "strengths": [...]}'
    )

    async def step(self, task: str, context: dict[str, Any]) -> dict[str, Any]:
        output_to_review = str(context.get("execution_result", context.get("research", "")))
        review_msg = [{"role": "user", "content": f"Output to review:\n{output_to_review}"}]

        fail_content, ftokens, fcost = await self.think(review_msg, self.SYSTEM_FAILURE)
        succ_content, stokens, scost = await self.think(review_msg, self.SYSTEM_SUCCESS)

        fail_result = _parse_json_retry(fail_content)
        if fail_result is None:
            # Retry
            fix = [
                {"role": "user", "content": f"Output to review:\n{output_to_review}"},
                {"role": "assistant", "content": fail_content},
                {
                    "role": "user",
                    "content": 'Return ONLY valid JSON: {"failure_score": N, "issues": [...]}',
                },
            ]
            raw2, ft2, fc2 = await self.think(fix, self.SYSTEM_FAILURE)
            ftokens += ft2
            fcost += fc2
            fail_result = _parse_json_retry(raw2) or {"failure_score": 5, "issues": []}

        succ_result = _parse_json_retry(succ_content)
        if succ_result is None:
            fix = [
                {"role": "user", "content": f"Output to review:\n{output_to_review}"},
                {"role": "assistant", "content": succ_content},
                {
                    "role": "user",
                    "content": 'Return ONLY valid JSON: {"success_score": N, "strengths": [...]}',
                },
            ]
            raw2, st2, sc2 = await self.think(fix, self.SYSTEM_SUCCESS)
            stokens += st2
            scost += sc2
            succ_result = _parse_json_retry(raw2) or {"success_score": 5, "strengths": []}

        failure_score = float(fail_result.get("failure_score", 5))
        success_score = float(succ_result.get("success_score", 5))
        composite = (success_score - failure_score + 10) / 20.0
        passed = composite >= 0.5

        return {
            "critic_passed": passed,
            "composite_score": composite,
            "failure_score": failure_score,
            "success_score": success_score,
            "issues": fail_result.get("issues", []),
            "strengths": succ_result.get("strengths", []),
            "critic_id": self.agent_id,
            "tokens": ftokens + stokens,
            "cost_usd": fcost + scost,
            "stated_confidence": composite,
        }
