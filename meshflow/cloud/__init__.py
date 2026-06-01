"""meshflow.cloud — MeshFlow Cloud integration.

Provides the telemetry reporter that ships run data to the MeshFlow Cloud
dashboard when ``MESHFLOW_CLOUD_KEY`` is set in the environment.

The entire module is zero-cost when no key is configured — no network
calls, no threads, no overhead.

Quickstart::

    # 1. Get your key from https://meshflow.dev/dashboard/api-keys
    # 2. Add to your .env:
    MESHFLOW_CLOUD_KEY=mfc_your_key_here

    # That's it. Every Mesh.run() / Team.run() / Crew.kickoff() call now
    # ships anonymised run telemetry to your Cloud dashboard automatically.

Manual usage::

    from meshflow.cloud import report_run
    report_run(result, workflow_name="my-pipeline", agent_count=3)
"""

from __future__ import annotations

from meshflow.cloud.reporter import is_enabled, report_run
from meshflow.cloud.model_router_analytics import RouterAnalytics, RouterSummary, TierStats

__all__ = ["report_run", "is_enabled", "RouterAnalytics", "RouterSummary", "TierStats"]
