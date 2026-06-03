"""CrewAI-compatible Pipeline — chain multiple Crews sequentially or in parallel.

A Pipeline connects independent Crews so the output of one stage feeds as
input to the next.  This is the Crew-level equivalent of LangGraph's sequential
graph or CrewAI's ``Pipeline`` class.

Usage::

    from meshflow import Agent, Task, Crew, Process
    from meshflow.agents.pipeline import Pipeline, PipelineKickoffResult

    # Stage 1 — Research crew
    researcher = Agent(name="researcher", role="researcher")
    research_task = Task(description="Research {topic}", expected_output="5 findings", agent=researcher)
    research_crew = Crew(agents=[researcher], tasks=[research_task])

    # Stage 2 — Writing crew
    writer = Agent(name="writer", role="executor")
    write_task = Task(description="Write a report on the research", expected_output="Report", agent=writer)
    writing_crew = Crew(agents=[writer], tasks=[write_task])

    # Chain them
    pipeline = Pipeline(stages=[research_crew, writing_crew])
    result = pipeline.kickoff(inputs={"topic": "AI governance"})

    print(result.final_output)          # final stage output
    print(result.stage_outputs)         # per-stage CrewOutputs
    print(result.total_tokens)          # total across all stages

Parallel stages
---------------
Wrap a list of Crews in another list to run them in parallel::

    # Stage 1: single crew
    # Stage 2: two crews run concurrently
    # Stage 3: single crew that receives both stage-2 outputs
    pipeline = Pipeline(stages=[crew_a, [crew_b, crew_c], crew_d])
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from meshflow.agents.crew import CrewOutput


@dataclass
class PipelineKickoffResult:
    """Aggregated result from a :class:`Pipeline` run.

    Attributes
    ----------
    final_output:   Raw text output from the last stage.
    stage_outputs:  List of ``CrewOutput`` objects, one per stage (parallel
                    stages produce a list of ``CrewOutput`` at that position).
    total_tokens:   Total tokens consumed across all stages.
    total_cost_usd: Total cost across all stages.
    """

    final_output: str
    stage_outputs: list[Any]   # list[CrewOutput | list[CrewOutput]]
    total_tokens: int = 0
    total_cost_usd: float = 0.0

    def __str__(self) -> str:
        return self.final_output


class Pipeline:
    """Chain of Crew stages executed sequentially (or in parallel sub-stages).

    Parameters
    ----------
    stages:
        Ordered list of Crews (or lists of Crews for parallel sub-stages).
        Output from stage N is passed as ``{"input": output}`` to stage N+1.
    verbose:
        Print stage progress.
    """

    def __init__(
        self,
        stages: list[Any],  # list[Crew | list[Crew]]
        verbose: bool = False,
    ) -> None:
        if not stages:
            raise ValueError("Pipeline requires at least one stage.")
        self.stages = stages
        self.verbose = verbose

    def kickoff(self, inputs: dict[str, Any] | None = None) -> PipelineKickoffResult:
        """Run all stages synchronously."""
        from meshflow.integrations._utils import run_sync
        return run_sync(self.akickoff(inputs))

    async def akickoff(self, inputs: dict[str, Any] | None = None) -> PipelineKickoffResult:
        """Run all stages asynchronously, passing each stage's output to the next."""
        stage_outputs: list[Any] = []
        current_inputs = dict(inputs or {})
        total_tokens = 0
        total_cost = 0.0
        last_output = ""

        for i, stage in enumerate(self.stages):
            if self.verbose:
                label = f"Stage {i+1}/{len(self.stages)}"
                if isinstance(stage, list):
                    print(f"[Pipeline] {label}: {len(stage)} parallel crew(s)")
                else:
                    print(f"[Pipeline] {label}: crew with {len(stage.tasks)} task(s)")

            if isinstance(stage, list):
                # Parallel sub-stage: all crews receive the same inputs
                results = await asyncio.gather(
                    *[crew.kickoff(current_inputs) for crew in stage],
                    return_exceptions=True,
                )
                parallel_outputs: list[CrewOutput] = []
                combined_outputs: list[str] = []
                for r in results:
                    if isinstance(r, Exception):
                        raise r
                    parallel_outputs.append(r)  # type: ignore[arg-type]
                    combined_outputs.append(r.raw)  # type: ignore[union-attr]
                    total_tokens += r.total_tokens  # type: ignore[union-attr]
                    total_cost += r.total_cost_usd  # type: ignore[union-attr]
                stage_outputs.append(parallel_outputs)
                last_output = "\n\n".join(combined_outputs)
                current_inputs = {**current_inputs, "input": last_output}
            else:
                # Sequential stage
                result: CrewOutput = await stage.kickoff(current_inputs)
                stage_outputs.append(result)
                last_output = result.raw
                total_tokens += result.total_tokens
                total_cost += result.total_cost_usd
                current_inputs = {**current_inputs, "input": last_output}

        return PipelineKickoffResult(
            final_output=last_output,
            stage_outputs=stage_outputs,
            total_tokens=total_tokens,
            total_cost_usd=round(total_cost, 6),
        )

    def __repr__(self) -> str:
        n_stages = len(self.stages)
        n_parallel = sum(1 for s in self.stages if isinstance(s, list))
        return f"Pipeline(stages={n_stages}, parallel_stages={n_parallel})"
