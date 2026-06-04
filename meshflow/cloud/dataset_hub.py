"""DatasetHub — push and pull eval datasets from meshflow.dev.

Usage::

    from meshflow.cloud import DatasetHub

    # Push rows to a named dataset (creates it if it doesn't exist)
    DatasetHub.push("hipaa-qa-v1", rows=[
        {"input": "What is PHI?", "expected_output": "Protected Health Information…"},
        {"input": "Is SSN PHI?",  "expected_output": "Yes, Social Security Numbers are PHI."},
    ])

    # Pull all rows back for local eval
    rows = DatasetHub.pull("hipaa-qa-v1")
    for row in rows:
        result = agent.run(row["input"])
        assert row["expected_output"] in result.output

    # List all dataset names for the org
    names = DatasetHub.list()

Environment variables
---------------------

MESHFLOW_API_KEY      — required (same key used by MeshFlowCloud)
MESHFLOW_CLOUD_URL    — optional; defaults to https://meshflow.dev
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

_DEFAULT_BASE = "https://meshflow.dev"
_TIMEOUT_S    = 15  # dataset payloads can be large


class DatasetHub:
    """Class-level interface for pushing/pulling eval datasets via the MeshFlow Cloud API."""

    @classmethod
    def _cfg(cls) -> tuple[str, str, bool]:
        key = os.environ.get("MESHFLOW_API_KEY", "")
        url = os.environ.get("MESHFLOW_CLOUD_URL", _DEFAULT_BASE).rstrip("/")
        ok  = os.environ.get("MESHFLOW_CLOUD_ENABLED", "1") != "0" and bool(key)
        return key, url, ok

    @classmethod
    def _request(cls, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        key, url, enabled = cls._cfg()
        if not enabled:
            return None
        data = json.dumps(payload).encode() if payload is not None else None
        headers: dict[str, str] = {"x-meshflow-key": key, "Accept": "application/json"}
        if data:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(f"{url}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, json.JSONDecodeError):
            return None

    @classmethod
    def push(
        cls,
        name: str,
        rows: list[dict[str, Any]],
        *,
        description: str = "",
    ) -> bool:
        """Append rows to a named dataset (creates the dataset if new).

        Each row dict must have an ``input`` key and optionally
        ``expected_output`` and ``metadata``.

        Returns ``True`` on success, ``False`` on error or when the SDK is
        disabled.
        """
        result = cls._request("POST", "/api/ingest/datasets", {
            "name":        name,
            "description": description,
            "rows":        rows,
        })
        return result is not None and "id" in result

    @classmethod
    def pull(
        cls,
        name: str,
        *,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Fetch all rows for a named dataset.

        Returns a list of dicts with keys ``input``, ``expected_output``, and
        ``metadata``.  Returns an empty list when offline or if the dataset is
        not found.
        """
        path   = f"/api/ingest/datasets?name={urllib.parse.quote(name)}&limit={limit}&offset={offset}"
        result = cls._request("GET", path)
        if not isinstance(result, dict):
            return []
        return result.get("rows", [])

    @classmethod
    def list(cls) -> list[dict[str, Any]]:
        """Return summary metadata for all datasets in this org.

        Each entry contains ``name``, ``description``, and ``row_count``.
        Returns an empty list when offline.
        """
        result = cls._request("GET", "/api/ingest/datasets")
        return result if isinstance(result, list) else []

    @classmethod
    def delete(cls, name: str) -> bool:
        """Delete a dataset and all its rows.  Returns True on success."""
        path   = f"/api/ingest/datasets?name={urllib.parse.quote(name)}"
        result = cls._request("DELETE", path)
        return isinstance(result, dict) and result.get("ok") is True


import urllib.parse  # noqa: E402 — deferred to avoid polluting the class namespace
