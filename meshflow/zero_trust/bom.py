"""AI Bill of Materials (AI-BOM) — Zero Trust supply chain visibility.

Implements the AI-BOM concept from the Anthropic Zero Trust guide (Part IV,
Phase 2 — Manage supply chain risks):

  "The AI-BOM concept extends software composition analysis to AI components,
   tracking model provenance, training dataset lineage, and fine-tuning
   parameters. Integrate an AI-BOM into existing supply chain security
   processes, treating model components with the same rigor applied to code
   dependencies."

The AI-BOM tracks:
  - Models used by each agent (provenance, version, fine-tuning info)
  - External tools / MCP servers (source, hash, last-verified)
  - Framework dependencies (name, version, OpenSSF score if available)
  - Risk signals (unverified hashes, unmaintained packages, known CVEs)

Usage::

    from meshflow.zero_trust.bom import AIBillOfMaterials, ModelComponent

    bom = AIBillOfMaterials(project="my-agent-system")
    bom.add_model(ModelComponent(
        name="claude-sonnet-4-6",
        provider="anthropic",
        version="claude-sonnet-4-6",
        access_method="api",
    ))
    bom.add_dependency("meshflow", version="1.0.0")

    report = bom.report()
    print(report["risk_summary"])

    # Export as CycloneDX-compatible JSON
    bom.to_cyclonedx("bom.json")
"""

from __future__ import annotations

import importlib.metadata
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class ModelComponent:
    """A model used in the agentic system."""

    name: str
    provider: str                       # anthropic, openai, google, bedrock, local
    version: str = ""
    access_method: str = "api"          # api | local | bedrock | azure
    base_model: str = ""                # if fine-tuned, the base
    fine_tuned: bool = False
    fine_tuning_dataset: str = ""       # lineage: dataset name/hash
    training_date: str = ""
    model_hash: str = ""                # sha256 of model weights (local only)
    verified: bool = False              # signature/attestation verified
    notes: str = ""

    def risk_flags(self) -> list[str]:
        flags = []
        if self.fine_tuned and not self.fine_tuning_dataset:
            flags.append("fine_tuning_dataset_untracked")
        if self.access_method == "local" and not self.model_hash:
            flags.append("local_model_hash_missing")
        if not self.version:
            flags.append("model_version_unspecified")
        return flags


@dataclass
class ToolComponent:
    """An external tool or MCP server used by agents."""

    name: str
    source_url: str = ""
    version: str = ""
    hash_sha256: str = ""
    last_verified: str = ""
    transport: str = "http"             # http | stdio | sse
    author: str = ""
    license: str = ""
    openssf_score: float = -1.0         # -1 = not checked
    notes: str = ""

    def risk_flags(self) -> list[str]:
        flags = []
        if not self.hash_sha256:
            flags.append("hash_missing")
        if not self.last_verified:
            flags.append("integrity_never_verified")
        if not self.source_url:
            flags.append("source_url_missing")
        if 0.0 <= self.openssf_score < 5.0:
            flags.append(f"low_openssf_score_{self.openssf_score:.1f}")
        return flags


@dataclass
class DependencyComponent:
    """A Python/npm/system dependency."""

    name: str
    version: str = ""
    ecosystem: str = "python"           # python | npm | system
    license: str = ""
    openssf_score: float = -1.0
    known_cves: list[str] = field(default_factory=list)
    is_dev_only: bool = False
    notes: str = ""

    def risk_flags(self) -> list[str]:
        flags = []
        if self.known_cves:
            flags.append(f"known_cves:{','.join(self.known_cves)}")
        if 0.0 <= self.openssf_score < 4.0:
            flags.append(f"low_openssf_score_{self.openssf_score:.1f}")
        if not self.version:
            flags.append("version_unpinned")
        return flags


