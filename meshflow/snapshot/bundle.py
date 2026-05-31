"""Sprint 65 — Compliance Snapshot.

Point-in-time ZIP package of all audit evidence for SOX/HIPAA annual audit
deliverables. Bundles: lineage graph, agent identities, canary experiments,
active locks, firing alerts, feature flags, policy rules, SLA contracts.

SnapshotManifest — metadata about what was collected and when.
SnapshotBundle   — in-memory representation before export.
SnapshotExporter — gathers data from all stores and writes ZIP.
"""

from __future__ import annotations

import io
import json
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SnapshotManifest:
    snapshot_id:  str
    created_at:   float
    created_by:   str
    description:  str
    sections:     list[str]
    record_counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id":   self.snapshot_id,
            "created_at":    self.created_at,
            "created_by":    self.created_by,
            "description":   self.description,
            "sections":      self.sections,
            "record_counts": self.record_counts,
        }


@dataclass
class SnapshotBundle:
    manifest:  SnapshotManifest
    sections:  dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def add_section(self, name: str, records: list[dict[str, Any]]) -> None:
        self.sections[name] = records
        self.manifest.record_counts[name] = len(records)
        if name not in self.manifest.sections:
            self.manifest.sections.append(name)

    def to_zip_bytes(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "manifest.json",
                json.dumps(self.manifest.to_dict(), indent=2),
            )
            for section, records in self.sections.items():
                zf.writestr(
                    f"{section}.json",
                    json.dumps(records, indent=2),
                )
        return buf.getvalue()

    def total_records(self) -> int:
        return sum(len(r) for r in self.sections.values())


class SnapshotExporter:
    """Gather data from all MeshFlow stores and produce a compliance ZIP.

    All store arguments are optional — pass only the ones you have wired up.
    """

    def __init__(
        self,
        *,
        identity_store: Any = None,
        lineage_graph: Any = None,
        canary_store: Any = None,
        lock_store: Any = None,
        alert_store: Any = None,
        flag_store: Any = None,
        policy_store: Any = None,
        sla_store: Any = None,
        vault_store: Any = None,
        tenant_store: Any = None,
    ) -> None:
        self._identity   = identity_store
        self._lineage    = lineage_graph
        self._canary     = canary_store
        self._locks      = lock_store
        self._alerts     = alert_store
        self._flags      = flag_store
        self._policy     = policy_store
        self._sla        = sla_store
        self._vault      = vault_store
        self._tenant     = tenant_store

    def export(
        self,
        created_by: str = "cli",
        description: str = "",
    ) -> SnapshotBundle:
        snapshot_id = str(uuid.uuid4())
        manifest = SnapshotManifest(
            snapshot_id=snapshot_id,
            created_at=time.time(),
            created_by=created_by,
            description=description or f"Compliance snapshot {snapshot_id[:8]}",
            sections=[],
            record_counts={},
        )
        bundle = SnapshotBundle(manifest=manifest)

        if self._identity is not None:
            try:
                identities = self._identity.list_identities()
                bundle.add_section("identities", [i.to_dict() for i in identities])
            except Exception:
                bundle.add_section("identities", [])

        if self._lineage is not None:
            try:
                nodes = self._lineage.all_nodes() if hasattr(self._lineage, "all_nodes") else []
                edges = self._lineage.all_edges() if hasattr(self._lineage, "all_edges") else []
                bundle.add_section("lineage_nodes", [n.to_dict() for n in nodes])
                bundle.add_section("lineage_edges", [e.to_dict() for e in edges])
            except Exception:
                bundle.add_section("lineage_nodes", [])
                bundle.add_section("lineage_edges", [])

        if self._canary is not None:
            try:
                exps = self._canary.list_experiments()
                bundle.add_section("canary_experiments", [e.to_dict() for e in exps])
            except Exception:
                bundle.add_section("canary_experiments", [])

        if self._locks is not None:
            try:
                locks = self._locks.list_locks(active_only=False)
                bundle.add_section("locks", [lk.to_dict() for lk in locks])
            except Exception:
                bundle.add_section("locks", [])

        if self._alerts is not None:
            try:
                alerts = self._alerts.list_alerts(limit=10000)
                bundle.add_section("alerts", [a.to_dict() for a in alerts])
            except Exception:
                bundle.add_section("alerts", [])

        if self._flags is not None:
            try:
                flags = self._flags.list_flags()
                bundle.add_section("feature_flags", [f.to_dict() for f in flags])
            except Exception:
                bundle.add_section("feature_flags", [])

        if self._policy is not None:
            try:
                rules = self._policy.list_rules()
                bundle.add_section("policy_rules", [r.to_dict() for r in rules])
            except Exception:
                bundle.add_section("policy_rules", [])

        if self._sla is not None:
            try:
                contracts = self._sla.list_contracts()
                bundle.add_section("sla_contracts", [c.to_dict() for c in contracts])
                breaches = self._sla.list_breaches(limit=10000)
                bundle.add_section("sla_breaches", [b.to_dict() for b in breaches])
            except Exception:
                bundle.add_section("sla_contracts", [])
                bundle.add_section("sla_breaches", [])

        if self._vault is not None:
            try:
                secrets = self._vault.list_secrets()
                # Never include values in snapshots — metadata only
                bundle.add_section("vault_secrets_metadata", secrets)
                audit = self._vault.audit_log(limit=10000)
                bundle.add_section("vault_audit", [a.to_dict() for a in audit])
            except Exception:
                bundle.add_section("vault_secrets_metadata", [])
                bundle.add_section("vault_audit", [])

        if self._tenant is not None:
            try:
                tenants = self._tenant.list_tenants()
                bundle.add_section("tenants", [t.to_dict() for t in tenants])
            except Exception:
                bundle.add_section("tenants", [])

        return bundle

    def export_to_file(self, path: str, **kwargs: Any) -> SnapshotBundle:
        bundle = self.export(**kwargs)
        with open(path, "wb") as f:
            f.write(bundle.to_zip_bytes())
        return bundle
