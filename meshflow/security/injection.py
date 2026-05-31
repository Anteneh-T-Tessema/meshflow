"""Sprint 49 — Prompt Injection Detection.

Detects prompt injection attacks in user-supplied text using pattern matching
and heuristic scoring.  Plugs directly into ``GuardrailStack`` via
``PromptInjectionGuardrail``.

Detection categories
--------------------
instruction_override  — "Ignore previous instructions", "Forget everything"
jailbreak             — DAN mode, "no restrictions", dev/god mode
role_play_attack      — "You are now X", "Act as if", persona hijacking
data_exfiltration     — "Print your system prompt", "Repeat above"
indirect_injection    — template variables, hidden unicode, external fetch
context_manipulation  — fake system/assistant turns, model stop-tokens
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .guardrails import Guardrail, GuardrailResult


# ── Pattern database ──────────────────────────────────────────────────────────
# Each entry: (pattern_name, confidence_weight, regex_string)
# Confidence weight reflects how reliably that pattern signals an attack.

_PATTERNS: dict[str, list[tuple[str, float, str]]] = {
    "instruction_override": [
        ("ignore_previous",      0.90, r"(?i)ignore\s+(all\s+)?(previous|prior|above|earlier)\s+instructions?"),
        ("forget_instructions",  0.90, r"(?i)forget\s+(everything|all|previous|prior)\s*(instructions?|rules?|guidelines?)?"),
        ("disregard",            0.85, r"(?i)disregard\s+(all\s+)?(previous|prior|above|earlier|your)\s+instructions?"),
        ("new_instructions",     0.80, r"(?i)(?:your\s+)?new\s+instructions?\s*(are|:)\s*"),
        ("override_instructions",0.85, r"(?i)override\s+(your\s+)?(instructions?|directives?|guidelines?)"),
        ("do_not_follow",        0.75, r"(?i)do\s+not\s+follow\s+(your\s+)?(instructions?|guidelines?|rules?)"),
        ("supersede",            0.80, r"(?i)(supersede|replace|cancel|void)\s+(all\s+)?(previous|prior|earlier)\s+(instructions?|context|prompt)"),
    ],
    "jailbreak": [
        ("dan_mode",             0.95, r"(?i)\b(DAN|do\s+anything\s+now)\b"),
        ("no_restrictions",      0.90, r"(?i)(pretend|act|imagine)\s+(you\s+have\s+)?(no\s+restrictions?|no\s+limitations?|no\s+rules?|no\s+guidelines?)"),
        ("evil_ai",              0.90, r"(?i)(you\s+are\s+(now\s+)?)?(an?\s+)?(evil|unrestricted|unfiltered|uncensored)\s+(ai|assistant|bot|system)"),
        ("dev_mode",             0.85, r"(?i)(developer|development|jailbreak|bypass|unlock)\s+mode"),
        ("opposite_mode",        0.80, r"(?i)opposite\s+(mode|day|world)\s*(enabled|activated|on)?"),
        ("god_mode",             0.85, r"(?i)god[\s\-]?mode\s*(enabled|activated|on)?"),
        ("stan_mode",            0.85, r"(?i)\bSTAN\b"),
        ("aim_mode",             0.85, r"(?i)\bAIM\b.*?(always|intelligent|machiavellian)"),
        ("no_ethical",           0.88, r"(?i)(without|ignore|bypass)\s+(ethical|moral|safety)\s+(guidelines?|constraints?|limits?|rules?)"),
    ],
    "role_play_attack": [
        ("you_are_now",          0.80, r"(?i)you\s+are\s+now\s+(?!an?\s+(?:agent|assistant|helpful))\S"),
        ("act_as",               0.75, r"(?i)act\s+as\s+(if\s+you\s+(were|are)\s+)?(?!a\s+(?:helpful|professional|responsible))\S"),
        ("pretend_to_be",        0.75, r"(?i)pretend\s+(to\s+be|you\s+are|you\s+were)\s+\S"),
        ("roleplay_no_safety",   0.90, r"(?i)role[\s\-]?play\s+.*?(no|without|ignore)\s+(safety|filter|restriction|guideline)"),
        ("character_no_rules",   0.85, r"(?i)(character|persona|role)\s+(that\s+)?(has\s+)?(no\s+)?(restrictions?|rules?|filters?|limitations?)"),
        ("fictional_wrapper",    0.70, r"(?i)(in\s+this\s+story|in\s+this\s+fictional|hypothetically speaking|for\s+a\s+novel)\s*.{0,60}(how\s+to|explain|tell\s+me)"),
    ],
    "data_exfiltration": [
        ("print_system_prompt",  0.95, r"(?i)(print|show|reveal|display|output|repeat|say)\s+(your\s+|the\s+)?(system\s+prompt|initial\s+prompt|original\s+prompt)"),
        ("repeat_above",         0.90, r"(?i)repeat\s+(everything|all|the\s+text)\s*(above|before|prior)"),
        ("what_instructions",    0.85, r"(?i)what\s+(are\s+your\s+)?(instructions?|prompt|directives?|rules?)\s*\?"),
        ("show_me_your",         0.85, r"(?i)show\s+me\s+your\s+(prompt|instructions?|system|directives?)"),
        ("what_were_you_told",   0.80, r"(?i)what\s+were\s+you\s+(told|instructed|given|asked)\s*\?"),
        ("dump_context",         0.90, r"(?i)(dump|output|extract|exfiltrate)\s+(your\s+)?(context|memory|training|data|prompt)"),
        ("leak_prompt",          0.90, r"(?i)(leak|expose|disclose)\s+(your\s+)?(system\s+)?(prompt|instructions?)"),
    ],
    "indirect_injection": [
        ("url_fetch_inject",     0.50, r"(?i)(?:fetch|retrieve|load|read|access|visit)\s+(?:this\s+)?(?:url|link|page|site):\s*https?://"),
        ("execute_external",     0.70, r"(?i)(run|execute|follow)\s+(the\s+)?(instructions?|commands?)\s+(from|at|in)\s+(this\s+)?(url|link|file|document)"),
        ("template_inject",      0.65, r"\{\{[^}]{0,200}\}\}|\$\{[^}]{0,200}\}"),
        ("hidden_unicode",       0.80, r"[​-‏  ﻿­]"),
        ("null_byte",            0.85, r"\x00"),
        ("base64_command",       0.60, r"(?i)(base64|b64)\s*(decode|encoded?)\s*[:=]\s*[A-Za-z0-9+/]{20,}"),
    ],
    "context_manipulation": [
        ("end_of_context_marker",0.85, r"(?i)(---+|===+|\*\*\*+|<<<|>>>)\s*(end\s+of\s+)?(system|instructions?|context|prompt)"),
        ("fake_assistant_turn",  0.90, r"(?im)^assistant\s*:\s*"),
        ("fake_system_turn",     0.85, r"(?im)^system\s*:\s*"),
        ("llm_stop_tokens",      0.70, r"(?i)(</?(s|system|inst|human|assistant)>|\[/?INST\]|\[/?SYS\])"),
        ("special_tokens",       0.75, r"(<\|(?:im_start|im_end|endoftext|system|user|assistant)\|>)"),
        ("begin_end_markers",    0.65, r"(?i)(<<SYS>>|<</SYS>>|\[INST\]|\[/INST\])"),
    ],
}


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class InjectionMatch:
    """A single pattern match within the scanned text."""

    category: str
    pattern_name: str
    matched_text: str       # truncated to 120 chars
    position: int           # character offset in original text
    confidence: float       # pattern-level confidence weight (0–1)


@dataclass
class InjectionResult:
    """Aggregated scan result for a piece of text."""

    detected: bool          # score >= threshold
    score: float            # aggregate 0.0–1.0
    categories: list[str]   # distinct categories that fired
    matches: list[InjectionMatch]
    blocked: bool           # score >= block_threshold

    @property
    def is_safe(self) -> bool:
        return not self.blocked

    def summary(self) -> str:
        if not self.detected:
            return f"clean (score={self.score:.2f})"
        status = "BLOCKED" if self.blocked else "WARN"
        cats = ", ".join(self.categories)
        return f"{status} score={self.score:.2f} categories=[{cats}] matches={len(self.matches)}"


# ── Detector ──────────────────────────────────────────────────────────────────

class PromptInjectionDetector:
    """Detect prompt injection attacks using pattern matching + heuristics.

    Parameters
    ----------
    threshold:
        Aggregate score at or above which ``InjectionResult.detected`` is
        ``True``.  Default 0.3 — flags suspicious text for logging.
    block_threshold:
        Score at or above which ``InjectionResult.blocked`` is ``True``.
        Default 0.6 — high confidence; block the request.
    enabled_categories:
        Subset of the six detection categories to activate.  ``None`` (default)
        enables all categories.
    """

    ALL_CATEGORIES: list[str] = list(_PATTERNS.keys())

    def __init__(
        self,
        threshold: float = 0.3,
        block_threshold: float = 0.6,
        enabled_categories: list[str] | None = None,
    ) -> None:
        if threshold < 0 or threshold > 1:
            raise ValueError("threshold must be in [0, 1]")
        if block_threshold < threshold:
            raise ValueError("block_threshold must be >= threshold")

        self.threshold = threshold
        self.block_threshold = block_threshold
        self._active = set(enabled_categories or self.ALL_CATEGORIES)

        # Pre-compile all active patterns once at construction time.
        self._compiled: dict[str, list[tuple[str, float, re.Pattern[str]]]] = {}
        for cat, patterns in _PATTERNS.items():
            if cat in self._active:
                self._compiled[cat] = [
                    (name, weight, re.compile(regex, re.MULTILINE))
                    for (name, weight, regex) in patterns
                ]

    # ── Public API ────────────────────────────────────────────────────────────

    def scan(self, text: str) -> InjectionResult:
        """Scan *text* for injection patterns and return an ``InjectionResult``."""
        matches: list[InjectionMatch] = []
        category_hits: set[str] = set()

        for cat, patterns in self._compiled.items():
            for name, weight, rx in patterns:
                for m in rx.finditer(text):
                    matches.append(InjectionMatch(
                        category=cat,
                        pattern_name=name,
                        matched_text=m.group(0)[:120],
                        position=m.start(),
                        confidence=weight,
                    ))
                    category_hits.add(cat)

        score = self._score(matches, category_hits)

        return InjectionResult(
            detected=score >= self.threshold,
            score=score,
            categories=sorted(category_hits),
            matches=matches,
            blocked=score >= self.block_threshold,
        )

    def is_safe(self, text: str) -> bool:
        """Return ``True`` iff the text does NOT meet the block threshold."""
        return not self.scan(text).blocked

    # ── Scoring ───────────────────────────────────────────────────────────────

    @staticmethod
    def _score(matches: list[InjectionMatch], category_hits: set[str]) -> float:
        if not matches:
            return 0.0
        # Base: highest individual pattern confidence.
        top = max(m.confidence for m in matches)
        # Bonus: 0.1 per additional category beyond the first (max +0.3).
        bonus = min(0.3, 0.1 * max(0, len(category_hits) - 1))
        return min(1.0, top + bonus)


# ── Guardrail integration ─────────────────────────────────────────────────────

class PromptInjectionGuardrail(Guardrail):
    """GuardrailStack-compatible guardrail that blocks prompt injection.

    A score in ``[threshold, block_threshold)`` produces a *warn* result (the
    text passes but metadata flags it as suspicious).  A score at or above
    ``block_threshold`` fails with ``passed=False``.

    Parameters
    ----------
    detector:
        Pre-configured ``PromptInjectionDetector``.  When ``None``, a detector
        is created from *threshold* and *block_threshold*.
    threshold / block_threshold:
        Forwarded to the auto-created detector when *detector* is ``None``.
    """

    def __init__(
        self,
        detector: PromptInjectionDetector | None = None,
        *,
        threshold: float = 0.3,
        block_threshold: float = 0.6,
        action: str = "block",
        name: str = "prompt_injection",
    ) -> None:
        super().__init__(action=action, name=name)  # type: ignore[arg-type]
        self._detector = detector or PromptInjectionDetector(
            threshold=threshold,
            block_threshold=block_threshold,
        )

    def check(self, text: str, **_: Any) -> GuardrailResult:  # type: ignore[override]
        result = self._detector.scan(text)

        if result.blocked:
            return GuardrailResult(
                passed=False,
                guardrail_name=self.name,
                reason=(
                    f"Prompt injection detected "
                    f"(score={result.score:.2f}, categories={result.categories})"
                ),
                severity="block",
                metadata={
                    "score": result.score,
                    "categories": result.categories,
                    "match_count": len(result.matches),
                    "matches": [
                        {
                            "category": m.category,
                            "pattern": m.pattern_name,
                            "text": m.matched_text,
                            "pos": m.position,
                            "confidence": m.confidence,
                        }
                        for m in result.matches
                    ],
                },
            )

        if result.detected:
            return GuardrailResult(
                passed=True,
                guardrail_name=self.name,
                reason=(
                    f"Suspicious content below block threshold "
                    f"(score={result.score:.2f})"
                ),
                severity="warn",
                metadata={
                    "score": result.score,
                    "categories": result.categories,
                    "near_block": result.score >= self._detector.block_threshold * 0.8,
                },
            )

        return GuardrailResult(
            passed=True,
            guardrail_name=self.name,
            reason="",
            metadata={"score": result.score},
        )
