"""meshflow env — generate and validate .env files for production deployments.

Usage::

    from meshflow.deploy.env_generator import EnvGenerator

    gen = EnvGenerator()
    gen.write(".env")            # generate with current values + sensible defaults

    issues = gen.validate(".env")
    for issue in issues:
        print(issue)

CLI::

    meshflow env                 # print to stdout
    meshflow env --output .env   # write to file
    meshflow env --validate .env # validate existing file
"""

from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass


@dataclass
class EnvVar:
    """Descriptor for one environment variable."""
    key: str
    default: str
    description: str
    required: bool = False
    secret: bool = False
    example: str = ""


# ── Full variable catalogue ────────────────────────────────────────────────────

_VARS: list[EnvVar] = [
    # ── LLM providers ────────────────────────────────────────────────────────
    EnvVar("ANTHROPIC_API_KEY", "", "Anthropic API key (required for Claude models)",
           required=False, secret=True, example="sk-ant-api03-..."),
    EnvVar("OPENAI_API_KEY", "", "OpenAI API key",
           secret=True, example="sk-..."),
    EnvVar("GEMINI_API_KEY", "", "Google Gemini API key",
           secret=True, example="AIzaSy..."),
    EnvVar("AZURE_OPENAI_API_KEY", "", "Azure OpenAI key",
           secret=True),
    EnvVar("AZURE_OPENAI_ENDPOINT", "", "Azure OpenAI endpoint URL",
           example="https://my-resource.openai.azure.com/"),

    # ── Server ────────────────────────────────────────────────────────────────
    EnvVar("MESHFLOW_HOST", "0.0.0.0", "HTTP server bind address"),
    EnvVar("MESHFLOW_PORT", "8000", "HTTP server port"),
    EnvVar("MESHFLOW_WORKERS", "1", "Number of async worker tasks"),
    EnvVar("MESHFLOW_CORS_ORIGINS", "*", "Allowed CORS origins (comma-separated)"),
    EnvVar("MESHFLOW_API_KEYS", "", "Comma-separated API keys for server auth",
           secret=True, example="key1,key2"),

    # ── Security ──────────────────────────────────────────────────────────────
    EnvVar("MESHFLOW_WEBHOOK_SECRET", "", "HMAC signing secret for outbound webhooks",
           required=True, secret=True),
    EnvVar("MESHFLOW_VAULT_KEY", "", "Encryption key for the secret vault (Fernet)",
           secret=True),

    # ── Persistence ───────────────────────────────────────────────────────────
    EnvVar("MESHFLOW_LEDGER_PATH", "/data/runs.db",
           "Path to the SQLite run ledger"),
    EnvVar("MESHFLOW_REGISTRY_PATH", "/data/registry.db",
           "Path to the agent registry database"),
    EnvVar("MESHFLOW_BUDGET_PATH", "/data/budgets.db",
           "Path to the cost budget database"),
    EnvVar("MESHFLOW_FEEDBACK_PATH", "/data/feedback.db",
           "Path to the feedback store"),
    EnvVar("DATABASE_URL", "",
           "PostgreSQL DSN (overrides SQLite when set)",
           example="postgresql://mesh:pass@localhost:5432/mesh"),

    # ── Policy ────────────────────────────────────────────────────────────────
    EnvVar("MESHFLOW_POLICY_FILE", "",
           "Path to a YAML policy file loaded at startup",
           example="/config/policy.yaml"),
    EnvVar("MESHFLOW_POLICY_MODE", "balanced",
           "Default policy mode: minimal | balanced | strict | legal-critical"),

    # ── Observability ─────────────────────────────────────────────────────────
    EnvVar("OTEL_SERVICE_NAME", "meshflow", "OpenTelemetry service name"),
    EnvVar("OTEL_EXPORTER_OTLP_ENDPOINT", "",
           "OTLP/HTTP endpoint (e.g. Jaeger, Grafana Tempo)",
           example="http://localhost:4318"),
    EnvVar("MESHFLOW_OTEL", "",
           "Set to '1' to enable OTEL export"),

    # ── Rate limiting ─────────────────────────────────────────────────────────
    EnvVar("MESHFLOW_RATE_LIMIT_RPS", "100",
           "Default requests-per-second rate limit"),
    EnvVar("MESHFLOW_RATE_LIMIT_BURST", "200",
           "Default burst cap for the token bucket"),

    # ── Cost guardrails ───────────────────────────────────────────────────────
    EnvVar("MESHFLOW_DEFAULT_BUDGET_USD", "10.0",
           "Per-run cost cap in USD (0 = unlimited)"),

    # ── Misc ──────────────────────────────────────────────────────────────────
    EnvVar("PYTHONUNBUFFERED", "1",
           "Flush stdout immediately (recommended for containers)"),
    EnvVar("MESHFLOW_LOG_LEVEL", "INFO",
           "Logging level: DEBUG | INFO | WARNING | ERROR"),
    EnvVar("MESHFLOW_MOCK", "0",
           "Set to '1' to use mock providers (no API calls)"),
]

_VAR_MAP: dict[str, EnvVar] = {v.key: v for v in _VARS}


