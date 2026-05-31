"""Tests for the built-in tool library (meshflow/tools/builtins.py).

All network/subprocess calls are mocked so tests run offline without API keys.
Tools decorated with @tool become Tool objects — call via tool.call(**kwargs).
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _get_tool(name: str):
    import meshflow.tools.builtins  # noqa: F401 — registers as side-effect
    from meshflow.tools.registry import global_registry
    return global_registry.get(name)


# ── calculator ────────────────────────────────────────────────────────────────


class TestCalculator:
    @pytest.mark.asyncio
    async def test_basic_arithmetic(self) -> None:
        calc = _get_tool("calculator")
        assert "42" in await calc.call(expression="6 * 7")

    @pytest.mark.asyncio
    async def test_power(self) -> None:
        calc = _get_tool("calculator")
        assert "1024" in await calc.call(expression="2 ** 10")

    @pytest.mark.asyncio
    async def test_float_division(self) -> None:
        calc = _get_tool("calculator")
        result = await calc.call(expression="10 / 3")
        assert "3.3" in result

    @pytest.mark.asyncio
    async def test_invalid_expression(self) -> None:
        calc = _get_tool("calculator")
        result = await calc.call(expression="__import__('os').system('ls')")
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_addition(self) -> None:
        calc = _get_tool("calculator")
        result = await calc.call(expression="100 + 200 + 300")
        assert "600" in result

    @pytest.mark.asyncio
    async def test_nested_parens(self) -> None:
        calc = _get_tool("calculator")
        result = await calc.call(expression="(3 + 4) * (2 + 1)")
        assert "21" in result


# ── datetime_now ──────────────────────────────────────────────────────────────


class TestDatetimeNow:
    @pytest.mark.asyncio
    async def test_returns_string(self) -> None:
        dt = _get_tool("datetime_now")
        result = await dt.call()
        assert isinstance(result, str)
        assert len(result) > 10

    @pytest.mark.asyncio
    async def test_iso_format(self) -> None:
        dt = _get_tool("datetime_now")
        result = await dt.call()
        from datetime import datetime
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
        assert parsed.year >= 2025


# ── json_query ────────────────────────────────────────────────────────────────


class TestJsonQuery:
    @pytest.mark.asyncio
    async def test_top_level_key(self) -> None:
        jq = _get_tool("json_query")
        data = json.dumps({"name": "Alice", "age": 30})
        result = await jq.call(data=data, path="name")
        assert "Alice" in result

    @pytest.mark.asyncio
    async def test_nested_path(self) -> None:
        jq = _get_tool("json_query")
        data = json.dumps({"user": {"email": "alice@example.com"}})
        result = await jq.call(data=data, path="user.email")
        assert "alice@example.com" in result

    @pytest.mark.asyncio
    async def test_array_index(self) -> None:
        jq = _get_tool("json_query")
        data = json.dumps({"items": ["a", "b", "c"]})
        result = await jq.call(data=data, path="items.1")
        assert "b" in result

    @pytest.mark.asyncio
    async def test_invalid_json_returns_error(self) -> None:
        jq = _get_tool("json_query")
        result = await jq.call(data="not json", path="key")
        assert "error" in result.lower() or "parse" in result.lower()

    @pytest.mark.asyncio
    async def test_missing_key_returns_error(self) -> None:
        jq = _get_tool("json_query")
        data = json.dumps({"x": 1})
        result = await jq.call(data=data, path="nonexistent")
        assert "error" in result.lower() or "path" in result.lower()


# ── shell (blocklist) ─────────────────────────────────────────────────────────


class TestShell:
    @pytest.mark.asyncio
    async def test_rm_rf_blocked(self) -> None:
        sh = _get_tool("shell")
        result = await sh.call(command="rm -rf /tmp/test")
        assert "Blocked" in result or "blocked" in result

    @pytest.mark.asyncio
    async def test_sudo_blocked(self) -> None:
        sh = _get_tool("shell")
        result = await sh.call(command="sudo apt-get install curl")
        assert "Blocked" in result or "blocked" in result

    @pytest.mark.asyncio
    async def test_fork_bomb_blocked(self) -> None:
        sh = _get_tool("shell")
        result = await sh.call(command=":(){ :|:& };:")
        assert "Blocked" in result or "blocked" in result

    @pytest.mark.asyncio
    async def test_safe_command_runs(self) -> None:
        sh = _get_tool("shell")
        with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_sh:
            mock_proc = MagicMock()
            mock_proc.communicate = AsyncMock(return_value=(b"hello\n", b""))
            mock_proc.kill = MagicMock()
            mock_sh.return_value = mock_proc
            result = await sh.call(command="echo hello")
        assert "hello" in result


# ── web_search (mocked) ───────────────────────────────────────────────────────


class TestWebSearch:
    @pytest.mark.asyncio
    async def test_returns_abstract_text(self) -> None:
        ws = _get_tool("web_search")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "AbstractText": "HIPAA is a healthcare data privacy law.",
            "RelatedTopics": [],
        }
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await ws.call(query="HIPAA")
        assert "HIPAA" in result

    @pytest.mark.asyncio
    async def test_related_topics_included(self) -> None:
        ws = _get_tool("web_search")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "AbstractText": "",
            "RelatedTopics": [{"Text": "related result"}],
        }
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await ws.call(query="test")
        assert "related result" in result

    @pytest.mark.asyncio
    async def test_no_results_fallback(self) -> None:
        ws = _get_tool("web_search")

        mock_response = MagicMock()
        mock_response.json.return_value = {"AbstractText": "", "RelatedTopics": []}
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await ws.call(query="xyzzy_nothing_here")
        assert "No instant answer" in result


# ── web_fetch (mocked) ────────────────────────────────────────────────────────


class TestWebFetch:
    @pytest.mark.asyncio
    async def test_strips_html_tags(self) -> None:
        wf = _get_tool("web_fetch")

        mock_response = MagicMock()
        mock_response.text = "<html><body><p>Hello World</p><script>bad()</script></body></html>"
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await wf.call(url="https://example.com")
        assert "Hello World" in result
        assert "<html>" not in result


# ── python_repl (mocked subprocess) ──────────────────────────────────────────


class TestPythonRepl:
    @pytest.mark.asyncio
    async def test_runs_simple_code(self) -> None:
        repl = _get_tool("python_repl")

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_proc = MagicMock()
            mock_proc.communicate = AsyncMock(return_value=(b"42\n", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc
            result = await repl.call(code="print(6 * 7)")
        assert "42" in result

    @pytest.mark.asyncio
    async def test_handles_stderr(self) -> None:
        repl = _get_tool("python_repl")

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_proc = MagicMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b"NameError: x\n"))
            mock_proc.returncode = 1
            mock_exec.return_value = mock_proc
            result = await repl.call(code="print(x)")
        assert isinstance(result, str)


# ── http_request (mocked) ─────────────────────────────────────────────────────


class TestHttpRequest:
    @pytest.mark.asyncio
    async def test_get_request_returns_body(self) -> None:
        http = _get_tool("http_request")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"status": "ok"}'
        mock_response.headers = {}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await http.call(method="GET", url="https://api.example.com")
        assert "200" in result or "ok" in result.lower()


# ── read_file / write_file ────────────────────────────────────────────────────


class TestReadWriteFile:
    @pytest.mark.asyncio
    async def test_write_then_read(self, tmp_path: Any) -> None:
        rf = _get_tool("read_file")
        wf_tool = _get_tool("write_file")
        path = str(tmp_path / "mesh_test.txt")
        with patch("meshflow.tools.builtins._WORKSPACE", str(tmp_path)):
            await wf_tool.call(path=path, content="Hello MeshFlow")
            content = await rf.call(path=path)
        assert "Hello MeshFlow" in content

    @pytest.mark.asyncio
    async def test_read_outside_workspace_blocked(self, tmp_path: Any) -> None:
        rf = _get_tool("read_file")
        with patch("meshflow.tools.builtins._WORKSPACE", str(tmp_path)):
            result = await rf.call(path="/etc/passwd")
        assert "not allowed" in result.lower() or "outside" in result.lower() or "error" in result.lower()


# ── global_registry coverage ──────────────────────────────────────────────────


class TestGlobalRegistry:
    def test_all_ten_tools_registered(self) -> None:
        import meshflow.tools.builtins  # noqa: F401
        from meshflow.tools.registry import global_registry
        expected = [
            "web_search", "web_fetch", "python_repl", "read_file", "write_file",
            "shell", "json_query", "http_request", "datetime_now", "calculator",
        ]
        for name in expected:
            assert global_registry.get(name) is not None, f"Missing tool: {name}"

    def test_calculator_has_description(self) -> None:
        import meshflow.tools.builtins  # noqa: F401
        from meshflow.tools.registry import global_registry
        calc = global_registry.get("calculator")
        assert calc.description
        assert calc.risk is not None
