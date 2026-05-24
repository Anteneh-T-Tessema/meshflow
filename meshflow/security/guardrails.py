"""Guardrails — input/output validation for every agent and node.

MeshFlow Guardrails are the regulated-industry differentiator: every agent
input and output is automatically validated before reaching the LLM or the
caller. No other OSS framework integrates this at the kernel level.

Usage (Agent API):
    from meshflow import Agent
    from meshflow.security.guardrails import (
        PIIBlockGuardrail, ConfidenceGuardrail, LengthGuardrail,
        ToxicityGuardrail, JSONSchemaGuardrail, KeywordBlockGuardrail,
        RegexGuardrail, GuardrailStack,
    )

    agent = Agent(
        name="hipaa_agent",
        role="researcher",
        input_guardrails=[PIIBlockGuardrail(action="block")],
        output_guardrails=[
            ConfidenceGuardrail(min_confidence=0.7),
            LengthGuardrail(max_chars=4000),
        ],
    )

Usage (standalone):
    stack = GuardrailStack([PIIBlockGuardrail(), ToxicityGuardrail()])
    passed, text, results = stack.run("Hello, my SSN is 123-45-6789")
    # passed=False, results[0].guardrail_name="pii_block"

Built-in guardrails
-------------------
PIIBlockGuardrail     — detect & block/mask PHI/PII via SensitiveDataDetector
JSONSchemaGuardrail   — validate JSON output against a schema
ConfidenceGuardrail   — block outputs below a stated confidence threshold
ToxicityGuardrail     — block profanity / harmful content patterns
LengthGuardrail       — enforce min/max character or token length
RegexGuardrail        — require or forbid regex patterns in text
KeywordBlockGuardrail — block text containing forbidden words or phrases
CostCapGuardrail      — block tasks that would exceed a cost budget
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Literal


# ── GuardrailResult ───────────────────────────────────────────────────────────

@dataclass
class GuardrailResult:
    """Outcome of a single guardrail check."""

    passed: bool
    guardrail_name: str
    reason: str = ""
    modified_text: str | None = None   # set when action="modify" rewrites the text
    severity: Literal["block", "warn", "modify"] = "block"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.passed


# ── GuardrailViolation ────────────────────────────────────────────────────────

class GuardrailViolation(Exception):
    """Raised when a blocking guardrail fails and the stack is in strict mode."""

    def __init__(self, result: GuardrailResult) -> None:
        self.result = result
        super().__init__(f"[{result.guardrail_name}] {result.reason}")


# ── Base Guardrail ────────────────────────────────────────────────────────────

class Guardrail(ABC):
    """Abstract base for all MeshFlow guardrails.

    Subclass and implement ``check(text) -> GuardrailResult``.

    Parameters
    ----------
    action:  "block" (default) — fail and surface reason to caller.
             "warn"  — pass but attach reason to metadata.
             "modify"— pass but return modified_text (e.g. masked PII).
    name:    Override the default guardrail name (used in results/logs).
    """

    def __init__(
        self,
        action: Literal["block", "warn", "modify"] = "block",
        name: str = "",
    ) -> None:
        self.action = action
        self.name = name or self.__class__.__name__.lower().replace("guardrail", "_guardrail")

    @abstractmethod
    def check(self, text: str) -> GuardrailResult:
        """Validate *text*.  Return a GuardrailResult with passed=True/False."""
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(action={self.action!r})"


# ── GuardrailStack ────────────────────────────────────────────────────────────

@dataclass
class GuardrailStack:
    """Run a sequence of guardrails against a piece of text.

    Parameters
    ----------
    guardrails:  Ordered list of Guardrail instances.
    mode:        "strict" — raise GuardrailViolation on first block.
                 "collect" — run all guardrails, return combined result.
    """

    guardrails: list[Guardrail] = field(default_factory=list)
    mode: Literal["strict", "collect"] = "strict"

    def run(self, text: str) -> tuple[bool, str, list[GuardrailResult]]:
        """Check *text* through all guardrails.

        Returns
        -------
        (all_passed, final_text, results)
        - all_passed: True only when every blocking guardrail passed.
        - final_text: original text, or rewritten text if any guardrail modified it.
        - results: list of GuardrailResult, one per guardrail.
        """
        results: list[GuardrailResult] = []
        current_text = text
        all_passed = True

        for g in self.guardrails:
            result = g.check(current_text)
            result.severity = g.action
            results.append(result)

            if g.action == "modify" and result.modified_text is not None:
                current_text = result.modified_text

            elif not result.passed and g.action == "block":
                all_passed = False
                if self.mode == "strict":
                    raise GuardrailViolation(result)

            elif not result.passed and g.action == "warn":
                pass  # warn: keep all_passed=True but carry the result

        return all_passed, current_text, results

    def add(self, guardrail: Guardrail) -> "GuardrailStack":
        self.guardrails.append(guardrail)
        return self

    def __len__(self) -> int:
        return len(self.guardrails)


# ═══════════════════════════════════════════════════════════════════════════════
# Built-in Guardrails
# ═══════════════════════════════════════════════════════════════════════════════

class PIIBlockGuardrail(Guardrail):
    """Detect and block or mask PHI/PII using SensitiveDataDetector.

    action="block"  — fail if any PII found; reason lists the categories.
    action="modify" — pass but return masked text (replaces values with [REDACTED]).
    action="warn"   — pass but attach match count to metadata.

    Parameters
    ----------
    categories:  Limit detection to these SensitiveMatch.kind values.
                 Defaults to all categories.
    min_confidence: Only flag matches above this confidence (0.0–1.0).
    """

    def __init__(
        self,
        action: Literal["block", "warn", "modify"] = "block",
        categories: list[str] | None = None,
        min_confidence: float = 0.5,
        name: str = "pii_block",
    ) -> None:
        super().__init__(action=action, name=name)
        self._categories = set(categories) if categories else None
        self._min_confidence = min_confidence

    def check(self, text: str) -> GuardrailResult:
        from meshflow.security.sensitive_data import get_detector
        detector = get_detector()
        matches = detector.detect(text)

        filtered = [
            m for m in matches
            if m.confidence >= self._min_confidence
            and (self._categories is None or m.kind in self._categories)
        ]

        if not filtered:
            return GuardrailResult(passed=True, guardrail_name=self.name)

        categories = sorted({m.kind for m in filtered})
        reason = f"PII detected: {', '.join(categories)} ({len(filtered)} match(es))"

        if self.action == "modify":
            masked = detector.mask(text)
            return GuardrailResult(
                passed=True,
                guardrail_name=self.name,
                reason=reason,
                modified_text=masked,
                metadata={"match_count": len(filtered), "categories": categories},
            )

        return GuardrailResult(
            passed=False,
            guardrail_name=self.name,
            reason=reason,
            metadata={"match_count": len(filtered), "categories": categories},
        )


class ConfidenceGuardrail(Guardrail):
    """Block outputs whose stated confidence falls below a threshold.

    Looks for the CONFIDENCE:0.XX marker that MeshFlow agents emit at the
    end of their responses (set by _extract_confidence in base.py).

    Parameters
    ----------
    min_confidence:  Block if confidence < this value (0.0–1.0). Default 0.7.
    missing_ok:      If True, pass when no confidence marker is found.
    """

    def __init__(
        self,
        min_confidence: float = 0.7,
        missing_ok: bool = True,
        action: Literal["block", "warn", "modify"] = "block",
        name: str = "confidence",
    ) -> None:
        super().__init__(action=action, name=name)
        self.min_confidence = min_confidence
        self.missing_ok = missing_ok
        self._pattern = re.compile(r"CONFIDENCE:\s*([0-9.]+)", re.IGNORECASE)

    def check(self, text: str) -> GuardrailResult:
        m = self._pattern.search(text)
        if m is None:
            if self.missing_ok:
                return GuardrailResult(passed=True, guardrail_name=self.name, reason="no confidence marker")
            return GuardrailResult(
                passed=False,
                guardrail_name=self.name,
                reason="no CONFIDENCE marker found in output",
            )

        confidence = float(m.group(1))
        if confidence < self.min_confidence:
            return GuardrailResult(
                passed=False,
                guardrail_name=self.name,
                reason=f"confidence {confidence:.2f} < threshold {self.min_confidence:.2f}",
                metadata={"confidence": confidence, "threshold": self.min_confidence},
            )

        return GuardrailResult(
            passed=True,
            guardrail_name=self.name,
            metadata={"confidence": confidence},
        )


class LengthGuardrail(Guardrail):
    """Enforce minimum and/or maximum text length.

    Parameters
    ----------
    min_chars:  Fail if len(text) < min_chars.
    max_chars:  Fail if len(text) > max_chars.
    unit:       "chars" (default) or "words".
    """

    def __init__(
        self,
        min_chars: int = 0,
        max_chars: int | None = None,
        unit: Literal["chars", "words"] = "chars",
        action: Literal["block", "warn", "modify"] = "block",
        name: str = "length",
    ) -> None:
        super().__init__(action=action, name=name)
        self.min_chars = min_chars
        self.max_chars = max_chars
        self.unit = unit

    def _measure(self, text: str) -> int:
        return len(text.split()) if self.unit == "words" else len(text)

    def check(self, text: str) -> GuardrailResult:
        size = self._measure(text)
        label = self.unit

        if size < self.min_chars:
            return GuardrailResult(
                passed=False,
                guardrail_name=self.name,
                reason=f"text too short: {size} {label} < minimum {self.min_chars}",
                metadata={"size": size, "unit": label},
            )

        if self.max_chars is not None and size > self.max_chars:
            return GuardrailResult(
                passed=False,
                guardrail_name=self.name,
                reason=f"text too long: {size} {label} > maximum {self.max_chars}",
                metadata={"size": size, "unit": label},
            )

        return GuardrailResult(
            passed=True, guardrail_name=self.name,
            metadata={"size": size, "unit": label},
        )


class ToxicityGuardrail(Guardrail):
    """Block outputs containing profanity or harmful content patterns.

    Uses a curated set of pattern categories. Extend via ``extra_patterns``.

    Parameters
    ----------
    categories:      Subset of ["profanity", "violence", "self_harm", "hate"].
                     Defaults to all.
    extra_patterns:  Additional regex strings to check.
    case_sensitive:  Default False (case-insensitive matching).
    """

    _BUILTIN: dict[str, list[str]] = {
        "violence": [
            r"\b(kill|murder|attack|bomb|detonate|explode|shoot|stab|massacre)\b",
            r"\bhow to (make|build|create) (a |an )?(bomb|weapon|explosive)",
        ],
        "self_harm": [
            r"\b(suicide|self.harm|self.destruct|end my life|kill myself)\b",
        ],
        "hate": [
            r"\b(terrorist|extremist)\b.{0,30}\b(how to|guide|instructions)\b",
        ],
        "profanity": [
            r"\bf[u\*]+ck\b",
            r"\bs[h\*]+it\b",
            r"\ba[s\*]+hole\b",
        ],
    }

    def __init__(
        self,
        categories: list[str] | None = None,
        extra_patterns: list[str] | None = None,
        case_sensitive: bool = False,
        action: Literal["block", "warn", "modify"] = "block",
        name: str = "toxicity",
    ) -> None:
        super().__init__(action=action, name=name)
        cats = set(categories) if categories else set(self._BUILTIN.keys())
        flags = 0 if case_sensitive else re.IGNORECASE

        patterns: list[re.Pattern] = []
        self._category_map: list[tuple[str, re.Pattern]] = []
        for cat, pats in self._BUILTIN.items():
            if cat in cats:
                for p in pats:
                    compiled = re.compile(p, flags)
                    self._category_map.append((cat, compiled))

        if extra_patterns:
            for p in extra_patterns:
                self._category_map.append(("custom", re.compile(p, flags)))

    def check(self, text: str) -> GuardrailResult:
        triggered: list[str] = []
        for cat, pattern in self._category_map:
            if pattern.search(text):
                triggered.append(cat)

        if triggered:
            cats = sorted(set(triggered))
            return GuardrailResult(
                passed=False,
                guardrail_name=self.name,
                reason=f"toxic content detected: {', '.join(cats)}",
                metadata={"categories": cats},
            )

        return GuardrailResult(passed=True, guardrail_name=self.name)


class JSONSchemaGuardrail(Guardrail):
    """Validate that the output contains valid JSON matching a given schema.

    Uses Python's built-in ``json`` module (no jsonschema dependency).
    Pass ``schema`` as a dict with "required" and "properties" keys to enable
    key-presence validation. Full JSON Schema draft-7 requires ``jsonschema``
    installed — falls back to key-presence check otherwise.

    Parameters
    ----------
    schema:        JSON Schema dict. None = just validate parseable JSON.
    extract_json:  If True, extract the first JSON block from markdown fences.
    """

    def __init__(
        self,
        schema: dict[str, Any] | None = None,
        extract_json: bool = True,
        action: Literal["block", "warn", "modify"] = "block",
        name: str = "json_schema",
    ) -> None:
        super().__init__(action=action, name=name)
        self._schema = schema
        self._extract = extract_json
        self._fence_re = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

    def _extract_json_text(self, text: str) -> str:
        if self._extract:
            m = self._fence_re.search(text)
            if m:
                return m.group(1).strip()
        return text.strip()

    def check(self, text: str) -> GuardrailResult:
        raw = self._extract_json_text(text)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            return GuardrailResult(
                passed=False,
                guardrail_name=self.name,
                reason=f"invalid JSON: {exc}",
            )

        if self._schema is None:
            return GuardrailResult(passed=True, guardrail_name=self.name)

        # Try full jsonschema validation first
        try:
            import jsonschema  # type: ignore[import]
            try:
                jsonschema.validate(data, self._schema)
                return GuardrailResult(passed=True, guardrail_name=self.name)
            except jsonschema.ValidationError as exc:
                return GuardrailResult(
                    passed=False,
                    guardrail_name=self.name,
                    reason=f"schema violation: {exc.message}",
                )
        except ImportError:
            pass

        # Fallback: check required keys
        required = self._schema.get("required", [])
        missing = [k for k in required if k not in data]
        if missing:
            return GuardrailResult(
                passed=False,
                guardrail_name=self.name,
                reason=f"missing required keys: {missing}",
            )

        return GuardrailResult(passed=True, guardrail_name=self.name)


class RegexGuardrail(Guardrail):
    """Require or forbid a regex pattern in the text.

    Parameters
    ----------
    pattern:    Regex pattern string.
    mode:       "require" — fail if pattern is NOT found.
                "forbid"  — fail if pattern IS found.
    flags:      re module flags (default: re.IGNORECASE).
    """

    def __init__(
        self,
        pattern: str,
        mode: Literal["require", "forbid"] = "require",
        flags: int = re.IGNORECASE,
        action: Literal["block", "warn", "modify"] = "block",
        name: str = "regex",
    ) -> None:
        super().__init__(action=action, name=name)
        self._pattern = re.compile(pattern, flags)
        self.mode = mode

    def check(self, text: str) -> GuardrailResult:
        found = bool(self._pattern.search(text))

        if self.mode == "require" and not found:
            return GuardrailResult(
                passed=False,
                guardrail_name=self.name,
                reason=f"required pattern not found: {self._pattern.pattern!r}",
            )

        if self.mode == "forbid" and found:
            return GuardrailResult(
                passed=False,
                guardrail_name=self.name,
                reason=f"forbidden pattern found: {self._pattern.pattern!r}",
            )

        return GuardrailResult(passed=True, guardrail_name=self.name)


class KeywordBlockGuardrail(Guardrail):
    """Block text containing any forbidden keywords or phrases.

    Parameters
    ----------
    keywords:       List of strings to block (exact word match by default).
    whole_word:     If True (default), match whole words only.
    case_sensitive: Default False.
    """

    def __init__(
        self,
        keywords: list[str],
        whole_word: bool = True,
        case_sensitive: bool = False,
        action: Literal["block", "warn", "modify"] = "block",
        name: str = "keyword_block",
    ) -> None:
        super().__init__(action=action, name=name)
        flags = 0 if case_sensitive else re.IGNORECASE
        self._patterns = []
        for kw in keywords:
            escaped = re.escape(kw)
            pat = rf"\b{escaped}\b" if whole_word else escaped
            self._patterns.append((kw, re.compile(pat, flags)))

    def check(self, text: str) -> GuardrailResult:
        triggered = [kw for kw, p in self._patterns if p.search(text)]
        if triggered:
            return GuardrailResult(
                passed=False,
                guardrail_name=self.name,
                reason=f"forbidden keyword(s) found: {triggered}",
                metadata={"keywords": triggered},
            )
        return GuardrailResult(passed=True, guardrail_name=self.name)


class CostCapGuardrail(Guardrail):
    """Block a task whose estimated token cost would exceed a budget.

    This guardrail estimates cost from text length (rough token count) and a
    per-token rate. Use on *input* tasks to avoid runaway costs.

    Parameters
    ----------
    max_cost_usd:       Reject if estimated cost > this value.
    input_rate_per_1k:  USD per 1,000 input tokens. Default: claude-sonnet rate.
    chars_per_token:    Characters per token approximation. Default 4.
    """

    def __init__(
        self,
        max_cost_usd: float = 0.10,
        input_rate_per_1k: float = 0.003,
        chars_per_token: int = 4,
        action: Literal["block", "warn", "modify"] = "block",
        name: str = "cost_cap",
    ) -> None:
        super().__init__(action=action, name=name)
        self.max_cost_usd = max_cost_usd
        self.rate = input_rate_per_1k / 1000.0
        self.chars_per_token = chars_per_token

    def check(self, text: str) -> GuardrailResult:
        tokens = len(text) / self.chars_per_token
        estimated = tokens * self.rate

        if estimated > self.max_cost_usd:
            return GuardrailResult(
                passed=False,
                guardrail_name=self.name,
                reason=(
                    f"estimated cost ${estimated:.4f} exceeds cap ${self.max_cost_usd:.4f} "
                    f"({tokens:.0f} tokens)"
                ),
                metadata={"estimated_usd": estimated, "cap_usd": self.max_cost_usd, "tokens": tokens},
            )

        return GuardrailResult(
            passed=True,
            guardrail_name=self.name,
            metadata={"estimated_usd": estimated, "tokens": tokens},
        )


class CustomGuardrail(Guardrail):
    """Wrap any callable as a Guardrail.

    Parameters
    ----------
    fn:     Callable with one of these return signatures:
            - ``bool``                    → (passed, reason="", no modify)
            - ``tuple[bool, str]``        → (passed, reason)
              When action="modify", the str is treated as the modified text.
            - ``tuple[bool, str, str]``   → (passed, reason, modified_text)
    name:   Name shown in GuardrailResult.
    """

    def __init__(
        self,
        fn: Callable[[str], bool | tuple],
        name: str = "custom",
        action: Literal["block", "warn", "modify"] = "block",
    ) -> None:
        super().__init__(action=action, name=name)
        self._fn = fn

    def check(self, text: str) -> GuardrailResult:
        result = self._fn(text)
        if not isinstance(result, tuple):
            return GuardrailResult(passed=bool(result), guardrail_name=self.name)

        if len(result) == 3:
            passed, reason, modified = result
            return GuardrailResult(
                passed=passed,
                guardrail_name=self.name,
                reason=str(reason),
                modified_text=str(modified) if self.action == "modify" else None,
            )

        passed, second = result
        if self.action == "modify":
            # (passed, modified_text) — second element is the new text
            return GuardrailResult(
                passed=bool(passed),
                guardrail_name=self.name,
                modified_text=str(second),
            )
        return GuardrailResult(passed=bool(passed), guardrail_name=self.name, reason=str(second))
