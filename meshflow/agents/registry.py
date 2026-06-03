"""Model registry — user-defined catalog of model specs.

Replaces ad-hoc pattern matching with an explicit, queryable registry.
Users register once; the rest of the system (ModelTierRouter, estimate_cost,
AdaptiveModelTierRouter) queries the registry before falling back to
``model_is_local()`` pattern detection.

Usage::

    from meshflow.agents.registry import DEFAULT_REGISTRY, ModelSpec

    # Register a custom Ollama fine-tune once at startup
    DEFAULT_REGISTRY.register(ModelSpec(
        model_id="corp-llm",
        is_local=True,
        quality_estimate=0.75,
        tags=["finance", "summarisation"],
    ))

    # Register a LiteLLM proxy endpoint
    DEFAULT_REGISTRY.register(ModelSpec(
        model_id="http://localhost:4000/v1",
        is_local=True,
        latency_ms_estimate=120.0,
    ))
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelSpec:
    """Metadata for a single model as declared by the user or the default catalog.

    Attributes
    ----------
    model_id:             Exact model identifier or a unique substring.
    is_local:             True → zero cost, no cloud warning.
    cost_input_per_1k:    USD per 1 000 input tokens (0.0 for local).
    cost_output_per_1k:   USD per 1 000 output tokens (0.0 for local).
    context_window:       Maximum context length in tokens.
    quality_estimate:     Baseline quality score 0–1 (used for cold-start routing).
    latency_ms_estimate:  Typical p50 latency in milliseconds.
    tags:                 Free-form labels, e.g. ``["code", "reasoning"]``.
    """

    model_id: str
    is_local: bool
    cost_input_per_1k: float = 0.0
    cost_output_per_1k: float = 0.0
    context_window: int = 4096
    quality_estimate: float = 0.7
    latency_ms_estimate: float = 500.0
    tags: list[str] = field(default_factory=list)

    def cost_usd(self, input_tokens: int, output_tokens: int) -> float:
        if self.is_local:
            return 0.0
        return (input_tokens / 1000) * self.cost_input_per_1k + (output_tokens / 1000) * self.cost_output_per_1k


class ModelRegistry:
    """Queryable catalog of :class:`ModelSpec` entries.

    Lookup order:
    1. Exact match on ``model_id``.
    2. Substring match — the spec whose ``model_id`` is a substring of the
       query (longest match wins).
    3. Returns ``None`` if unregistered.
    """

    def __init__(self) -> None:
        self._specs: dict[str, ModelSpec] = {}

    # ── Mutation ──────────────────────────────────────────────────────────────

    def register(self, spec: ModelSpec | dict[str, Any]) -> None:
        """Add or overwrite a model spec.

        Accepts either a :class:`ModelSpec` instance or a plain dict with the
        same keys — convenient for config-file driven setup.
        """
        if isinstance(spec, dict):
            spec = ModelSpec(**spec)
        self._specs[spec.model_id] = spec

    def remove(self, model_id: str) -> None:
        """Remove a spec by its exact model_id (no-op if not found)."""
        self._specs.pop(model_id, None)

    # ── Lookup ────────────────────────────────────────────────────────────────

    def get(self, model_id: str) -> ModelSpec | None:
        """Return the best-matching spec, or ``None`` if unregistered."""
        # Exact match
        if model_id in self._specs:
            return self._specs[model_id]
        # Substring match — find all specs whose model_id appears in the query,
        # prefer the longest (most specific) key.
        candidates = [
            spec for key, spec in self._specs.items() if key in model_id or model_id in key
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda s: len(s.model_id))

    def is_local(self, model_id: str) -> bool:
        """Return locality — registry first, pattern detection as fallback."""
        spec = self.get(model_id)
        if spec is not None:
            return spec.is_local
        from meshflow.agents.base import model_is_local
        return model_is_local(model_id)

    def cost_usd(self, model_id: str, input_tokens: int, output_tokens: int) -> float:
        """Return estimated cost — registry first, pricing table as fallback."""
        spec = self.get(model_id)
        if spec is not None:
            return spec.cost_usd(input_tokens, output_tokens)
        from meshflow.agents.base import _cost_usd
        return _cost_usd(model_id, input_tokens, output_tokens)

    def __contains__(self, model_id: str) -> bool:
        return self.get(model_id) is not None

    def __len__(self) -> int:
        return len(self._specs)

    def all(self) -> list[ModelSpec]:
        return list(self._specs.values())


# ── Default registry pre-populated with well-known models ────────────────────

DEFAULT_REGISTRY = ModelRegistry()

# Claude family
DEFAULT_REGISTRY.register(ModelSpec("claude-opus-4",        is_local=False, cost_input_per_1k=0.015,  cost_output_per_1k=0.075,  context_window=200_000, quality_estimate=0.97, latency_ms_estimate=1200, tags=["reasoning", "analysis"]))
DEFAULT_REGISTRY.register(ModelSpec("claude-sonnet-4",      is_local=False, cost_input_per_1k=0.003,  cost_output_per_1k=0.015,  context_window=200_000, quality_estimate=0.92, latency_ms_estimate=800,  tags=["general"]))
DEFAULT_REGISTRY.register(ModelSpec("claude-haiku-4",       is_local=False, cost_input_per_1k=0.00025,cost_output_per_1k=0.00125, context_window=200_000, quality_estimate=0.83, latency_ms_estimate=350,  tags=["fast", "cheap"]))

# OpenAI
DEFAULT_REGISTRY.register(ModelSpec("gpt-4o",               is_local=False, cost_input_per_1k=0.005,  cost_output_per_1k=0.015,  context_window=128_000, quality_estimate=0.93, latency_ms_estimate=900,  tags=["general"]))
DEFAULT_REGISTRY.register(ModelSpec("gpt-4o-mini",          is_local=False, cost_input_per_1k=0.00015,cost_output_per_1k=0.0006,  context_window=128_000, quality_estimate=0.82, latency_ms_estimate=400,  tags=["fast", "cheap"]))
DEFAULT_REGISTRY.register(ModelSpec("gpt-4-turbo",          is_local=False, cost_input_per_1k=0.01,   cost_output_per_1k=0.03,   context_window=128_000, quality_estimate=0.93, latency_ms_estimate=1000, tags=["reasoning"]))

# Gemini
DEFAULT_REGISTRY.register(ModelSpec("gemini-2.0-flash",     is_local=False, cost_input_per_1k=0.0001, cost_output_per_1k=0.0004,  context_window=1_000_000, quality_estimate=0.85, latency_ms_estimate=400, tags=["fast", "long-context"]))
DEFAULT_REGISTRY.register(ModelSpec("gemini-1.5-pro",       is_local=False, cost_input_per_1k=0.0035, cost_output_per_1k=0.0105,  context_window=2_000_000, quality_estimate=0.90, latency_ms_estimate=900, tags=["long-context"]))

# AWS Bedrock
DEFAULT_REGISTRY.register(ModelSpec("meta.llama3-70b",      is_local=False, cost_input_per_1k=0.00265,cost_output_per_1k=0.0035,  context_window=8_192,   quality_estimate=0.88, latency_ms_estimate=700,  tags=["open-weight", "cloud"]))
DEFAULT_REGISTRY.register(ModelSpec("meta.llama3-8b",       is_local=False, cost_input_per_1k=0.0003, cost_output_per_1k=0.0006,  context_window=8_192,   quality_estimate=0.76, latency_ms_estimate=300,  tags=["fast", "cloud"]))

# Local / Ollama — zero cost
DEFAULT_REGISTRY.register(ModelSpec("llama3.2",             is_local=True,  quality_estimate=0.78, latency_ms_estimate=250,  tags=["local", "fast"]))
DEFAULT_REGISTRY.register(ModelSpec("llama3.1",             is_local=True,  quality_estimate=0.80, latency_ms_estimate=400,  tags=["local"]))
DEFAULT_REGISTRY.register(ModelSpec("mistral",              is_local=True,  quality_estimate=0.80, latency_ms_estimate=300,  tags=["local"]))
DEFAULT_REGISTRY.register(ModelSpec("mistral:7b",           is_local=True,  quality_estimate=0.80, latency_ms_estimate=300,  tags=["local"]))
DEFAULT_REGISTRY.register(ModelSpec("codellama",            is_local=True,  quality_estimate=0.82, latency_ms_estimate=400,  tags=["local", "code"]))
DEFAULT_REGISTRY.register(ModelSpec("gemma2",               is_local=True,  quality_estimate=0.77, latency_ms_estimate=280,  tags=["local"]))
DEFAULT_REGISTRY.register(ModelSpec("phi3",                 is_local=True,  quality_estimate=0.74, latency_ms_estimate=150,  tags=["local", "fast"]))
DEFAULT_REGISTRY.register(ModelSpec("qwen2.5",              is_local=True,  quality_estimate=0.79, latency_ms_estimate=300,  tags=["local"]))
DEFAULT_REGISTRY.register(ModelSpec("deepseek-coder",       is_local=True,  quality_estimate=0.84, latency_ms_estimate=400,  tags=["local", "code"]))
