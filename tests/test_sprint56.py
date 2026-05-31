"""Sprint 56 — Data Lineage Graph tests."""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

import meshflow
from meshflow.lineage.graph import LineageEdge, LineageGraph, LineageNode


def _g() -> LineageGraph:
    return LineageGraph(":memory:")


# ── LineageNode ───────────────────────────────────────────────────────────────

class TestLineageNode(unittest.TestCase):
    def _node(self) -> LineageNode:
        return LineageNode("n1", "source", "my-file", "r1", "agent-A", 1000.0, {"x": 1})

    def test_to_dict_keys(self):
        d = self._node().to_dict()
        for k in ("node_id", "kind", "name", "run_id", "agent_name", "ts", "metadata"):
            self.assertIn(k, d)

    def test_to_dict_values(self):
        d = self._node().to_dict()
        self.assertEqual(d["node_id"], "n1")
        self.assertEqual(d["metadata"], {"x": 1})


# ── LineageEdge ───────────────────────────────────────────────────────────────

class TestLineageEdge(unittest.TestCase):
    def test_to_dict_keys(self):
        e = LineageEdge("e1", "n1", "n2", "derived_from", 1000.0, {})
        d = e.to_dict()
        for k in ("edge_id", "source_id", "target_id", "relation", "ts", "metadata"):
            self.assertIn(k, d)


# ── LineageGraph — add_node ───────────────────────────────────────────────────

class TestLineageGraphAddNode(unittest.TestCase):
    def test_add_node_returns_node(self):
        g = _g()
        n = g.add_node("source", "file.csv", run_id="r1")
        self.assertIsInstance(n, LineageNode)

    def test_add_node_stores(self):
        g = _g()
        n = g.add_node("source", "file.csv")
        self.assertEqual(g.node_count(), 1)

    def test_add_node_kind(self):
        g = _g()
        n = g.add_node("transform", "pii-redactor", agent_name="redactor")
        self.assertEqual(n.kind, "transform")

    def test_add_node_metadata(self):
        g = _g()
        n = g.add_node("sink", "db", metadata={"table": "users"})
        fetched = g.get_node(n.node_id)
        self.assertEqual(fetched.metadata, {"table": "users"})

    def test_add_node_explicit_ts(self):
        g = _g()
        n = g.add_node("source", "file", ts=12345.0)
        self.assertEqual(g.get_node(n.node_id).ts, 12345.0)

    def test_add_node_explicit_id(self):
        g = _g()
        n = g.add_node("source", "file", node_id="my-node")
        self.assertEqual(n.node_id, "my-node")

    def test_multiple_nodes(self):
        g = _g()
        g.add_node("source", "a")
        g.add_node("transform", "b")
        g.add_node("sink", "c")
        self.assertEqual(g.node_count(), 3)


# ── LineageGraph — add_edge ───────────────────────────────────────────────────

class TestLineageGraphAddEdge(unittest.TestCase):
    def test_add_edge_returns_edge(self):
        g = _g()
        n1 = g.add_node("source", "a")
        n2 = g.add_node("sink", "b")
        e = g.add_edge(n1.node_id, n2.node_id, "derived_from")
        self.assertIsInstance(e, LineageEdge)

    def test_add_edge_stored(self):
        g = _g()
        n1 = g.add_node("source", "a")
        n2 = g.add_node("sink", "b")
        g.add_edge(n1.node_id, n2.node_id)
        self.assertEqual(g.edge_count(), 1)

    def test_add_edge_fields(self):
        g = _g()
        n1 = g.add_node("source", "a")
        n2 = g.add_node("sink", "b")
        e = g.add_edge(n1.node_id, n2.node_id, "produced", metadata={"k": "v"})
        fetched = g.get_edge(e.edge_id)
        self.assertEqual(fetched.relation, "produced")
        self.assertEqual(fetched.metadata, {"k": "v"})

    def test_edges_from(self):
        g = _g()
        n1 = g.add_node("source", "a")
        n2 = g.add_node("sink", "b")
        n3 = g.add_node("sink", "c")
        g.add_edge(n1.node_id, n2.node_id)
        g.add_edge(n1.node_id, n3.node_id)
        self.assertEqual(len(g.edges_from(n1.node_id)), 2)

    def test_edges_to(self):
        g = _g()
        n1 = g.add_node("source", "a")
        n2 = g.add_node("source", "b")
        n3 = g.add_node("sink", "c")
        g.add_edge(n1.node_id, n3.node_id)
        g.add_edge(n2.node_id, n3.node_id)
        self.assertEqual(len(g.edges_to(n3.node_id)), 2)


