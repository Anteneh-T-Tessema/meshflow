"""Unit tests for GroupChat speaker transition graphs."""
import unittest
from unittest.mock import AsyncMock, MagicMock

from meshflow.agents.conversation import GroupChat, GroupChatManager, ChatMessage
from meshflow.agents.builder import Agent


class TestGroupChatTransitionGraphs(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.a = MagicMock()
        self.a.name = "Alice"
        self.b = MagicMock()
        self.b.name = "Bob"
        self.c = MagicMock()
        self.c.name = "Charlie"
        self.agents = [self.a, self.b, self.c]

    def test_transition_graph_round_robin_constraints(self):
        # Alice can only transition to Bob
        # Bob can only transition to Charlie
        # Charlie can only transition to Alice
        allowed = {
            "Alice": ["Bob"],
            "Bob": ["Charlie"],
            "Charlie": ["Alice"],
        }
        chat = GroupChat(
            agents=self.agents,
            speaker_selection="round_robin",
            allowed_transitions=allowed,
        )

        # Alice speaks first
        self.assertEqual(chat._pick_next(last_speaker=self.a).name, "Bob")
        self.assertEqual(chat._pick_next(last_speaker=self.b).name, "Charlie")
        self.assertEqual(chat._pick_next(last_speaker=self.c).name, "Alice")

    def test_transition_graph_random_constraints(self):
        # Alice can only transition to Bob or Charlie
        allowed = {
            "Alice": ["Bob", "Charlie"],
        }
        chat = GroupChat(
            agents=self.agents,
            speaker_selection="random",
            allowed_transitions=allowed,
        )

        for _ in range(20):
            next_agent = chat._pick_next(last_speaker=self.a)
            self.assertIn(next_agent.name, ["Bob", "Charlie"])

    async def test_auto_select_single_allowed_candidate_skips_llm(self):
        # Alice can only transition to Bob
        allowed = {"Alice": ["Bob"]}
        chat = GroupChat(
            agents=self.agents,
            speaker_selection="auto",
            allowed_transitions=allowed,
        )
        manager = GroupChatManager(chat)

        # Mock selector run (should NOT be called since only 1 candidate Bob is allowed)
        self.a.run = AsyncMock()

        next_agent = await manager._auto_select(chat, last_speaker=self.a)
        self.assertEqual(next_agent.name, "Bob")
        self.a.run.assert_not_called()

    async def test_auto_select_multiple_allowed_candidates_queries_llm(self):
        # Alice can transition to Bob or Charlie
        allowed = {"Alice": ["Bob", "Charlie"]}
        chat = GroupChat(
            agents=self.agents,
            speaker_selection="auto",
            allowed_transitions=allowed,
        )
        manager = GroupChatManager(chat)

        # Mock first agent to act as LLM selector and choose Charlie
        self.a.run = AsyncMock(return_value={"result": "Charlie"})

        next_agent = await manager._auto_select(chat, last_speaker=self.a)
        self.assertEqual(next_agent.name, "Charlie")
        self.a.run.assert_called_once()
        
        # Verify prompt only lists Bob and Charlie
        prompt = self.a.run.call_args[0][0]
        self.assertIn("Bob", prompt)
        self.assertIn("Charlie", prompt)
        self.assertNotIn("Alice", prompt)


if __name__ == "__main__":
    unittest.main()
