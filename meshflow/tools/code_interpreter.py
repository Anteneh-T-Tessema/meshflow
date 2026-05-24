"""Sandboxed Python code interpreter — zero external dependencies.

Each call spawns a fresh subprocess for isolation. Supports optional
module allow-listing and per-call environment variable injection.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CodeResult:
    """Result of a sandboxed code execution."""

    stdout: str = ""
    stderr: str = ""
    return_value: str = ""
    error: str = ""
    execution_time_ms: float = 0.0
    timed_out: bool = False

    @property
    def success(self) -> bool:
        return not self.error and not self.timed_out

    def __str__(self) -> str:
        if not self.success:
            return f"[Error] {self.error}"
        parts = [self.stdout.strip(), self.return_value.strip()]
        return "\n".join(p for p in parts if p) or "(no output)"


class CodeInterpreter:
    """Run Python snippets in a sandboxed subprocess.

    Zero external dependencies — stdlib subprocess + tempfile only.
    Each :meth:`run` call spawns a fresh interpreter for isolation.

    Parameters
    ----------
    timeout_s:        Default wall-clock timeout per call.
    allowed_modules:  If set, restrict imports to this allow-list.
    env_vars:         Extra environment variables injected into each call.
    """

    def __init__(
        self,
        timeout_s: float = 10.0,
        allowed_modules: list[str] | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> None:
        self.timeout_s = timeout_s
        self.allowed_modules = allowed_modules
        self._base_env: dict[str, str] = env_vars or {}

    def run(
        self,
        code: str,
        *,
        timeout_s: float | None = None,
        env: dict[str, str] | None = None,
    ) -> CodeResult:
        """Execute *code* in a fresh subprocess and return the result."""
        t = timeout_s if timeout_s is not None else self.timeout_s
        start = time.monotonic()

        preamble = self._build_preamble()
        full_code = preamble + "\n" + code

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(full_code)
            path = f.name

        try:
            merged_env = {**os.environ, **self._base_env, **(env or {})}
            proc = subprocess.run(
                [sys.executable, path],
                capture_output=True,
                text=True,
                timeout=t,
                env=merged_env,
            )
            elapsed = (time.monotonic() - start) * 1000.0
            err = proc.stderr.strip() if proc.returncode != 0 else ""
            return CodeResult(
                stdout=proc.stdout,
                stderr=proc.stderr,
                error=err,
                execution_time_ms=elapsed,
            )
        except subprocess.TimeoutExpired:
            return CodeResult(
                error=f"Execution timed out after {t}s",
                timed_out=True,
                execution_time_ms=t * 1000.0,
            )
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _build_preamble(self) -> str:
        if self.allowed_modules is None:
            return ""
        import json
        allowed_json = json.dumps(self.allowed_modules)
        return f"""
import builtins as _bi
_allowed = {allowed_json}
_orig_import = _bi.__import__
def _restricted_import(name, *args, **kwargs):
    top = name.split('.')[0]
    _safe = ('builtins', '__future__', '_thread', '_warnings', 'abc',
             'codecs', 'collections', 'contextlib', 'copy', 'dataclasses',
             'datetime', 'enum', 'functools', 'gc', 'importlib', 'io',
             'itertools', 'operator', 'os', 'pathlib', 're', 'sys',
             'textwrap', 'threading', 'time', 'traceback', 'types',
             'typing', 'warnings', 'weakref')
    if top not in _allowed and top not in _safe:
        raise ImportError(f"Module '{{name}}' is not in the allow-list")
    return _orig_import(name, *args, **kwargs)
_bi.__import__ = _restricted_import
"""
