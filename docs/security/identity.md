# Agent Identity & Zero-Trust Auth

Every MeshFlow agent can carry a cryptographically signed identity token for zero-trust authentication.

## Sign and verify tokens

```python
from meshflow import AgentIdentity, AgentToken, sign_token, verify_token, decode_token

# Create identity
identity = AgentIdentity(
    agent_id="researcher-01",
    role="researcher",
    tenant_id="acme-corp",
    permissions=["read:knowledge", "write:ledger"],
)

# Sign (HMAC-SHA256)
secret = "shared-secret-32-chars-minimum!!"
token: AgentToken = sign_token(identity, secret=secret)
print(token.value)   # JWT-like string

# Verify on the receiving side
verified: AgentIdentity = verify_token(token.value, secret=secret)
print(verified.agent_id, verified.role)

# Decode without verifying (for debugging)
payload = decode_token(token.value)
```

## IdentityStore

```python
from meshflow import IdentityStore

store = IdentityStore("identities.db")
store.register(identity)

retrieved = store.get("researcher-01")
store.revoke("researcher-01")
store.list()  # all registered identities
```

## Zero-trust pattern

```python
# Agent-to-agent: attach token to A2A messages
from meshflow import A2AClient, AgentToken

client = A2AClient(base_url="http://remote-agent:8080")
token: AgentToken = sign_token(my_identity, secret=SECRET)

response = await client.send(
    task="summarize this document",
    token=token.value,  # attached to X-Agent-Token header
)
```

## StepRuntime integration

When `AgentIdentity` is available, `StepRuntime` validates it as step 1 of the 15-step governance kernel. Invalid or revoked tokens cause the step to be blocked with `verdict="reject"`.

```python
from meshflow import StepRuntime

runtime = StepRuntime(
    ledger=ledger,
    policy=policy,
    identity_store=store,       # enables identity validation
    required_role="researcher", # block if token role != researcher
)
```