# ── Traversal ─────────────────────────────────────────────────────────────────

class TestLineageTraversal(unittest.TestCase):
    def _chain(self) -> tuple[LineageGraph, LineageNode, LineageNode, LineageNode]:
        """src → xfm → sink"""
        g = _g()
        src = g.add_node("source",    "raw-data",    run_id="r1", agent_name="ingest")
        xfm = g.add_node("transform", "pii-redact",  run_id="r1", agent_name="redactor")
        snk = g.add_node("sink",      "reports-db",  run_id="r1", agent_name="writer")
        g.add_edge(src.node_id, xfm.node_id, "transformed_by")
        g.add_edge(xfm.node_id, snk.node_id, "written_to")
        return g, src, xfm, snk

    def test_trace_upstream_direct_parent(self):
        g, src, xfm, snk = self._chain()
        upstream = g.trace_upstream(xfm.node_id)
        ids = [n.node_id for n in upstream]
        self.assertIn(src.node_id, ids)

    def test_trace_upstream_transitive(self):
        g, src, xfm, snk = self._chain()
        upstream = g.trace_upstream(snk.node_id)
        ids = [n.node_id for n in upstream]
        self.assertIn(src.node_id, ids)
        self.assertIn(xfm.node_id, ids)

    def test_trace_upstream_root_is_empty(self):
        g, src, xfm, snk = self._chain()
        self.assertEqual(g.trace_upstream(src.node_id), [])

    def test_trace_downstream_direct_child(self):
        g, src, xfm, snk = self._chain()
        downstream = g.trace_downstream(xfm.node_id)
        ids = [n.node_id for n in downstream]
        self.assertIn(snk.node_id, ids)

    def test_trace_downstream_transitive(self):
        g, src, xfm, snk = self._chain()
        downstream = g.trace_downstream(src.node_id)
        ids = [n.node_id for n in downstream]
        self.assertIn(xfm.node_id, ids)
        self.assertIn(snk.node_id, ids)

    def test_impact_analysis_alias(self):
        g, src, xfm, snk = self._chain()
        self.assertEqual(
            [n.node_id for n in g.impact_analysis(src.node_id)],
            [n.node_id for n in g.trace_downstream(src.node_id)],
        )

    def test_trace_upstream_no_duplicates(self):
        g = _g()
        a = g.add_node("source", "a")
        b = g.add_node("transform", "b")
        c = g.add_node("sink", "c")
        g.add_edge(a.node_id, b.node_id)
        g.add_edge(a.node_id, c.node_id)
        g.add_edge(b.node_id, c.node_id)
        upstream = g.trace_upstream(c.node_id)
        ids = [n.node_id for n in upstream]
        self.assertEqual(len(ids), len(set(ids)))

    def test_subgraph_contains_all_nodes(self):
        g, src, xfm, snk = self._chain()
        sg = g.subgraph(xfm.node_id)
        node_ids = {n["node_id"] for n in sg["nodes"]}
        self.assertIn(src.node_id, node_ids)
        self.assertIn(xfm.node_id, node_ids)
        self.assertIn(snk.node_id, node_ids)

    def test_subgraph_contains_edges(self):
        g, src, xfm, snk = self._chain()
        sg = g.subgraph(src.node_id)
        self.assertGreater(len(sg["edges"]), 0)


# ── Queries ───────────────────────────────────────────────────────────────────

class TestLineageQueries(unittest.TestCase):
    def test_for_run_filters(self):
        g = _g()
        g.add_node("source", "a", run_id="r1")
        g.add_node("source", "b", run_id="r2")
        nodes = g.for_run("r1")
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].name, "a")

    def test_for_agent_filters(self):
        g = _g()
        g.add_node("source", "a", agent_name="agent-X")
        g.add_node("source", "b", agent_name="agent-Y")
        nodes = g.for_agent("agent-X")
        self.assertEqual(len(nodes), 1)

    def test_get_node_unknown_none(self):
        self.assertIsNone(_g().get_node("no-such"))

    def test_get_edge_unknown_none(self):
        self.assertIsNone(_g().get_edge("no-such"))

    def test_node_count(self):
        g = _g()
        g.add_node("source", "a")
        g.add_node("source", "b")
        self.assertEqual(g.node_count(), 2)

    def test_edge_count(self):
        g = _g()
        n1 = g.add_node("source", "a")
        n2 = g.add_node("sink", "b")
        g.add_edge(n1.node_id, n2.node_id)
        self.assertEqual(g.edge_count(), 1)


