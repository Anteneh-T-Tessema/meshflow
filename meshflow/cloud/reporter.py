"""MeshFlow Cloud telemetry reporter.

Fires a single HTTP POST to the MeshFlow Cloud ingest endpoint after each
workflow run completes. Completely optional — activates only when the
``MESHFLOW_CLOUD_KEY`` environment variable is set. Never blocks the
caller. All errors are silently swallowed.

Configuration::

    # Minimum — just set the key, everything else uses defaults
    MESHFLOW_CLOUD_KEY=mfc_your_key_here

    # Optional — point at a self-hosted or staging endpoint
    MESHFLOW_CLOUD_ENDPOINT=https://your-instance.example.com/api/ingest/run

Usage (automatic)::

    # reporter fires automatically from Mesh.run() / Team.run() / Crew.kickoff()
    # when MESHFLOW_CLOUD_KEY is present in the environment.

Usage (manual)::

    from meshflow.cloud.reporter import report_run
    report_run(
        result,
        workflow_name="hipaa-intake-pipeline",
        agent_count=4,
        policy_mode="regulated",
        compliance="hipaa",
    )
"""

from __future__ import annotations

import json
import os
import threading
import urllib.request
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from meshflow.core.schemas import RunResult

_DEFAULT_ENDPOINT = "https://meshflow.dev/api/ingest/run"


def _zt_status_payload() -> dict[str, Any]:
    """Snapshot the current Zero Trust posture for telemetry."""
    try:
        import os
        from meshflow.core.mesh import _zt_from_env
        zt = _zt_from_env()
        p = zt._policy
        enabled = p.controls_enabled()
        disabled = p.controls_disabled()
        total = len(enabled) + len(disabled)
        return {
            "tier":             p.tier.value,
            "regulation":       p.regulation or None,
            "score_pct":        int(100 * len(enabled) / max(total, 1)),
            "controls_enabled": len(enabled),
            "controls_gap":     len(disabled),
            "env_tier":         os.environ.get("MESHFLOW_ZT_TIER", ""),
            "env_regulation":   os.environ.get("MESHFLOW_ZT_REGULATION", ""),
        }
    except Exception:
        return {}


def report_run(
    result: RunResult,
    *,
    workflow_name: str = "unknown",
    agent_count: int = 0,
    policy_mode: str = "standard",
    compliance: str | None = None,
) -> None:
    """Send run telemetry to MeshFlow Cloud in a daemon background thread.

    Returns immediately — telemetry must never affect the caller's latency.
    All network errors and exceptions are silently suppressed.

    Parameters
    ----------
    result:
        The ``RunResult`` returned by ``Mesh.run()`` / ``Team.run()`` etc.
    workflow_name:
        Human-readable identifier for the workflow. Shown in the Cloud
        cost analytics dashboard grouped by workflow.
    agent_count:
        Number of agent nodes that participated in the run.
    policy_mode:
        Governance policy mode string (``"dev"`` / ``"standard"`` /
        ``"regulated"`` / ``"legal-critical"``).
    compliance:
        Active compliance framework, if any (e.g. ``"hipaa"``). ``None``
        when no framework is active.
    """
    key = os.environ.get("MESHFLOW_CLOUD_KEY", "").strip()
    if not key:
        return

    endpoint = os.environ.get("MESHFLOW_CLOUD_ENDPOINT", _DEFAULT_ENDPOINT)

    # Resolve status to a plain string regardless of whether it is an enum
    status_str: str
    if hasattr(result.status, "value"):
        status_str = result.status.value
    else:
        status_str = str(result.status)

    payload: dict[str, Any] = {
        "run_id":                   result.run_id,
        "workflow_name":            workflow_name,
        "agent_count":              agent_count,
        "total_cost_usd":           round(result.total_cost_usd, 6),
        "total_tokens":             result.total_tokens,
        "cache_hit_rate":           0.0,   # TODO: wire when StepRuntime tracks cache reads
        "policy":                   policy_mode,
        "compliance":               compliance,
        "status":                   status_str,
        "duration_ms":              int(result.duration_s * 1000),
        "violations":               result.collusion_alerts,
        "human_approvals_required": result.human_approvals_required,
        "ledger_entries":           result.ledger_entries,
        "total_carbon_g":           round(result.total_carbon_g, 4),
        "zero_trust":               _zt_status_payload(),
    }

    def _fire() -> None:
        try:
            body = json.dumps(payload).encode()
            req = urllib.request.Request(
                endpoint,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "x-meshflow-key": key,
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5):
                pass
        except Exception:
            pass  # never surface telemetry errors to the caller

    thread = threading.Thread(
        target=_fire,
        daemon=True,
        name="meshflow-cloud-reporter",
    )
    thread.start()


def is_enabled() -> bool:
    """Return ``True`` if a Cloud API key is configured in the environment."""
    return bool(os.environ.get("MESHFLOW_CLOUD_KEY", "").strip())
