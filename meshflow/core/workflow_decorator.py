"""@workflow decorator — make Python-defined pipelines portable, versionable, and CI-diffable.

Closes the Haystack pipeline-serialization gap: Haystack's YAML/JSON configs let
you store, version, diff, and deploy pipelines in CI.  MeshFlow's existing
``WorkflowDefinition.to_yaml()`` method handles round-trip export, but only for
instances built via the API.  This decorator wraps *any* factory function so it
gains first-class YAML export, CI diffing, and schema validation.

Usage::

    from meshflow.core.workflow_decorator import workflow

    @workflow
    def research_pipeline():
        from meshflow.core.workflow import WorkflowDefinition
        from meshflow.core.node import MeshNode, NodeKind, RiskTier

        wf = WorkflowDefinition(name="research", version="2")
        wf.add_node(MeshNode(id="planner",  kind=NodeKind.NATIVE))
        wf.add_node(MeshNode(id="executor", kind=NodeKind.NATIVE))
        wf.add_edge("planner", "executor")
        wf.set_terminal("executor")
        return wf

    # Export to YAML
    yaml_str = research_pipeline.to_yaml()           # in-memory
    research_pipeline.to_yaml("research.yaml")       # write to disk

    # Round-trip: load the saved YAML back
    wf = research_pipeline.load("research.yaml")

    # CI diff: compare two pipeline versions
    diff = research_pipeline.diff("v1/research.yaml", "v2/research.yaml")
    print(diff.summary())

    # Get the live WorkflowDefinition object
    wf = research_pipeline()

Pipeline YAML is versionable::

    git add research.yaml
    git diff research.yaml          # human-readable topology changes
    meshflow diff research_v1.yaml research_v2.yaml

"""

from __future__ import annotations

import functools
from typing import Any, Callable


# ── WorkflowProxy ─────────────────────────────────────────────────────────────


class WorkflowProxy:
    """Callable wrapper returned by @workflow.

    Adds YAML export, load, and diff helpers to any factory function that
    returns a ``WorkflowDefinition``.  The function is called lazily — the first
    call (or an explicit ``build()``) materialises the workflow.

    Parameters
    ----------
    fn:     The decorated factory function.
    """

    def __init__(self, fn: Callable[[], Any]) -> None:
        self._fn = fn
        self._cached: Any = None
        functools.update_wrapper(self, fn)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Build and return the WorkflowDefinition."""
        result = self._fn(*args, **kwargs)
        self._cached = result
        return result

    def build(self) -> Any:
        """Materialise the WorkflowDefinition (alias for calling the proxy)."""
        return self()

    def to_yaml(self, path: str | None = None) -> str:
        """Export the workflow to a YAML string (and optionally write to *path*).

        Parameters
        ----------
        path:
            If provided, write the YAML to this file and return the string.

        Returns
        -------
        YAML string representation of the workflow topology.

        Example
        -------
        ::

            @workflow
            def my_pipeline():
                ...

            yaml_str = my_pipeline.to_yaml()            # in-memory string
            my_pipeline.to_yaml("pipeline.yaml")        # save to disk
        """
        wf = self._cached if self._cached is not None else self()
        return wf.to_yaml(path=path)

    def load(self, path: str, node_registry: dict[str, Any] | None = None) -> Any:
        """Load a WorkflowDefinition from a saved YAML file.

        This is the round-trip complement of ``to_yaml(path)``.

        Parameters
        ----------
        path:
            Path to the YAML file previously written by ``to_yaml()``.
        node_registry:
            Optional mapping of ref strings to live Python objects.

        Returns
        -------
        A fresh ``WorkflowDefinition`` loaded from disk.
        """
        from meshflow.core.workflow import WorkflowDefinition
        return WorkflowDefinition.from_yaml(path, node_registry=node_registry)

    def diff(self, path_a: str, path_b: str) -> Any:
        """Compare two YAML versions of this workflow and return a DiffResult.

        Parameters
        ----------
        path_a:     Path to the first (older) YAML.
        path_b:     Path to the second (newer) YAML.

        Returns
        -------
        ``DiffResult`` from :func:`meshflow.core.diff.workflow_diff`.

        Example
        -------
        ::

            diff = my_pipeline.diff("v1/pipeline.yaml", "v2/pipeline.yaml")
            print(diff.summary())
            if diff.has_breaking_changes:
                raise RuntimeError("Breaking pipeline changes detected in CI")
        """
        from meshflow.core.diff import workflow_diff
        return workflow_diff(path_a, path_b)

    def schema(self) -> dict[str, Any]:
        """Return the JSON schema for this workflow's node topology."""
        wf = self._cached if self._cached is not None else self()
        return wf.describe()

    def __repr__(self) -> str:
        return f"<@workflow {self._fn.__name__!r}>"


# ── @workflow decorator ───────────────────────────────────────────────────────


def workflow(fn: Callable[[], Any]) -> WorkflowProxy:
    """Decorator that turns a workflow factory function into a portable, CI-diffable pipeline.

    The decorated function must return a ``WorkflowDefinition``.  After decoration
    it gains ``.to_yaml()``, ``.load()``, ``.diff()``, and ``.schema()`` methods
    while still being callable as before.

    Parameters
    ----------
    fn:     Factory function that builds and returns a ``WorkflowDefinition``.

    Returns
    -------
    ``WorkflowProxy`` — a callable that behaves like *fn* but adds serialization.

    Example
    -------
    ::

        from meshflow.core.workflow_decorator import workflow
        from meshflow.core.workflow import WorkflowDefinition

        @workflow
        def hipaa_audit_pipeline():
            wf = WorkflowDefinition(name="hipaa-audit")
            # ... add nodes, edges ...
            return wf

        # CI pipeline usage:
        hipaa_audit_pipeline.to_yaml("pipelines/hipaa_audit.yaml")
        diff = hipaa_audit_pipeline.diff("pipelines/hipaa_audit_v1.yaml",
                                          "pipelines/hipaa_audit_v2.yaml")
    """
    return WorkflowProxy(fn)


__all__ = ["workflow", "WorkflowProxy"]
