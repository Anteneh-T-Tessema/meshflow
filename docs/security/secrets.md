# Secret Scanner

`SecretScanner` detects leaked credentials and secrets in text before they are logged or returned to callers, with optional in-place redaction.

```python
from meshflow.security.secrets import SecretScanner

scanner = SecretScanner(redact=True)
result = scanner.scan("Here is my key: sk-ant-api03-abc123...")
print(result.summary())        # "SECRETS FOUND: 1 match(es) in [api_keys]"
print(result.redacted_text)    # "Here is my key: [REDACTED:api_keys]"
```

## Credential pattern coverage

Eight detection categories covering 30+ named patterns:

| Category       | What is detected                                                     |
|----------------|----------------------------------------------------------------------|
| `api_keys`     | AWS (AKIA…), GCP (AIza…), GitHub (ghp_/gho_/ghs_/ghr_), Stripe (sk_live_/rk_live_), Twilio, SendGrid (SG.…), Slack (xoxb-/xoxp-), HuggingFace (hf_…), Anthropic (sk-ant-…), OpenAI (sk-…), Cohere, Pinecone, Databricks (dapi…), generic `api_key=` |
| `tokens`       | JWT (eyJ…), `Authorization: Bearer`, OAuth refresh/access/session tokens |
| `private_keys` | RSA, PKCS8, EC, OpenSSH, DSA, PGP PEM blocks                        |
| `passwords`    | URL-embedded credentials (`scheme://user:pass@host`), `password=`, `DB_PASS=` |
| `database`     | PostgreSQL, MySQL, MongoDB, Redis, MSSQL, JDBC connection strings    |
| `cloud`        | Azure storage connection strings and SAS tokens, S3 presigned URLs   |
| `certificates` | X.509 PEM blocks, PKCS12 hints                                       |

## SecretMatch

```python
@dataclass
class SecretMatch:
    category:     str    # e.g. "api_keys"
    pattern_name: str    # e.g. "anthropic_key"
    matched_text: str    # first 6 chars + "***" — never the full secret
    position:     int    # character offset
    confidence:   float  # 0.70–0.99 (patterns below min_confidence are skipped)
    raw_length:   int    # length of original matched text
```

## SecretScanResult

```python
@dataclass
class SecretScanResult:
    found:         bool
    categories:    list[str]
    matches:       list[SecretMatch]
    redacted_text: str | None  # set when scanner.redact=True

    result.is_clean    # True iff not found
    result.summary()   # "SECRETS FOUND: 2 match(es) in [api_keys, tokens]"
```

## SecretScanner

```python
SecretScanner(
    enabled_categories: list[str] | None = None,  # None = all eight
    min_confidence:     float = 0.70,
    redact:             bool = False,
)
```

```python
# Quick check
if not scanner.is_clean(text):
    raise ValueError("credential leak detected")

# Full scan
result = scanner.scan(text)
for m in result.matches:
    print(f"{m.pattern_name} at offset {m.position} (confidence {m.confidence:.0%})")
```

## SecretScanGuardrail

Use as a post-generation output guardrail to prevent the LLM from emitting secrets:

```python
from meshflow.security.secrets import SecretScanGuardrail
from meshflow.agents.builder import Agent

agent = Agent(
    name="code-agent",
    role="executor",
    output_guardrails=[
        SecretScanGuardrail(action="block"),   # halt on any secret
        # or: action="modify" to scrub secrets transparently
        # or: action="warn"   to log but pass through
    ],
)
```

| Action     | Behaviour                                                    |
|------------|--------------------------------------------------------------|
| `"block"`  | `passed=False` — run is halted before caller receives output |
| `"modify"` | `passed=True` with `modified_text` containing `[REDACTED:…]` replacements |
| `"warn"`   | `passed=True` with match metadata attached                   |

When `action="modify"`, the scanner is automatically constructed with `redact=True`.
