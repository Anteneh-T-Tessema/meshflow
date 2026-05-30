"""meshflow doctor — pre-flight production readiness check.

Runs a battery of checks against the current environment and reports
which pass, which warn, and which are hard failures.

Usage::

    from meshflow.deploy.doctor import Doctor

    doc = Doctor()
    report = doc.run()
    print(report.summary())
    sys.exit(0 if report.ok else 1)

CLI::

    meshflow doctor
    meshflow doctor --fix          # auto-fix safe issues (generate webhook secret)
    meshflow doctor --json         # machine-readable output
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import sqlite3
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CheckStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    message: str
    detail: str = ""
    fix_hint: str = ""

    @property
    def icon(self) -> str:
        return {"pass": "✓", "warn": "⚠", "fail": "✗", "skip": "–"}[self.status]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "detail": self.detail,
            "fix_hint": self.fix_hint,
        }


@dataclass
class DoctorReport:
    checks: list[CheckResult] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return not any(c.status == CheckStatus.FAIL for c in self.checks)

    @property
    def warnings(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == CheckStatus.WARN]

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == CheckStatus.FAIL]

    @property
    def passed(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == CheckStatus.PASS]

    def summary(self, *, color: bool = False) -> str:
        lines = [
            "",
            "  MeshFlow Doctor",
            f"  {'─' * 40}",
        ]
        for c in self.checks:
            icon = c.icon
            line = f"  {icon}  {c.name:<35} {c.message}"
            if c.detail:
                line += f"\n     {c.detail}"
            lines.append(line)
            if c.fix_hint and c.status != CheckStatus.PASS:
                lines.append(f"     → {c.fix_hint}")
        lines += [
            f"  {'─' * 40}",
            f"  {len(self.passed)} passed  {len(self.warnings)} warnings  "
            f"{len(self.failures)} failures  ({self.duration_ms:.0f}ms)",
            "  " + ("Ready for production." if self.ok else "Fix failures before deploying."),
            "",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [c.to_dict() for c in self.checks],
            "n_passed": len(self.passed),
            "n_warnings": len(self.warnings),
            "n_failures": len(self.failures),
            "duration_ms": round(self.duration_ms, 1),
        }


class Doctor:
    """Run all pre-flight checks and return a :class:`DoctorReport`.

    Parameters
    ----------
    port:      The port meshflow serve will listen on (checked for availability).
    db_path:   The ledger DB path to validate write access for.
    data_dir:  Directory that must have adequate free space.
    """

    def __init__(
        self,
        port: int = 8000,
        db_path: str = "meshflow_runs.db",
        data_dir: str = ".",
    ) -> None:
        self._port = port
        self._db_path = db_path
        self._data_dir = data_dir

    def run(self) -> DoctorReport:
        """Execute all checks and return the aggregated report."""
        t0 = time.monotonic()
        checks = [
            self._check_python_version(),
            self._check_llm_provider(),
            self._check_webhook_secret(),
            self._check_db_write(),
            self._check_port(),
            self._check_disk_space(),
            self._check_memory(),
            self._check_policy_file(),
            self._check_dependencies(),
            self._check_docker(),
        ]
        return DoctorReport(
            checks=checks,
            duration_ms=(time.monotonic() - t0) * 1000,
        )

    # ── Individual checks ──────────────────────────────────────────────────────

    def _check_python_version(self) -> CheckResult:
        major, minor = platform.python_version_tuple()[:2]
        ver = f"{major}.{minor}"
        if int(major) < 3 or (int(major) == 3 and int(minor) < 11):
            return CheckResult(
                name="Python version",
                status=CheckStatus.FAIL,
                message=f"Python {ver} — requires ≥ 3.11",
                fix_hint="Upgrade to Python 3.11 or later.",
            )
        return CheckResult(
            name="Python version",
            status=CheckStatus.PASS,
            message=f"Python {ver}",
        )

    def _check_llm_provider(self) -> CheckResult:
        providers = {
            "ANTHROPIC_API_KEY": "Anthropic",
            "OPENAI_API_KEY": "OpenAI",
            "GEMINI_API_KEY": "Gemini",
            "AWS_ACCESS_KEY_ID": "AWS Bedrock",
            "AZURE_OPENAI_API_KEY": "Azure OpenAI",
        }
        found = [name for env, name in providers.items() if os.environ.get(env)]
        if not found:
            return CheckResult(
                name="LLM provider",
                status=CheckStatus.FAIL,
                message="No API key found",
                detail=f"Checked: {', '.join(providers)}",
                fix_hint="Set at least one provider key, e.g. ANTHROPIC_API_KEY=sk-ant-...",
            )
        return CheckResult(
            name="LLM provider",
            status=CheckStatus.PASS,
            message=f"Found: {', '.join(found)}",
        )

    def _check_webhook_secret(self) -> CheckResult:
        secret = os.environ.get("MESHFLOW_WEBHOOK_SECRET", "")
        insecure = {"", "change-me", "change-me-in-production", "secret", "test"}
        if not secret:
            return CheckResult(
                name="Webhook secret",
                status=CheckStatus.WARN,
                message="MESHFLOW_WEBHOOK_SECRET not set",
                fix_hint="Set MESHFLOW_WEBHOOK_SECRET to a random 32+ char string.",
            )
        if secret.lower() in insecure:
            return CheckResult(
                name="Webhook secret",
                status=CheckStatus.WARN,
                message="Default/insecure webhook secret in use",
                fix_hint="Replace with: python -c \"import secrets; print(secrets.token_hex(32))\"",
            )
        return CheckResult(
            name="Webhook secret",
            status=CheckStatus.PASS,
            message="Secret is set and non-default",
        )

    def _check_db_write(self) -> CheckResult:
        if self._db_path == ":memory:":
            return CheckResult(
                name="Database",
                status=CheckStatus.PASS,
                message=":memory: (test mode)",
            )
        # Check parent directory is writable
        parent = os.path.dirname(os.path.abspath(self._db_path)) or "."
        if not os.access(parent, os.W_OK):
            return CheckResult(
                name="Database",
                status=CheckStatus.FAIL,
                message=f"Directory not writable: {parent}",
                fix_hint=f"Run: chmod 755 {parent}",
            )
        # Try opening and writing to the DB
        try:
            conn = sqlite3.connect(self._db_path, timeout=3)
            conn.execute("CREATE TABLE IF NOT EXISTS _doctor_ping (ts REAL)")
            conn.execute("INSERT INTO _doctor_ping VALUES (?)", (time.time(),))
            conn.execute("DROP TABLE _doctor_ping")
            conn.commit()
            conn.close()
            return CheckResult(
                name="Database",
                status=CheckStatus.PASS,
                message=f"SQLite writable: {self._db_path}",
            )
        except Exception as exc:
            return CheckResult(
                name="Database",
                status=CheckStatus.FAIL,
                message=f"Cannot write to DB: {exc}",
                fix_hint="Check file permissions or use --ledger /data/runs.db",
            )

    def _check_port(self) -> CheckResult:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            result = s.connect_ex(("127.0.0.1", self._port))
        if result == 0:
            return CheckResult(
                name=f"Port {self._port}",
                status=CheckStatus.WARN,
                message=f"Port {self._port} is already in use",
                fix_hint=f"Kill the process or pass --port <other> to meshflow serve.",
            )
        return CheckResult(
            name=f"Port {self._port}",
            status=CheckStatus.PASS,
            message=f"Port {self._port} is free",
        )

    def _check_disk_space(self) -> CheckResult:
        try:
            usage = shutil.disk_usage(self._data_dir)
            free_gb = usage.free / 1024 ** 3
            if free_gb < 0.5:
                return CheckResult(
                    name="Disk space",
                    status=CheckStatus.FAIL,
                    message=f"Only {free_gb:.1f} GB free in {self._data_dir}",
                    fix_hint="Free up disk space before deploying.",
                )
            if free_gb < 2.0:
                return CheckResult(
                    name="Disk space",
                    status=CheckStatus.WARN,
                    message=f"{free_gb:.1f} GB free (< 2 GB recommended)",
                )
            return CheckResult(
                name="Disk space",
                status=CheckStatus.PASS,
                message=f"{free_gb:.1f} GB free",
            )
        except Exception as exc:
            return CheckResult(
                name="Disk space",
                status=CheckStatus.SKIP,
                message=f"Cannot check: {exc}",
            )

    def _check_memory(self) -> CheckResult:
        try:
            import psutil  # optional
            vm = psutil.virtual_memory()
            avail_mb = vm.available / 1024 ** 2
            if avail_mb < 256:
                return CheckResult(
                    name="Available memory",
                    status=CheckStatus.FAIL,
                    message=f"Only {avail_mb:.0f} MB available",
                    fix_hint="Close other processes or provision more RAM.",
                )
            if avail_mb < 512:
                return CheckResult(
                    name="Available memory",
                    status=CheckStatus.WARN,
                    message=f"{avail_mb:.0f} MB available (< 512 MB recommended)",
                )
            return CheckResult(
                name="Available memory",
                status=CheckStatus.PASS,
                message=f"{avail_mb:.0f} MB available",
            )
        except ImportError:
            # psutil not installed — use /proc/meminfo on Linux
            try:
                with open("/proc/meminfo") as f:
                    for line in f:
                        if line.startswith("MemAvailable"):
                            avail_kb = int(line.split()[1])
                            avail_mb = avail_kb / 1024
                            status = CheckStatus.PASS if avail_mb >= 512 else CheckStatus.WARN
                            return CheckResult(
                                name="Available memory",
                                status=status,
                                message=f"{avail_mb:.0f} MB available",
                            )
            except Exception:
                pass
            return CheckResult(
                name="Available memory",
                status=CheckStatus.SKIP,
                message="psutil not installed — install for memory checks",
                fix_hint="pip install psutil",
            )

    def _check_policy_file(self) -> CheckResult:
        path = os.environ.get("MESHFLOW_POLICY_FILE", "")
        if not path:
            return CheckResult(
                name="Policy file",
                status=CheckStatus.SKIP,
                message="MESHFLOW_POLICY_FILE not set (using defaults)",
            )
        if not os.path.exists(path):
            return CheckResult(
                name="Policy file",
                status=CheckStatus.FAIL,
                message=f"Policy file not found: {path}",
                fix_hint=f"Create {path} or unset MESHFLOW_POLICY_FILE.",
            )
        try:
            import yaml  # type: ignore[import]
            with open(path) as f:
                yaml.safe_load(f)
            return CheckResult(
                name="Policy file",
                status=CheckStatus.PASS,
                message=f"Valid YAML: {path}",
            )
        except ImportError:
            return CheckResult(
                name="Policy file",
                status=CheckStatus.PASS,
                message=f"Found (yaml not installed for validation): {path}",
            )
        except Exception as exc:
            return CheckResult(
                name="Policy file",
                status=CheckStatus.FAIL,
                message=f"Invalid YAML: {exc}",
                fix_hint="Fix the YAML syntax in your policy file.",
            )

    def _check_dependencies(self) -> CheckResult:
        required = ["aiohttp", "cryptography"]
        optional = ["anthropic", "openai", "psutil", "yaml"]
        missing_req: list[str] = []
        missing_opt: list[str] = []

        for pkg in required:
            try:
                __import__(pkg)
            except ImportError:
                missing_req.append(pkg)

        for pkg in optional:
            try:
                __import__(pkg)
            except ImportError:
                missing_opt.append(pkg)

        if missing_req:
            return CheckResult(
                name="Dependencies",
                status=CheckStatus.FAIL,
                message=f"Missing required: {', '.join(missing_req)}",
                fix_hint=f"pip install {' '.join(missing_req)}",
            )
        if missing_opt:
            return CheckResult(
                name="Dependencies",
                status=CheckStatus.WARN,
                message=f"Optional not installed: {', '.join(missing_opt)}",
                detail="Some features may be unavailable.",
                fix_hint=f"pip install {' '.join(missing_opt)}",
            )
        return CheckResult(
            name="Dependencies",
            status=CheckStatus.PASS,
            message="All required packages present",
        )

    def _check_docker(self) -> CheckResult:
        docker = shutil.which("docker")
        if not docker:
            return CheckResult(
                name="Docker",
                status=CheckStatus.SKIP,
                message="docker not in PATH (required for meshflow deploy)",
                fix_hint="Install Docker: https://docs.docker.com/get-docker/",
            )
        import subprocess
        try:
            result = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                version = result.stdout.strip()
                return CheckResult(
                    name="Docker",
                    status=CheckStatus.PASS,
                    message=f"Docker {version}",
                )
            return CheckResult(
                name="Docker",
                status=CheckStatus.WARN,
                message="Docker found but daemon not running",
                fix_hint="Start the Docker daemon.",
            )
        except Exception as exc:
            return CheckResult(
                name="Docker",
                status=CheckStatus.WARN,
                message=f"Docker check failed: {exc}",
            )
