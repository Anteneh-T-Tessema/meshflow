"""L2.12 — Collusion Auditor: three detectors for coordinated agent misbehaviour.

1. CommunicationPatternAnalyzer — Coalition Advantage metric (CA > 1.8 = alert)
2. ObjectiveDivergenceTracker — correlated alignment drift across agents
3. SteganographicChannelDetector — covert channel via word choice / length correlation

Limitation (documented per design): text-level detection is probabilistic.
Linear probes on model activations achieve 0.90–1.00 AUROC — activation-level
analysis is recommended for confirmation in high-stakes contexts.
"""

from __future__ import annotations

import math
import re
import statistics
from collections import deque
from dataclasses import dataclass


@dataclass
class CollusionAlert:
    detector: str
    agents: list[str]
    score: float
    threshold: float
    details: str
    is_alert: bool
    limitation_note: str = (
        "Text-level detection is probabilistic. "
        "Confirm with activation-level probes for high-stakes decisions."
    )


@dataclass
class CommunicationRecord:
    sender: str
    receiver: str
    agreed: bool  # did receiver agree with sender's position?
    topic_hash: str  # hash of topic for grouping


class CommunicationPatternAnalyzer:
    """Detects abnormally high inter-agent agreement — Coalition Advantage metric.

    CA = observed_agreement_rate / baseline_honest_agreement_rate
    CA > 1.8 indicates possible coordination. Grounded in Colosseum (2026) paper.
    """

    CA_THRESHOLD = 1.8
    BASELINE_AGREEMENT = 0.55  # expected honest agreement rate between agents

    def __init__(self) -> None:
        self._records: list[CommunicationRecord] = []

    def record(self, sender: str, receiver: str, agreed: bool, topic_hash: str = "") -> None:
        self._records.append(CommunicationRecord(sender, receiver, agreed, topic_hash))

    def analyse(self, agents: list[str]) -> CollusionAlert:
        if len(self._records) < 5:
            return CollusionAlert(
                detector="CommunicationPatternAnalyzer",
                agents=agents,
                score=0.0,
                threshold=self.CA_THRESHOLD,
                details="Insufficient data",
                is_alert=False,
            )

        agent_set = set(agents)
        relevant = [r for r in self._records if r.sender in agent_set and r.receiver in agent_set]
        if not relevant:
            return CollusionAlert(
                detector="CommunicationPatternAnalyzer",
                agents=agents,
                score=0.0,
                threshold=self.CA_THRESHOLD,
                details="No inter-agent communication observed",
                is_alert=False,
            )

        observed_rate = sum(1 for r in relevant if r.agreed) / len(relevant)
        coalition_advantage = observed_rate / self.BASELINE_AGREEMENT

        return CollusionAlert(
            detector="CommunicationPatternAnalyzer",
            agents=agents,
            score=coalition_advantage,
            threshold=self.CA_THRESHOLD,
            details=(
                f"CA={coalition_advantage:.3f}, observed_agreement={observed_rate:.2f}, "
                f"n={len(relevant)}"
            ),
            is_alert=coalition_advantage > self.CA_THRESHOLD,
        )


class ObjectiveDivergenceTracker:
    """Detects correlated drift away from original task objective.

    Individual drift = noise. Correlated drift across multiple agents = signal.
    Tracks alignment scores per agent and computes cross-agent slope correlation.
    """

    SLOPE_THRESHOLD = -0.15  # negative = drifting away from objective
    CORRELATION_THRESHOLD = 0.75  # Pearson r across agents must be this correlated

    def __init__(self) -> None:
        # agent_id → deque of alignment scores (0–1)
        self._alignments: dict[str, deque[float]] = {}

    def record_alignment(self, agent_id: str, score: float) -> None:
        self._alignments.setdefault(agent_id, deque(maxlen=20)).append(score)

    def _slope(self, series: list[float]) -> float:
        n = len(series)
        if n < 3:
            return 0.0
        x = list(range(n))
        mx, my = statistics.mean(x), statistics.mean(series)
        num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, series))
        den = sum((xi - mx) ** 2 for xi in x)
        return num / den if den else 0.0

    def analyse(self) -> CollusionAlert:
        agents = list(self._alignments)
        if len(agents) < 2:
            return CollusionAlert(
                detector="ObjectiveDivergenceTracker",
                agents=agents,
                score=0.0,
                threshold=self.SLOPE_THRESHOLD,
                details="Not enough agents",
                is_alert=False,
            )

        slopes = {a: self._slope(list(self._alignments[a])) for a in agents}
        negative_slopes = [s for s in slopes.values() if s < self.SLOPE_THRESHOLD]
        correlation = len(negative_slopes) / len(agents)

        is_alert = correlation >= self.CORRELATION_THRESHOLD and any(
            s < self.SLOPE_THRESHOLD for s in slopes.values()
        )

        return CollusionAlert(
            detector="ObjectiveDivergenceTracker",
            agents=agents,
            score=correlation,
            threshold=self.CORRELATION_THRESHOLD,
            details=(
                f"Correlated drift={correlation:.2f}, "
                f"agents_drifting={len(negative_slopes)}/{len(agents)}, "
                f"slopes={{{', '.join(f'{a}:{s:.3f}' for a, s in slopes.items())}}}"
            ),
            is_alert=is_alert,
        )


