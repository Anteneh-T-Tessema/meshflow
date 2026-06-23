from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Optional

# LangChain callbacks fallback imports
try:
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError:
    class BaseCallbackHandler:  # type: ignore
        pass


class GuardrailViolationError(RuntimeError):
    """Exception raised when a guardrail rule is violated in pre-execution or runtime."""
    pass


class PromptSafetyCache:
    """Thread-safe LRU cache mapping prompts/inputs to their safety scan results."""

    def __init__(self, maxsize: int = 1000) -> None:
        self.maxsize = maxsize
        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()

        # Cache statistics for validation
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key in self._cache:
                self.hits += 1
                self._cache.move_to_end(key)
                return self._cache[key]
            self.misses += 1
            return None

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = value
            if len(self._cache) > self.maxsize:
                self._cache.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self.hits = 0
            self.misses = 0


# Global safety cache instance
_SAFETY_CACHE = PromptSafetyCache()


class LangGraphGuardCallback(BaseCallbackHandler):
    """Langchain/LangGraph callback handler for active in-flight pre-execution guardrails."""

    def __init__(self, guardian: Any, ledger: Any = None) -> None:
        self.guardian = guardian
        self.ledger = ledger

    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        **kwargs: Any,
    ) -> Any:
        # Check node inputs for prompt injection/unsafe text
        if inputs:
            for k, v in inputs.items():
                text_to_scan = ""
                if isinstance(v, str):
                    text_to_scan = v
                elif isinstance(v, list):
                    text_to_scan = " ".join(
                        msg.content if hasattr(msg, "content") else str(msg)
                        for msg in v
                    )
                elif isinstance(v, dict):
                    text_to_scan = " ".join(str(val) for val in v.values())
                
                if text_to_scan:
                    from meshflow.core.schemas import Message
                    msg = Message(
                        sender_id="langgraph_node",
                        receiver_id="target",
                        content=text_to_scan,
                    )
                    allowed, reason = self.guardian.evaluate_message(msg)
                    if not allowed:
                        raise GuardrailViolationError(
                            f"LangGraph node input blocked by guardrail: {reason}"
                        )

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        **kwargs: Any,
    ) -> Any:
        tool_name = serialized.get("name", "unknown")
        from meshflow.core.schemas import Message
        msg = Message(
            sender_id="langgraph_tool",
            receiver_id=tool_name,
            content=input_str,
        )
        allowed, reason = self.guardian.evaluate_message(msg)
        if not allowed:
            raise GuardrailViolationError(
                f"LangGraph tool call '{tool_name}' blocked by guardrail: {reason}"
            )


class CrewAIGuardCallback(BaseCallbackHandler):
    """LangChain callback handler to scan LLM prompts inside CrewAI agents."""

    def __init__(self, guardian: Any, ledger: Any = None) -> None:
        self.guardian = guardian
        self.ledger = ledger

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        **kwargs: Any,
    ) -> Any:
        for prompt in prompts:
            from meshflow.core.schemas import Message
            msg = Message(
                sender_id="crewai_llm",
                receiver_id="llm",
                content=prompt,
            )
            allowed, reason = self.guardian.evaluate_message(msg)
            if not allowed:
                raise GuardrailViolationError(
                    f"CrewAI LLM prompt blocked by guardrail: {reason}"
                )


def _register_autogen_guard(agent: Any, guardian: Any, ledger: Any) -> None:
    """Helper to register the pre-reply conversation firewall on AutoGen agents."""
    if not hasattr(agent, "register_reply"):
        return

    # Check if hook already registered to prevent duplicates
    if hasattr(agent, "_guardrail_hook_registered"):
        return

    def guard_reply_hook(recipient: Any, messages: list[dict[str, Any]], sender: Any, config: Any) -> tuple[bool, Any]:
        if messages:
            last_message = messages[-1]
            content = last_message.get("content", "")
            if content:
                from meshflow.core.schemas import Message
                msg = Message(
                    sender_id=sender.name if hasattr(sender, "name") else "sender",
                    receiver_id=recipient.name if hasattr(recipient, "name") else "recipient",
                    content=content,
                )
                allowed, reason = guardian.evaluate_message(msg)
                if not allowed:
                    raise GuardrailViolationError(
                        f"AutoGen message from '{msg.sender_id}' to '{msg.receiver_id}' "
                        f"blocked by guardrail: {reason}"
                    )
        return False, None  # Pass-through validation (allow other replies to proceed)

    agent.register_reply(
        trigger=None,
        reply_func=guard_reply_hook,
        position=0,
    )
    agent._guardrail_hook_registered = True
