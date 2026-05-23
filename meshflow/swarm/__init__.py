"""meshflow.swarm — SwarmTRM neural consensus engine integrated into MeshFlow.

Quick start
-----------
    from meshflow.swarm import SwarmNode, SwarmConfig, swarm_verifier, available_domains

    # List all 53+ verifier domains
    print(available_domains())

    # Build a governed node backed by the swarm (requires torch)
    node = SwarmNode.create("aml_check", verifier_type="aml",
                            config=SwarmConfig(initial_agents=5, max_depth=10))

    # Deterministic-only verifier hook (no torch needed)
    hook = swarm_verifier("hipaa")
    result = hook(phi_record, {})

    # Expose a domain as an MCP tool
    from meshflow.swarm import register_swarm_domain
    from meshflow.mcp.server import MCPServer
    srv = MCPServer()
    register_swarm_domain(srv, "aml")
"""

from meshflow.swarm.verifiers import (
    VerificationResult,
    DeterministicVerifier,
    ERPAuditVerifier,
    BillableCaptureVerifier,
    CodeModernizationVerifier,
    PytestVerifier,
    DASCVerifier,
)
from meshflow.swarm.node import SwarmNode, swarm_verifier, register_swarm_domain

# Lazy re-exports so torch is never imported at package load time
def _lazy(attr: str):
    def _get():
        from meshflow.swarm import engine as _eng
        return getattr(_eng, attr)
    return property(_get)


def available_domains() -> list:
    """Return all 53+ verifier domain keys across built-in, industry, and reasoning registries."""
    from meshflow.swarm.engine import available_domains as _ad
    return _ad()


# SwarmConfig and SwarmTRM are importable but require torch at instantiation time
def __getattr__(name: str):
    _swarm_exports = {"SwarmConfig", "SwarmTRM", "SwarmInferenceResult",
                      "SwarmTraceStep", "SwarmAccounting", "AgentSnapshot",
                      "RecursiveUnit"}
    if name in _swarm_exports:
        from meshflow.swarm import engine as _eng
        obj = getattr(_eng, name, None)
        if obj is not None:
            return obj
        # RecursiveUnit lives in recursive_unit module
        from meshflow.swarm import recursive_unit as _ru
        return getattr(_ru, name)
    raise AttributeError(f"module 'meshflow.swarm' has no attribute '{name}'")


__all__ = [
    # Verifier base types (no torch)
    "VerificationResult",
    "DeterministicVerifier",
    "ERPAuditVerifier",
    "BillableCaptureVerifier",
    "CodeModernizationVerifier",
    "PytestVerifier",
    "DASCVerifier",
    # Node factory and hooks (no torch at import; torch needed to run)
    "SwarmNode",
    "swarm_verifier",
    "register_swarm_domain",
    # Domain listing (no torch)
    "available_domains",
    # Engine types (torch needed at instantiation)
    "SwarmConfig",
    "SwarmTRM",
    "SwarmInferenceResult",
    "SwarmTraceStep",
    "SwarmAccounting",
    "AgentSnapshot",
    "RecursiveUnit",
]
