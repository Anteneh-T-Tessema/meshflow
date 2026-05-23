"""SwarmNode — SwarmTRM neural consensus engine as a governed MeshFlow node.

Usage
-----
    from meshflow.swarm import SwarmNode, SwarmConfig

    node = SwarmNode.create(
        node_id="hipaa_checker",
        verifier_type="hipaa",
        config=SwarmConfig(initial_agents=5, max_depth=10),
    )
    result = await node.run(NodeInput(task=task_dict))

    # Use as a DascGate verifier hook
    from meshflow.swarm import swarm_verifier
    hook = swarm_verifier("aml")
    verdict = hook(output_dict, {})
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Dict, Optional

from meshflow.core.node import MeshNode, NodeInput, NodeKind, NodeOutput
from meshflow.core.schemas import RiskTier


def _load_engine():
    """Deferred import of SwarmTRM to keep torch optional."""
    from meshflow.swarm.engine import SwarmTRM, SwarmConfig, SwarmInferenceResult
    return SwarmTRM, SwarmConfig, SwarmInferenceResult


def _load_verifier_for_domain(domain: str):
    """Return a DeterministicVerifier instance for *domain*, checking all registries."""
    # Built-in FLL verifiers
    _BUILTIN = {
        "erp": "ERPAuditVerifier",
        "billable": "BillableCaptureVerifier",
        "modernize": "CodeModernizationVerifier",
        "dasc": "DASCVerifier",
        "qa": "PytestVerifier",
    }
    if domain in _BUILTIN:
        import importlib
        mod = importlib.import_module("meshflow.swarm.verifiers")
        return getattr(mod, _BUILTIN[domain])()

    # Industry verifiers
    try:
        from meshflow.swarm.industries.registry import get_verifier as _iv
        return _iv(domain)
    except KeyError:
        pass

    # Reasoning verifiers
    try:
        from meshflow.swarm.reasoning.registry import get_verifier as _rv
        return _rv(domain)
    except KeyError:
        pass

    raise KeyError(
        f"Unknown verifier domain '{domain}'. "
        "Call meshflow.swarm.available_domains() to see all options."
    )


# ── swarm_verifier — DascGate-compatible hook ─────────────────────────────────

def swarm_verifier(domain: str) -> Callable[[Any, Dict], Any]:
    """Return a deterministic verifier hook for *domain*.

    The returned callable matches the DascGate verifier-hook signature:
        hook(output: Any, context: dict) -> VerificationResult

    Example::

        from meshflow.swarm import swarm_verifier

        hook = swarm_verifier("hipaa")
        result = hook(phi_record, {"patient_id": "P-001"})
        if not result.is_valid:
            raise ValueError(result.violations)
    """
    verifier = _load_verifier_for_domain(domain)

    def _hook(output: Any, context: Dict) -> Any:
        return verifier.verify(output, context or {})

    _hook.__name__ = f"swarm_verifier_{domain}"
    return _hook


# ── SwarmNode — factory class returning governed MeshNodes ────────────────────

class SwarmNode:
    """Factory that creates MeshNodes backed by the SwarmTRM consensus engine.

    Returned nodes are ordinary MeshNodes and pass through the full StepRuntime
    governance lifecycle (identity, policy, HITL, ledger, OTEL).
    """

    @staticmethod
    def create(
        node_id: str,
        verifier_type: str = "erp",
        config: Any = None,
        context: Optional[Dict[str, Any]] = None,
        risk: RiskTier = RiskTier.INTERNAL,
        record_trace: bool = True,
    ) -> MeshNode:
        """Create a governed MeshNode that runs SwarmTRM inference.

        Parameters
        ----------
        node_id:
            Unique identifier for this node within the workflow graph.
        verifier_type:
            Domain key passed to ``SwarmTRM.run()`` — e.g. ``"hipaa"``, ``"aml"``,
            ``"linear_system"``. Call ``available_domains()`` for the full list.
        config:
            Optional ``SwarmConfig`` instance. Defaults to engine defaults.
        context:
            Static context dict merged with per-call context at runtime.
        risk:
            MeshFlow ``RiskTier`` assigned to the node (defaults to INTERNAL).
        record_trace:
            If True, per-step trace is included in the ``NodeOutput.metadata``.
        """
        static_context: Dict[str, Any] = dict(context or {})

        async def _runner(inp: NodeInput) -> NodeOutput:
            SwarmTRM, SwarmConfig, SwarmInferenceResult = _load_engine()

            engine = SwarmTRM()
            merged_ctx = {**static_context, **inp.context}
            effective_config = config

            t0 = time.perf_counter()
            result: SwarmInferenceResult = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: engine.run(
                    task=inp.task,
                    verifier_type=verifier_type,
                    context=merged_ctx,
                    config=effective_config,
                ),
            )
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

            # Serialise result for NodeOutput
            answer_str = (
                json.dumps(result.answer, default=str)
                if isinstance(result.answer, dict)
                else str(result.answer)
            )

            metadata: Dict[str, Any] = {
                "swarm_verified": result.verified,
                "swarm_violations": result.violations,
                "swarm_steps": result.steps,
                "swarm_recommendation": result.recommendation,
                "swarm_low_confidence": result.low_confidence,
                "swarm_accounting": {
                    "prompt_tokens": result.accounting.prompt_tokens,
                    "completion_tokens": result.accounting.completion_tokens,
                    "wall_ms": result.accounting.wall_ms,
                    "agent_steps": result.accounting.agent_steps,
                },
                "elapsed_ms": elapsed_ms,
                "verifier_type": verifier_type,
            }
            if record_trace:
                metadata["swarm_trace"] = [
                    {
                        "step": s.step,
                        "n_agents": s.n_agents,
                        "consensus_conf": s.consensus_conf,
                        "verified": s.verified,
                        "topology": s.topology,
                    }
                    for s in result.trace
                ]
            if result.remediation_steps:
                metadata["remediation_steps"] = result.remediation_steps

            return NodeOutput(
                content=answer_str,
                structured={
                    "answer": result.answer,
                    "verified": result.verified,
                    "violations": result.violations,
                    "recommendation": result.recommendation,
                },
                tokens_used=result.accounting.prompt_tokens + result.accounting.completion_tokens,
                confidence=result.confidence,
                metadata=metadata,
            )

        return MeshNode(
            id=node_id,
            kind=NodeKind.PYTHON,
            risk_profile=risk,
            capabilities=["swarm_inference", "deterministic_verification", f"domain_{verifier_type}"],
            metadata={"verifier_type": verifier_type, "swarm": True},
            _runner=_runner,
        )


# ── MCPServer integration ─────────────────────────────────────────────────────

def register_swarm_domain(
    server: Any,
    domain: str,
    description: str = "",
    config: Any = None,
    context: Optional[Dict[str, Any]] = None,
    tool_name: str = "",
) -> None:
    """Expose a SwarmTRM domain verifier as an MCP tool on *server*.

    This keeps the MCP server module free of swarm imports — call this after
    creating an MCPServer instance to add domain tools on-demand.

    Example::

        srv = MCPServer(name="Compliance AI", policy="hipaa")
        register_swarm_domain(srv, "hipaa")
        register_swarm_domain(srv, "aml")
        register_swarm_domain(srv, "drug_interaction")
        srv.run_stdio()
    """
    tname = tool_name or f"swarm_{domain}"
    desc = description or (
        f"Run SwarmTRM neural consensus verification for the '{domain}' domain. "
        "Passes the task through a multi-agent swarm with deterministic verifier "
        "gating. Returns verified answer, confidence score, violations, and remediation steps."
    )

    async def _fn(arguments: Dict[str, Any]) -> str:
        import json as _json
        node = SwarmNode.create(
            node_id=tname,
            verifier_type=domain,
            config=config,
            context=context,
        )
        task = arguments.get("task") or arguments
        inp = NodeInput(
            task=task if isinstance(task, (str, dict)) else str(task),
            context={k: v for k, v in arguments.items() if k != "task"},
        )
        out = await node.run(inp)
        return _json.dumps({
            "answer": out.structured.get("answer"),
            "verified": out.structured.get("verified"),
            "confidence": out.confidence,
            "violations": out.structured.get("violations", []),
            "recommendation": out.structured.get("recommendation"),
            "domain": domain,
        }, default=str)

    from meshflow.mcp.server import MCPToolEntry
    server._tools[tname] = MCPToolEntry(
        name=tname,
        description=desc,
        input_schema={
            "type": "object",
            "properties": {
                "task": {
                    "type": ["string", "object"],
                    "description": "Task payload — string question or domain-specific dict.",
                },
            },
            "required": ["task"],
        },
        fn=_fn,
    )
