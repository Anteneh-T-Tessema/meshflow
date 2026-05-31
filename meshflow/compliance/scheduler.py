"""Scheduled compliance report delivery.

ScheduledReporter generates a ComplianceReporter artifact on demand and
delivers it to one of three sinks:
  - file   — appends/writes to a local path
  - webhook — HTTP POST with HMAC-SHA256 signature
  - stdout  — prints to console

ScheduleStore persists schedule configs to a JSON file so the CLI can
list, add, remove, and trigger runs.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any


# ── Data model ────────────────────────────────────────────────────────────────

_VALID_FRAMEWORKS = {"hipaa", "sox", "gdpr", "pci", "nerc"}
_VALID_SINKS = {"file", "webhook", "stdout"}


@dataclass
class ReportSchedule:
    schedule_id: str
    framework: str
    interval_seconds: int
    sink_type: str
    sink_config: dict[str, Any]
    db_path: str = "meshflow_runs.db"
    tenant_id: str = ""
    last_run_at: float = 0.0
    next_run_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReportSchedule:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def is_due(self) -> bool:
        return time.time() >= self.next_run_at

    def mark_ran(self) -> None:
        self.last_run_at = time.time()
        self.next_run_at = self.last_run_at + self.interval_seconds


# ── Schedule store ────────────────────────────────────────────────────────────

class ScheduleStore:
    """JSON-backed persistence for report schedules."""

    def __init__(self, path: str = "") -> None:
        self._path = path or os.environ.get(
            "MESHFLOW_SCHEDULE_FILE",
            os.path.expanduser("~/.meshflow/schedules.json"),
        )

    def _load(self) -> list[dict[str, Any]]:
        try:
            with open(self._path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save(self, records: list[dict[str, Any]]) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self._path)), exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(records, f, indent=2)

    def add(self, schedule: ReportSchedule) -> ReportSchedule:
        records = self._load()
        records.append(schedule.to_dict())
        self._save(records)
        return schedule

    def list_all(self) -> list[ReportSchedule]:
        return [ReportSchedule.from_dict(r) for r in self._load()]

    def get(self, schedule_id: str) -> ReportSchedule | None:
        for r in self._load():
            if r.get("schedule_id") == schedule_id:
                return ReportSchedule.from_dict(r)
        return None

    def remove(self, schedule_id: str) -> bool:
        records = self._load()
        new_records = [r for r in records if r.get("schedule_id") != schedule_id]
        if len(new_records) == len(records):
            return False
        self._save(new_records)
        return True

    def update(self, schedule: ReportSchedule) -> None:
        records = self._load()
        for i, r in enumerate(records):
            if r.get("schedule_id") == schedule.schedule_id:
                records[i] = schedule.to_dict()
                break
        self._save(records)


# ── Sinks ─────────────────────────────────────────────────────────────────────

def _deliver_file(content: str, config: dict[str, Any]) -> None:
    path = config.get("path", "compliance_report.txt")
    mode = config.get("mode", "w")  # "w" overwrites, "a" appends
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, mode) as f:
        if mode == "a":
            f.write(f"\n{'='*60}\n")
        f.write(content)


def _deliver_webhook(content: str, config: dict[str, Any]) -> None:
    import urllib.request

    url = config.get("url", "")
    if not url:
        raise ValueError("webhook sink requires 'url' in sink_config")
    secret = config.get("secret", "")
    payload = content.encode()
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if secret:
        sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        headers["X-MeshFlow-Signature"] = f"sha256={sig}"
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    urllib.request.urlopen(req, timeout=15)


def _deliver_stdout(content: str, _config: dict[str, Any]) -> None:
    print(content)


_SINK_FNS = {
    "file": _deliver_file,
    "webhook": _deliver_webhook,
    "stdout": _deliver_stdout,
}


# ── ScheduledReporter ─────────────────────────────────────────────────────────

class ScheduledReporter:
    """Generate and deliver a compliance report for one schedule entry."""

    def __init__(self, schedule: ReportSchedule) -> None:
        self._schedule = schedule

    async def run_now(self) -> dict[str, Any]:
        """Generate report, deliver to sink, return summary dict."""
        from meshflow.compliance.reporter import ComplianceReporter
        from meshflow.core.ledger import ReplayLedger

        db = self._schedule.db_path
        if not os.path.exists(db):
            raise FileNotFoundError(f"Ledger not found at '{db}'")

        tenant_id = self._schedule.tenant_id or "default"
        ledger = ReplayLedger(db, tenant_id=tenant_id)
        all_runs = await ledger.list_runs()
        steps = []
        run_ids = all_runs[-50:]
        for rid in run_ids:
            run_steps = await ledger.get_run(rid) or []
            steps.extend(run_steps)

        reporter = ComplianceReporter()
        report = reporter.generate(self._schedule.framework, steps, run_ids=run_ids)

        sink_type = self._schedule.sink_type
        if sink_type not in _SINK_FNS:
            raise ValueError(f"Unknown sink_type '{sink_type}' — choose from {_VALID_SINKS}")

        content = report.to_json() if sink_type == "webhook" else report.to_text()
        _SINK_FNS[sink_type](content, self._schedule.sink_config)

        return {
            "schedule_id": self._schedule.schedule_id,
            "framework": self._schedule.framework,
            "sink_type": sink_type,
            "run_ids_audited": len(run_ids),
            "overall_status": report.summary.overall_status,
            "delivered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }


# ── Convenience factory ───────────────────────────────────────────────────────

def create_schedule(
    framework: str,
    interval_seconds: int,
    sink_type: str,
    sink_config: dict[str, Any],
    db_path: str = "meshflow_runs.db",
    tenant_id: str = "",
) -> ReportSchedule:
    if framework not in _VALID_FRAMEWORKS:
        raise ValueError(f"Unknown framework '{framework}' — choose from {_VALID_FRAMEWORKS}")
    if sink_type not in _VALID_SINKS:
        raise ValueError(f"Unknown sink_type '{sink_type}' — choose from {_VALID_SINKS}")
    now = time.time()
    return ReportSchedule(
        schedule_id=str(uuid.uuid4())[:8],
        framework=framework,
        interval_seconds=interval_seconds,
        sink_type=sink_type,
        sink_config=sink_config,
        db_path=db_path,
        tenant_id=tenant_id,
        last_run_at=0.0,
        next_run_at=now + interval_seconds,
    )