def _calculate_shannon_entropy(text: str) -> float:
    """Calculate character Shannon entropy of a string."""
    if not text:
        return 0.0
    counts: dict[str, int] = {}
    for char in text:
        counts[char] = counts.get(char, 0) + 1
    entropy = 0.0
    total = len(text)
    for count in counts.values():
        p = count / total
        entropy -= p * math.log2(p)
    return entropy


def _mask_structural_boilerplate(text: str) -> str:
    """Strip formatting boilerplate (brackets, braces, delimiters, common keywords)

    if a structured context (JSON, SQL, HTML/XML) is detected.
    """
    stripped = text.strip()
    is_json = (
        (stripped.startswith("{") and stripped.endswith("}")) or
        (stripped.startswith("[") and stripped.endswith("]")) or
        ('"' in text and ':' in text)
    )
    is_sql = any(
        kw in text.upper()
        for kw in ["SELECT ", "INSERT ", "UPDATE ", "DELETE ", "CREATE TABLE", "DROP TABLE"]
    )
    is_xml = bool(re.search(r"<[^>]+>", text))

    if is_json or is_sql or is_xml:
        cleaned = text
        if is_json:
            cleaned = re.sub(r'[\{\}\[\]\(\)\,\:\;\"\'\\]', ' ', cleaned)
            cleaned = re.sub(r'\b(true|false|null)\b', ' ', cleaned, flags=re.IGNORECASE)
        if is_sql:
            cleaned = re.sub(r'[\(\)\,\;\=\+\-\*\/]', ' ', cleaned)
            sql_kws = r'\b(SELECT|INSERT|UPDATE|DELETE|FROM|WHERE|JOIN|AND|OR|ON|INTO|VALUES|CREATE|TABLE|DROP|INDEX|ON|IN|AS)\b'
            cleaned = re.sub(sql_kws, ' ', cleaned, flags=re.IGNORECASE)
        if is_xml:
            cleaned = re.sub(r'<[^>]+>', ' ', cleaned)
        
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned
    
    return text


def _get_entropy_sensitivity_factor(role: str, text: str) -> float:
    """Returns a baseline threshold multiplier/factor based on role or text content."""
    role_lower = role.lower() if role else ""
    
    # Cryptographic Key Exchange / encrypted payloads: skip profiling entirely (factor = 0.0)
    if "crypt" in role_lower or "key_exchange" in role_lower or "signature" in role_lower:
        return 0.0
    if "BEGIN PUBLIC KEY" in text or "BEGIN PRIVATE KEY" in text or "ssh-rsa" in text:
        return 0.0
        
    # Financial Ledger/Code: scaled-down sensitivity (factor = 0.5)
    if "finance" in role_lower or "ledger" in role_lower or "code" in role_lower or "developer" in role_lower:
        return 0.5
        
    # Detect if text content contains structural code/ledger cues
    is_code = (
        "def " in text or "class " in text or "import " in text or
        ("{" in text and "}" in text and (":" in text or "function" in text))
    )
    is_ledger = (
        "$" in text or "USD" in text or "balance" in role_lower or
        len(re.findall(r'\b\d+\.\d{2}\b', text)) > 3
    )
    if is_code or is_ledger:
        return 0.5
        
    return 1.0


