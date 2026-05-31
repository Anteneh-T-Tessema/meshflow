# SwarmTRM Neural Consensus

SwarmTRM is MeshFlow's neural consensus engine — 53 deterministic domain verifiers that vote on agent outputs before they are committed.

## Quick start

```python
from meshflow import SwarmNode, swarm_verifier, register_swarm_domain, VerificationResult

# Define a domain verifier
@swarm_verifier(domain="medical", weight=2.0)
def verify_medical_claim(output: str) -> VerificationResult:
    has_disclaimer = "consult a doctor" in output.lower()
    return VerificationResult(
        passed=has_disclaimer,
        confidence=0.95 if has_disclaimer else 0.2,
        reason="Medical output must include disclaimer",
    )

# Register a domain
register_swarm_domain("medical", verifiers=[verify_medical_claim])

# Use in an agent
node = SwarmNode(
    agent=Agent(name="medical-ai", role="executor"),
    domain="medical",
    consensus_threshold=0.7,  # require 70% verifier agreement
)
result = await node.run("What should I take for a headache?")
print(result.consensus_passed)
print(result.verifier_votes)  # dict of verifier_name → vote
```

## Available domains

```python
from meshflow import swarm_available_domains
print(swarm_available_domains())
# → ["finance", "medical", "legal", "security", "code", "compliance", ...]
```

## DeterministicVerifier

Pre-built rule-based verifiers with no ML dependency:

```python
from meshflow import DeterministicVerifier

verifier = DeterministicVerifier(
    name="no-pii",
    rule=lambda text: not any(pattern in text for pattern in ["SSN", "DOB"]),
    confidence=0.99,
)
```

## VerificationResult fields

| Field | Type | Description |
|-------|------|-------------|
| `passed` | `bool` | Whether this verifier approved the output |
| `confidence` | `float` | 0–1 verifier confidence score |
| `reason` | `str` | Human-readable explanation |
| `metadata` | `dict` | Optional extra context |

## Require SwarmTRM

```bash
pip install "meshflow[swarm]"   # adds torch + pydantic
```

Zero-dep fallback: SwarmTRM gracefully disables if torch is not installed — agents still run, consensus step is skipped.
