"""Built-in tool library for MeshFlow agents.

10 essential tools covering the most common agent use cases.
All tools are registered in the global_registry on import.

Tools:
  web_search    — DuckDuckGo Instant Answer API (free, no API key)
  web_fetch     — Fetch and strip HTML from a URL
  python_repl   — Execute Python in a subprocess sandbox (5s timeout)
  read_file     — Read a local file (restricted to MESHFLOW_WORKSPACE_DIR)
  write_file    — Write a local file (same restriction)
  shell         — Run a shell command (subprocess, 10s timeout, blocklist enforced)
  json_query    — Query JSON data with a simple path expression
  http_request  — Generic HTTP tool (GET/POST/PUT/DELETE)
  datetime_now  — Return current ISO timestamp
  calculator    — Safe arithmetic expression evaluator (no eval())

Usage::

    from meshflow.tools.builtins import *  # registers all tools in global_registry
    from meshflow.tools.registry import global_registry
    calc = global_registry.get("calculator")
    result = await calc.call(expression="2 ** 10")
"""

from __future__ import annotations

import ast
import json
import operator
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable, cast

from meshflow.core.schemas import RiskTier
from meshflow.tools.registry import tool

_WORKSPACE = os.environ.get("MESHFLOW_WORKSPACE_DIR", os.getcwd())

_SHELL_BLOCKLIST = [
    "rm -rf",
    "rm -fr",
    "sudo ",
    "curl | sh",
    "wget | sh",
    "mkfs",
    "dd if=",
    ":(){ :|:& };:",
    "> /dev/",
]


# ── 1. web_search ─────────────────────────────────────────────────────────────