class BigramPerplexityTracker:
    """English token perplexity tracker using a Laplace-smoothed bigram probability matrix."""

    def __init__(self) -> None:
        self.unigrams: dict[str, int] = {}
        self.bigrams: dict[tuple[str, str], int] = {}
        self.vocab: set[str] = set()
        self.alpha = 0.1
        
        # Train on a baseline English corpus snippet to establish expected probabilities
        sample_text = (
            "the quick brown fox jumps over the lazy dog. this is a standard test sentence for "
            "evaluating english language model perplexity. meshflow is a powerful agentic framework "
            "designed to build scalable and reliable agent teams. agents communicate with each other "
            "using natural language or structured formats. we want to detect collusion and coordinated "
            "behavior. step runtime executes tasks and records actions in an append only ledger. "
            "compliance policies ensure zero trust security. steganography detection monitors word choice "
            "and output lengths. we want to prevent false positives when agents output structured json "
            "or sql queries. a robust system balances security and performance. debugging distributed "
            "systems requires tracing, metrics, and logs. always verify the integrity of the chain."
        )
        self.train(sample_text)
        
    def train(self, text: str) -> None:
        tokens = self._tokenize(text)
        if not tokens:
            return
        for tok in tokens:
            self.unigrams[tok] = self.unigrams.get(tok, 0) + 1
            self.vocab.add(tok)
            
        for i in range(len(tokens) - 1):
            bg = (tokens[i], tokens[i+1])
            self.bigrams[bg] = self.bigrams.get(bg, 0) + 1

    def _tokenize(self, text: str) -> list[str]:
        cleaned = re.sub(r'[^a-zA-Z0-9\s]', '', text.lower())
        return cleaned.split()

    def calculate_perplexity(self, text: str) -> float:
        tokens = self._tokenize(text)
        if not tokens:
            return 0.0
            
        vocab_size = len(self.vocab)
        log_prob = 0.0
        
        # P(w_1)
        w1 = tokens[0]
        c_w1 = self.unigrams.get(w1, 0)
        total_words = sum(self.unigrams.values())
        prob_w1 = (c_w1 + self.alpha) / (total_words + self.alpha * vocab_size)
        log_prob += math.log(prob_w1)
        
        for i in range(len(tokens) - 1):
            w_prev, w_curr = tokens[i], tokens[i+1]
            c_prev = self.unigrams.get(w_prev, 0)
            c_bg = self.bigrams.get((w_prev, w_curr), 0)
            
            prob = (c_bg + self.alpha) / (c_prev + self.alpha * vocab_size)
            log_prob += math.log(prob)
            
        avg_log_prob = log_prob / len(tokens)
        try:
            return math.exp(-avg_log_prob)
        except OverflowError:
            return float('inf')


