"""Multi-dimensional task scoring for model tier routing.

Replaces the raw character-count heuristic with a 5-factor composite score
that better captures task *complexity*, not just task *length*.

Composite formula (all weights sum to 1.0)::

    composite = clip(
        0.35 * length_score          # chars / 2000, capped at 1.0
        + 0.20 * question_density    # "?" marks — proxy for ambiguity
        + 0.20 * conjunction_density # adversative conjunctions — proxy for nuance
        + 0.15 * technical_density   # domain/code keywords
        + 0.10 * tool_score          # tool count / 5, capped at 1.0
    ) * type_multiplier              # code=1.2, analysis=1.1, summary=0.85, chat=0.8

Result is clipped to [0.0, 1.0].

Tier mapping (defaults, overridden by AdaptiveModelTierRouter thresholds)::

    0.00 – smart_threshold  → fast tier
    smart_threshold – large_threshold → smart tier
    large_threshold – 1.00  → large tier
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ── Confidence extraction ─────────────────────────────────────────────────────

_CONF_RE = re.compile(r"CONFIDENCE:\s*(0?\.\d+|1\.0)", re.IGNORECASE)


def extract_confidence(text: str) -> float | None:
    """Extract the CONFIDENCE:0.XX marker from agent output.

    Agents emit this on their final line when using :class:`~meshflow.agents.library`
    prompts or the ``EarlyExitAgent``. Returns ``None`` if no marker is found.

    Examples::

        >>> extract_confidence("Answer here.\\nCONFIDENCE:0.85")
        0.85
        >>> extract_confidence("No marker") is None
        True
    """
    m = _CONF_RE.search(text or "")
    if m is None:
        return None
    try:
        val = float(m.group(1))
        return max(0.0, min(1.0, val))
    except ValueError:
        return None


# ── Task classification keywords ─────────────────────────────────────────────

_CODE_KEYWORDS = frozenset(
    "def class import function return async await lambda yield "
    "SELECT INSERT UPDATE DELETE FROM WHERE JOIN "
    "docker kubernetes terraform ansible helm "
    "function const let var => null undefined "
    "public private protected override interface abstract "
    "implement refactor debug trace stack".split()
)

_ANALYSIS_KEYWORDS = frozenset(
    "analyse analyze compare evaluate assess explain describe summarize "
    "investigate research strategy competitive landscape trade-off "
    "recommendation architecture design pattern pros cons ".split()
)

_SUMMARY_KEYWORDS = frozenset(
    "summarise summarize tldr brief overview abstract digest "
    "extract key points highlights ".split()
)

_CHAT_KEYWORDS = frozenset(
    "hello hi thanks thank you okay sure yes no please ".split()
)

_ADVERSATIVE = frozenset(
    "however although whereas despite nevertheless nonetheless "
    "notwithstanding but yet while although though".split()
)

_TECHNICAL_ALL = _CODE_KEYWORDS | _ANALYSIS_KEYWORDS


# ── Dataclass ─────────────────────────────────────────────────────────────────


@dataclass
class TaskScore:
    """Routing-relevant characteristics of a task.

    Attributes
    ----------
    length:      Raw character count.
    complexity:  0–1 heuristic derived from question/conjunction/tech densities.
    task_type:   ``"code"``, ``"analysis"``, ``"summary"``, ``"chat"``, or ``"unknown"``.
    tool_count:  Number of tools available to the agent.
    composite:   Final 0–1 routing score used to select the model tier.
    """

    length: int
    complexity: float
    task_type: str
    tool_count: int
    composite: float


# ── Scorer ────────────────────────────────────────────────────────────────────


class TaskScorer:
    """Compute a multi-dimensional routing score for a task string.

    All methods are pure (no I/O, no state). The scorer is safe to share
    across threads and across router instances.

    Usage::

        scorer = TaskScorer()
        score = scorer.score("Analyse the competitive landscape and compare...")
        # score.composite → 0.61
        # score.task_type → "analysis"
        # score.tool_count → 0
    """

    # ── Public API ────────────────────────────────────────────────────────────

    def score(self, task: str, tools: list[str] | None = None) -> TaskScore:
        """Compute the full routing score for *task*.

        Parameters
        ----------
        task:   The task text (any length).
        tools:  Tool names available to the agent (affects tool_score).
        """
        n_tools = len(tools) if tools else 0
        task_lower = task.lower()
        words = re.findall(r"\b\w+\b", task_lower)
        sentences = max(len(re.split(r"[.!?]+", task)), 1)

        length_score = min(len(task) / 2000.0, 1.0)
        question_score = self._question_density(task, sentences)
        conjunction_score = self._conjunction_density(words)
        technical_score = self._technical_density(words)
        tool_score = min(n_tools / 5.0, 1.0)

        complexity = (
            0.35 * length_score
            + 0.20 * question_score
            + 0.20 * conjunction_score
            + 0.15 * technical_score
            + 0.10 * tool_score
        )

        task_type = self._classify_type(words)
        type_mult = {"code": 1.2, "analysis": 1.1, "summary": 0.85, "chat": 0.8}.get(task_type, 1.0)

        composite = max(0.0, min(1.0, complexity * type_mult))

        return TaskScore(
            length=len(task),
            complexity=round(complexity, 4),
            task_type=task_type,
            tool_count=n_tools,
            composite=round(composite, 4),
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _question_density(text: str, n_sentences: int) -> float:
        n_q = text.count("?")
        return min(n_q / max(n_sentences, 1), 1.0)

    @staticmethod
    def _conjunction_density(words: list[str]) -> float:
        if not words:
            return 0.0
        hits = sum(1 for w in words if w in _ADVERSATIVE)
        return min(hits / (len(words) / 10.0), 1.0) if len(words) >= 10 else hits * 0.1

    @staticmethod
    def _technical_density(words: list[str]) -> float:
        if not words:
            return 0.0
        hits = sum(1 for w in words if w in _TECHNICAL_ALL)
        return min(hits / max(len(words) * 0.15, 1.0), 1.0)

    @staticmethod
    def _classify_type(words: list[str]) -> str:
        word_set = set(words)
        code_hits = len(word_set & _CODE_KEYWORDS)
        analysis_hits = len(word_set & _ANALYSIS_KEYWORDS)
        summary_hits = len(word_set & _SUMMARY_KEYWORDS)
        chat_hits = len(word_set & _CHAT_KEYWORDS)
        best_score = max(code_hits, analysis_hits, summary_hits, chat_hits)
        if best_score == 0:
            return "unknown"
        if code_hits == best_score:
            return "code"
        if analysis_hits == best_score:
            return "analysis"
        if summary_hits == best_score:
            return "summary"
        return "chat"


# Module-level singleton — routers share this by default
_DEFAULT_SCORER = TaskScorer()


def score_task(task: str, tools: list[str] | None = None) -> TaskScore:
    """Convenience wrapper around the module-level :class:`TaskScorer` instance."""
    return _DEFAULT_SCORER.score(task, tools)
