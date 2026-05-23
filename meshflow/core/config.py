"""Declarative YAML workflow config — Docker Compose-style workflow definitions.

Write your entire multi-agent system in a single meshflow.yaml and load it
with one function call:

    mesh = meshflow.load("meshflow.yaml")
    result = await mesh.run("Build a research report on LLMs")

Example meshflow.yaml:
----------------------
version: "1.0"

policy:
  mode: regulated
  budget_usd: 5.0
  max_steps: 20

agents:
  - name: planner
    role: planner
    model: claude-sonnet-4-6

  - name: researcher
    role: researcher
    model: claude-sonnet-4-6
    memory: true
    tools:
      - web_search
      - read_file

  - name: writer
    role: executor
    model: claude-haiku-4-5-20251001
    system_prompt: "Write clear, concise reports."

  - name: critic
    role: critic
    model: claude-sonnet-4-6

team:
  name: research_team
  pattern: supervised     # sequential | parallel | hierarchical | supervised | reflective
  agents: [planner, researcher, writer, critic]

# OR define a workflow graph directly:
workflow:
  name: pipeline
  nodes:
    - id: ingest
      agent: researcher
    - id: analyze
      agent: analyst
    - id: report
      agent: writer
  edges:
    - from: ingest
      to: analyze
    - from: analyze
      to: report
  terminal: report
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


# ── Schema validation helpers ─────────────────────────────────────────────────

_REQUIRED_KEYS = {"version"}
_VALID_PATTERNS = {"sequential", "parallel", "hierarchical", "supervised", "reflective"}
_VALID_MODES = {"dev", "standard", "regulated", "legal-critical", "hipaa"}


def _require(d: dict, key: str, context: str) -> Any:
    if key not in d:
        raise ValueError(f"meshflow.yaml [{context}] is missing required field '{key}'")
    return d[key]


# ── Policy parsing ────────────────────────────────────────────────────────────

def _parse_policy(raw: dict[str, Any] | str | None) -> "Any":
    from meshflow.core.schemas import policy_for_mode

    if raw is None:
        return policy_for_mode("standard")
    if isinstance(raw, str):
        return policy_for_mode(raw)

    mode = raw.get("mode", "standard")
    overrides = {k: v for k, v in raw.items() if k != "mode"}
    return policy_for_mode(mode, **overrides)


# ── Agent parsing ─────────────────────────────────────────────────────────────

def _parse_agents(raw_list: list[dict], tool_registry: Any = None) -> dict[str, Any]:
    """Build a name → Agent mapping from the YAML agent declarations."""
    from meshflow.agents.builder import Agent
    from meshflow.core.schemas import RiskTier

    agents: dict[str, Any] = {}

    for raw in raw_list:
        name = _require(raw, "name", "agents[]")
        role = raw.get("role", "executor")
        model = raw.get("model", "claude-sonnet-4-6")
        memory = raw.get("memory", False)
        system_prompt = raw.get("system_prompt", "")
        risk_str = raw.get("risk", "read_only").upper()
        risk = getattr(RiskTier, risk_str, RiskTier.READ_ONLY)

        # Resolve tool names to Tool objects if registry provided
        tool_names: list[str] = raw.get("tools", [])
        tools: list[Any] = []
        if tool_registry and tool_names:
            for tname in tool_names:
                tool = tool_registry.get(tname)
                if tool:
                    tools.append(tool)

        agent = Agent(
            name=name,
            role=role,
            model=model,
            tools=tools,
            memory=memory,
            system_prompt=system_prompt,
            risk=risk,
        )
        agents[name] = agent

    return agents


# ── Team parsing ──────────────────────────────────────────────────────────────

def _parse_team(raw: dict, agents: dict[str, Any], policy: Any) -> Any:
    from meshflow.agents.team import Team

    name = raw.get("name", "team")
    pattern = raw.get("pattern", "sequential")
    if pattern not in _VALID_PATTERNS:
        raise ValueError(
            f"meshflow.yaml [team.pattern] must be one of {_VALID_PATTERNS}, got '{pattern}'"
        )

    agent_names: list[str] = raw.get("agents", list(agents.keys()))
    team_agents: list[Any] = []
    for aname in agent_names:
        if aname not in agents:
            raise ValueError(
                f"meshflow.yaml [team.agents] references unknown agent '{aname}'. "
                f"Defined agents: {list(agents.keys())}"
            )
        team_agents.append(agents[aname])

    return Team(name=name, agents=team_agents, pattern=pattern, policy=policy)


# ── Workflow graph parsing ────────────────────────────────────────────────────

def _parse_workflow(raw: dict, agents: dict[str, Any], policy: Any) -> Any:
    from meshflow.core.workflow import WorkflowDefinition

    name = raw.get("name", "workflow")
    wf = WorkflowDefinition(name, policy=policy)

    node_defs: list[dict] = raw.get("nodes", [])
    node_map: dict[str, Any] = {}

    for nd in node_defs:
        node_id = _require(nd, "id", "workflow.nodes[]")
        agent_name = _require(nd, "agent", f"workflow.nodes[{node_id}]")
        if agent_name not in agents:
            raise ValueError(
                f"meshflow.yaml [workflow.nodes] node '{node_id}' "
                f"references unknown agent '{agent_name}'"
            )
        mesh_node = agents[agent_name].to_mesh_node()
        # Override the node id to match the workflow node id (not the agent name)
        mesh_node.id = node_id
        node_map[node_id] = mesh_node
        wf.add_node(mesh_node)

    for edge in raw.get("edges", []):
        src = _require(edge, "from", "workflow.edges[]")
        dst = _require(edge, "to", f"workflow.edges[from={src}]")
        condition = edge.get("condition")
        if condition:
            wf.add_conditional_edge(src, dst, condition)
        else:
            wf.add_edge(src, dst)

    terminal = raw.get("terminal")
    if terminal:
        wf.set_terminal(terminal)

    return wf


# ── GroupChat parsing ─────────────────────────────────────────────────────────

def _parse_groupchat(raw: dict, agents: dict[str, Any], policy: Any) -> Any:
    from meshflow.agents.conversation import GroupChat, GroupChatManager

    agent_names: list[str] = raw.get("agents", list(agents.keys()))
    chat_agents = [agents[n] for n in agent_names if n in agents]

    chat = GroupChat(
        agents=chat_agents,
        max_turns=raw.get("max_turns", 20),
        speaker_selection=raw.get("speaker_selection", "round_robin"),
        termination=raw.get("termination", "TERMINATE"),
    )
    return GroupChatManager(chat, policy=policy)


# ── MeshFlowConfig ────────────────────────────────────────────────────────────

class MeshFlowConfig:
    """Parsed configuration from a meshflow.yaml file.

    Attributes
    ----------
    policy:     Governance policy.
    agents:     name → Agent mapping.
    team:       Team object if [team] is defined, else None.
    workflow:   WorkflowDefinition if [workflow] is defined, else None.
    groupchat:  GroupChatManager if [groupchat] is defined, else None.
    raw:        Original parsed YAML dict.
    """

    def __init__(self, raw: dict[str, Any], tool_registry: Any = None) -> None:
        self.raw = raw
        self.policy = _parse_policy(raw.get("policy"))
        self.agents = _parse_agents(raw.get("agents", []), tool_registry)

        self.team = None
        self.workflow = None
        self.groupchat = None

        if "team" in raw:
            self.team = _parse_team(raw["team"], self.agents, self.policy)
        if "workflow" in raw:
            self.workflow = _parse_workflow(raw["workflow"], self.agents, self.policy)
        if "groupchat" in raw:
            self.groupchat = _parse_groupchat(raw["groupchat"], self.agents, self.policy)

        if not self.team and not self.workflow and not self.groupchat and self.agents:
            # Default: sequential team of all agents
            from meshflow.agents.team import Team
            self.team = Team(
                name="default_team",
                agents=list(self.agents.values()),
                pattern="sequential",
                policy=self.policy,
            )

    async def run(self, task: str, **kwargs: Any) -> Any:
        """Run the configured workflow, team, or groupchat on a task."""
        if self.workflow:
            from meshflow.core.mesh import Mesh
            return await Mesh(policy=self.policy).run_workflow(self.workflow, task, **kwargs)
        if self.groupchat:
            return await self.groupchat.run(task, kwargs or None)
        if self.team:
            return await self.team.run(task, kwargs or None)
        raise RuntimeError("No runnable component defined in meshflow.yaml")


# ── Public API ────────────────────────────────────────────────────────────────

def load(
    path: str | Path,
    tool_registry: Any = None,
    env_expand: bool = True,
) -> MeshFlowConfig:
    """Load a meshflow.yaml file and return a runnable MeshFlowConfig.

    Parameters
    ----------
    path:          Path to the YAML file.
    tool_registry: Optional ToolRegistry for resolving tool names.
    env_expand:    Expand ``${VAR}`` environment variable references in YAML values.

    Raises
    ------
    FileNotFoundError: If the path does not exist.
    ValueError:        If the YAML is invalid or missing required fields.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"meshflow.yaml not found: {p.absolute()}")

    text = p.read_text(encoding="utf-8")

    if env_expand:
        text = os.path.expandvars(text)

    raw: dict[str, Any] = yaml.safe_load(text) or {}

    if "version" not in raw:
        raise ValueError("meshflow.yaml must declare a 'version' field.")

    return MeshFlowConfig(raw, tool_registry)


def loads(
    text: str,
    tool_registry: Any = None,
) -> MeshFlowConfig:
    """Load meshflow config from a YAML string (for programmatic use / tests)."""
    raw: dict[str, Any] = yaml.safe_load(text) or {}
    if "version" not in raw:
        raise ValueError("meshflow yaml must declare a 'version' field.")
    return MeshFlowConfig(raw, tool_registry)


__all__ = ["MeshFlowConfig", "load", "loads"]