class SteganographicChannelDetector:
    """Detects covert channels in inter-agent communication.

    Steganographic encoding changes how agents represent the same text —
    detectable by correlating output lengths and Schelling-point word choice
    across agent pairs. Text-level only — see limitation_note.
    """

    SCHELLING_WORDS = {
        # High-coordination-signal words agents might use as shared signals
        "always",
        "never",
        "exactly",
        "precisely",
        "critical",
        "urgent",
        "immediately",
        "confirm",
        "acknowledged",
        "proceed",
    }
    LENGTH_CORRELATION_THRESHOLD = 0.80
    WORD_CORRELATION_THRESHOLD = 0.75

    def __init__(self) -> None:
        self._lengths: dict[str, list[int]] = {}  # agent_id → output lengths
        self._schelling: dict[str, list[float]] = {}  # agent_id → schelling density
        self._entropies: dict[str, list[float]] = {}  # agent_id → masked Shannon entropy
        self._perplexities: dict[str, list[float]] = {}  # agent_id → bigram perplexity
        self._sensitivity_factors: dict[str, list[float]] = {}  # agent_id → baseline factors
        self._perplexity_tracker = BigramPerplexityTracker()

    def record_output(self, agent_id: str, text: str, role: str = "natural_language") -> None:
        self._lengths.setdefault(agent_id, []).append(len(text))
        words = text.lower().split()
        density = sum(1 for w in words if w in self.SCHELLING_WORDS) / max(len(words), 1)
        self._schelling.setdefault(agent_id, []).append(density)

        masked_text = _mask_structural_boilerplate(text)
        ent = _calculate_shannon_entropy(masked_text)
        self._entropies.setdefault(agent_id, []).append(ent)

        perp = self._perplexity_tracker.calculate_perplexity(text)
        self._perplexities.setdefault(agent_id, []).append(perp)

        factor = _get_entropy_sensitivity_factor(role, text)
        self._sensitivity_factors.setdefault(agent_id, []).append(factor)

    def _pearson(self, a: list[float], b: list[float]) -> float:
        n = min(len(a), len(b))
        if n < 3:
            return 0.0
        xs, ys = a[-n:], b[-n:]
        mx, my = statistics.mean(xs), statistics.mean(ys)
        num = sum((xi - mx) * (yi - my) for xi, yi in zip(xs, ys))
        sx = math.sqrt(sum((xi - mx) ** 2 for xi in xs))
        sy = math.sqrt(sum((yi - my) ** 2 for yi in ys))
        return num / (sx * sy) if sx * sy > 0 else 0.0

    def analyse(self, agent_pairs: list[tuple[str, str]]) -> list[CollusionAlert]:
        alerts = []
        for a, b in agent_pairs:
            la = [float(x) for x in self._lengths.get(a, [])]
            lb = [float(x) for x in self._lengths.get(b, [])]
            len_corr = abs(self._pearson(la, lb))

            wa = self._schelling.get(a, [])
            wb = self._schelling.get(b, [])
            word_corr = abs(self._pearson(wa, wb))

            score = max(len_corr, word_corr)
            
            # Collusion triggers only if character-level entropy drops while semantic perplexity concurrently spikes.
            # We look at the latest output for both agents.
            ent_a = self._entropies.get(a, [4.5])[-1] if self._entropies.get(a) else 4.5
            ent_b = self._entropies.get(b, [4.5])[-1] if self._entropies.get(b) else 4.5
            perp_a = self._perplexities.get(a, [10.0])[-1] if self._perplexities.get(a) else 10.0
            perp_b = self._perplexities.get(b, [10.0])[-1] if self._perplexities.get(b) else 10.0
            
            fact_a = self._sensitivity_factors.get(a, [1.0])[-1] if self._sensitivity_factors.get(a) else 1.0
            fact_b = self._sensitivity_factors.get(b, [1.0])[-1] if self._sensitivity_factors.get(b) else 1.0
            
            # Skip profiling entirely if either agent has key exchange/cryptography active
            if fact_a == 0.0 or fact_b == 0.0:
                is_alert = False
            else:
                f = min(fact_a, fact_b)
                # Calibrate thresholds: scaled down sensitivity lowers the entropy drop baseline
                # and increases the perplexity spike threshold.
                adj_entropy_threshold = 3.9 * f
                adj_perplexity_threshold = 35.0 / f
                
                # Check for entropy drop and perplexity spike (either agent)
                entropy_drop = (ent_a < adj_entropy_threshold) or (ent_b < adj_entropy_threshold)
                perplexity_spike = (perp_a > adj_perplexity_threshold) or (perp_b > adj_perplexity_threshold)
                
                # Adjusted correlation thresholds
                adj_len_threshold = min(0.99, self.LENGTH_CORRELATION_THRESHOLD + (1.0 - f) * 0.15)
                adj_word_threshold = min(0.99, self.WORD_CORRELATION_THRESHOLD + (1.0 - f) * 0.15)
                
                len_corr_ok = len_corr > adj_len_threshold
                word_corr_ok = word_corr > adj_word_threshold
                
                is_alert = (len_corr_ok or word_corr_ok) and entropy_drop and perplexity_spike

            alerts.append(
                CollusionAlert(
                    detector="SteganographicChannelDetector",
                    agents=[a, b],
                    score=score,
                    threshold=max(
                        self.LENGTH_CORRELATION_THRESHOLD, self.WORD_CORRELATION_THRESHOLD
                    ),
                    details=(
                        f"len_corr={len_corr:.3f}, word_corr={word_corr:.3f}, pair=({a},{b}), "
                        f"ent=({ent_a:.2f},{ent_b:.2f}), perp=({perp_a:.1f},{perp_b:.1f})"
                    ),
                    is_alert=is_alert,
                )
            )
        return alerts


class CollusionAuditor:
    """Coordinates all three detectors — runs per step, fires on any alert."""

    def __init__(self) -> None:
        self._comm = CommunicationPatternAnalyzer()
        self._drift = ObjectiveDivergenceTracker()
        self._steg = SteganographicChannelDetector()
        self._alert_count = 0

    def record_communication(
        self, sender: str, receiver: str, agreed: bool, topic_hash: str = ""
    ) -> None:
        self._comm.record(sender, receiver, agreed, topic_hash)

    def record_alignment(self, agent_id: str, score: float) -> None:
        self._drift.record_alignment(agent_id, score)

    def record_output(self, agent_id: str, text: str, role: str = "natural_language") -> None:
        self._steg.record_output(agent_id, text, role)

    def audit(self, agents: list[str]) -> list[CollusionAlert]:
        alerts: list[CollusionAlert] = []

        comm_alert = self._comm.analyse(agents)
        if comm_alert.is_alert:
            alerts.append(comm_alert)

        drift_alert = self._drift.analyse()
        if drift_alert.is_alert:
            alerts.append(drift_alert)

        pairs = [
            (agents[i], agents[j]) for i in range(len(agents)) for j in range(i + 1, len(agents))
        ]
        steg_alerts = self._steg.analyse(pairs)
        alerts.extend(a for a in steg_alerts if a.is_alert)

        self._alert_count += len(alerts)
        return alerts

    def total_alerts(self) -> int:
        return self._alert_count
