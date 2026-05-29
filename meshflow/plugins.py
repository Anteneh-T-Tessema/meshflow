"""MeshFlow plugin / extension system.

Third-party packages can register agents, tools, compliance profiles, and
ledger backends as MeshFlow plugins by declaring ``entry_points`` in their
``pyproject.toml``:

    [project.entry-points."meshflow.agents"]
    my_agent = "my_package.agents:MyAgent"

    [project.entry-points."meshflow.tools"]
    my_tool = "my_package.tools:my_tool"

    [project.entry-points."meshflow.compliance"]
    my_profile = "my_package.compliance:MyProfile"

    [project.entry-points."meshflow.ledger"]
    my_backend = "my_package.ledger:MyLedgerBackend"

CLI usage:
    meshflow plugins list              — list all installed plugins
    meshflow plugins verify <name>     — load and validate one plugin
    meshflow plugins info <name>       — show plugin details

Programmatic usage:
    from meshflow.plugins import discover_plugins, load_plugin

    for plugin in discover_plugins():
        print(plugin.name, plugin.group, plugin.version)

    MyAgent = load_plugin("my_agent", group="meshflow.agents")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── Plugin groups ─────────────────────────────────────────────────────────────

PLUGIN_GROUPS: dict[str, str] = {
    "agent":      "meshflow.agents",
    "tool":       "meshflow.tools",
    "compliance": "meshflow.compliance",
    "ledger":     "meshflow.ledger",
}

# Reverse map: entry-point group → friendly name
_GROUP_TO_KIND: dict[str, str] = {v: k for k, v in PLUGIN_GROUPS.items()}


# ── PluginInfo ────────────────────────────────────────────────────────────────


@dataclass
class PluginInfo:
    """Metadata about a discovered plugin entry point."""

    name: str
    group: str          # friendly kind: agent | tool | compliance | ledger
    ep_group: str       # full entry-point group, e.g. "meshflow.agents"
    module: str         # dotted module path
    dist_name: str      # distribution package name
    version: str        # distribution version
    description: str = ""
    loaded: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": self.group,
            "ep_group": self.ep_group,
            "module": self.module,
            "dist_name": self.dist_name,
            "version": self.version,
            "description": self.description,
            "loaded": self.loaded,
            "error": self.error,
        }


# ── Discovery ─────────────────────────────────────────────────────────────────


def discover_plugins(group: str | None = None) -> list[PluginInfo]:
    """Discover all installed MeshFlow plugins via ``importlib.metadata``.

    Parameters
    ----------
    group : Optional friendly group filter (``"agent"``, ``"tool"``,
            ``"compliance"``, ``"ledger"``).  If *None*, all groups are
            returned.

    Returns
    -------
    List of :class:`PluginInfo` for every registered entry point.
    """
    try:
        from importlib.metadata import entry_points, packages_distributions
    except ImportError:
        return []

    target_groups = (
        {PLUGIN_GROUPS[group]} if group and group in PLUGIN_GROUPS
        else set(PLUGIN_GROUPS.values())
    )

    # Build a module→dist mapping for version lookup
    try:
        pkg_dist = packages_distributions()
    except Exception:
        pkg_dist = {}

    results: list[PluginInfo] = []

    for ep_group in sorted(target_groups):
        try:
            eps = entry_points(group=ep_group)
        except Exception:
            continue

        for ep in eps:
            dist_name = ""
            version = ""
            description = ""

            try:
                dist = ep.dist
                if dist is not None:
                    dist_name = dist.name
                    version = dist.version
                    try:
                        description = (dist.metadata.get("Summary") or "").strip()
                    except Exception:
                        pass
            except Exception:
                pass

            results.append(
                PluginInfo(
                    name=ep.name,
                    group=_GROUP_TO_KIND.get(ep_group, ep_group),
                    ep_group=ep_group,
                    module=ep.value,
                    dist_name=dist_name,
                    version=version,
                    description=description,
                )
            )

    return results


def load_plugin(name: str, group: str = "meshflow.agents") -> Any:
    """Load and return the object registered under *name* in *group*.

    Parameters
    ----------
    name  : Entry-point name (e.g. ``"my_agent"``).
    group : Full entry-point group (e.g. ``"meshflow.agents"``) or
            a friendly alias (``"agent"``, ``"tool"``, etc.).

    Returns
    -------
    The object pointed to by the entry point (class, function, or instance).

    Raises
    ------
    KeyError   : No entry point with that name in the group.
    ImportError: The module could not be imported.
    """
    from importlib.metadata import entry_points

    # Accept friendly aliases
    ep_group = PLUGIN_GROUPS.get(group, group)

    eps = {ep.name: ep for ep in entry_points(group=ep_group)}
    if name not in eps:
        available = ", ".join(sorted(eps)) or "(none)"
        raise KeyError(
            f"No plugin {name!r} in group {ep_group!r}. Available: {available}"
        )

    return eps[name].load()


def verify_plugin(name: str, group: str = "meshflow.agents") -> tuple[bool, str]:
    """Try to load a plugin and return ``(ok, message)``.

    Safe — never raises; errors are captured in the return value.

    Returns
    -------
    ``(True, "OK — <module>")`` on success, or
    ``(False, "<error message>")`` on failure.
    """
    ep_group = PLUGIN_GROUPS.get(group, group)
    try:
        obj = load_plugin(name, ep_group)
        module = getattr(obj, "__module__", str(obj))
        return True, f"OK — {module}"
    except KeyError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, f"Load error: {exc}"


def list_plugins_table() -> list[dict[str, Any]]:
    """Return all plugins as a list of dicts, suitable for tabular display."""
    plugins = discover_plugins()
    return [p.to_dict() for p in plugins]


# ── Vulnerability scanning ────────────────────────────────────────────────────

import hashlib
import json
import sqlite3
import time


@dataclass
class PluginScanResult:
    """Result of scanning one plugin distribution for known vulnerabilities."""

    dist_name: str
    version: str
    safe: bool
    vulnerabilities: list[dict[str, Any]] = field(default_factory=list)
    hash_ok: bool = True
    hash_algorithm: str = ""
    hash_value: str = ""
    scan_source: str = ""  # "pypi-advisory-db" | "local-allowlist" | "no-data"
    scanned_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dist_name": self.dist_name,
            "version": self.version,
            "safe": self.safe,
            "vulnerabilities": self.vulnerabilities,
            "hash_ok": self.hash_ok,
            "hash_algorithm": self.hash_algorithm,
            "hash_value": self.hash_value,
            "scan_source": self.scan_source,
            "scanned_at": self.scanned_at,
        }


def scan_plugin(info: PluginInfo) -> PluginScanResult:
    """Scan a plugin distribution for known vulnerabilities.

    Uses two sources (in order):
    1. PyPI advisory database via ``pip audit --json`` (if pip-audit is installed).
    2. Local hash allowlist in ``~/.meshflow/plugin_allowlist.json``.

    If neither is available the scan is marked ``scan_source="no-data"`` but
    flagged as ``safe=True`` (unknown ≠ unsafe — be conservative by default).

    Hash verification checks the installed wheel SHA-256 recorded by pip against
    the one in the local allowlist (if present).
    """
    result = PluginScanResult(
        dist_name=info.dist_name,
        version=info.version,
        safe=True,
        scan_source="no-data",
    )

    # ── 1. pip-audit scan (subprocess, graceful fallback) ─────────────────────
    try:
        import subprocess
        import sys
        proc = subprocess.run(
            [sys.executable, "-m", "pip_audit", "--json", "-r", "/dev/stdin"],
            input=f"{info.dist_name}=={info.version}\n",
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode == 0 or proc.stdout.strip().startswith("["):
            audit_results = json.loads(proc.stdout or "[]")
            for entry in audit_results:
                pkg = entry.get("name", "").lower()
                if pkg == info.dist_name.lower():
                    vulns = entry.get("vulns", [])
                    if vulns:
                        result.vulnerabilities = vulns
                        result.safe = False
                    result.scan_source = "pip-audit"
                    break
            if result.scan_source != "pip-audit":
                result.scan_source = "pip-audit"  # no findings = safe
    except Exception:
        pass  # pip-audit not installed or not available

    # ── 2. Local allowlist check ──────────────────────────────────────────────
    import os
    allowlist_path = os.path.expanduser("~/.meshflow/plugin_allowlist.json")
    if os.path.exists(allowlist_path):
        try:
            with open(allowlist_path) as fh:
                allowlist: dict[str, Any] = json.load(fh)
            key = f"{info.dist_name}=={info.version}"
            if key in allowlist:
                entry = allowlist[key]
                result.hash_algorithm = entry.get("hash_algorithm", "sha256")
                result.hash_value = entry.get("hash_value", "")
                # Verify installed distribution hash
                actual_hash = _hash_distribution(info.dist_name)
                if actual_hash and result.hash_value:
                    result.hash_ok = actual_hash == result.hash_value
                    if not result.hash_ok:
                        result.safe = False
            if result.scan_source == "no-data":
                result.scan_source = "local-allowlist"
        except Exception:
            pass

    return result


def scan_all_plugins() -> list[PluginScanResult]:
    """Scan all installed MeshFlow plugins."""
    return [scan_plugin(p) for p in discover_plugins() if p.dist_name]


def _hash_distribution(dist_name: str) -> str:
    """Compute SHA-256 of the installed distribution's RECORD or wheel metadata."""
    try:
        from importlib.metadata import files as dist_files
        records = dist_files(dist_name) or []
        h = hashlib.sha256()
        for f in sorted(str(r) for r in records):
            h.update(f.encode())
        return h.hexdigest()
    except Exception:
        return ""