@tool(
    name="web_search",
    description="Search the web using DuckDuckGo Instant Answer API",
    risk=RiskTier.EXTERNAL_IO,
    tags=["search", "web"],
)
async def web_search(query: str) -> str:
    """Search the web for a query using DuckDuckGo."""
    try:
        import httpx

        url = "https://api.duckduckgo.com/"
        params = {"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params)
            data = response.json()
        abstract = data.get("AbstractText", "")
        related = [r.get("Text", "") for r in data.get("RelatedTopics", [])[:3] if "Text" in r]
        parts = [abstract] if abstract else []
        parts.extend(related)
        return "\n".join(parts) if parts else f"No instant answer found for: {query}"
    except ImportError:
        return "web_search requires httpx: pip install httpx"
    except Exception as exc:
        return f"Search error: {exc}"


# ── 2. web_fetch ──────────────────────────────────────────────────────────────


@tool(
    name="web_fetch",
    description="Fetch a URL and return plain text content",
    risk=RiskTier.EXTERNAL_IO,
    tags=["web", "fetch"],
)
async def web_fetch(url: str) -> str:
    """Fetch a URL and strip HTML tags to return plain text."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": "MeshFlowBot/1.0"})
            response.raise_for_status()
            html = response.text
        # Strip HTML tags
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:8000]
    except ImportError:
        return "web_fetch requires httpx: pip install httpx"
    except Exception as exc:
        return f"Fetch error: {exc}"


# ── 3. python_repl ────────────────────────────────────────────────────────────


@tool(
    name="python_repl",
    description="Execute Python code in a sandbox subprocess",
    risk=RiskTier.EXTERNAL_IO,
    tags=["code", "python"],
)
async def python_repl(code: str) -> str:
    """Run Python code in a subprocess with a 5-second timeout. Returns stdout + stderr."""
    import asyncio

    try:
        proc = await asyncio.create_subprocess_exec(
            "python3",
            "-c",
            code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            return "Error: execution timed out after 5 seconds"
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        result = out
        if err:
            result += f"\n[stderr]\n{err}"
        return result[:4000] or "(no output)"
    except Exception as exc:
        return f"Execution error: {exc}"


# ── 4. read_file ──────────────────────────────────────────────────────────────


@tool(
    name="read_file",
    description="Read a local file (restricted to workspace directory)",
    risk=RiskTier.READ_ONLY,
    tags=["file", "read"],
)
async def read_file(path: str) -> str:
    """Read a file from the workspace. Raises if path escapes the workspace."""
    resolved = os.path.realpath(os.path.join(_WORKSPACE, path))
    if not resolved.startswith(os.path.realpath(_WORKSPACE)):
        return f"Access denied: path '{path}' is outside the workspace directory."
    try:
        with open(resolved, encoding="utf-8", errors="replace") as f:
            return f.read(50_000)
    except FileNotFoundError:
        return f"File not found: {path}"
    except Exception as exc:
        return f"Read error: {exc}"


# ── 5. write_file ─────────────────────────────────────────────────────────────


@tool(
    name="write_file",
    description="Write content to a file in the workspace directory",
    risk=RiskTier.INTERNAL,
    tags=["file", "write"],
)
async def write_file(path: str, content: str) -> str:
    """Write content to a file. Creates parent directories if needed."""
    resolved = os.path.realpath(os.path.join(_WORKSPACE, path))
    if not resolved.startswith(os.path.realpath(_WORKSPACE)):
        return f"Access denied: path '{path}' is outside the workspace directory."
    try:
        os.makedirs(os.path.dirname(resolved), exist_ok=True)
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Written {len(content)} characters to {path}"
    except Exception as exc:
        return f"Write error: {exc}"


# ── 6. shell ──────────────────────────────────────────────────────────────────


@tool(
    name="shell",
    description="Execute a shell command (blocklisted dangerous commands)",
    risk=RiskTier.EXTERNAL_IO,
    tags=["shell", "system"],
)
async def shell(command: str) -> str:
    """Run a shell command with a 10-second timeout. Dangerous patterns are blocked."""
    for blocked in _SHELL_BLOCKLIST:
        if blocked in command:
            return f"Blocked: command contains disallowed pattern '{blocked}'"
    import asyncio

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=_WORKSPACE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        except asyncio.TimeoutError:
            proc.kill()
            return "Error: command timed out after 10 seconds"
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        result = out
        if err:
            result += f"\n[stderr]\n{err}"
        return result[:4000] or "(no output)"
    except Exception as exc:
        return f"Shell error: {exc}"


# ── 7. json_query ─────────────────────────────────────────────────────────────


@tool(
    name="json_query",
    description="Query JSON data with a dot-path expression",
    risk=RiskTier.READ_ONLY,
    tags=["json", "data"],
)
async def json_query(data: str, path: str) -> str:
    """Query JSON data with a simple dot-path (e.g. 'results.0.name').

    Supports array indexing with integers. Returns the value at the path.
    """
    try:
        obj = json.loads(data)
    except json.JSONDecodeError as exc:
        return f"JSON parse error: {exc}"
    parts = path.split(".")
    current: Any = obj
    for part in parts:
        if not part:
            continue
        try:
            if isinstance(current, list):
                current = current[int(part)]
            elif isinstance(current, dict):
                current = current[part]
            else:
                return f"Cannot navigate into {type(current).__name__} at path '{part}'"
        except (KeyError, IndexError, ValueError) as exc:
            return f"Path error at '{part}': {exc}"
    return json.dumps(current, indent=2) if isinstance(current, (dict, list)) else str(current)


# ── 8. http_request ───────────────────────────────────────────────────────────


@tool(
    name="http_request",
    description="Make an HTTP request (GET, POST, PUT, DELETE)",
    risk=RiskTier.EXTERNAL_IO,
    tags=["http", "api", "web"],
)
async def http_request(method: str, url: str, body: str = "", headers_json: str = "") -> str:
    """Make an HTTP request and return the response body.

    method: GET, POST, PUT, DELETE, PATCH
    body: optional JSON string for request body
    headers_json: optional JSON string of extra headers
    """
    try:
        import httpx

        method = method.upper()
        extra_headers: dict[str, str] = {}
        if headers_json:
            try:
                extra_headers = json.loads(headers_json)
            except json.JSONDecodeError:
                pass
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            req_kwargs: dict[str, Any] = {"headers": extra_headers}
            if body and method in ("POST", "PUT", "PATCH"):
                req_kwargs["content"] = body.encode()
                if "Content-Type" not in extra_headers:
                    req_kwargs["headers"] = {**extra_headers, "Content-Type": "application/json"}
            response = await client.request(method, url, **req_kwargs)
            return f"HTTP {response.status_code}\n{response.text[:4000]}"
    except ImportError:
        return "http_request requires httpx: pip install httpx"
    except Exception as exc:
        return f"HTTP error: {exc}"


# ── 9. datetime_now ───────────────────────────────────────────────────────────


@tool(
    name="datetime_now",
    description="Return the current UTC date and time as ISO 8601",
    risk=RiskTier.READ_ONLY,
    tags=["time", "date"],
)
async def datetime_now() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


# ── 10. calculator ────────────────────────────────────────────────────────────

_SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(node: Any) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError(f"Unsupported literal: {node.value!r}")
    if isinstance(node, ast.BinOp):
        op = _SAFE_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return float(
            cast(Callable[[float, float], float], op)(_safe_eval(node.left), _safe_eval(node.right))
        )
    if isinstance(node, ast.UnaryOp):
        op = _SAFE_OPS.get(type(node.op))
        if op is None:
            raise ValueError("Unsupported unary operator")
        return float(cast(Callable[[float], float], op)(_safe_eval(node.operand)))
    raise ValueError(f"Unsupported expression: {ast.dump(node)}")


@tool(
    name="calculator",
    description="Evaluate a safe arithmetic expression",
    risk=RiskTier.READ_ONLY,
    tags=["math", "arithmetic"],
)
async def calculator(expression: str) -> str:
    """Evaluate an arithmetic expression safely (no eval()). Supports +,-,*,/,**,%."""
    try:
        tree = ast.parse(expression.strip(), mode="eval")
        result = _safe_eval(tree.body)
        return str(int(result) if result == int(result) else result)
    except Exception as exc:
        return f"Calculator error: {exc}"
