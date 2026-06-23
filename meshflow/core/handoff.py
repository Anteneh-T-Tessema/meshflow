"""SessionHandoff — human-readable markdown session continuity.

Emits and parses a ``STATE_HANDOFF.md``-style note summarising what an agent
session worked on, so the next run — by a human or another agent — can resume
with full context without replaying the entire transcript. It complements
:class:`~meshflow.core.compactor.ContextCompactor` (which manages the token
budget of a live context window): where the compactor optimises for what the
*model* needs to keep going, ``SessionHandoff`` optimises for what a *human*
(or the next session) needs to pick up cold — plain markdown, git-diffable,
skimmable in seconds.

Usage::

    from meshflow.core.handoff import HandoffNote, SessionHandoff

    note = HandoffNote(
        focus="Building an async queue worker with adjustable concurrency",
        decisions=[
            "Added max_concurrency to cap parallel job execution",
            "Added a `processed` flag to Job to avoid re-processing",
        ],
        artifacts=["AsyncQueueWorker class", "Job dataclass"],
        next_action="Wire max_concurrency into the worker's run loop",
    )
    SessionHandoff.write("STATE_HANDOFF.md", note)

    # Next session — resume from where the last one left off
    resumed = SessionHandoff.load("STATE_HANDOFF.md")
    if resumed:
        print(resumed.next_action)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

# Section headings, in file order — mirrors the STATE_HANDOFF.md convention.
_SECTION_TITLES: dict[str, str] = {
    "focus": "Architectural focus",
    "decisions": "Decisions made",
    "artifacts": "Modified or created artifacts",
    "next_action": "Immediate next action",
}

_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$")
_BULLET_RE = re.compile(r"^[*\-]\s+(.*)$")


@dataclass
class HandoffNote:
    """One session's worth of continuity context, ready for markdown round-tripping."""

    focus: str = ""
    decisions: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    next_action: str = ""


class SessionHandoff:
    """Read and write :class:`HandoffNote` as ``STATE_HANDOFF.md``-style markdown."""

    @staticmethod
    def render(note: HandoffNote) -> str:
        """Serialise *note* to markdown matching the STATE_HANDOFF.md convention."""
        lines = [
            f"### {_SECTION_TITLES['focus']}",
            note.focus,
            "",
            f"### {_SECTION_TITLES['decisions']}",
            *(f"* {item}" for item in note.decisions),
            "",
            f"### {_SECTION_TITLES['artifacts']}",
            *(f"- {item}" for item in note.artifacts),
            "",
            f"### {_SECTION_TITLES['next_action']}",
            note.next_action,
        ]
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def parse(markdown: str) -> HandoffNote:
        """Parse STATE_HANDOFF.md-style markdown back into a :class:`HandoffNote`.

        Unknown headings are ignored; missing sections default to empty.
        """
        title_to_field = {title: key for key, title in _SECTION_TITLES.items()}
        sections: dict[str, list[str]] = {key: [] for key in _SECTION_TITLES}
        current: str | None = None

        for raw_line in markdown.splitlines():
            heading = _HEADING_RE.match(raw_line.strip())
            if heading:
                current = title_to_field.get(heading.group(1).strip())
                continue
            if current is None:
                continue
            line = raw_line.strip()
            if line:
                sections[current].append(line)

        def prose(key: str) -> str:
            return " ".join(line for line in sections[key] if not _BULLET_RE.match(line)).strip()

        def bullets(key: str) -> list[str]:
            return [m.group(1).strip() for line in sections[key] if (m := _BULLET_RE.match(line))]

        return HandoffNote(
            focus=prose("focus"),
            decisions=bullets("decisions"),
            artifacts=bullets("artifacts"),
            next_action=prose("next_action"),
        )

    @classmethod
    def write(cls, path: str, note: HandoffNote) -> None:
        """Write *note* to *path* as markdown, overwriting any existing file."""
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(cls.render(note))

    @classmethod
    def load(cls, path: str) -> HandoffNote | None:
        """Load and parse the handoff file at *path*; ``None`` if it doesn't exist."""
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            return cls.parse(fh.read())


__all__ = ["HandoffNote", "SessionHandoff"]
