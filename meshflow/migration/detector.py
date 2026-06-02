"""Project detector — scan a directory and classify its AI-agent framework usage.

Supports:
  - LangGraph  (langgraph)
  - CrewAI     (crewai)
  - AutoGen    (autogen / pyautogen)
  - OpenAI Agents SDK (openai-agents / agents)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence


# ── Patterns ─────────────────────────────────────────────────────────────────

_FRAMEWORK_IMPORT_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "langgraph": [
        re.compile(r"\bfrom\s+langgraph\b"),
        re.compile(r"\bimport\s+langgraph\b"),
    ],
    "crewai": [
        re.compile(r"\bfrom\s+crewai\b"),
        re.compile(r"\bimport\s+crewai\b"),
    ],
    "autogen": [
        re.compile(r"\bfrom\s+(autogen|pyautogen)\b"),
        re.compile(r"\bimport\s+(autogen|pyautogen)\b"),
    ],
    "openai-agents": [
        re.compile(r"\bfrom\s+agents\b"),
        re.compile(r"\bimport\s+agents\b"),
        re.compile(r"\bfrom\s+openai_agents\b"),
        re.compile(r"\bimport\s+openai_agents\b"),
    ],
}

# Patterns that indicate an agent definition (rough count)
_AGENT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bAgent\s*\("),
    re.compile(r"\bAssistantAgent\s*\("),
    re.compile(r"\bConversableAgent\s*\("),
    re.compile(r"class\s+\w+\s*\(\s*\w*Agent\w*\s*\)"),
    re.compile(r"@agent"),
]

# Patterns that indicate a tool definition
_TOOL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"@tool"),
    re.compile(r"\btool\s*=\s*Tool\s*\("),
    re.compile(r"\bTool\s*\("),
    re.compile(r"def\s+\w+\s*\(.*\)\s*->\s*str"),
    re.compile(r"@function_tool"),
]

# Patterns that indicate a workflow / graph
_WORKFLOW_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bStateGraph\s*\("),
    re.compile(r"\bWorkflow\s*\("),
    re.compile(r"\.add_node\s*\("),
    re.compile(r"\.add_edge\s*\("),
    re.compile(r"\bCrew\s*\("),
    re.compile(r"\bGroupChat\s*\("),
    re.compile(r"\bSwarm\b"),
]

# Simple LangGraph StateGraph usage that maps cleanly to zero-rewrite path
_SIMPLE_STATGRAPH_PATTERN = re.compile(r"\bStateGraph\s*\(")


@dataclass
class DetectionResult:
    """Outcome of a directory scan."""

    frameworks: list[str]
    file_count: int
    agent_count: int
    tool_count: int
    complexity: str          # "simple" | "moderate" | "complex"
    migration_path: str      # "zero_rewrite" | "wrapper" | "native"
    estimated_effort: str    # "< 1 hour" | "1-4 hours" | "1-2 days"
    scanned_files: list[str] = field(default_factory=list)

    def __str__(self) -> str:  # pragma: no cover
        fw = ", ".join(self.frameworks) if self.frameworks else "none detected"
        return (
            f"Frameworks  : {fw}\n"
            f"Files       : {self.file_count}\n"
            f"Agents      : {self.agent_count}\n"
            f"Tools       : {self.tool_count}\n"
            f"Complexity  : {self.complexity}\n"
            f"Path        : {self.migration_path}\n"
            f"Effort      : {self.estimated_effort}"
        )


class ProjectDetector:
    """Scan a directory tree and detect which AI frameworks are in use."""

    def __init__(self, path: str | os.PathLike[str] = ".") -> None:
        self.path = Path(path).resolve()

    # ── Public API ────────────────────────────────────────────────────────────

    def detect(self) -> DetectionResult:
        """Return a :class:`DetectionResult` for *self.path*."""
        py_files = self._collect_python_files()

        frameworks: set[str] = set()
        agent_count = 0
        tool_count = 0
        workflow_count = 0
        stategraph_count = 0

        for fpath in py_files:
            try:
                source = Path(fpath).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            # Framework detection
            for fw, patterns in _FRAMEWORK_IMPORT_PATTERNS.items():
                for pat in patterns:
                    if pat.search(source):
                        frameworks.add(fw)
                        break

            agent_count += self._count_matches(source, _AGENT_PATTERNS)
            tool_count += self._count_matches(source, _TOOL_PATTERNS)
            workflow_count += self._count_matches(source, _WORKFLOW_PATTERNS)
            stategraph_count += len(_SIMPLE_STATGRAPH_PATTERN.findall(source))

        complexity = self._classify_complexity(agent_count, tool_count, workflow_count)
        migration_path = self._migration_path(frameworks, complexity, stategraph_count)
        effort = self._effort(migration_path, complexity)

        return DetectionResult(
            frameworks=sorted(frameworks),
            file_count=len(py_files),
            agent_count=agent_count,
            tool_count=tool_count,
            complexity=complexity,
            migration_path=migration_path,
            estimated_effort=effort,
            scanned_files=[str(f) for f in py_files],
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _collect_python_files(self) -> list[Path]:
        if not self.path.exists():
            return []
        files: list[Path] = []
        skip_dirs = {".venv", "venv", "__pycache__", ".git", "node_modules", "site-packages"}
        for root, dirs, names in os.walk(self.path):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for name in names:
                if name.endswith(".py"):
                    files.append(Path(root) / name)
        return files

    @staticmethod
    def _count_matches(source: str, patterns: Sequence[re.Pattern[str]]) -> int:
        total = 0
        for pat in patterns:
            total += len(pat.findall(source))
        return total

    @staticmethod
    def _classify_complexity(agents: int, tools: int, workflows: int) -> str:
        total = agents + tools + workflows
        if total <= 3:
            return "simple"
        if total <= 12:
            return "moderate"
        return "complex"

    @staticmethod
    def _migration_path(
        frameworks: set[str],
        complexity: str,
        stategraph_count: int,
    ) -> str:
        if not frameworks:
            return "native"
        # Pure LangGraph with StateGraph and simple complexity → zero_rewrite
        if frameworks == {"langgraph"} and complexity == "simple" and stategraph_count > 0:
            return "zero_rewrite"
        # Single-framework projects of moderate size → wrapper
        if len(frameworks) == 1 and complexity in ("simple", "moderate"):
            return "wrapper"
        # Complex or multi-framework → native rewrite
        return "native"

    @staticmethod
    def _effort(migration_path: str, complexity: str) -> str:
        if migration_path == "zero_rewrite":
            return "< 1 hour"
        if migration_path == "wrapper":
            return "1-4 hours" if complexity == "simple" else "1-4 hours"
        # native
        if complexity == "simple":
            return "1-4 hours"
        if complexity == "moderate":
            return "1-2 days"
        return "1-2 days"
