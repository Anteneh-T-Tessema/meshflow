"""V7 — Cross-Run Learning: CORAL-inspired shared persistent memory.

CORAL (2026): 3–10× improvement rates through autonomous multi-agent
self-evolution via shared persistent memory across runs.

Agents accumulate strategy patterns, failure modes, and solution templates
that persist across independent runs — the mesh gets smarter over time.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunPattern:
    """A learned pattern from a completed run."""

    pattern_id: str
    task_type: str
    agent_config: str  # JSON: roles and model tiers used
    strategy_summary: str  # what approach worked
    success_rate: float
    avg_cost_usd: float
    avg_tokens: int
    avg_carbon_g: float
    sample_count: int = 1
    last_seen: float = field(default_factory=time.time)


@dataclass
class FailureMode:
    """A documented failure pattern — so future runs can avoid it."""

    mode_id: str
    description: str
    trigger_conditions: str  # JSON: what caused it
    mitigation: str
    occurrence_count: int = 1
    last_seen: float = field(default_factory=time.time)


@dataclass
class LearningQuery:
    task_description: str
    estimated_tokens: int
    available_roles: list[str]


@dataclass
class LearningRecommendation:
    recommended_config: str  # JSON: suggested agent config
    predicted_success_rate: float
    predicted_cost_usd: float
    known_failure_modes: list[str]
    confidence: float
    basis: str  # "n patterns observed"


class CrossRunStore:
    """SQLite-backed store for cross-run learning data.

    In production: swap for Postgres. The schema is identical.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._setup()

    def _setup(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS run_patterns (
                pattern_id TEXT PRIMARY KEY,
                task_type TEXT,
                agent_config TEXT,
                strategy_summary TEXT,
                success_rate REAL,
                avg_cost_usd REAL,
                avg_tokens INTEGER,
                avg_carbon_g REAL,
                sample_count INTEGER,
                last_seen REAL
            );
            CREATE TABLE IF NOT EXISTS failure_modes (
                mode_id TEXT PRIMARY KEY,
                description TEXT,
                trigger_conditions TEXT,
                mitigation TEXT,
                occurrence_count INTEGER,
                last_seen REAL
            );
        """)
        self._conn.commit()

    def save_pattern(self, pattern: RunPattern) -> None:
        existing = self._conn.execute(
            "SELECT pattern_id, sample_count, avg_cost_usd, avg_tokens, success_rate "
            "FROM run_patterns WHERE task_type=? AND agent_config=?",
            (pattern.task_type, pattern.agent_config),
        ).fetchone()

        if existing:
            pid, n, old_cost, old_tokens, old_sr = existing
            n_new = n + 1
            # Running averages
            new_cost = (old_cost * n + pattern.avg_cost_usd) / n_new
            new_tokens = int((old_tokens * n + pattern.avg_tokens) / n_new)
            new_sr = (old_sr * n + pattern.success_rate) / n_new
            self._conn.execute(
                "UPDATE run_patterns SET sample_count=?, avg_cost_usd=?, "
                "avg_tokens=?, success_rate=?, last_seen=? WHERE pattern_id=?",
                (n_new, new_cost, new_tokens, new_sr, time.time(), pid),
            )
        else:
            self._conn.execute(
                "INSERT INTO run_patterns VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    pattern.pattern_id,
                    pattern.task_type,
                    pattern.agent_config,
                    pattern.strategy_summary,
                    pattern.success_rate,
                    pattern.avg_cost_usd,
                    pattern.avg_tokens,
                    pattern.avg_carbon_g,
                    pattern.sample_count,
                    pattern.last_seen,
                ),
            )
        self._conn.commit()

    def save_failure_mode(self, mode: FailureMode) -> None:
        existing = self._conn.execute(
            "SELECT mode_id, occurrence_count FROM failure_modes WHERE description=?",
            (mode.description,),
        ).fetchone()
        if existing:
            mid, n = existing
            self._conn.execute(
                "UPDATE failure_modes SET occurrence_count=?, last_seen=? WHERE mode_id=?",
                (n + 1, time.time(), mid),
            )
        else:
            self._conn.execute(
                "INSERT INTO failure_modes VALUES (?,?,?,?,?,?)",
                (
                    mode.mode_id,
                    mode.description,
                    mode.trigger_conditions,
                    mode.mitigation,
                    mode.occurrence_count,
                    mode.last_seen,
                ),
            )
        self._conn.commit()

    def find_patterns(self, task_type: str, limit: int = 5) -> list[RunPattern]:
        rows = self._conn.execute(
            "SELECT * FROM run_patterns WHERE task_type LIKE ? "
            "ORDER BY success_rate DESC, sample_count DESC LIMIT ?",
            (f"%{task_type}%", limit),
        ).fetchall()
        return [RunPattern(*row) for row in rows]

    def find_failure_modes(self, task_type: str = "", limit: int = 10) -> list[FailureMode]:
        rows = self._conn.execute(
            "SELECT * FROM failure_modes ORDER BY occurrence_count DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [FailureMode(*row) for row in rows]

    def pattern_count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM run_patterns").fetchone()[0])


class CrossRunLearner:
    """Translates stored patterns into actionable recommendations for new runs."""

    def __init__(self, store: CrossRunStore) -> None:
        self._store = store

    def recommend(self, query: LearningQuery) -> LearningRecommendation:
        task_words = query.task_description.lower().split()
        task_type = self._classify_task(task_words)
        patterns = self._store.find_patterns(task_type)
        failure_modes = self._store.find_failure_modes(task_type)

        if not patterns:
            return LearningRecommendation(
                recommended_config=json.dumps({"roles": query.available_roles}),
                predicted_success_rate=0.75,
                predicted_cost_usd=0.05,
                known_failure_modes=[],
                confidence=0.1,
                basis="No prior patterns — using defaults",
            )

        # Use best pattern (highest success rate × sample count)
        best = max(patterns, key=lambda p: p.success_rate * min(p.sample_count, 10))
        total_samples = sum(p.sample_count for p in patterns)

        return LearningRecommendation(
            recommended_config=best.agent_config,
            predicted_success_rate=best.success_rate,
            predicted_cost_usd=best.avg_cost_usd,
            known_failure_modes=[f.description for f in failure_modes[:3]],
            confidence=min(0.95, total_samples / 20),
            basis=f"{total_samples} prior runs observed, best pattern: {best.strategy_summary}",
        )

    def record_run_outcome(
        self,
        task_description: str,
        agent_config: dict[str, Any],
        strategy: str,
        success: bool,
        cost_usd: float,
        tokens: int,
        carbon_g: float,
    ) -> None:
        task_type = self._classify_task(task_description.lower().split())
        pattern_id = hashlib.md5(
            f"{task_type}:{json.dumps(agent_config, sort_keys=True)}".encode()
        ).hexdigest()

        self._store.save_pattern(
            RunPattern(
                pattern_id=pattern_id,
                task_type=task_type,
                agent_config=json.dumps(agent_config, sort_keys=True),
                strategy_summary=strategy,
                success_rate=1.0 if success else 0.0,
                avg_cost_usd=cost_usd,
                avg_tokens=tokens,
                avg_carbon_g=carbon_g,
            )
        )

    def record_failure(self, description: str, conditions: dict[str, Any], mitigation: str) -> None:
        mode_id = hashlib.md5(description.encode()).hexdigest()
        self._store.save_failure_mode(
            FailureMode(
                mode_id=mode_id,
                description=description,
                trigger_conditions=json.dumps(conditions),
                mitigation=mitigation,
            )
        )

    def _classify_task(self, words: list[str]) -> str:
        keywords = {
            "research": ["research", "find", "search", "investigate", "analyze"],
            "code": ["code", "implement", "build", "fix", "debug", "program"],
            "write": ["write", "draft", "compose", "summarize", "report"],
            "data": ["data", "query", "sql", "database", "aggregate"],
            "plan": ["plan", "design", "architect", "strategy"],
        }
        for task_type, kws in keywords.items():
            if any(w in words for w in kws):
                return task_type
        return "general"