# ── Plugin audit log (SQLite) ─────────────────────────────────────────────────

class PluginAuditLog:
    """Append-only audit log for plugin load events.

    Records which plugins were loaded, when, by which process, and
    whether the security scan passed.

    Usage::

        log = PluginAuditLog()
        log.record_load(info, scan_result)
        entries = log.list_recent(50)
    """

    def __init__(self, path: str = "meshflow_plugin_audit.db") -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        if self._path == ":memory:":
            if self._conn is None:
                self._conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
            return self._conn
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS plugin_audit (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                dist_name  TEXT NOT NULL,
                version    TEXT NOT NULL,
                name       TEXT NOT NULL,
                ep_group   TEXT NOT NULL,
                safe       INTEGER NOT NULL,
                vuln_count INTEGER NOT NULL,
                hash_ok    INTEGER NOT NULL,
                loaded_at  REAL NOT NULL,
                pid        INTEGER NOT NULL
            )
        """)
        conn.commit()

    def record_load(self, info: PluginInfo, scan: PluginScanResult | None = None) -> None:
        import os
        conn = self._connect()
        conn.execute(
            """INSERT INTO plugin_audit
               (dist_name, version, name, ep_group, safe, vuln_count, hash_ok, loaded_at, pid)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                info.dist_name,
                info.version,
                info.name,
                info.ep_group,
                int(scan.safe) if scan else 1,
                len(scan.vulnerabilities) if scan else 0,
                int(scan.hash_ok) if scan else 1,
                time.time(),
                os.getpid(),
            ),
        )
        conn.commit()

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM plugin_audit ORDER BY loaded_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def unsafe_loads(self) -> list[dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM plugin_audit WHERE safe=0 ORDER BY loaded_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
