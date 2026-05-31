"""Sprint 28 — Code Interpreter: sandboxed Python execution."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meshflow.tools.code_interpreter import CodeInterpreter, CodeResult


# ── CodeResult ─────────────────────────────────────────────────────────────────

class TestCodeResult:
    def test_success_true_when_no_error(self):
        r = CodeResult(stdout="hi")
        assert r.success is True

    def test_success_false_on_error(self):
        r = CodeResult(error="boom")
        assert r.success is False

    def test_success_false_on_timeout(self):
        r = CodeResult(timed_out=True)
        assert r.success is False

    def test_str_returns_stdout(self):
        r = CodeResult(stdout="hello\n")
        assert str(r) == "hello"

    def test_str_returns_error_prefix_on_failure(self):
        r = CodeResult(error="ZeroDivisionError")
        assert "[Error]" in str(r)

    def test_str_no_output(self):
        r = CodeResult()
        assert str(r) == "(no output)"

    def test_str_return_value_appended(self):
        r = CodeResult(stdout="line1", return_value="42")
        assert "line1" in str(r)
        assert "42" in str(r)


# ── CodeInterpreter — basic execution ─────────────────────────────────────────

class TestCodeInterpreterBasic:
    def test_hello_world(self):
        ci = CodeInterpreter()
        result = ci.run("print('hello')")
        assert result.success
        assert "hello" in result.stdout

    def test_arithmetic(self):
        ci = CodeInterpreter()
        result = ci.run("print(2 + 2)")
        assert result.success
        assert "4" in result.stdout

    def test_multiline_code(self):
        ci = CodeInterpreter()
        code = "x = 10\ny = 20\nprint(x + y)"
        result = ci.run(code)
        assert result.success
        assert "30" in result.stdout

    def test_stdlib_import(self):
        ci = CodeInterpreter()
        result = ci.run("import math; print(math.pi)")
        assert result.success
        assert "3.14" in result.stdout

    def test_error_captured(self):
        ci = CodeInterpreter()
        result = ci.run("raise ValueError('bad input')")
        assert not result.success
        assert "ValueError" in result.error or "ValueError" in result.stderr

    def test_syntax_error_captured(self):
        ci = CodeInterpreter()
        result = ci.run("def broken(:\n    pass")
        assert not result.success

    def test_execution_time_positive(self):
        ci = CodeInterpreter()
        result = ci.run("print('x')")
        assert result.execution_time_ms >= 0

    def test_empty_code(self):
        ci = CodeInterpreter()
        result = ci.run("")
        assert result.success


# ── CodeInterpreter — timeout ─────────────────────────────────────────────────

class TestCodeInterpreterTimeout:
    def test_timeout_kills_long_running(self):
        ci = CodeInterpreter(timeout_s=0.5)
        result = ci.run("import time; time.sleep(10)")
        assert result.timed_out
        assert not result.success

    def test_per_call_timeout_override(self):
        ci = CodeInterpreter(timeout_s=30.0)
        result = ci.run("import time; time.sleep(10)", timeout_s=0.5)
        assert result.timed_out


# ── CodeInterpreter — env injection ──────────────────────────────────────────

class TestCodeInterpreterEnv:
    def test_env_var_visible_in_subprocess(self):
        ci = CodeInterpreter(env_vars={"MY_SECRET": "abc123"})
        result = ci.run("import os; print(os.environ['MY_SECRET'])")
        assert result.success
        assert "abc123" in result.stdout

    def test_per_call_env_override(self):
        ci = CodeInterpreter()
        result = ci.run(
            "import os; print(os.environ.get('PER_CALL', 'missing'))",
            env={"PER_CALL": "present"},
        )
        assert result.success
        assert "present" in result.stdout


# ── CodeInterpreter — module allow-list ──────────────────────────────────────

class TestCodeInterpreterAllowList:
    def test_allowed_module_works(self):
        ci = CodeInterpreter(allowed_modules=["math", "os", "sys"])
        result = ci.run("import math; print(math.sqrt(9))")
        assert result.success
        assert "3.0" in result.stdout

    def test_blocked_module_raises_import_error(self):
        ci = CodeInterpreter(allowed_modules=["math"])
        result = ci.run("import subprocess")
        assert not result.success
        assert "not in the allow-list" in (result.error + result.stderr)


# ── CodeInterpreter — isolation ───────────────────────────────────────────────

class TestCodeInterpreterIsolation:
    def test_each_run_independent(self):
        ci = CodeInterpreter()
        ci.run("x = 999")               # define x in one run
        result = ci.run("print(globals().get('x', 'gone'))")  # second run is fresh
        assert result.success
        # x should not be present (fresh subprocess)
        assert "999" not in result.stdout


# ── Public API ────────────────────────────────────────────────────────────────

class TestPublicAPI:
    def test_imported_from_tools(self):
        from meshflow.tools.code_interpreter import CodeInterpreter, CodeResult
        assert CodeInterpreter is not None
        assert CodeResult is not None
