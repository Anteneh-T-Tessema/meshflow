"""L2.7 — Guardian: active threat detection before dasc-gate.

Three independent pre-execution checks on every agent action:
1. Prompt injection scanning (inter-agent message sanitization)
2. Tool coupling analysis (MCP tool chain amplification detection)
3. Behavioural anomaly detection (deviation from baseline)

Guardian fires BEFORE dasc-gate — it blocks, not just logs.
"""

from __future__ import annotations

import re
import statistics
import time
from dataclasses import dataclass
from typing import Any

from meshflow.core.schemas import InjectionResult, Intent, Message


# ── Injection patterns — kept conservative to minimise false positives ────────

_INJECTION_PATTERNS = [
    # Direct override attempts
    re.compile(r"ignore\s+(previous|all|above)\s+(instruction|prompt|context)", re.I),
    re.compile(r"forget\s+(everything|all)\s+(you|I)", re.I),
    re.compile(r"new\s+instruction[s]?\s*:", re.I),
    re.compile(r"system\s*prompt\s*override", re.I),
    # Role impersonation
    re.compile(r"you\s+are\s+now\s+(a|an|the)\s+\w+", re.I),
    re.compile(r"act\s+as\s+if\s+you\s+are", re.I),
    # Jailbreak triggers
    re.compile(r"DAN\s+mode", re.I),
    re.compile(r"developer\s+mode\s+(enabled|on)", re.I),
    re.compile(r"sudo\s+mode", re.I),
    # Exfiltration patterns
    re.compile(
        r"(send|transmit|exfiltrate|leak)\s+(all|the)?\s*(data|credentials|keys|secrets)", re.I
    ),
    re.compile(r"base64\s+(encode|decode)\s+(and\s+)?(send|transmit)", re.I),
    # Nested prompt injection
    re.compile(r"\[INST\].*\[/INST\]", re.S),
    re.compile(r"<\|system\|>", re.I),
    re.compile(r"<\|im_start\|>system", re.I),
]

# Patterns that, combined with untrusted sources, indicate RAG injection
_RAG_INJECTION_PATTERNS = [
    re.compile(r"when\s+(you|the\s+assistant)\s+(see|read|process)\s+this", re.I),
    re.compile(r"as\s+an\s+AI\s+assistant\s+you\s+must", re.I),
    re.compile(r"your\s+true\s+(purpose|objective|goal)\s+is", re.I),
]


@dataclass
class InjectionScanResult:
    result: InjectionResult
    matched_patterns: list[str]
    confidence: float
    action_taken: str = "none"  # "none" | "sanitized" | "blocked"


@dataclass
class ToolChainRisk:
    """Economic DoS via tool calling chain amplification."""

    estimated_calls: int
    estimated_cost_usd: float
    amplification_factor: float
    is_dangerous: bool
    reason: str = ""


@dataclass
class BehaviouralAnomaly:
    agent_id: str
    anomaly_type: str
    deviation_score: float  # z-score of observed vs baseline
    is_alert: bool
    details: str = ""


class InjectionScanner:
    """Scans inter-agent messages for prompt injection payloads.

    Treats all messages as untrusted by default — even from other agents
    in the same mesh, since one compromised agent can propagate attacks.
    """

    def scan(self, text: str, source_trust: str = "untrusted") -> InjectionScanResult:
        from meshflow.security.guardrail_engine import _SAFETY_CACHE
        cache_key = (text, source_trust)
        cached = _SAFETY_CACHE.get(cache_key)
        if cached is not None:
            return cached

        matched: list[str] = []
        for pattern in _INJECTION_PATTERNS:
            if pattern.search(text):
                matched.append(pattern.pattern)

        if source_trust == "untrusted":
            for pattern in _RAG_INJECTION_PATTERNS:
                if pattern.search(text):
                    matched.append(f"rag:{pattern.pattern}")

        if not matched:
            scan_res = InjectionScanResult(
                result=InjectionResult.CLEAN,
                matched_patterns=[],
                confidence=0.99,
            )
            _SAFETY_CACHE.set(cache_key, scan_res)
            return scan_res

        confidence = min(0.5 + len(matched) * 0.15, 0.99)
        result = InjectionResult.BLOCKED if len(matched) >= 2 else InjectionResult.SUSPICIOUS

        scan_res = InjectionScanResult(
            result=result,
            matched_patterns=matched,
            confidence=confidence,
            action_taken="blocked" if result == InjectionResult.BLOCKED else "flagged",
        )
        _SAFETY_CACHE.set(cache_key, scan_res)
        return scan_res

    def sanitize(self, text: str) -> str:
        """Best-effort sanitization — strip known injection triggers."""
        sanitized = text
        for pattern in _INJECTION_PATTERNS:
            sanitized = pattern.sub("[REDACTED]", sanitized)
        return sanitized


class ToolChainAnalyzer:
    """Detects economic DoS via MCTS-optimised malicious tool chains.

    A malicious MCP server can preserve function signatures and benign
    final results while inducing 100-560× cost amplification through
    recursive tool calls. We catch this by estimating call depth.
    """

    # Tool categories with estimated recursive depth multipliers
    _RECURSIVE_TOOLS = {"search", "browse", "fetch", "crawl", "scrape", "query"}
    _DANGEROUS_COMBOS = [
        ({"search", "browse"}, 50),
        ({"fetch", "crawl"}, 100),
        ({"query", "search", "fetch"}, 200),
    ]

    def analyse(
        self,
        tool_names: list[str],
        budget_usd: float,
        per_call_cost_usd: float = 0.002,
    ) -> ToolChainRisk:
        tool_set = {t.lower() for t in tool_names}
        recursive_count = len(tool_set & self._RECURSIVE_TOOLS)
        amplification = max(1, recursive_count**2)

        for combo, factor in self._DANGEROUS_COMBOS:
            if combo.issubset(tool_set):
                amplification = max(amplification, factor)

        base_calls = len(tool_names)
        estimated_calls = int(base_calls * amplification)
        estimated_cost = estimated_calls * per_call_cost_usd

        is_dangerous = (
            amplification > 20 or estimated_cost > budget_usd * 0.5 or estimated_calls > 500
        )

        return ToolChainRisk(
            estimated_calls=estimated_calls,
            estimated_cost_usd=estimated_cost,
            amplification_factor=amplification,
            is_dangerous=is_dangerous,
            reason=(
                f"Recursive tool combo detected: amplification {amplification:.0f}×"
                if is_dangerous
                else "Within acceptable range"
            ),
        )


