"""Agent composition utilities — pipe and chain agents without a full WorkflowDefinition.

Closes the "agent composition" gap: instead of building an entire YAML workflow to
connect two or three agents, you can compose them inline as a simple callable.

Two composition patterns
------------------------
``pipe(agent_a, agent_b, ...)``
    Sequential pipeline: each agent's ``result`` becomes the next agent's task.
    Returns the final agent's result dict.

``parallel(agent_a, agent_b, ...)``
    Run all agents concurrently on the same task, collect all results.

``AgentPipeline``
    Fluent builder: ``AgentPipeline().pipe(a).pipe(b).run(task)``.

Usage::

    from meshflow import Agent
    from meshflow.agents.compose import pipe, parallel, AgentPipeline

    researcher = Agent(name="researcher", role="researcher")
    writer     = Agent(name="writer",     role="executor")
    critic     = Agent(name="critic",     role="critic")

    # Sequential pipe
    result = await pipe(researcher, writer)(task="Write about AI governance")

    # Parallel
    results = await parallel(researcher, writer)(task="Summarise AI trends")
    print(results["researcher"]["result"], results["writer"]["result"])

    # Fluent builder
    pipeline = AgentPipeline().pipe(researcher).pipe(writer).branch(critic)
    result = await pipeline.run("Explain HIPAA compliance")
"""

from __future__ import annotations

import asyncio
from typing import Any


# ── Sequential pipe ───────────────────────────────────────────────────────────

class _PipedAgents:
    """Callable that chains *agents* sequentially."""

    def __init__(self, agents: list[Any], context_key: str = "result") -> None:
        self._agents = agents
        self._context_key = context_key

    async def __call__(
        self,
        task: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ctx = dict(context or {})
        result: dict[str, Any] = {}
        current_task = task

        for agent in self._agents:
            result = await agent.run(current_task, ctx)
            # The output of this agent becomes the task for the next
            current_task = result.get("result", "") or current_task
            # Also merge structured output into context for downstream agents
            ctx.update({
                f"_{getattr(agent, 'name', 'agent')}_output": current_task,
            })

        return result

    def pipe(self, *more_agents: Any) -> "_PipedAgents":
        """Extend the pipeline with more agents."""
        return _PipedAgents(self._agents + list(more_agents), self._context_key)


def pipe(*agents: Any, context_key: str = "result") -> _PipedAgents:
    """Return a callable that chains *agents* sequentially.

    Each agent receives the ``result`` field of the previous agent as its task.

    Usage::

        chain = pipe(researcher, writer)
        final = await chain("Research and write about HIPAA")

        # Or inline
        final = await pipe(researcher, writer)("Research and write about HIPAA")
    """
    return _PipedAgents(list(agents), context_key)


# ── Parallel run ──────────────────────────────────────────────────────────────

class _ParallelAgents:
    """Callable that runs *agents* concurrently on the same task."""

    def __init__(self, agents: list[Any]) -> None:
        self._agents = agents

    async def __call__(
        self,
        task: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, dict[str, Any]]:
        ctx = dict(context or {})
        tasks = [agent.run(task, ctx.copy()) for agent in self._agents]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {
            getattr(agent, "name", f"agent_{i}"): (
                result if not isinstance(result, Exception)
                else {"result": "", "error": str(result)}
            )
            for i, (agent, result) in enumerate(zip(self._agents, results))
        }


def parallel(*agents: Any) -> _ParallelAgents:
    """Return a callable that runs all *agents* concurrently on the same task.

    Returns a dict keyed by agent name.

    Usage::

        results = await parallel(researcher_a, researcher_b)("What is LangGraph?")
        print(results["researcher_a"]["result"])
        print(results["researcher_b"]["result"])
    """
    return _ParallelAgents(list(agents))


# ── AgentPipeline (fluent builder) ────────────────────────────────────────────

class AgentPipeline:
    """Fluent builder for composing agents into a pipeline.

    Supports sequential ``pipe()``, parallel ``branch()``, and conditional
    ``when()`` steps.

    Usage::

        pipeline = (
            AgentPipeline()
            .pipe(researcher)
            .branch(writer, critic)  # both run on researcher output
            .pipe(synthesiser)       # synthesiser receives all branch outputs
        )
        result = await pipeline.run("Write a report on AI safety")
    """

    def __init__(self) -> None:
        self._steps: list[dict[str, Any]] = []

    def pipe(self, agent: Any) -> "AgentPipeline":
        """Add a sequential step."""
        self._steps.append({"type": "sequential", "agents": [agent]})
        return self

    def branch(self, *agents: Any) -> "AgentPipeline":
        """Add a parallel branch step — all agents run on the same input."""
        self._steps.append({"type": "parallel", "agents": list(agents)})
        return self

    def when(
        self,
        condition: Any,
        true_agent: Any,
        false_agent: Any | None = None,
    ) -> "AgentPipeline":
        """Add a conditional step.

        *condition* can be:
        - A callable ``(result: str) -> bool``
        - A string keyword: if the keyword appears in the previous result, run *true_agent*.
        """
        self._steps.append({
            "type": "conditional",
            "condition": condition,
            "true_agent": true_agent,
            "false_agent": false_agent,
        })
        return self

    async def run(
        self,
        task: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute the pipeline and return the final result dict."""
        ctx = dict(context or {})
        current_task = task
        last_result: dict[str, Any] = {}

        for step in self._steps:
            step_type = step["type"]

            if step_type == "sequential":
                agent = step["agents"][0]
                last_result = await agent.run(current_task, ctx)
                output = last_result.get("result", "") or current_task
                current_task = output
                ctx[f"_{getattr(agent, 'name', 'agent')}_output"] = output

            elif step_type == "parallel":
                results = await parallel(*step["agents"])(current_task, ctx)
                # Merge all outputs; last_result is the combined dict
                combined_text = "\n\n".join(
                    f"[{name}] {r.get('result', '')}"
                    for name, r in results.items()
                )
                last_result = {
                    "result": combined_text,
                    "branch_results": results,
                    "agent_name": "branch",
                }
                current_task = combined_text
                ctx["_branch_outputs"] = results

            elif step_type == "conditional":
                cond = step["condition"]
                if callable(cond):
                    fires = cond(current_task)
                else:
                    fires = str(cond).lower() in current_task.lower()

                agent = step["true_agent"] if fires else step.get("false_agent")
                if agent is not None:
                    last_result = await agent.run(current_task, ctx)
                    current_task = last_result.get("result", "") or current_task

        return last_result or {"result": current_task}

    def __repr__(self) -> str:
        steps = " → ".join(
            f"[{s['type']}:{','.join(getattr(a,'name',str(a)) for a in s['agents'])}]"
            for s in self._steps
        )
        return f"AgentPipeline({steps})"


__all__ = ["pipe", "parallel", "AgentPipeline", "_PipedAgents", "_ParallelAgents"]
