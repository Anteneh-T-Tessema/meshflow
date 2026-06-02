"""Code transformer — suggest MeshFlow-compatible diffs for existing agent files.

Strategy: regex-based pattern matching (no AST).  Returns *suggestions* rather
than silently rewriting code so that developers can review before applying.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


# ── Import replacement table ──────────────────────────────────────────────────

# (search_pattern, replacement_line, description)
_IMPORT_REPLACEMENTS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"from langgraph\.graph import StateGraph"),
        "from meshflow import StateGraph  # meshflow: zero-rewrite compatible",
        "Replace LangGraph StateGraph import with MeshFlow-compatible StateGraph",
    ),
    (
        re.compile(r"from langgraph\.graph import\s+(.+)"),
        "from meshflow.integrations.langgraph import {groups}  # meshflow: adapted",
        "Redirect LangGraph graph imports to MeshFlow LangGraph integration",
    ),
    (
        re.compile(r"from langgraph\b(.*)"),
        "from meshflow.integrations.langgraph import MeshFlowLangGraph  # meshflow: adapted",
        "Redirect top-level langgraph import to MeshFlow integration layer",
    ),
    (
        re.compile(r"from crewai\b(.*)"),
        "from meshflow.integrations.crewai import MeshFlowCrewAI  # meshflow: adapted",
        "Redirect crewai import to MeshFlow CrewAI integration layer",
    ),
    (
        re.compile(r"import crewai\b(.*)"),
        "import meshflow.integrations.crewai as crewai  # meshflow: adapted",
        "Redirect crewai module import to MeshFlow integration layer",
    ),
    (
        re.compile(r"from (autogen|pyautogen)\b(.*)"),
        "from meshflow.integrations.autogen import MeshFlowAutoGen  # meshflow: adapted",
        "Redirect autogen import to MeshFlow AutoGen integration layer",
    ),
    (
        re.compile(r"import (autogen|pyautogen)\b(.*)"),
        "import meshflow.integrations.autogen as autogen  # meshflow: adapted",
        "Redirect autogen module import to MeshFlow integration layer",
    ),
]

# Pattern to detect an agent function/class definition without govern()
_AGENT_FUNCTION_PATTERN = re.compile(
    r"^(async\s+def|def)\s+(\w*agent\w*|run_agent|\w+_agent)\s*\(",
    re.IGNORECASE | re.MULTILINE,
)

# Pattern to detect class inheriting from Agent-like base
_AGENT_CLASS_PATTERN = re.compile(
    r"^class\s+(\w+)\s*\(.*Agent.*\)\s*:",
    re.MULTILINE,
)

# Pattern to detect existing govern() wrapper
_GOVERN_PATTERN = re.compile(r"\bgovern\s*\(")

# Pattern to detect existing CostCap
_COST_CAP_PATTERN = re.compile(r"\bCostCap\s*\(")

# Pattern to detect existing compliance_profile
_COMPLIANCE_PATTERN = re.compile(r"\bcompliance_profile\s*\(")


@dataclass
class Change:
    """A single suggested code change."""

    line_number: int          # 1-based; 0 = file-level addition
    original: str             # original line content (empty for insertions)
    replacement: str          # suggested replacement / addition
    description: str          # human-readable explanation
    change_type: str          # "replace" | "insert_before" | "insert_after" | "insert_top"


@dataclass
class TransformResult:
    """Result of transforming a single Python source file."""

    original_path: str
    suggested_changes: list[Change] = field(default_factory=list)
    rewrite_required: bool = False

    def has_changes(self) -> bool:
        return bool(self.suggested_changes)

    def apply(self, dry_run: bool = False) -> str:
        """Apply *suggested_changes* and return the transformed source.

        If *dry_run* is True the file is never written — only the new source
        string is returned.
        """
        source = Path(self.original_path).read_text(encoding="utf-8")
        lines = source.splitlines(keepends=True)

        # Process insertions at the top first (reverse order to keep line numbers stable)
        top_inserts: list[str] = []
        line_changes: dict[int, Change] = {}

        for change in self.suggested_changes:
            if change.change_type == "insert_top":
                top_inserts.append(change.replacement + "\n")
            else:
                line_changes[change.line_number] = change

        new_lines: list[str] = []
        if top_inserts:
            # Insert after the __future__ import block if present
            future_end = 0
            for i, line in enumerate(lines):
                if line.startswith("from __future__") or line.startswith("import __future__"):
                    future_end = i + 1
                elif future_end and line.strip() and not line.startswith("#"):
                    break
            # Splice in top inserts
            new_lines.extend(lines[:future_end])
            new_lines.extend(top_inserts)
            new_lines.extend(lines[future_end:])
            lines = new_lines
            new_lines = []

        for i, line in enumerate(lines):
            lineno = i + 1
            if lineno in line_changes:
                change = line_changes[lineno]
                if change.change_type == "replace":
                    new_lines.append(change.replacement + "\n")
                elif change.change_type == "insert_before":
                    new_lines.append(change.replacement + "\n")
                    new_lines.append(line)
                elif change.change_type == "insert_after":
                    new_lines.append(line)
                    new_lines.append(change.replacement + "\n")
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)

        result_source = "".join(new_lines)

        if not dry_run:
            Path(self.original_path).write_text(result_source, encoding="utf-8")

        return result_source


class CodeTransformer:
    """Analyse a Python file and build a list of suggested MeshFlow changes."""

    def transform(self, file_path: str | Path) -> TransformResult:
        """Return a :class:`TransformResult` for *file_path*."""
        path = Path(file_path)
        source = path.read_text(encoding="utf-8", errors="ignore")
        lines = source.splitlines()

        result = TransformResult(original_path=str(path))

        self._suggest_import_replacements(lines, result)
        self._suggest_govern_wrapper(source, lines, result)
        self._suggest_cost_cap(source, result)
        self._suggest_compliance_profile(source, result)

        # If there are agent classes we can't automatically wrap → flag rewrite
        if _AGENT_CLASS_PATTERN.search(source) and not _GOVERN_PATTERN.search(source):
            result.rewrite_required = True

        return result

    # ── Private helpers ───────────────────────────────────────────────────────

    def _suggest_import_replacements(
        self, lines: list[str], result: TransformResult
    ) -> None:
        for i, line in enumerate(lines):
            stripped = line.strip()
            for pattern, replacement_template, description in _IMPORT_REPLACEMENTS:
                m = pattern.search(stripped)
                if m:
                    # Expand {groups} placeholder if present
                    if "{groups}" in replacement_template and m.lastindex:
                        groups_str = m.group(1) if m.lastindex >= 1 else ""
                        replacement = replacement_template.format(groups=groups_str)
                    else:
                        replacement = replacement_template
                    # Preserve indentation
                    indent = len(line) - len(line.lstrip())
                    result.suggested_changes.append(
                        Change(
                            line_number=i + 1,
                            original=line.rstrip(),
                            replacement=" " * indent + replacement,
                            description=description,
                            change_type="replace",
                        )
                    )
                    break  # one replacement per line

    def _suggest_govern_wrapper(
        self, source: str, lines: list[str], result: TransformResult
    ) -> None:
        if _GOVERN_PATTERN.search(source):
            return  # already wrapped

        # Check for agent functions
        for m in _AGENT_FUNCTION_PATTERN.finditer(source):
            line_no = source[: m.start()].count("\n") + 1
            fn_line = lines[line_no - 1] if line_no <= len(lines) else ""
            indent = len(fn_line) - len(fn_line.lstrip())
            result.suggested_changes.append(
                Change(
                    line_number=line_no,
                    original=fn_line.rstrip(),
                    replacement=" " * indent + "@govern()  # meshflow: add governance wrapper",
                    description=(
                        "Add @govern() decorator to enforce governance policies "
                        "(DascGate, PII blockers, budget trackers, ledger logging)"
                    ),
                    change_type="insert_before",
                )
            )
            # Also suggest the import at the top of the file
            if not any(
                "from meshflow" in c.replacement and "govern" in c.replacement
                for c in result.suggested_changes
            ):
                result.suggested_changes.append(
                    Change(
                        line_number=0,
                        original="",
                        replacement="from meshflow.governance import govern",
                        description="Import govern decorator from MeshFlow",
                        change_type="insert_top",
                    )
                )

    def _suggest_cost_cap(self, source: str, result: TransformResult) -> None:
        if _COST_CAP_PATTERN.search(source):
            return  # already present

        # Only suggest if there's evidence of agent activity
        has_agent = bool(
            _AGENT_FUNCTION_PATTERN.search(source) or _AGENT_CLASS_PATTERN.search(source)
        )
        if not has_agent:
            return

        result.suggested_changes.append(
            Change(
                line_number=0,
                original="",
                replacement=(
                    "from meshflow.core.schemas import Policy\n"
                    "# meshflow: add cost cap — e.g. policy = Policy(budget_usd=1.00)"
                ),
                description=(
                    "Add CostCap / Policy with budget_usd to prevent runaway spend"
                ),
                change_type="insert_top",
            )
        )

    def _suggest_compliance_profile(self, source: str, result: TransformResult) -> None:
        if _COMPLIANCE_PATTERN.search(source):
            return

        has_agent = bool(
            _AGENT_FUNCTION_PATTERN.search(source) or _AGENT_CLASS_PATTERN.search(source)
        )
        if not has_agent:
            return

        result.suggested_changes.append(
            Change(
                line_number=0,
                original="",
                replacement=(
                    "# meshflow: set compliance profile — e.g.\n"
                    "# from meshflow.compliance import ComplianceGuard\n"
                    "# guard = ComplianceGuard(framework='hipaa')"
                ),
                description="Add compliance_profile() to declare regulatory scope",
                change_type="insert_top",
            )
        )
