# Policy Engine

MeshFlow's policy engine evaluates declarative YAML rules against agent context at every call, providing auditable, deterministic access control for regulated environments.

```python
from meshflow.policy import PolicyEngine, PolicyStore, PolicyLoader

store = PolicyStore("meshflow_policy.db")
engine = PolicyEngine(store, audit=True)

decision = engine.evaluate({"agent": "phi_agent", "role": "researcher"})
print(decision.is_allowed)    # True or False
print(decision.rule_name)     # which rule matched
print(decision.reason)        # human-readable explanation
```

## `PolicyEngine` and `PolicyStore`

`PolicyStore` persists rules and a decision audit log to SQLite. `PolicyEngine` evaluates context against all enabled rules — first `DENY` wins, then first `ALLOW`, then default allow.

```python
engine.add_rule(
    name="block-phi-access-without-clearance",
    action=PolicyAction.DENY,
    conditions=[
        ("data_class", "eq", "PHI"),
        ("clearance_level", "lt", "2"),
    ],
    framework="hipaa",
    priority=100,
    description="Deny PHI access to agents without clearance level 2+",
)

engine.is_allowed({"data_class": "PHI", "clearance_level": "1"})  # False
```

## YAML Policy Format

Load rules from a YAML file. Pass the path with `meshflow serve --policy-file`:

```yaml
rules:
  - name: deny-phi-without-clearance
    description: HIPAA — block PHI access below clearance level 2
    framework: hipaa
    priority: 100
    action: deny
    conditions:
      - field: data_class
        op: eq
        value: PHI
      - field: clearance_level
        op: lt
        value: "2"

  - name: deny-pii-export
    description: GDPR — block bulk PII exports
    framework: gdpr
    priority: 90
    action: deny
    conditions:
      - field: operation
        op: eq
        value: bulk_export
      - field: data_contains_pii
        op: eq
        value: "true"

  - name: log-financial-writes
    description: SOX — log all financial record modifications
    framework: sox
    priority: 50
    action: log
    conditions:
      - field: record_type
        op: in
        value: "journal_entry,gl_adjustment,account_close"

  - name: alert-nerc-cip-access
    description: NERC CIP — alert on critical infrastructure access
    framework: nerc
    priority: 80
    action: alert
    conditions:
      - field: asset_class
        op: eq
        value: BES_CYBER_ASSET
      - field: access_type
        op: eq
        value: write
```

Load from code:

```python
from meshflow.policy import PolicyLoader, PolicyStore

store = PolicyStore("meshflow_policy.db")
with open("meshflow.policy.yaml") as f:
    PolicyLoader.from_yaml(f.read(), store)
```

## `PolicyCondition` Operators

| Operator | Description | Example |
|---|---|---|
| `eq` | Exact string equality | `data_class eq PHI` |
| `neq` | Not equal | `role neq admin` |
| `in` | Value in comma-separated list | `tier in "1,2,3"` |
| `not_in` | Value not in list | `status not_in "suspended,deleted"` |
| `gt` | Greater than (numeric) | `cost_usd gt 1.0` |
| `lt` | Less than (numeric) | `clearance_level lt 2` |
| `gte` | Greater than or equal | `confidence gte 0.7` |
| `lte` | Less than or equal | `tokens lte 4096` |
| `contains` | Substring present | `input_text contains "SSN"` |
| `exists` | Field is present in context | `patient_id exists` |

All conditions in a rule use AND logic. Use multiple rules for OR logic.

## `meshflow serve --policy-file`

```bash
meshflow serve --policy-file ./meshflow.policy.yaml --host 0.0.0.0 --port 8000
```

Rules are loaded at startup and applied to every agent invocation.

## `meshflow policy` CLI

```bash
# Add a rule inline
meshflow policy add \
  --name "deny-uncleared-phi" \
  --action deny \
  --conditions "data_class=PHI,clearance_level<2" \
  --framework hipaa \
  --priority 100

# List all rules
meshflow policy list --framework hipaa

# Enable / disable a rule by name
meshflow policy enable  --name "deny-uncleared-phi"
meshflow policy disable --name "deny-uncleared-phi"

# Evaluate a context dict against all rules
meshflow policy evaluate --context '{"data_class":"PHI","clearance_level":"1"}'
```

## `PolicyDecision` Fields

```python
@dataclass
class PolicyDecision:
    action: PolicyAction       # ALLOW | DENY | LOG | ALERT
    rule_name: str             # name of the matched rule, or "" for default allow
    reason: str                # human-readable explanation
    matched: bool              # False when no rules matched (default allow)

    @property
    def is_allowed(self) -> bool: ...   # True unless action == DENY
```

Every decision is written to the `policy_decisions` table when `audit=True` (the default), providing a full decision audit log queryable via `PolicyStore.decision_log(limit=50)`.