class AIBillOfMaterials:
    """Tracks all AI/ML components in an agentic system for supply chain visibility.

    Parameters
    ----------
    project:   Human-readable project name.
    auto_scan: If True, automatically scan installed Python packages on creation.
    """

    def __init__(self, project: str = "meshflow-project", *, auto_scan: bool = False) -> None:
        self.project = project
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.models:       list[ModelComponent]      = []
        self.tools:        list[ToolComponent]        = []
        self.dependencies: list[DependencyComponent]  = []
        if auto_scan:
            self._auto_scan_dependencies()

    # ── Add components ────────────────────────────────────────────────────────

    def add_model(self, component: ModelComponent) -> "AIBillOfMaterials":
        self.models.append(component)
        return self

    def add_tool(self, component: ToolComponent) -> "AIBillOfMaterials":
        self.tools.append(component)
        return self

    def add_dependency(
        self,
        name: str,
        *,
        version: str = "",
        ecosystem: str = "python",
        license: str = "",
        openssf_score: float = -1.0,
        known_cves: list[str] | None = None,
    ) -> "AIBillOfMaterials":
        if not version and ecosystem == "python":
            try:
                version = importlib.metadata.version(name)
            except Exception:
                pass
        self.dependencies.append(DependencyComponent(
            name=name,
            version=version,
            ecosystem=ecosystem,
            license=license,
            openssf_score=openssf_score,
            known_cves=known_cves or [],
        ))
        return self

    # ── Analysis ─────────────────────────────────────────────────────────────

    def all_risk_flags(self) -> dict[str, list[str]]:
        flags: dict[str, list[str]] = {}
        for m in self.models:
            f = m.risk_flags()
            if f:
                flags[f"model:{m.name}"] = f
        for t in self.tools:
            f = t.risk_flags()
            if f:
                flags[f"tool:{t.name}"] = f
        for d in self.dependencies:
            f = d.risk_flags()
            if f:
                flags[f"dep:{d.name}"] = f
        return flags

    def risk_summary(self) -> dict[str, Any]:
        flags = self.all_risk_flags()
        critical = [k for k, v in flags.items() if any("cve" in f or "hash_missing" in f for f in v)]
        high = [k for k, v in flags.items() if any("unverified" in f or "low_openssf" in f for f in v)]
        return {
            "total_components": len(self.models) + len(self.tools) + len(self.dependencies),
            "flagged_components": len(flags),
            "critical": critical,
            "high": high,
            "all_flags": flags,
            "risk_level": "critical" if critical else ("high" if high else "ok"),
        }

    def report(self) -> dict[str, Any]:
        return {
            "project":     self.project,
            "created_at":  self.created_at,
            "models":      [{"name": m.name, "provider": m.provider, "version": m.version,
                             "risk_flags": m.risk_flags()} for m in self.models],
            "tools":       [{"name": t.name, "version": t.version,
                             "risk_flags": t.risk_flags()} for t in self.tools],
            "dependencies":[{"name": d.name, "version": d.version,
                             "risk_flags": d.risk_flags()} for d in self.dependencies],
            "risk_summary": self.risk_summary(),
        }

    # ── Export ────────────────────────────────────────────────────────────────

    def to_cyclonedx(self, path: str | Path | None = None) -> dict[str, Any]:
        """Export as CycloneDX-compatible JSON (OWASP AI-BOM standard)."""
        components = []
        for m in self.models:
            components.append({
                "type": "machine-learning-model",
                "name": m.name,
                "version": m.version,
                "supplier": {"name": m.provider},
                "properties": [
                    {"name": "accessMethod",    "value": m.access_method},
                    {"name": "fineTuned",       "value": str(m.fine_tuned)},
                    {"name": "trainingDataset", "value": m.fine_tuning_dataset},
                ],
                "hashes": [{"alg": "SHA-256", "content": m.model_hash}] if m.model_hash else [],
            })
        for t in self.tools:
            components.append({
                "type": "library",
                "name": t.name,
                "version": t.version,
                "externalReferences": [{"type": "website", "url": t.source_url}] if t.source_url else [],
                "hashes": [{"alg": "SHA-256", "content": t.hash_sha256}] if t.hash_sha256 else [],
            })
        for d in self.dependencies:
            components.append({
                "type": "library",
                "name": d.name,
                "version": d.version,
                "licenses": [{"license": {"name": d.license}}] if d.license else [],
            })
        doc = {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "version": 1,
            "metadata": {
                "timestamp": self.created_at,
                "component": {"type": "application", "name": self.project},
            },
            "components": components,
        }
        if path:
            Path(path).write_text(json.dumps(doc, indent=2))
        return doc

    # ── Auto-scan ─────────────────────────────────────────────────────────────

    def _auto_scan_dependencies(self) -> None:
        """Scan installed Python packages and add relevant ones to the BOM."""
        ai_packages = {
            "meshflow", "anthropic", "openai", "google-generativeai", "boto3",
            "langchain", "crewai", "langchain-core", "langchain-community",
            "sentence-transformers", "chromadb", "faiss-cpu", "numpy",
            "pydantic", "fastapi", "starlette", "uvicorn", "aiohttp",
        }
        for dist in importlib.metadata.distributions():
            name = dist.metadata["Name"]
            if name and name.lower() in {p.lower() for p in ai_packages}:
                version = dist.metadata["Version"] or ""
                license_str = dist.metadata.get("License") or ""
                self.add_dependency(name, version=version, license=license_str)

    @classmethod
    def from_meshflow_project(cls, project: str = "meshflow") -> "AIBillOfMaterials":
        """Build a BOM for a standard MeshFlow project with the core model lineup."""
        bom = cls(project=project, auto_scan=True)
        # MeshFlow's default model lineup
        for model_id, provider in [
            ("claude-sonnet-4-6", "anthropic"),
            ("claude-haiku-4-5-20251001", "anthropic"),
            ("claude-opus-4-8", "anthropic"),
        ]:
            bom.add_model(ModelComponent(
                name=model_id,
                provider=provider,
                version=model_id,
                access_method="api",
                verified=True,
            ))
        return bom


__all__ = [
    "AIBillOfMaterials",
    "ModelComponent",
    "ToolComponent",
    "DependencyComponent",
]
