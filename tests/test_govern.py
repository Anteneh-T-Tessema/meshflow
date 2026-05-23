from __future__ import annotations

import asyncio

from meshflow import govern
from meshflow.core.schemas import PolicyMode


def test_govern_wraps_sync_callable_with_standard_audit():
    def app(task: str, context: dict) -> str:
        return f"handled:{task}"

    result = asyncio.run(govern(app).run("hello"))

    assert result.completed is True
    assert "handled:hello" in result.output
    assert len(result.steps) == 1


def test_govern_accepts_policy_mode_string():
    def app(task: str, context: dict) -> str:
        return "ok"

    governed = govern(app, policy="dev")

    assert governed._policy.mode == PolicyMode.DEV
