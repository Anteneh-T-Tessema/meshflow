# Compliance Snapshots

`SnapshotExporter` produces a point-in-time ZIP package of all audit evidence — the primary deliverable for HIPAA §164.312 annual reviews, GDPR Art. 30 processing records, and SOX control documentation.

```python
from meshflow.snapshot.bundle import SnapshotExporter
from meshflow.policy.engine import PolicyStore
from meshflow.sla.tracker import SLAStore
from meshflow.vault.store import VaultStore
from meshflow.tenant.store import TenantStore

exporter = SnapshotExporter(
    policy_store=PolicyStore("meshflow_policy.db"),
    sla_store=SLAStore("meshflow_sla.db"),
    vault_store=VaultStore("meshflow_vault.db", passphrase="..."),
    tenant_store=TenantStore("meshflow_tenants.db"),
)

bundle = exporter.export_to_file(
    "audit_2026Q1.zip",
    created_by="compliance-officer@example.com",
    description="Q1 2026 HIPAA annual audit package",
)

print(bundle.total_records())              # total records across all sections
print(bundle.manifest.snapshot_id)        # UUID for this snapshot
print(bundle.manifest.record_counts)      # per-section counts
```

## ZIP Structure

The exported ZIP contains one JSON file per section plus a `manifest.json`:

```
audit_2026Q1.zip
├── manifest.json           # SnapshotManifest — metadata about the snapshot
├── policy_rules.json       # all policy rules (enabled and disabled)
├── sla_contracts.json      # all SLA contracts
├── sla_breaches.json       # all recorded SLA breaches
├── vault_secrets_metadata.json   # secret metadata — NO plaintext values
├── vault_audit.json        # full vault access log
├── tenants.json            # tenant registry
├── identities.json         # agent identity records (if identity_store provided)
├── lineage_nodes.json      # lineage graph nodes (if lineage_graph provided)
├── lineage_edges.json      # lineage graph edges
├── canary_experiments.json # canary/A-B experiment records
├── locks.json              # distributed lock history
├── alerts.json             # fired alert records
└── feature_flags.json      # feature flag states
```

All files use `ZIP_DEFLATED` compression.

## `SnapshotManifest` Fields

```python
@dataclass
class SnapshotManifest:
    snapshot_id:   str              # UUID
    created_at:    float            # Unix timestamp
    created_by:    str              # operator identifier
    description:   str
    sections:      list[str]        # section names included
    record_counts: dict[str, int]   # records per section
```

## `SnapshotBundle` API

```python
bundle.add_section("custom_section", records)   # add arbitrary section
bundle.to_zip_bytes()                           # bytes — write to any file-like object
bundle.total_records()                          # sum across all sections
bundle.manifest.to_dict()                       # serialize manifest
```

## `SnapshotExporter` Constructor

All store arguments are optional. Pass only the stores you have wired up — sections for missing stores are simply omitted from the ZIP.

```python
SnapshotExporter(
    identity_store=...,    # AgentIdentityStore
    lineage_graph=...,     # LineageGraph
    canary_store=...,      # CanaryStore
    lock_store=...,        # LockStore
    alert_store=...,       # AlertStore
    flag_store=...,        # FlagStore
    policy_store=...,      # PolicyStore
    sla_store=...,         # SLAStore
    vault_store=...,       # VaultStore
    tenant_store=...,      # TenantStore
)
```

Vault secret **values are never written to the snapshot**. Only `vault_secrets_metadata` (name, category, description, created_by, created_at, rotated_at) and the `vault_audit` log are included.

## `meshflow snapshot export` CLI

```bash
meshflow snapshot export \
  --output audit_2026Q1.zip \
  --description "Q1 HIPAA annual audit package" \
  --created-by compliance@example.com \
  --policy-db meshflow_policy.db \
  --sla-db meshflow_sla.db \
  --vault-db meshflow_vault.db \
  --vault-passphrase "$VAULT_PASSPHRASE" \
  --tenant-db meshflow_tenants.db
```

## Regulatory Compliance Use

| Regulation | Artifact requirement | Snapshot section |
|---|---|---|
| HIPAA §164.312(b) | Audit controls — record access to ePHI | `vault_audit`, `identities` |
| HIPAA §164.312(c)(1) | Integrity — detect unauthorized alteration | `policy_rules`, manifest hash |
| GDPR Art. 30 | Records of processing activities | `tenants`, `policy_rules` |
| SOX Section 302/404 | Internal controls documentation | `policy_rules`, `sla_contracts`, `sla_breaches` |
| PCI-DSS Req. 10 | Audit log review and retention | `vault_audit`, `alerts` |

Snapshots are point-in-time immutable artifacts. Store them in WORM storage (S3 Object Lock, Azure Immutable Blob) alongside the corresponding ledger export from `ReplayLedger.archive_run()` to satisfy long-term retention requirements.