# ── GDPR erasure ──────────────────────────────────────────────────────────────

class TestLineageGDPRErasure(unittest.TestCase):
    def test_delete_subject_removes_nodes(self):
        g = _g()
        g.add_node("source", "alice-data")
        g.add_node("source", "alice-data")
        g.add_node("source", "bob-data")
        n = g.delete_subject("alice-data")
        self.assertEqual(n, 2)
        self.assertEqual(g.node_count(), 1)

    def test_delete_subject_removes_edges(self):
        g = _g()
        n1 = g.add_node("source", "alice-data")
        n2 = g.add_node("sink", "report")
        g.add_edge(n1.node_id, n2.node_id)
        g.delete_subject("alice-data")
        self.assertEqual(g.edge_count(), 0)

    def test_delete_subject_unknown_returns_zero(self):
        self.assertEqual(_g().delete_subject("nobody"), 0)

    def test_delete_node_removes_node_and_edges(self):
        g = _g()
        n1 = g.add_node("source", "a")
        n2 = g.add_node("sink", "b")
        g.add_edge(n1.node_id, n2.node_id)
        ok = g.delete_node(n1.node_id)
        self.assertTrue(ok)
        self.assertEqual(g.node_count(), 1)
        self.assertEqual(g.edge_count(), 0)

    def test_delete_node_unknown_returns_false(self):
        self.assertFalse(_g().delete_node("no-such"))


# ── CLI tests ─────────────────────────────────────────────────────────────────

class TestLineageCLI(unittest.TestCase):
    def _args(self, cmd, **kw):
        import argparse
        ns = argparse.Namespace(lineage_cmd=cmd, db=":memory:",
                                json_output=False, yes=False)
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def test_show_missing_exits(self):
        from meshflow.cli.main import _cmd_lineage
        with self.assertRaises(SystemExit):
            _cmd_lineage(self._args("show", node_id="no-such"))

    def test_trace_empty(self):
        from meshflow.cli.main import _cmd_lineage
        import io
        g = LineageGraph(":memory:")
        n = g.add_node("source", "a")
        with patch("meshflow.lineage.graph.LineageGraph", return_value=g):
            with patch("sys.stdout", new_callable=io.StringIO) as out:
                _cmd_lineage(self._args("trace", node_id=n.node_id))
        self.assertIn("No upstream", out.getvalue())

    def test_stats_output(self):
        from meshflow.cli.main import _cmd_lineage
        import io
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            _cmd_lineage(self._args("stats"))
        self.assertIn("Nodes", out.getvalue())

    def test_run_no_data(self):
        from meshflow.cli.main import _cmd_lineage
        import io
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            _cmd_lineage(self._args("run", run_id="r-none"))
        self.assertIn("No lineage", out.getvalue())

    def test_delete_with_yes(self):
        from meshflow.cli.main import _cmd_lineage
        import io
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            _cmd_lineage(self._args("delete", name="test-subject", yes=True))
        self.assertIn("Deleted", out.getvalue())


# ── Subprocess help ───────────────────────────────────────────────────────────

class TestSubprocessHelp(unittest.TestCase):
    def test_lineage_help(self):
        r = subprocess.run(["meshflow", "lineage", "--help"],
                           capture_output=True, text=True, timeout=15)
        self.assertIn(r.returncode, (0, 1))


# ── Public exports ────────────────────────────────────────────────────────────

class TestPublicExports(unittest.TestCase):
    def test_version(self):
        self.assertGreaterEqual(meshflow.__version__, "0.77.0")

    def test_lineage_node_exported(self):
        self.assertIs(meshflow.LineageNode, LineageNode)

    def test_lineage_edge_exported(self):
        self.assertIs(meshflow.LineageEdge, LineageEdge)

    def test_lineage_graph_exported(self):
        self.assertIs(meshflow.LineageGraph, LineageGraph)

    def test_all_contains_lineage(self):
        for name in ("LineageNode", "LineageEdge", "LineageGraph"):
            self.assertIn(name, meshflow.__all__)

    def test_sprint55_exports_intact(self):
        for name in ("LockRecord", "LockStore", "DistributedLock"):
            self.assertTrue(hasattr(meshflow, name))


if __name__ == "__main__":
    unittest.main()
