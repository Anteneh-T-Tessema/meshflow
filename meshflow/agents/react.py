"""ReActAgent — the agentic loop: Plan → Act → Observe → Reflect → repeat.

This is the foundation of autonomous agent behavior. Without a loop, agents
are one-shot LLM calls. With it, they plan multi-step strategies, use tools,
observe results, and adjust until the task is done.

Usage::

    from meshflow.agents.react import ReActAgent
    from meshflow import Agent, tool, RiskTier

    @tool(name="web_search", description="Search the web", risk=RiskTier.EXTERNAL_IO)
    async def web_search(query: str) -> str:
        return f"Results for: {query}"

    agent = Agent(name="researcher", role="researcher", tools=[web_search])
    react = ReActAgent(agent, max_steps=8)

    result = await react.run("Find the latest HIPAA enforcement actions from 2025")
    print(result.answer)
    print(f"Completed in {result.steps_taken} steps, {result.total_tokens} tokens")
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any



# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class ThoughtStep:
    thought: str
    action: str          # tool name or "Final Answer"
    action_input: Any    # dict for tools, string for Final Answer
    observation: str     # tool result or ""
    step: int
    tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class ReActResult:
    answer: str
    steps: list[ThoughtStep]
    steps_taken: int
    total_tokens: int
    total_cost_usd: float
    finished: bool       # False = hit max_steps without Final Answer
    agent_name: str


# ── Prompt templates ──────────────────────────────────────────────────────────

_SYSTEM = (
    "You are a helpful assistant that solves tasks step by step using available tools.\n\n"
    "STRICT FORMAT — follow this exactly every response:\n"
    "Thought: <your reasoning about what to do next>\n"
    "Action: <tool_name or 'Final Answer'>\n"
    "Action Input: <JSON object for tool, or your final answer string>\n\n"
    "Rules:\n"
    "- Always start with Thought:\n"
    "- Action must be one of the available tools OR exactly 'Final Answer'\n"
    "- Action Input must be valid JSON when calling a tool\n"
    "- When you have enough information, use Action: Final Answer\n"
    "- Never skip steps or combine multiple actions in one response"
)

_INITIAL_PROMPT = """\
Task: {task}

Available tools:
{tools_desc}

Begin. Remember: Thought → Action → Action Input format only.
"""

_CONTINUE_PROMPT = """\
Task: {task}

Available tools:
{tools_desc}

{scratchpad}

