"""7M-parameter Recursive Unit for SwarmTRM.

Designed for high-speed, cache-resident reasoning. Requires torch.
Import is deferred so the meshflow package can be imported without torch.
"""
from __future__ import annotations



def _require_torch():
    try:
        import torch
        return torch
    except ImportError as exc:
        raise ImportError(
            "meshflow[swarm] requires PyTorch. Install it with: pip install torch"
        ) from exc


class RecursiveUnit:
    """7M-parameter shared neural core used by SwarmTRM agents."""

    def __init__(self, d_model: int = 768, n_heads: int = 8) -> None:
        torch = _require_torch()

        self.d_model = d_model
        self._module = _RecursiveModule(d_model, n_heads)
        self._module.eval()

    # Delegate attribute access to the underlying nn.Module
    def __getattr__(self, name: str):
        if name.startswith("_") or name == "d_model":
            raise AttributeError(name)
        return getattr(self._module, name)

    def eval(self):
        self._module.eval()
        return self

    @property
    def reasoning_layer(self):
        return self._module.reasoning_layer

    @property
    def parameter_count(self) -> int:
        return sum(p.numel() for p in self._module.parameters())


class _RecursiveModule:
    """Inner nn.Module wrapping — instantiated only when torch is available."""

    def __init__(self, d_model: int, n_heads: int) -> None:
        torch = _require_torch()
        import torch.nn as nn

        self.d_model = d_model
        self.encoder = nn.Linear(d_model, d_model // 2)
        self.decoder = nn.Linear(d_model // 2, d_model)
        self.reasoning_layer = nn.TransformerEncoderLayer(
            d_model=d_model // 2,
            nhead=n_heads,
            dim_feedforward=d_model * 8,
            batch_first=True,
        )
        self.plasticity_gate = nn.Sequential(
            nn.Linear(d_model // 2, 1),
            nn.Sigmoid(),
        )

    def parameters(self):
        torch = _require_torch()
        params = []
        for attr in ["encoder", "decoder", "reasoning_layer", "plasticity_gate"]:
            m = getattr(self, attr)
            if hasattr(m, "parameters"):
                params.extend(m.parameters())
        return iter(params)

    def eval(self):
        for attr in ["encoder", "decoder", "reasoning_layer", "plasticity_gate"]:
            m = getattr(self, attr)
            if hasattr(m, "eval"):
                m.eval()
        return self
