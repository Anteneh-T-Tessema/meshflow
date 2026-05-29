"""Task — CrewAI-compatible task abstraction for MeshFlow.

Usage (CrewAI style):
    from meshflow import Agent, Task, Crew, Process

    analyst = Agent(name="analyst", role="researcher", model="claude-sonnet-4-6")
    writer  = Agent(name="writer",  role="executor",   model="claude-sonnet-4-6")

    research = Task(
        description="Research {topic} and list the top 5 findings.",
        expected_output="A bullet list of 5 key findings with confidence scores.",
        agent=analyst,
    )
    report = Task(
        description="Write an executive summary from the research findings.",
        expected_output="A 2-paragraph executive summary ready for board review.",
        agent=writer,
        context=[research],          # receives research.output automatically
    )

    crew = Crew(agents=[analyst, writer], tasks=[research, report])
    result = await crew.kickoff(inputs={"topic": "agentic AI"})
    print(result.raw)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskOutput:
    """The result of a single Task execution."""

    raw: str
    task_description: str = ""
    agent_name: str = ""
    tokens: int = 0
    cost_usd: float = 0.0

    def __str__(self) -> str:
        return self.raw

    def __repr__(self) -> str:
        preview = self.raw[:80].replace("\n", " ")
        return f"TaskOutput(agent={self.agent_name!r}, preview={preview!r})"


@dataclass
class Task:
    """A unit of work assigned to one Agent.

    Parameters
    ----------
    description:     What to do.  Supports ``{placeholder}`` substitution from
                     ``kickoff(inputs={"placeholder": "value"})``.
    expected_output: What a successful completion looks like (fed to the agent).
    agent:           The Agent responsible for this task.
    human_input:     If True, pause after the agent replies to request human review.
    context:         List of prior Tasks whose output is prepended as context.
    tools:           Extra tools available only for this task (merged with agent tools).
    output:          Filled in after the task runs; None beforehand.
    """

    description: str
    expected_output: str
    agent: Any = None              # Agent instance
    human_input: bool = False
    context: list["Task"] | None = None
    tools: list[Any] = field(default_factory=list)
    knowledge: list[Any] = field(default_factory=list)   # str | VectorStore | KnowledgeSource
    output: TaskOutput | None = field(default=None, init=False, repr=False)
    max_context_chars: int = 8000  # max chars of injected prior-task context; 0 = unlimited
    context_filter: Any = None
    """Optional callable ``(task: Task, output: TaskOutput) -> bool``.

    When set, only tasks whose output passes the filter are injected as context.
    Built-in factories: ``confidence_filter(min_conf)``, ``tag_filter(tags)``.

    Example::

        from meshflow.agents.task import Task, confidence_filter

        review = Task(
            description="Review the research",
            expected_output="Feedback",
            agent=critic,
            context=[research_task, data_task],
            context_filter=confidence_filter(0.80),  # only inject if confidence >= 0.80
        )
    """

    def _build_prompt(self, inputs: dict[str, Any] | None) -> str:
        desc = self.description
        if inputs:
            for k, v in inputs.items():
                desc = desc.replace(f"{{{k}}}", str(v))

        parts = [
            f"Task: {desc}",
            f"Expected output: {self.expected_output}",
        ]

        if self.context:
            ctx_sections = []
            seen_hashes: set[str] = set()
            for t in self.context:
                if t.output is not None and (
                    self.context_filter is None
                    or self.context_filter(t, t.output)
                ):
                    raw = t.output.raw
                    # Deduplicate: skip if identical output was already injected
                    import hashlib
                    h = hashlib.md5(raw.encode()).hexdigest()
                    if h in seen_hashes:
                        continue
                    seen_hashes.add(h)
                    ctx_sections.append(
                        f"--- Output from '{t.description[:60]}' ---\n{raw}"
                    )
            if ctx_sections:
                combined = "\n\n".join(ctx_sections)
                # Enforce max_context_chars cap — truncate oldest sections first
                if self.max_context_chars > 0 and len(combined) > self.max_context_chars:
                    # Rebuild from newest sections first until budget is exhausted
                    budget = self.max_context_chars
                    kept: list[str] = []
                    for sec in reversed(ctx_sections):
                        if len(sec) <= budget:
                            kept.append(sec)
                            budget -= len(sec)
                        else:
                            kept.append(sec[:budget] + "\n[...truncated]")
                            break
                    combined = "\n\n".join(reversed(kept))
                parts.append("Context from prior tasks:\n" + combined)

        return "\n\n".join(parts)

    async def run(self, inputs: dict[str, Any] | None = None) -> TaskOutput:
        """Execute the task with its assigned agent and return the output."""
        if self.agent is None:
            raise ValueError(f"Task has no agent assigned: {self.description[:60]!r}")

        prompt = self._build_prompt(inputs)

        # Inject per-task knowledge into prompt if provided
        knowledge_ctx = ""
        if self.knowledge:
            from meshflow.intelligence.knowledge import AgentKnowledge
            ak = AgentKnowledge(self.knowledge)
            k_text = ak.context_string(prompt, max_chars=1200)
            if k_text:
                knowledge_ctx = f"\n\n[Task Knowledge]\n{k_text}"
        if knowledge_ctx:
            prompt = prompt + knowledge_ctx

        extra_tools = list(self.tools)
        if extra_tools:
            original_tools = list(self.agent.tools)
            self.agent.tools = original_tools + extra_tools

        try:
            result = await self.agent.run(prompt)
        finally:
            if extra_tools:
                self.agent.tools = original_tools  # type: ignore[possibly-undefined]

        self.output = TaskOutput(
            raw=result.get("result", ""),
            task_description=self.description[:120],
            agent_name=result.get("agent_name", getattr(self.agent, "name", "")),
            tokens=result.get("tokens", 0),
            cost_usd=result.get("cost_usd", 0.0),
        )
        return self.output


# ── Built-in context filter factories ────────────────────────────────────────

def confidence_filter(min_confidence: float = 0.80) -> Any:
    """Return a context filter that only injects tasks with stated confidence >= *min_confidence*.

    The confidence is extracted from the raw output text (``CONFIDENCE:0.XX``
    marker).  Tasks without a confidence marker are included by default.

    Usage::

        review = Task(
            description="Review findings",
            expected_output="Issues list",
            agent=critic,
            context=[research, data],
            context_filter=confidence_filter(0.80),
        )
    """
    import re as _re
    _CONF_RE = _re.compile(r"CONFIDENCE:\s*(0?\.\d+|1\.0)", _re.IGNORECASE)

    def _filter(task: "Task", output: TaskOutput) -> bool:
        m = _CONF_RE.search(output.raw)
        if not m:
            return True  # no marker → include
        try:
            return float(m.group(1)) >= min_confidence
        except ValueError:
            return True

    return _filter


def tag_filter(*required_tags: str) -> Any:
    """Return a context filter that only injects tasks whose description contains ALL *required_tags*.

    Tags are matched case-insensitively against the task description.

    Usage::

        synthesis = Task(
            description="Synthesise results",
            expected_output="Summary",
            agent=writer,
            context=[task_a, task_b, task_c],
            context_filter=tag_filter("verified", "high-quality"),
        )
    """
    def _filter(task: "Task", output: TaskOutput) -> bool:
        desc_lower = task.description.lower()
        return all(tag.lower() in desc_lower for tag in required_tags)

    return _filter


def min_length_filter(min_chars: int = 50) -> Any:
    """Only inject context whose output is at least *min_chars* characters."""
    def _filter(task: "Task", output: TaskOutput) -> bool:
        return len(output.raw.strip()) >= min_chars

    return _filter


__all__ = [
    "Task", "TaskOutput",
    "confidence_filter", "tag_filter", "min_length_filter",
]
