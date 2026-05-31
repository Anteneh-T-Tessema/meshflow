# Secret Vault

`VaultStore` is an encrypted-at-rest secret store for agent credentials and API keys. Every read is access-logged. Secrets use Fernet AES encryption with PBKDF2-SHA256 key derivation and a unique random salt per secret.

```python
from meshflow.vault.store import VaultStore

vault = VaultStore("meshflow_vault.db", passphrase="my-master-passphrase")

secret = vault.store("openai_key", "sk-proj-...", category="api_key")
retrieved = vault.retrieve("openai_key", accessed_by="scheduler")
print(retrieved.value)   # "sk-proj-..."
```

## Encryption Design

- **Algorithm**: Fernet (AES-128-CBC + HMAC-SHA256). Falls back to a keyed XOR stream if `cryptography` is not installed.
- **Key derivation**: PBKDF2-SHA256, 100,000 iterations, 16-byte random salt per secret.
- **Per-secret salt**: The same passphrase produces different ciphertext for every secret stored, preventing rainbow-table attacks across secrets.
- **Audit log**: Every `store`, `retrieve`, `rotate`, and `delete` operation is written to `vault_audit` before it completes.

## `VaultSecret` Fields

```python
@dataclass
class VaultSecret:
    secret_id:   str            # UUID
    name:        str            # lookup key
    value:       str            # plaintext (never persisted)
    category:    str            # e.g. "api_key", "db_password"
    description: str
    created_by:  str
    created_at:  float          # Unix timestamp
    rotated_at:  float | None   # set after rotate()
```

`VaultSecret.to_dict()` omits `value` — safe to log or include in snapshots.

## Operations

### `store`

```python
secret = vault.store(
    name="stripe_webhook_secret",
    value="whsec_...",
    category="webhook",
    description="Stripe webhook signing secret",
    created_by="ops-team",
)
```

### `retrieve`

```python
secret = vault.retrieve("stripe_webhook_secret", accessed_by="billing_agent")
# Returns None if not found. Every call is audit-logged.
```

### `rotate`

Re-encrypts with a fresh random salt. The old ciphertext is overwritten atomically.

```python
vault.rotate("stripe_webhook_secret", new_value="whsec_NEW...", rotated_by="ops-team")
```

### `delete`

```python
vault.delete("stripe_webhook_secret", deleted_by="ops-team")
# Audit log entry is written before deletion
```

### `list_secrets` — Metadata Only

```python
secrets = vault.list_secrets(category="api_key")
# Returns list[dict] — never includes plaintext values
# Fields: secret_id, name, category, description, created_by, created_at, rotated_at
```

### `audit_log`

```python
log = vault.audit_log(name="openai_key", limit=50)
for entry in log:
    print(entry.operation, entry.accessed_by, entry.ts)
# operations: "read" | "write" | "rotate" | "delete"
```

`vault.audit_log()` with no `name` returns the full cross-secret audit log.

## `meshflow vault` CLI

```bash
# Store a secret (prompts for value)
meshflow vault store openai_key --category api_key --passphrase "my-passphrase"

# Retrieve and print to stdout
meshflow vault retrieve openai_key --passphrase "my-passphrase"

# Rotate a secret
meshflow vault rotate openai_key --passphrase "my-passphrase"

# Delete a secret
meshflow vault delete openai_key --passphrase "my-passphrase"

# List all secrets (metadata only)
meshflow vault list --db meshflow_vault.db

# Show audit log for a secret
meshflow vault audit --name openai_key --limit 20
```

All commands default to `meshflow_vault.db` and accept `--db` to override.

## Using Vault Secrets in Agents

```python
vault = VaultStore("meshflow_vault.db", passphrase=os.environ["VAULT_PASSPHRASE"])
secret = vault.retrieve("anthropic_api_key", accessed_by="agent-runner")

import anthropic
client = anthropic.Anthropic(api_key=secret.value)
```

Never pass plaintext secrets as Agent parameters — always retrieve them at runtime from the vault.
