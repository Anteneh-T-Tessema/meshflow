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