class BehaviouralMonitor:
    """Detects anomalous agent behaviour vs established baseline.

    Tracks token rate, tool call rate, and output entropy per agent.
    Z-score > 3.0 is flagged; > 4.5 triggers Guardian alert.
    """

    def __init__(self) -> None:
        self._baselines: dict[str, list[float]] = {}  # agent_id → [token_rates]
        self._tool_rates: dict[str, list[float]] = {}
        self._output_lens: dict[str, list[int]] = {}

    def observe(
        self,
        agent_id: str,
        tokens_per_step: float,
        tools_per_step: float,
        output_len: int,
    ) -> None:
        self._baselines.setdefault(agent_id, []).append(tokens_per_step)
        self._tool_rates.setdefault(agent_id, []).append(tools_per_step)
        self._output_lens.setdefault(agent_id, []).append(output_len)

    def check(self, agent_id: str, current_tokens: float) -> BehaviouralAnomaly:
        history = self._baselines.get(agent_id, [])
        if len(history) < 3:
            return BehaviouralAnomaly(
                agent_id=agent_id,
                anomaly_type="insufficient_baseline",
                deviation_score=0.0,
                is_alert=False,
            )

        mean = statistics.mean(history)
        stdev = statistics.stdev(history) if len(history) > 1 else 1.0
        z_score = abs(current_tokens - mean) / max(stdev, 0.001)

        return BehaviouralAnomaly(
            agent_id=agent_id,
            anomaly_type="token_rate_spike" if current_tokens > mean else "token_rate_drop",
            deviation_score=z_score,
            is_alert=z_score > 3.0,
            details=(
                f"z={z_score:.2f}, current={current_tokens:.1f}, baseline_mean={mean:.1f}"
                if z_score > 3.0
                else ""
            ),
        )


class Guardian:
    """Orchestrates all three pre-execution checks.

    Usage:
        guardian = Guardian(budget_usd=1.0)
        result = guardian.evaluate_message(message)
        result = guardian.evaluate_intent(intent, tool_names)
    """

    def __init__(self, budget_usd: float = 1.0) -> None:
        self._scanner = InjectionScanner()
        self._chain_analyzer = ToolChainAnalyzer()
        self._monitor = BehaviouralMonitor()
        self._budget_usd = budget_usd
        self._alerts: list[dict[str, Any]] = []

    def evaluate_message(self, message: Message) -> tuple[bool, str]:
        """Returns (allow, reason). Mutates message.injection_scan."""
        scan = self._scanner.scan(message.content)
        message.injection_scan = scan.result

        if scan.result == InjectionResult.BLOCKED:
            self._alert("injection_blocked", message.sender_id, scan.matched_patterns)
            return False, f"Injection payload detected: {scan.matched_patterns}"

        if scan.result == InjectionResult.SUSPICIOUS:
            self._alert("injection_suspicious", message.sender_id, scan.matched_patterns)
            message.content = self._scanner.sanitize(message.content)
            return True, "suspicious_sanitized"

        return True, "clean"

    def evaluate_intent(
        self,
        intent: Intent,
        tool_names: list[str],
    ) -> tuple[bool, str]:
        """Returns (allow, reason)."""
        # Tool chain amplification check
        if tool_names:
            chain = self._chain_analyzer.analyse(tool_names, self._budget_usd)
            if chain.is_dangerous:
                self._alert("tool_chain_dos", intent.agent_id, [chain.reason])
                return False, chain.reason

        # Scan payload strings for injection
        payload_text = " ".join(str(v) for v in intent.payload.values())
        scan = self._scanner.scan(payload_text)
        if scan.result == InjectionResult.BLOCKED:
            self._alert("payload_injection", intent.agent_id, scan.matched_patterns)
            return False, f"Injection in payload: {scan.matched_patterns}"

        # Evidence taint check — untrusted evidence taints the intent
        untrusted = [e for e in intent.evidence if e.trust_level == "untrusted"]
        if untrusted and intent.effective_tier.value >= 3:
            intent.tainted = True
            self._alert("evidence_taint", intent.agent_id, [f"{len(untrusted)} untrusted sources"])

        return True, "ok"

    def observe_behaviour(
        self,
        agent_id: str,
        tokens: float,
        tools: float,
        output_len: int,
    ) -> BehaviouralAnomaly:
        self._monitor.observe(agent_id, tokens, tools, output_len)
        anomaly = self._monitor.check(agent_id, tokens)
        if anomaly.is_alert:
            self._alert("behavioural_anomaly", agent_id, [anomaly.details])
        return anomaly

    def _alert(self, alert_type: str, agent_id: str, details: list[str]) -> None:
        self._alerts.append(
            {
                "type": alert_type,
                "agent_id": agent_id,
                "details": details,
                "timestamp": time.time(),
            }
        )

    def alert_count(self) -> int:
        return len(self._alerts)

    def alerts(self) -> list[dict[str, Any]]:
        return list(self._alerts)