@dataclass
class ValidationIssue:
    key: str
    severity: str  # "error" | "warning"
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.key}: {self.message}"


class EnvGenerator:
    """Generate, render, and validate MeshFlow `.env` files.

    Parameters
    ----------
    auto_generate_secrets:
        If True (default), automatically generate secure values for secret
        variables that have no value set in the current environment.
    """

    def __init__(self, auto_generate_secrets: bool = True) -> None:
        self._auto_secrets = auto_generate_secrets
        self._overrides: dict[str, str] = {}

    def set(self, key: str, value: str) -> "EnvGenerator":
        """Override a specific variable value before rendering."""
        self._overrides[key] = value
        return self

    def render(self) -> str:
        """Render the full .env file as a string."""
        lines = [
            "# MeshFlow production environment configuration",
            "# Generated by: meshflow env",
            "# Edit the values below, then source this file or pass to Docker.",
            "",
        ]

        sections = [
            ("LLM Providers", ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
                                "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"]),
            ("Server", ["MESHFLOW_HOST", "MESHFLOW_PORT", "MESHFLOW_WORKERS",
                        "MESHFLOW_CORS_ORIGINS", "MESHFLOW_API_KEYS"]),
            ("Security", ["MESHFLOW_WEBHOOK_SECRET", "MESHFLOW_VAULT_KEY"]),
            ("Persistence", ["MESHFLOW_LEDGER_PATH", "MESHFLOW_REGISTRY_PATH",
                             "MESHFLOW_BUDGET_PATH", "MESHFLOW_FEEDBACK_PATH",
                             "DATABASE_URL"]),
            ("Policy", ["MESHFLOW_POLICY_FILE", "MESHFLOW_POLICY_MODE"]),
            ("Observability", ["OTEL_SERVICE_NAME", "OTEL_EXPORTER_OTLP_ENDPOINT",
                               "MESHFLOW_OTEL"]),
            ("Rate Limiting", ["MESHFLOW_RATE_LIMIT_RPS", "MESHFLOW_RATE_LIMIT_BURST"]),
            ("Cost Guardrails", ["MESHFLOW_DEFAULT_BUDGET_USD"]),
            ("Misc", ["PYTHONUNBUFFERED", "MESHFLOW_LOG_LEVEL",
                      "MESHFLOW_MOCK"]),
        ]

        for section_name, keys in sections:
            lines.append(f"# {'─' * 10} {section_name} {'─' * (40 - len(section_name))}")
            for key in keys:
                var = _VAR_MAP.get(key)
                if var is None:
                    continue
                value = self._resolve(var)
                lines.append(f"# {var.description}")
                if var.example:
                    lines.append(f"# Example: {var.example}")
                lines.append(f"{key}={value}")
                lines.append("")

        return "\n".join(lines)

    def write(self, path: str = ".env", *, overwrite: bool = False) -> None:
        """Write the rendered .env to *path*.

        Parameters
        ----------
        overwrite: If False (default), raise if the file already exists.
        """
        if os.path.exists(path) and not overwrite:
            raise FileExistsError(
                f"{path} already exists. Pass overwrite=True or use --overwrite."
            )
        with open(path, "w") as f:
            f.write(self.render())

    def validate(self, path: str = ".env") -> list[ValidationIssue]:
        """Validate an existing .env file and return a list of issues."""
        issues: list[ValidationIssue] = []

        if not os.path.exists(path):
            issues.append(ValidationIssue(
                key=path, severity="error",
                message="File does not exist.",
            ))
            return issues

        # Parse the file
        parsed: dict[str, str] = {}
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                m = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line)
                if m:
                    parsed[m.group(1)] = m.group(2).strip('"\'')

        # Check required vars
        for var in _VARS:
            if var.required:
                val = parsed.get(var.key, "")
                if not val:
                    issues.append(ValidationIssue(
                        key=var.key, severity="error",
                        message=f"Required variable is not set. {var.description}",
                    ))

        # Check insecure defaults
        insecure = {"MESHFLOW_WEBHOOK_SECRET": {"change-me", "change-me-in-production", ""}}
        for key, bad_vals in insecure.items():
            val = parsed.get(key, "")
            if val.lower() in bad_vals or val in bad_vals:
                issues.append(ValidationIssue(
                    key=key, severity="warning",
                    message="Default or insecure value in use.",
                ))

        # Check unknown vars
        known = {v.key for v in _VARS}
        for key in parsed:
            if key not in known and not key.startswith("MESHFLOW_RATE_LIMIT_TENANT_"):
                issues.append(ValidationIssue(
                    key=key, severity="warning",
                    message="Unknown variable — may be a typo.",
                ))

        return issues

    # ── Internals ──────────────────────────────────────────────────────────────

    def _resolve(self, var: EnvVar) -> str:
        """Determine the value for a variable in priority order."""
        # 1. Explicit override
        if var.key in self._overrides:
            return self._overrides[var.key]
        # 2. Current environment
        env_val = os.environ.get(var.key, "")
        if env_val:
            return env_val
        # 3. Auto-generate secrets
        if var.secret and self._auto_secrets and var.key == "MESHFLOW_WEBHOOK_SECRET":
            return secrets.token_hex(32)
        # 4. Default
        return var.default
