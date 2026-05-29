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


__all__ = ["AgentTemplate", "TemplateRegistry"]
