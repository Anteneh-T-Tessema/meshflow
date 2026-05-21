"""MeshFlow — gold-standard multi-agent orchestration."""
from meshflow.core.mesh import Mesh
from meshflow.core.schemas import (
    AgentRole, Evidence, Intent, Message, Policy,
    RiskTier, RunResult, RunStatus,
)
from meshflow.agents.adapters import from_autogen, from_callable, from_crewai, from_langgraph

__version__ = "0.6.0"
__all__ = [
    "Mesh",
    "Policy",
    "AgentRole",
    "RiskTier",
    "RunStatus",
    "RunResult",
    "Evidence",
    "Intent",
    "Message",
    "from_crewai",
    "from_autogen",
    "from_langgraph",
    "from_callable",
]
