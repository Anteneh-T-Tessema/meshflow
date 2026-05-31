# Sensitive Data Detection

`SensitiveDataDetector` scans text for PHI, PII, and credentials, returning rich match objects so callers can audit what was found rather than just receiving a scrubbed string.

```python
from meshflow.security.sensitive_data import SensitiveDataDetector, get_detector

detector = SensitiveDataDetector()
matches = detector.detect("Patient John Smith SSN: 123-45-6789")
masked  = detector.mask("John Smith 123-45-6789")
report  = detector.audit_report("John Smith SSN: 123-45-6789")
```

## SensitiveMatch

Each match returned by `detect()` is a `SensitiveMatch` dataclass:

| Field           | Type    | Description                                       |
|-----------------|---------|---------------------------------------------------|
| `kind`          | `str`   | Pattern label, e.g. `"SSN"`, `"EMAIL"`, `"JWT"` |
| `category`      | `str`   | `"phi"` \| `"pii"` \| `"credential"`            |
| `value_preview` | `str`   | First 6 chars + `"…"` — never the full value     |
| `start`         | `int`   | Character offset in source text                   |
| `end`           | `int`   | Exclusive end offset                              |
| `confidence`    | `float` | `1.0` for regex patterns, `0.7` for heuristics   |

```python
for m in matches:
    print(m.kind, m.category, m.value_preview, m.confidence)
    d = m.to_dict()  # JSON-serialisable dict
```

## SensitiveDataDetector

```python
SensitiveDataDetector(
    phi_enabled: bool = True,
    credential_enabled: bool = True,
    min_confidence: float = 0.6,
)
```

### Pattern coverage

**PHI / PII (11 patterns)**

| Kind          | Category     | Example match                    |
|---------------|-------------|----------------------------------|
| `SSN`         | `phi`       | `123-45-6789`                    |
| `EMAIL`       | `pii`       | `user@example.com`               |
| `PHONE`       | `pii`       | `(555) 867-5309`                 |
| `DATE`        | `pii`       | `Jan 15, 2024`                   |
| `ZIP`         | `pii`       | `94105-1234`                     |
| `IP`          | `pii`       | `192.168.1.1`                    |
| `URL`         | `pii`       | `https://internal.example.com`   |
| `MRN`         | `phi`       | `MRN: A-12345`                   |
| `NPI`         | `phi`       | NPI number (10 digits)           |
| `CREDIT_CARD` | `pii`       | `4111 1111 1111 1111`            |
| `NAME`        | `pii`       | `John Smith` (confidence 0.7)    |

**Credentials (12 patterns)**

| Kind                | Example prefix            |
|---------------------|---------------------------|
| `API_KEY_ANTHROPIC` | `sk-ant-…`               |
| `API_KEY_OPENAI`    | `sk-` (48 chars)          |
| `API_KEY_GENERIC`   | `api_key=…`               |
| `AWS_ACCESS_KEY`    | `AKIA…`                   |
| `AWS_SECRET_KEY`    | `aws_secret=…`            |
| `GITHUB_TOKEN`      | `ghp_…`, `gho_…`, `ghs_…` |
| `JWT`               | `eyJ…`                    |
| `PRIVATE_KEY`       | `-----BEGIN RSA…`         |
| `DB_CONN_STRING`    | `postgresql://user:pass@` |
| `HIGH_ENTROPY_HEX`  | 40+ hex chars (conf 0.6)  |
| `GCP_KEY`           | `"type": "service_account"` |
| `BEARER_TOKEN`      | `Bearer <token>`          |

### Methods

```python
# Returns list[SensitiveMatch], ordered by position
matches = detector.detect(text)

# Returns text with all matches replaced
# PHI/PII → "[REDACTED]"   credentials → "[CREDENTIAL-REDACTED]"
safe = detector.mask(text)

# Quick boolean checks
detector.has_credentials(text)  # True if any credential pattern fires
detector.has_phi(text)          # True if any PHI/PII pattern fires

# Compliance-ready summary dict
report = detector.audit_report(text)
# {
#   "total_matches": 2,
#   "has_phi": True, "has_pii": False, "has_credentials": False,
#   "kinds_found": ["EMAIL", "SSN"],
#   "by_category": {"phi": ["SSN"]},
#   "high_confidence_matches": [...]
# }
```

## Global singleton

```python
from meshflow.security.sensitive_data import get_detector

detector = get_detector()   # cached SensitiveDataDetector()
```

`reset_detector()` clears the singleton — useful in tests.

## PIIBlockGuardrail

The guardrail wraps `SensitiveDataDetector` into the `GuardrailStack`:

```python
from meshflow.security.guardrails import PIIBlockGuardrail
from meshflow.agents.builder import Agent

agent = Agent(
    name="hipaa-agent",
    role="researcher",
    input_guardrails=[PIIBlockGuardrail(action="block")],
    output_guardrails=[PIIBlockGuardrail(action="modify")],  # mask instead of block
)
```

| Action     | Behaviour                                         |
|------------|---------------------------------------------------|
| `"block"`  | `GuardrailResult(passed=False)` when PII found    |
| `"modify"` | Returns masked text; call succeeds                |
| `"warn"`   | Passes with match count in `metadata`             |