Continue. What is your next Thought?
"""


# ── Parser ────────────────────────────────────────────────────────────────────

def _tools_desc(tools: list[Any]) -> str:
    lines = []
    for t in tools:
        name = getattr(t, "name", str(t))
        desc = getattr(t, "description", "")
        lines.append(f"  - {name}: {desc}")
    return "\n".join(lines) or "  (no tools available — use Final Answer directly)"


def _parse_react_output(raw: str) -> tuple[str, str, Any]:
    """Return (thought, action, action_input) from a ReAct-format LLM response."""
    thought = ""
    action = ""
    action_input: Any = ""

    thought_m = re.search(r"Thought:\s*(.+?)(?=\nAction:|\Z)", raw, re.DOTALL | re.IGNORECASE)
    if thought_m:
        thought = thought_m.group(1).strip()

    action_m = re.search(r"Action:\s*(.+?)(?=\nAction Input:|\Z)", raw, re.DOTALL | re.IGNORECASE)
    if action_m:
        action = action_m.group(1).strip()

    input_m = re.search(r"Action Input:\s*(.+)", raw, re.DOTALL | re.IGNORECASE)
    if input_m:
        raw_input = input_m.group(1).strip()
        try:
            action_input = json.loads(raw_input)
        except (json.JSONDecodeError, ValueError):
            action_input = raw_input

    if not action:
        if "Final Answer" in raw:
            action = "Final Answer"
            parts = raw.split("Final Answer", 1)
            action_input = parts[1].strip().lstrip(":").strip() if len(parts) > 1 else raw
        else:
            action = "Final Answer"
            action_input = raw

    return thought, action, action_input


def _format_scratchpad(steps: list[ThoughtStep]) -> str:
    parts = []
    for s in steps:
        parts.append(f"Thought: {s.thought}")
        parts.append(f"Action: {s.action}")
        inp = json.dumps(s.action_input) if isinstance(s.action_input, dict) else str(s.action_input)
        parts.append(f"Action Input: {inp}")
        if s.observation:
            parts.append(f"Observation: {s.observation}")
    return "\n".join(parts)


# ── ReActAgent ────────────────────────────────────────────────────────────────

class ReActAgent:
    """Agentic loop: wraps any MeshFlow Agent in a ReAct (Reason+Act) loop.

    Each thought-act-observe cycle goes through the agent's LLM provider.
    Tool calls are executed locally with the registered tool functions.
    Every cycle is tracked in the step history for full auditability.

    Parameters
    ----------
    agent:
        A MeshFlow ``Agent`` instance with tools registered.
    max_steps:
        Hard limit on thought-act cycles (default 10).
    reflect_every:
        Every N steps, inject a reflection prompt. 0 = never.
    """

    def __init__(self, agent: Any, max_steps: int = 10, reflect_every: int = 0) -> None:
        self._agent = agent
        self._max_steps = max_steps
        self._reflect_every = reflect_every

    async def run(self, task: str, context: dict[str, Any] | None = None) -> ReActResult:
        built = self._agent._build()
        tools = self._agent.tools
        tool_fns: dict[str, Any] = {
            getattr(t, "name", str(t)): getattr(t, "fn", None)
            for t in tools
            if hasattr(t, "fn")
        }

        steps: list[ThoughtStep] = []
        total_tokens = 0
        total_cost = 0.0
        tools_text = _tools_desc(tools)

        for step_n in range(self._max_steps):
            if steps:
                scratchpad = _format_scratchpad(steps)
                prompt = _CONTINUE_PROMPT.format(
                    task=task, tools_desc=tools_text, scratchpad=scratchpad
                )
            else:
                prompt = _INITIAL_PROMPT.format(task=task, tools_desc=tools_text)

            if self._reflect_every and step_n > 0 and step_n % self._reflect_every == 0:
                prompt += (
                    "\n\n[Reflection check] Are you making progress? "
                    "If you are going in circles, switch strategy or give a Final Answer."
                )

            raw, tokens, cost = await built.think(
                [{"role": "user", "content": prompt}],
                system=_SYSTEM,
            )
            total_tokens += tokens
            total_cost += cost

            thought, action, action_input = _parse_react_output(raw)
            is_final = action.strip().lower() in ("final answer", "finalanswer", "final_answer")

            observation = ""
            if not is_final:
                fn = tool_fns.get(action)
                if fn is None:
                    observation = (
                        f"Error: tool '{action}' not found. "
                        f"Available tools: {list(tool_fns)}"
                    )
                else:
                    import asyncio
                    import inspect
                    try:
                        kwargs = action_input if isinstance(action_input, dict) else {"input": str(action_input)}
                        if inspect.iscoroutinefunction(fn):
                            obs = await fn(**kwargs)
                        else:
                            loop = asyncio.get_event_loop()
                            obs = await loop.run_in_executor(None, lambda: fn(**kwargs))
                        observation = str(obs)
                    except Exception as exc:
                        observation = f"Tool error: {exc}"

            steps.append(ThoughtStep(
                thought=thought,
                action=action,
                action_input=action_input,
                observation=observation,
                step=step_n + 1,
                tokens=tokens,
                cost_usd=cost,
            ))

            if is_final:
                answer = str(action_input) if action_input else thought
                return ReActResult(
                    answer=answer,
                    steps=steps,
                    steps_taken=step_n + 1,
                    total_tokens=total_tokens,
                    total_cost_usd=total_cost,
                    finished=True,
                    agent_name=self._agent.name,
                )

        last = steps[-1] if steps else None
        answer = str(last.observation or last.thought) if last else "[no answer]"
        return ReActResult(
            answer=answer,
            steps=steps,
            steps_taken=self._max_steps,
            total_tokens=total_tokens,
            total_cost_usd=total_cost,
            finished=False,
            agent_name=self._agent.name,
        )
