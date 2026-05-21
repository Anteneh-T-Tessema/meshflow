"""MeshFlow — the control plane for multi-agent systems.

  Use LangGraph to build graphs.
  Use CrewAI to build crews.
  Use AutoGen to build agent conversations.
  Use MeshFlow to govern, orchestrate, audit, and standardize them all.
"""
from meshflow.core.mesh import Mesh, MeshEvent
from meshflow.core.node import MeshNode, NodeInput, NodeKind, NodeOutput
from meshflow.core.workflow import HumanDecision, WorkflowDefinition, WorkflowResult
from meshflow.core.ledger import LedgerBackend, PostgresLedgerBackend, ReplayLedger, SQLiteLedgerBackend
from meshflow.core.runtime import StepRuntime, RuntimeOutcome
from meshflow.core.schemas import (
    AgentRole, Evidence, HumanInLoopConfig, Intent, Message,
    Policy, RiskTier, RunResult, RunStatus,
)
from meshflow.agents.adapters import from_autogen, from_callable, from_crewai, from_langgraph

__version__ = "0.7.0"
__all__ = [
    # Orchestration
    "Mesh",
    "MeshEvent",
    # Universal node
    "MeshNode",
    "NodeInput",
    "NodeOutput",
    "NodeKind",
    # Workflow
    "WorkflowDefinition",
    "WorkflowResult",
    "HumanDecision",
    # Kernel
    "StepRuntime",
    "RuntimeOutcome",
    # Ledger
    "ReplayLedger",
    "LedgerBackend",
    "SQLiteLedgerBackend",
    "PostgresLedgerBackend",
    # Policy + schemas
    "Policy",
    "HumanInLoopConfig",
    "AgentRole",
    "RiskTier",
    "RunStatus",
    "RunResult",
    "Evidence",
    "Intent",
    "Message",
    # Framework adapters (legacy path — prefer MeshNode.from_*)
    "from_crewai",
    "from_autogen",
    "from_langgraph",
    "from_callable",
]
