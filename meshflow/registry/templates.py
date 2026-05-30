"""AgentTemplate registry — share and reuse serialisable agent definitions (shared gap).

A ``AgentTemplate`` is a YAML-serialisable snapshot of an ``Agent`` spec.
``TemplateRegistry`` stores templates locally in ``~/.meshflow/templates/`` and
offers BM25-style search over their descriptions.

Usage::

    from meshflow.registry.templates import AgentTemplate, TemplateRegistry

    # Build from a live agent
    tmpl = AgentTemplate(
        name="market-researcher",
        role="researcher",
        model="claude-sonnet-4-6",
        system_prompt="You are an expert market researcher.",
        tools=["web_search"],
        description="Deep market research with web search and structured reports.",
        tags=["research", "market"],
    )

    # Save to local registry
    reg = TemplateRegistry()
    reg.publish(tmpl)

    # Discover
    results = reg.search("research market")
    agent = results[0].to_agent()
    result = await agent.run("What is the AI chip market size?")

    # CLI
    meshflow templates list
    meshflow templates publish my_agent.yaml
    meshflow templates pull market-researcher
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ── AgentTemplate ──────────────────────────────────────────────────────────────

@dataclass
class AgentTemplate:
    """Serialisable agent definition that can be stored and shared.

    Parameters
    ----------
    name:           Unique slug (kebab-case).
    role:           Agent role (planner/researcher/executor/critic/orchestrator/guardian).
    model:          Model string, e.g. ``"claude-sonnet-4-6"``.
    system_prompt:  System prompt override.
    tools:          Tool name strings.
    skills:         Built-in skill names.
    knowledge:      Knowledge source paths / URLs.
    description:    Human-readable description used for search.
    tags:           Categorisation tags.
    version:        Semver string.
    author:         Author identifier.
    metadata:       Arbitrary key/value pairs.
    """

    name: str
    role: str = "executor"
    model: str = ""
    system_prompt: str = ""
    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    knowledge: list[str] = field(default_factory=list)
    description: str = ""
    tags: list[str] = field(default_factory=list)
    version: str = "1.0.0"
    author: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Serialisation ──────────────────────────────────────────────────────────

    def to_yaml(self) -> str:
        """Serialise to YAML string."""
        data = {
            "name": self.name,
            "role": self.role,
            "model": self.model,
            "system_prompt": self.system_prompt,
            "tools": self.tools,
            "skills": self.skills,
            "knowledge": self.knowledge,
            "description": self.description,
            "tags": self.tags,
            "version": self.version,
            "author": self.author,
            "metadata": self.metadata,
        }
        return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)

    def to_dict(self) -> dict[str, Any]:
        return yaml.safe_load(self.to_yaml())

    @classmethod
    def from_yaml(cls, path: str) -> "AgentTemplate":
        """Load a template from a YAML file."""
        with open(path) as fh:
            data = yaml.safe_load(fh)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentTemplate":
        return cls(
            name=data.get("name", "unnamed"),
            role=data.get("role", "executor"),
            model=data.get("model", ""),
            system_prompt=data.get("system_prompt", ""),
            tools=list(data.get("tools", [])),
            skills=list(data.get("skills", [])),
            knowledge=list(data.get("knowledge", [])),
            description=data.get("description", ""),
            tags=list(data.get("tags", [])),
            version=str(data.get("version", "1.0.0")),
            author=data.get("author", ""),
            metadata=dict(data.get("metadata", {})),
        )

    # ── Instantiation ──────────────────────────────────────────────────────────

    def to_agent(self) -> Any:
        """Instantiate a live ``Agent`` from this template."""
        from meshflow.agents.builder import Agent

        return Agent(
            name=self.name,
            role=self.role,
            model=self.model,
            system_prompt=self.system_prompt,
            tools=[],   # string-only tool names not auto-resolved yet
            skills=list(self.skills),
            knowledge=list(self.knowledge),
        )

    def __str__(self) -> str:
        return f"AgentTemplate(name={self.name!r}, role={self.role!r}, v{self.version})"


# ── BM25 mini search ──────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _bm25_score(query_tokens: list[str], doc_tokens: list[str], avg_len: float, n_docs: int, df: dict[str, int]) -> float:
    K1, B = 1.5, 0.75
    tf: dict[str, int] = {}
    for t in doc_tokens:
        tf[t] = tf.get(t, 0) + 1
    doc_len = len(doc_tokens)
    score = 0.0
    for qt in query_tokens:
        f = tf.get(qt, 0)
        if f == 0:
            continue
        df_qt = df.get(qt, 0)
        if df_qt == 0:
            continue
        idf = math.log((n_docs - df_qt + 0.5) / (df_qt + 0.5) + 1)
        denom = f + K1 * (1 - B + B * doc_len / max(avg_len, 1))
        score += idf * f * (K1 + 1) / denom
    return score


# ── TemplateRegistry ──────────────────────────────────────────────────────────

class TemplateRegistry:
    """Local file-based registry stored under ``~/.meshflow/templates/``.

    Each template is a YAML file named ``<template-name>.yaml``.

    Parameters
    ----------
    registry_dir:
        Override the default ``~/.meshflow/templates/`` path.
    """

    def __init__(self, registry_dir: str | None = None) -> None:
        if registry_dir:
            self._dir = Path(registry_dir)
        else:
            self._dir = Path.home() / ".meshflow" / "templates"
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, name: str) -> Path:
        safe = re.sub(r"[^a-z0-9_\-]", "_", name.lower())
        return self._dir / f"{safe}.yaml"

    # ── Write ──────────────────────────────────────────────────────────────────

    def publish(self, template: AgentTemplate) -> Path:
        """Save *template* to the local registry.  Returns the written path."""
        path = self._path_for(template.name)
        path.write_text(template.to_yaml(), encoding="utf-8")
        return path

    def delete(self, name: str) -> bool:
        """Remove a template by name.  Returns True if it existed."""
        p = self._path_for(name)
        if p.exists():
            p.unlink()
            return True
        return False

    # ── Read ───────────────────────────────────────────────────────────────────

    def pull(self, name: str) -> AgentTemplate:
        """Retrieve a template by name.  Raises ``KeyError`` if not found."""
        p = self._path_for(name)
        if not p.exists():
            raise KeyError(f"Template {name!r} not found in registry at {self._dir}")
        return AgentTemplate.from_yaml(str(p))

    def get(self, name: str) -> AgentTemplate | None:
        """Return a template or ``None`` if not found."""
        try:
            return self.pull(name)
        except KeyError:
            return None

    def list(self) -> list[AgentTemplate]:
        """Return all templates in the registry."""
        result = []
        for p in sorted(self._dir.glob("*.yaml")):
            try:
                result.append(AgentTemplate.from_yaml(str(p)))
            except Exception:
                pass
        return result

    # ── Search ─────────────────────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 10) -> list[AgentTemplate]:
        """BM25 search over template names, descriptions, tags, and roles."""
        all_templates = self.list()
        if not all_templates:
            return []

        q_tokens = _tokenize(query)
        if not q_tokens:
            return all_templates[:top_k]

        docs = []
        for t in all_templates:
            combined = " ".join([
                t.name, t.description, t.role,
                " ".join(t.tags), " ".join(t.skills),
            ])
            docs.append(_tokenize(combined))

        n = len(docs)
        avg_len = sum(len(d) for d in docs) / max(n, 1)
        df: dict[str, int] = {}
        for doc in docs:
            for tok in set(doc):
                df[tok] = df.get(tok, 0) + 1

        scored: list[tuple[float, AgentTemplate]] = []
        for tmpl, doc_tokens in zip(all_templates, docs):
            s = _bm25_score(q_tokens, doc_tokens, avg_len, n, df)
            if s > 0:
                scored.append((s, tmpl))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in scored[:top_k]]

    def stats(self) -> dict[str, Any]:
        return {
            "registry_dir": str(self._dir),
            "template_count": len(list(self._dir.glob("*.yaml"))),
        }


# ── MarketplaceClient ─────────────────────────────────────────────────────────


class MarketplaceClient:
    """HTTP client for a remote MeshFlow template marketplace.

    Talks to a ``MarketplaceServer`` (or any compatible HTTP registry) to
    push, pull, list, and search agent templates without a local registry.

    Usage::

        client = MarketplaceClient("http://marketplace.meshflow.io")

        # Publish a local template to the remote registry
        client.push(tmpl)

        # Pull a template by name
        tmpl = client.pull("market-researcher")

        # Search
        results = client.search("compliance HIPAA")
    """

    def __init__(self, base_url: str, timeout: int = 10) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, data: bytes | None = None) -> Any:
        import urllib.request
        import urllib.error

        url = f"{self.base_url}{path}"
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return yaml.safe_load(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode()
            raise RuntimeError(f"Marketplace {method} {path} → HTTP {exc.code}: {body}") from exc

    def push(self, template: "AgentTemplate") -> str:
        """Upload a template to the remote registry. Returns the registry URL."""
        import json
        payload = json.dumps(template.to_dict()).encode()
        result = self._request("POST", "/templates", data=payload)
        return result.get("url", f"{self.base_url}/templates/{template.name}")

    def pull(self, name: str) -> "AgentTemplate":
        """Download a template by name from the remote registry."""
        data = self._request("GET", f"/templates/{name}")
        return AgentTemplate(**{k: v for k, v in data.items() if k in AgentTemplate.__dataclass_fields__})

    def list_all(self) -> list[dict[str, str]]:
        """List all templates in the remote registry (name + description)."""
        result = self._request("GET", "/templates")
        return result if isinstance(result, list) else []

    def search(self, query: str, top_k: int = 5) -> list["AgentTemplate"]:
        """BM25 search over the remote registry."""
        import urllib.parse
        q = urllib.parse.quote_plus(query)
        results = self._request("GET", f"/templates/search?q={q}&top={top_k}")
        out = []
        for item in (results if isinstance(results, list) else []):
            try:
                out.append(AgentTemplate(**{k: v for k, v in item.items()
                                            if k in AgentTemplate.__dataclass_fields__}))
            except Exception:
                pass
        return out


# ── MarketplaceServer ─────────────────────────────────────────────────────────


class MarketplaceServer:
    """Self-hostable HTTP marketplace server for MeshFlow templates.

    Wraps a ``TemplateRegistry`` and exposes it over HTTP so teams can share
    templates internally or publish them publicly.

    Endpoints::

        GET  /templates              — list all (name + description JSON array)
        GET  /templates/<name>       — fetch one template as JSON
        POST /templates              — publish a template (JSON body)
        GET  /templates/search?q=.. — BM25 search

    Usage::

        server = MarketplaceServer(registry_dir="~/.meshflow/marketplace", port=9900)
        server.start()               # background thread
        print(server.url())
        server.stop()

    CLI::

        meshflow marketplace serve --port 9900
        meshflow templates share my-agent --url http://localhost:9900
    """

    def __init__(
        self,
        registry_dir: str = "",
        port: int = 9900,
        host: str = "127.0.0.1",
    ) -> None:
        self._reg = TemplateRegistry(
            registry_dir=registry_dir or os.path.expanduser("~/.meshflow/marketplace")
        )
        self._port = port
        self._host = host
        self._server: Any = None
        self._thread: Any = None

    def url(self) -> str:
        return f"http://{self._host}:{self._port}"

    def start(self, daemon: bool = True) -> None:
        import http.server
        import json as _json
        import threading
        import urllib.parse

        reg = self._reg

        class _Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:  # type: ignore[override]
                pass

            def _send(self, code: int, data: Any) -> None:
                body = _json.dumps(data, default=str).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                parts = [p for p in parsed.path.split("/") if p]
                qs = urllib.parse.parse_qs(parsed.query)

                if parts == ["templates"]:
                    items = [{"name": t.name, "description": t.description, "tags": t.tags}
                             for t in reg.list()]
                    self._send(200, items)
                elif len(parts) == 2 and parts[0] == "templates" and parts[1] != "search":
                    try:
                        t = reg.pull(parts[1])
                        self._send(200, t.to_dict())
                    except KeyError:
                        self._send(404, {"error": f"template {parts[1]!r} not found"})
                elif len(parts) == 2 and parts[0] == "templates" and parts[1] == "search":
                    q = qs.get("q", [""])[0]
                    top = int(qs.get("top", ["5"])[0])
                    results = reg.search(q, top_k=top)
                    self._send(200, [t.to_dict() for t in results])
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self) -> None:
                import json as _json2
                parts = [p for p in self.path.split("/") if p]
                if parts == ["templates"]:
                    length = int(self.headers.get("Content-Length", 0))
                    body = self.rfile.read(length)
                    try:
                        data = _json2.loads(body)
                        fields = {k: v for k, v in data.items()
                                  if k in AgentTemplate.__dataclass_fields__}
                        tmpl = AgentTemplate(**fields)
                        path = reg.publish(tmpl)
                        self._send(200, {"status": "ok", "url": f"/templates/{tmpl.name}",
                                         "path": str(path)})
                    except Exception as exc:
                        self._send(400, {"error": str(exc)})
                else:
                    self._send(404, {"error": "not found"})

            def do_OPTIONS(self) -> None:
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

        self._server = http.server.HTTPServer((self._host, self._port), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=daemon)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()


__all__ = ["AgentTemplate", "TemplateRegistry", "MarketplaceClient", "MarketplaceServer"]
