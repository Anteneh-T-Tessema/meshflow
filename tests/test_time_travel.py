"""Unit tests for DurableWorkflowExecutor State Forking ("Time Travel")."""
import asyncio
import unittest
import time

from meshflow.core.durable import DurableWorkflowExecutor
from meshflow.core.node import NodeOutput


class TestTimeTravel(unittest.TestCase):

    def test_fork_copies_only_upstream_checkpoints(self):
        # Create an executor in memory
        executor = DurableWorkflowExecutor(backend="memory")
        parent_id = executor.run_id

        # Save simulated checkpoints in order
        t0 = time.time()
        executor._store.save(parent_id, "node_a", NodeOutput(content="result A"))
        
        # Simulate slight delay so timestamps are distinct
        time.sleep(0.01)
        executor._store.save(parent_id, "node_b", NodeOutput(content="result B"))
        
        time.sleep(0.01)
        executor._store.save(parent_id, "node_c", NodeOutput(content="result C"))

        # Fork before node_b
        forked = executor.fork(parent_id, before_node_id="node_b")
        fork_id = forked.run_id

        # Verify that node_a is completed (since it ran before node_b)
        self.assertTrue(forked.is_completed("node_a"))
        self.assertEqual(forked._store.load(fork_id, "node_a").content, "result A")

        # Verify that node_b is NOT completed (since we forked before it)
        self.assertFalse(forked.is_completed("node_b"))

        # Verify that node_c is NOT completed (since it ran after node_b)
        self.assertFalse(forked.is_completed("node_c"))

    def test_fork_nonexistent_node_raises_value_error(self):
        executor = DurableWorkflowExecutor(backend="memory")
        parent_id = executor.run_id
        executor._store.save(parent_id, "node_a", NodeOutput(content="result A"))

        with self.assertRaises(ValueError):
            executor.fork(parent_id, before_node_id="nonexistent_node")

    def test_fork_copies_only_upstream_checkpoints_sqlite(self):
        # Create an executor using sqlite in-memory database
        executor = DurableWorkflowExecutor(backend="sqlite", db_path=":memory:")
        parent_id = executor.run_id

        # Save simulated checkpoints in order
        executor._store.save(parent_id, "node_a", NodeOutput(content="result A"))
        time.sleep(0.01)
        executor._store.save(parent_id, "node_b", NodeOutput(content="result B"))
        time.sleep(0.01)
        executor._store.save(parent_id, "node_c", NodeOutput(content="result C"))

        # Fork before node_b
        forked = executor.fork(parent_id, before_node_id="node_b")
        fork_id = forked.run_id

        # Verify that node_a is completed (since it ran before node_b)
        self.assertTrue(forked.is_completed("node_a"))
        self.assertEqual(forked._store.load(fork_id, "node_a").content, "result A")

        # Verify that node_b is NOT completed (since we forked before it)
        self.assertFalse(forked.is_completed("node_b"))

        # Verify that node_c is NOT completed (since it ran after node_b)
        self.assertFalse(forked.is_completed("node_c"))


if __name__ == "__main__":
    unittest.main()
