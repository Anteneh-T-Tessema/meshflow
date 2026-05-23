# MeshFlow Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| 0.x (current) | Yes |

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report security issues by emailing **security@meshflow.dev** with:

- A description of the vulnerability and its potential impact.
- Steps to reproduce or a proof-of-concept (if safe to share).
- Affected versions and configurations.

You will receive an acknowledgement within **48 hours** and a status update
within **7 business days**.  We follow responsible disclosure and will coordinate
a public disclosure date with you, typically 90 days after the report or when
a fix is available, whichever comes first.

We do not currently offer a bug bounty programme, but we will credit
researchers in the release notes unless you prefer to remain anonymous.

---

## Threat Model

MeshFlow sits between untrusted task inputs and LLM APIs.  The primary threat
vectors are:

| Threat | Mitigation |
|---|---|
| Prompt injection via task input | DascGate policy checks; output validation |
| Unauthorised API access | Bearer token auth; `MESHFLOW_API_KEYS` |
| Ledger tampering | SHA-256 hash chain; chain validation on read |
| Agent collusion / coordinated deception | Collusion detection (cross-step entropy analysis) |
| PHI leakage in audit logs | PHI scrubber; `scrub_phi` policy flag |
| Privilege escalation via tool calls | Tool schema validation; shell blocklist; workspace confinement |
| Exfiltration via web/shell tools | Blocklist patterns; `MESHFLOW_WORKSPACE_DIR` confinement |
| Supply chain attacks | Minimal dependency surface; pinned base images |

---

## Authentication

API keys are passed as:
- `Authorization: Bearer <key>` header, or
- `X-API-Key: <key>` header

Keys are loaded at startup from `MESHFLOW_API_KEYS` (comma-separated).
Rotate keys by updating the environment variable and restarting the server.
There is no built-in key storage — use a secrets manager (AWS Secrets Manager,
HashiCorp Vault, Kubernetes Secrets) for production deployments.

---

## Transport Security

Run the server with TLS in production:

```bash
meshflow serve \
  --tls-cert /etc/tls/cert.pem \
  --tls-key  /etc/tls/key.pem
```

Do not expose the HTTP port directly.  Use a TLS-terminating load balancer
(AWS ALB, GCP Load Balancer, Nginx) or enable TLS at the server level.

---

## Tool Security

The built-in tools include safeguards:

- **Shell tool** — blocked command patterns (`rm -rf`, `sudo`, `chmod`, `curl`,
  pipe to shell, etc.). Use `MESHFLOW_WORKSPACE_DIR` to confine file access.
- **Python REPL** — restricted to safe operations; `exec`/`eval`/`import` of
  dangerous modules is blocked via AST inspection.
- **Calculator** — uses AST-based safe evaluation; never calls `eval()`.
- **File tools** — paths are restricted to `MESHFLOW_WORKSPACE_DIR`.

---

## Dependency Surface

MeshFlow's mandatory runtime dependencies are intentionally minimal:

| Package | Purpose |
|---|---|
| `aiohttp` | Async HTTP server |
| `anthropic` | Claude API client |
| `httpx` | Webhook delivery |

Optional dependencies (only loaded when the relevant feature is used):
`openai`, `google-generativeai`, `boto3`, `numpy`, `pydantic`.

---

## Secrets in Audit Logs

Never pass secrets (API keys, passwords, tokens) as task inputs.  Task inputs
are logged verbatim in the ledger.  Use environment variables or a secrets
manager to supply credentials to tools.

---

## Container Security

The production Docker image is built from `python:3.11-slim` with no shell or
package manager in the final image layer.  The build stage is discarded.

Recommended container hardening:

```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 1000
  readOnlyRootFilesystem: true
  allowPrivilegeEscalation: false
  capabilities:
    drop: ["ALL"]
```

---

## Incident Response

1. Revoke compromised API keys by removing them from `MESHFLOW_API_KEYS` and
   restarting the server.
2. Use `ReplayLedger.anonymize_run()` to scrub outputs from compromised runs
   while preserving the audit chain structure.
3. Use `ReplayLedger.delete_run()` or `delete_tenant()` for full erasure when
   required by law or policy.
4. Preserve chain hashes before any modification for forensic analysis.
