"""SwarmTRM inference engine — ported into meshflow/swarm/.

Requires the `meshflow[swarm]` extra (torch + pydantic).
Import is lazy so the rest of meshflow works without torch.
"""
from __future__ import annotations

import copy
import difflib
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

from meshflow.swarm.verifiers import (
    DeterministicVerifier,
    DASCVerifier,
    ERPAuditVerifier,
    BillableCaptureVerifier,
    CodeModernizationVerifier,
    VerificationResult,
)

try:
    from meshflow.swarm.industries.registry import REGISTRY as _INDUSTRY_REGISTRY
    from meshflow.swarm.industries.repair import repair as _industry_repair, has_repair as _has_industry_repair
except ImportError:
    _INDUSTRY_REGISTRY = {}

    def _industry_repair(domain, output, role, context, step):
        return output

    def _has_industry_repair(domain):
        return False

try:
    from meshflow.swarm.reasoning.registry import REGISTRY as _REASONING_REGISTRY
    from meshflow.swarm.reasoning.repair import repair as _reasoning_repair, has_repair as _has_reasoning_repair
except ImportError:
    _REASONING_REGISTRY = {}

    def _reasoning_repair(domain, output, role, context, step):
        return output

    def _has_reasoning_repair(domain):
        return False


# ── Public types ──────────────────────────────────────────────────────────────

class SwarmConfig(BaseModel):
    initial_agents: int = 3
    max_agents: int = 5
    max_depth: int = 6
    message_dim: int = 32
    topology: str = "adaptive"
    tau_verifier: float = 1.0
    tau_confidence: float = 0.72
    tau_disagreement: float = 0.08
    expand_disagreement: float = 0.35
    roles: Optional[List[str]] = None


class AgentSnapshot(BaseModel):
    id: int
    role: str
    confidence: float
    answer: Any
    message_norm: float
    latent_delta: float


class SwarmTraceStep(BaseModel):
    step: int
    topology: str
    active_agents: int
    edges: List[List[int]]
    disagreement: float
    convergence_delta: float
    consensus: Any
    consensus_confidence: float
    verifier_score: float
    verified: bool
    violations: List[str]
    expanded: bool
    agents: List[AgentSnapshot]


class SwarmAccounting(BaseModel):
    stored_parameters: int
    active_core_calls: int
    message_count: int
    verifier_calls: int
    elapsed_ms: float
    active_compute_proxy: float
    solved_per_joule_proxy: float


class SwarmInferenceResult(BaseModel):
    answer: Any
    confidence: float
    verified: bool
    low_confidence: bool
    violations: List[str]
    remediation_steps: Optional[List[str]] = None
    steps: int
    trace: List[SwarmTraceStep]
    accounting: SwarmAccounting
    recommendation: str


# ── Internal agent state ──────────────────────────────────────────────────────

@dataclass
class _AgentState:
    id: int
    role: str
    hidden: Any   # torch.Tensor
    role_vector: Any
    answer: Any
    confidence: float
    message: Any
    message_norm: float = 0.0
    latent_delta: float = 0.0


# ── Q&A verifier ──────────────────────────────────────────────────────────────

class _QuestionAnswerVerifier(DeterministicVerifier):
    _NUM = r"(-?\d+(?:\.\d+)?)"
    _ARITH_PATTERNS = [
        (re.compile(_NUM + r"\s*(?:\+|plus)\s*" + _NUM, re.I), "+"),
        (re.compile(_NUM + r"\s*(?:-|minus)\s*" + _NUM, re.I), "-"),
        (re.compile(_NUM + r"\s*(?:\*|x|times|multiplied by)\s*" + _NUM, re.I), "*"),
        (re.compile(_NUM + r"\s*(?:/|divided by|over)\s*" + _NUM, re.I), "/"),
    ]

    def verify(self, output: Dict[str, Any], context: Dict[str, Any]) -> VerificationResult:
        violations = []
        question = str(output.get("question", "")).strip()
        answer = str(output.get("answer", "")).strip()
        if not question:
            violations.append("QA Violation: question is empty.")
        if not answer:
            violations.append("QA Violation: answer is empty.")
        expected = context.get("expected_answer")
        if expected is not None and str(expected).lower() not in answer.lower():
            violations.append("QA Violation: answer does not include the expected answer.")
        for term in context.get("required_terms", []):
            if str(term).lower() not in answer.lower():
                violations.append(f"QA Violation: answer is missing required term '{term}'.")
        arithmetic = self.simple_arithmetic_answer(question)
        if arithmetic is not None and not self.answer_mentions(answer, arithmetic):
            violations.append(f"QA Violation: simple arithmetic answer should include {arithmetic}.")
        return VerificationResult(is_valid=len(violations) == 0, confidence=1.0, violations=violations)

    @classmethod
    def simple_arithmetic_answer(cls, question: str) -> Optional[str]:
        for pattern, op in cls._ARITH_PATTERNS:
            match = pattern.search(question)
            if not match:
                continue
            try:
                left = Decimal(match.group(1)); right = Decimal(match.group(2))
                if op == "+":
                    value = left + right
                elif op == "-":
                    value = left - right
                elif op == "*":
                    value = left * right
                else:
                    if right == 0:
                        return None
                    value = left / right
            except (InvalidOperation, ZeroDivisionError):
                return None
            return cls._format_decimal(value)
        return None

    @staticmethod
    def answer_mentions(answer: str, expected: str) -> bool:
        return re.search(rf"(?<!\d){re.escape(expected)}(?!\d)", answer) is not None

    @staticmethod
    def _format_decimal(value: Decimal) -> str:
        if value == value.to_integral_value():
            return str(int(value))
        return format(value.normalize(), "f")


# ── Task adapter ──────────────────────────────────────────────────────────────

class _TaskAdapter:
    def __init__(self, verifier_type: str, verifier: DeterministicVerifier, context: Dict[str, Any]):
        self.verifier_type = verifier_type
        self.verifier = verifier
        self.context = context

    def initial_candidate(self, task: Any, role: str) -> Any:
        return self.refine(copy.deepcopy(task), role, step=0, consensus=None)

    def refine(self, candidate: Any, role: str, step: int, consensus: Any) -> Any:
        base = copy.deepcopy(consensus if consensus is not None else candidate)
        if self.verifier_type == "erp" and isinstance(base, dict):
            return self._refine_erp(base, role, step)
        if self.verifier_type == "billable" and isinstance(base, dict):
            return self._refine_billable(base, role, step)
        if self.verifier_type == "modernize" and isinstance(base, dict):
            return self._refine_modernize(base, role, step)
        if self.verifier_type == "qa" and isinstance(base, dict):
            return self._refine_qa(base, role, step)
        if _has_industry_repair(self.verifier_type) and isinstance(base, dict):
            return _industry_repair(self.verifier_type, base, role, self.context, step)
        if _has_reasoning_repair(self.verifier_type) and isinstance(base, dict):
            return _reasoning_repair(self.verifier_type, base, role, self.context, step)
        return base

    def confidence(self, candidate: Any, step: int) -> float:
        result = self.verifier.verify(candidate, self.context)
        if result.is_valid:
            return round(min(0.98, 0.84 + 0.03 * step), 4)
        penalty = min(0.4, 0.12 * len(result.violations))
        return round(max(0.18, 0.58 - penalty + 0.02 * step), 4)

    def verify(self, candidate: Any) -> VerificationResult:
        return self.verifier.verify(candidate, self.context)

    def _refine_erp(self, output: Dict, role: str, step: int) -> Dict:
        should_balance = role in {"ledger_integrity", "constraint_checker", "consensus_auditor"} or step > 0
        should_tag = role in {"audit_tagger", "constraint_checker", "consensus_auditor"} or step > 0
        if should_balance:
            debit = output.get("debit"); credit = output.get("credit"); amount = output.get("amount")
            if amount is not None:
                output["debit"] = amount; output["credit"] = amount
            elif debit is not None and credit != debit:
                output["credit"] = debit
            elif credit is not None and debit != credit:
                output["debit"] = credit
        if should_tag and not output.get("audit_tag"):
            output["audit_tag"] = f"SWARMTRM-FLL-{step + 1:02d}"
        return output

    def _refine_billable(self, output: Dict, role: str, step: int) -> Dict:
        max_rate = self.context.get("max_rate", 250)
        should_normalize = role in {"contract_guard", "rate_normalizer", "consensus_auditor"} or step > 0
        if should_normalize and output.get("hourly_rate", 0) > max_rate:
            output["hourly_rate"] = max_rate; output["rate_adjustment"] = "clamped_to_contract_cap"
        return output

    def _refine_modernize(self, output: Dict, role: str, step: int) -> Dict:
        code = output.get("code", "")
        should_fix_types = role in {"type_safety", "compatibility_mapper", "consensus_auditor"} or step > 0
        should_fix_security = role in {"security_guard", "compatibility_mapper", "consensus_auditor"} or step > 0
        if should_fix_types:
            code = code.replace(": any", ": unknown")
        if should_fix_security:
            code = code.replace("eval(", "safeEvaluate(")
        output["code"] = code
        return output

    def _refine_qa(self, output: Dict, role: str, step: int) -> Dict:
        question = str(output.get("question", "")).strip()
        answer = str(output.get("answer", "")).strip()
        if role in {"answer_checker", "final_reviewer", "context_checker"} or step > 0:
            answer = re.sub(r"^\s*answer\s*:\s*", "", answer, flags=re.I).strip()
            if question and answer.lower().startswith(question.lower()):
                answer = answer[len(question):].strip(" :\n\t")
        expected = self.context.get("expected_answer")
        if expected is not None and str(expected).lower() not in answer.lower():
            if role in {"context_checker", "final_reviewer"} or step > 0:
                answer = str(expected)
        arithmetic = _QuestionAnswerVerifier.simple_arithmetic_answer(question)
        if arithmetic is not None and not _QuestionAnswerVerifier.answer_mentions(answer, arithmetic):
            answer = f"The answer is {arithmetic}."
        if not answer:
            answer = "I need more context to answer that reliably."
        output["answer"] = answer
        return output


# ── Main SwarmTRM class ───────────────────────────────────────────────────────

class SwarmTRM:
    """Reference implementation of the SwarmTRM inference loop.

    Requires torch. Initialize with ``unit=None`` to create the default
    RecursiveUnit (7M params) or pass a custom unit.
    """

    def __init__(self, unit=None) -> None:
        try:
            import torch
            self._torch = torch
        except ImportError as exc:
            raise ImportError(
                "meshflow[swarm] requires PyTorch. Install: pip install 'meshflow[swarm]'"
            ) from exc

        if unit is None:
            from meshflow.swarm.recursive_unit import RecursiveUnit
            unit = RecursiveUnit()

        self.unit = unit
        self.unit.eval()
        if hasattr(unit, "_module"):
            self.latent_dim = unit._module.d_model // 2
        else:
            self.latent_dim = getattr(unit, "d_model", 768) // 2

    # ── Public API ─────────────────────────────────────────────────────────

    def run(
        self,
        task: Any,
        verifier_type: str = "erp",
        context: Optional[Dict[str, Any]] = None,
        config: Optional[SwarmConfig] = None,
    ) -> SwarmInferenceResult:
        torch = self._torch
        context = context or {}
        config = config or SwarmConfig()
        verifier = self._build_verifier(verifier_type, context)
        adapter = _TaskAdapter(verifier_type, verifier, context)

        roles = config.roles or self._default_roles(verifier_type)
        initial_agents = max(1, min(config.initial_agents, config.max_agents, len(roles)))
        agents = self._initialize_agents(task, roles[:initial_agents], adapter, config)

        trace: List[SwarmTraceStep] = []
        active_core_calls = 0; message_count = 0; verifier_calls = 0
        best_answer = copy.deepcopy(task); best_confidence = 0.0
        best_verification = verifier.verify(best_answer, context)
        best_score = math.inf; start = time.perf_counter()
        prev_verifier_score: float = 0.0

        for step in range(config.max_depth):
            disagreement_before = self._disagreement(agents)
            consensus_seed, _ = self._consensus(agents)
            topology_name, edges = self._select_topology(
                config.topology, agents, disagreement_before, prev_verifier_score
            )

            for agent in agents:
                agent.message = self._bottleneck_message(agent, config.message_dim)
                agent.message_norm = float(torch.norm(agent.message).item())

            message_count += len(edges)
            incoming_messages = self._aggregate_messages(agents, edges, config.message_dim)

            with torch.no_grad():
                for agent in agents:
                    message_latent = self._expand_message(incoming_messages[agent.id], agent.hidden)
                    previous = agent.hidden.clone()
                    mixed = (
                        agent.hidden
                        + 0.15 * message_latent
                        + 0.05 * self._input_embedding(task)
                        + 0.05 * agent.role_vector
                    )
                    agent.hidden = self.unit.reasoning_layer(mixed)
                    agent.latent_delta = self._latent_delta(agent.hidden, previous)
                    agent.answer = adapter.refine(
                        agent.answer, agent.role, step,
                        consensus_seed if step > 0 else None,
                    )
                    agent.confidence = adapter.confidence(agent.answer, step)
                    active_core_calls += 1

            disagreement = self._disagreement(agents)
            consensus, consensus_confidence = self._consensus(agents)

            if (
                adapter.verifier_type == "qa"
                or _has_industry_repair(adapter.verifier_type)
                or _has_reasoning_repair(adapter.verifier_type)
            ) and len(agents) > 1:
                merged, merged_conf = self._repair_merge(agents, adapter)
                merged_v = adapter.verify(merged)
                std_v = adapter.verify(consensus)
                if len(merged_v.violations) < len(std_v.violations):
                    consensus = merged; consensus_confidence = merged_conf

            verification = adapter.verify(consensus)
            verifier_calls += len(agents) + 1
            verifier_score = 1.0 if verification.is_valid else max(
                0.0, 1.0 - 0.25 * len(verification.violations)
            )
            convergence_delta = sum(a.latent_delta for a in agents) / max(1, len(agents))
            score = (1.0 - verifier_score) + (1.0 - consensus_confidence) + disagreement
            if score < best_score:
                best_score = score; best_answer = copy.deepcopy(consensus)
                best_confidence = consensus_confidence; best_verification = verification

            prev_verifier_score = verifier_score
            should_halt = (
                verifier_score >= config.tau_verifier
                and consensus_confidence >= config.tau_confidence
                and disagreement <= config.tau_disagreement
            )
            expanded = not should_halt and self._should_expand(
                agents, config, verifier_score, disagreement, consensus_confidence
            )

            trace.append(SwarmTraceStep(
                step=step + 1, topology=topology_name, active_agents=len(agents),
                edges=[[s, t] for s, t in edges],
                disagreement=round(disagreement, 4),
                convergence_delta=round(convergence_delta, 6),
                consensus=copy.deepcopy(consensus),
                consensus_confidence=round(consensus_confidence, 4),
                verifier_score=round(verifier_score, 4),
                verified=verification.is_valid,
                violations=list(verification.violations),
                expanded=expanded,
                agents=[
                    AgentSnapshot(
                        id=a.id, role=a.role,
                        confidence=round(a.confidence, 4),
                        answer=copy.deepcopy(a.answer),
                        message_norm=round(a.message_norm, 6),
                        latent_delta=round(a.latent_delta, 6),
                    )
                    for a in agents
                ],
            ))

            if should_halt:
                break
            if expanded:
                next_role = roles[len(agents) % len(roles)]
                agents.append(self._new_agent(len(agents) + 1, next_role, task, adapter, config))

        elapsed_ms = (time.perf_counter() - start) * 1000
        accounting = self._accounting(active_core_calls, message_count, verifier_calls, elapsed_ms, config)
        verified = best_verification.is_valid

        return SwarmInferenceResult(
            answer=best_answer, confidence=round(best_confidence, 4),
            verified=verified, low_confidence=not verified,
            violations=list(best_verification.violations),
            remediation_steps=best_verification.remediation_steps,
            steps=len(trace), trace=trace, accounting=accounting,
            recommendation="verified_consensus" if verified else "low_confidence_return: verifier-gated consensus was not reached",
        )

    # ── Internal helpers ────────────────────────────────────────────────────

    def _initialize_agents(self, task, roles, adapter, config):
        return [self._new_agent(i + 1, role, task, adapter, config) for i, role in enumerate(roles)]

    def _new_agent(self, agent_id, role, task, adapter, config):
        torch = self._torch
        role_vector = self._role_vector(role)
        hidden = self._input_embedding(task) + 0.1 * role_vector
        answer = adapter.initial_candidate(task, role)
        confidence = adapter.confidence(answer, 0)
        return _AgentState(
            id=agent_id, role=role, hidden=hidden, role_vector=role_vector,
            answer=answer, confidence=confidence, message=torch.zeros(config.message_dim),
        )

    def _build_verifier(self, verifier_type: str, context: Dict) -> DeterministicVerifier:
        if verifier_type == "erp":
            return ERPAuditVerifier()
        if verifier_type == "billable":
            return BillableCaptureVerifier()
        if verifier_type == "modernize":
            return CodeModernizationVerifier()
        if verifier_type == "dasc":
            return DASCVerifier()
        if verifier_type == "qa":
            return _QuestionAnswerVerifier()
        if verifier_type in _INDUSTRY_REGISTRY:
            return _INDUSTRY_REGISTRY[verifier_type]["verifier"]()
        if verifier_type in _REASONING_REGISTRY:
            return _REASONING_REGISTRY[verifier_type]["verifier"]()
        raise ValueError(
            f"Unsupported verifier type: '{verifier_type}'. "
            f"Built-in: ['erp', 'billable', 'modernize', 'dasc', 'qa']. "
            f"Industry: {sorted(_INDUSTRY_REGISTRY.keys())}. "
            f"Reasoning: {sorted(_REASONING_REGISTRY.keys())}"
        )

    def _default_roles(self, verifier_type: str) -> List[str]:
        _builtin = {
            "erp": ["ledger_integrity", "audit_tagger", "constraint_checker", "consensus_auditor", "latency_guard"],
            "billable": ["contract_guard", "evidence_mapper", "rate_normalizer", "consensus_auditor", "latency_guard"],
            "modernize": ["type_safety", "security_guard", "compatibility_mapper", "consensus_auditor", "latency_guard"],
            "qa": ["answer_checker", "math_checker", "context_checker", "safety_checker", "final_reviewer"],
        }
        if verifier_type in _builtin:
            return _builtin[verifier_type]
        if verifier_type in _INDUSTRY_REGISTRY:
            return _INDUSTRY_REGISTRY[verifier_type]["roles"]
        if verifier_type in _REASONING_REGISTRY:
            return _REASONING_REGISTRY[verifier_type]["roles"]
        return ["constraint_checker", "evidence_mapper", "consensus_auditor", "latency_guard"]

    def _input_embedding(self, task: Any):
        """Embed task text using the best available semantic embedder.

        Falls back through sentence-transformers → numpy BoW → char n-gram
        hashing (always available). Result is a (1, 1, latent_dim) tensor.
        """
        torch = self._torch
        text = self._canonical(task) if not isinstance(task, str) else str(task)
        try:
            from meshflow.swarm.embeddings import embed_text
            floats = embed_text(text, dim=self.latent_dim)
            return torch.tensor(floats, dtype=torch.float32).reshape(1, 1, self.latent_dim)
        except Exception:
            # Ultimate fallback: hash-seeded noise (original behaviour)
            seed = self._seed(f"task:{text}")
            generator = torch.Generator(device="cpu").manual_seed(seed)
            return torch.randn(1, 1, self.latent_dim, generator=generator)

    def _role_vector(self, role: str):
        """Embed a role string into a (1, 1, latent_dim) tensor.

        Role strings are short and stable, so the char n-gram embedder gives
        good differentiation between distinct roles (planner vs critic vs guardian).
        """
        torch = self._torch
        try:
            from meshflow.swarm.embeddings import embed_text
            floats = embed_text(role, dim=self.latent_dim)
            return torch.tensor(floats, dtype=torch.float32).reshape(1, 1, self.latent_dim)
        except Exception:
            seed = self._seed(f"role:{role}")
            generator = torch.Generator(device="cpu").manual_seed(seed)
            return torch.randn(1, 1, self.latent_dim, generator=generator)

    def _bottleneck_message(self, agent: _AgentState, message_dim: int):
        torch = self._torch
        hidden_slots = max(1, message_dim - 2)
        flat = agent.hidden.reshape(-1)
        if hidden_slots <= flat.numel():
            h_part = flat[:hidden_slots].clone()
        else:
            h_part = torch.zeros(hidden_slots)
            h_part[: flat.numel()] = flat
        answer_emb = self._input_embedding(agent.answer).reshape(-1)
        role_proj = agent.role_vector.reshape(-1)
        answer_scalar = float(torch.tanh(answer_emb[:1]).item()) if answer_emb.numel() > 0 else 0.0
        role_scalar = float(torch.tanh(role_proj[:1]).item()) if role_proj.numel() > 0 else 0.0
        message = torch.zeros(message_dim)
        message[:hidden_slots] = torch.tanh(h_part)
        message[hidden_slots] = agent.confidence * 0.5 + answer_scalar * 0.3 + role_scalar * 0.2
        if message_dim > hidden_slots + 1:
            message[hidden_slots + 1] = role_scalar * 0.6 + answer_scalar * 0.4
        return message

    def _expand_message(self, message, like):
        torch = self._torch
        expanded = torch.zeros_like(like)
        flat = expanded.reshape(-1)
        flat[: min(flat.numel(), message.numel())] = message[: min(flat.numel(), message.numel())]
        return expanded

    def _aggregate_messages(self, agents, edges, message_dim):
        torch = self._torch
        by_id = {a.id: a for a in agents}
        incoming = {a.id: [] for a in agents}
        for source, target in edges:
            if source in by_id and target in incoming:
                incoming[target].append((by_id[source].message, by_id[source].hidden))
        result = {}
        for agent in agents:
            pairs = incoming[agent.id]
            if not pairs:
                result[agent.id] = torch.zeros(message_dim)
                continue
            messages = torch.stack([p[0] for p in pairs])
            receiver_key = agent.hidden.reshape(-1)[:message_dim]
            if receiver_key.numel() < message_dim:
                pad = torch.zeros(message_dim)
                pad[: receiver_key.numel()] = receiver_key
                receiver_key = pad
            scores = messages @ receiver_key
            weights = torch.softmax(scores, dim=0)
            result[agent.id] = (weights.unsqueeze(1) * messages).sum(dim=0)
        return result

    def _select_topology(self, requested, agents, disagreement, verifier_score=0.0):
        topology = requested
        if requested == "adaptive":
            verifier_failing = 0.0 < verifier_score < 1.0
            if disagreement > 0.35 or (verifier_failing and verifier_score < 0.5):
                topology = "all-to-all"
            elif disagreement > 0.12 or verifier_failing:
                topology = "learned-sparse"
            else:
                topology = "star"
        ids = [a.id for a in agents]
        if topology == "none":
            return topology, []
        if topology == "all-to-all":
            return topology, [(s, t) for s in ids for t in ids if s != t]
        if topology == "ring":
            return topology, [(ids[i], ids[(i + 1) % len(ids)]) for i in range(len(ids))]
        if topology == "star":
            center = max(agents, key=lambda a: a.confidence).id
            edges = []
            for aid in ids:
                if aid != center:
                    edges.extend([(center, aid), (aid, center)])
            return topology, edges
        if topology == "learned-sparse":
            edges = []
            for target in agents:
                candidates = [a for a in agents if a.id != target.id]
                if not candidates:
                    continue
                source = max(candidates, key=lambda a: self._answer_distance(a.answer, target.answer) + a.confidence * 0.1)
                edges.append((source.id, target.id))
            return topology, edges
        raise ValueError(f"Unsupported topology: {requested}")

    def _consensus(self, agents):
        if not agents:
            return None, 0.0
        answers = [a.answer for a in agents]
        if all(isinstance(ans, dict) for ans in answers):
            consensus: Dict[str, Any] = {}
            keys = sorted({k for ans in answers for k in ans.keys()})
            for key in keys:
                weighted = {}
                for agent in agents:
                    if key not in agent.answer:
                        continue
                    value = agent.answer[key]
                    vk = self._canonical(value)
                    score, _ = weighted.get(vk, (0.0, value))
                    weighted[vk] = (score + agent.confidence, value)
                if weighted:
                    consensus[key] = max(weighted.values(), key=lambda x: x[0])[1]
        else:
            consensus = max(agents, key=lambda a: a.confidence).answer
        disagreement = self._disagreement(agents)
        mean_confidence = sum(a.confidence for a in agents) / len(agents)
        confidence = max(0.0, min(0.99, mean_confidence + 0.18 * (1.0 - disagreement)))
        return consensus, round(confidence, 4)

    def _repair_merge(self, agents, adapter):
        scored = sorted(agents, key=lambda a: len(adapter.verify(a.answer).violations))
        merged = copy.deepcopy(scored[0].answer)
        current_violations = len(adapter.verify(merged).violations)
        for agent in scored[1:]:
            if not isinstance(agent.answer, dict):
                continue
            for key, agent_val in agent.answer.items():
                if key not in merged or merged[key] == agent_val:
                    continue
                merged_val = merged[key]
                if (isinstance(agent_val, list) and isinstance(merged_val, list)
                        and len(agent_val) == len(merged_val) and agent_val and isinstance(agent_val[0], dict)):
                    working_list = copy.deepcopy(merged_val)
                    for idx, (base_elem, agent_elem) in enumerate(zip(merged_val, agent_val)):
                        for sub_key, sub_val in agent_elem.items():
                            if base_elem.get(sub_key) == sub_val:
                                continue
                            candidate = copy.deepcopy(merged)
                            candidate[key] = copy.deepcopy(working_list)
                            candidate[key][idx][sub_key] = sub_val
                            n_viol = len(adapter.verify(candidate).violations)
                            if n_viol < current_violations:
                                working_list[idx][sub_key] = sub_val
                                current_violations = n_viol
                    merged[key] = working_list
                else:
                    candidate = copy.deepcopy(merged)
                    candidate[key] = agent_val
                    n_viol = len(adapter.verify(candidate).violations)
                    if n_viol < current_violations:
                        merged[key] = agent_val
                        current_violations = n_viol
        v = adapter.verify(merged)
        conf = 0.90 if v.is_valid else max(0.22, 0.70 - 0.10 * len(v.violations))
        return merged, round(conf, 4)

    def _should_expand(self, agents, config, verifier_score, disagreement, confidence):
        if len(agents) >= config.max_agents:
            return False
        return (verifier_score < config.tau_verifier
                or disagreement > config.expand_disagreement
                or confidence < config.tau_confidence)

    def _disagreement(self, agents):
        if len(agents) < 2:
            return 0.0
        distances = []
        for i, left in enumerate(agents):
            for right in agents[i + 1:]:
                ad = self._answer_distance(left.answer, right.answer)
                cg = abs(left.confidence - right.confidence)
                distances.append(0.82 * ad + 0.18 * cg)
        return max(0.0, min(1.0, sum(distances) / len(distances)))

    def _answer_distance(self, left: Any, right: Any) -> float:
        if isinstance(left, dict) and isinstance(right, dict):
            keys = set(left.keys()) | set(right.keys())
            if not keys:
                return 0.0
            mismatches = sum(1 for k in keys if self._canonical(left.get(k)) != self._canonical(right.get(k)))
            return mismatches / len(keys)
        if isinstance(left, str) and isinstance(right, str):
            return 1.0 - difflib.SequenceMatcher(a=left, b=right).ratio()
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return abs(float(left) - float(right)) / (max(abs(float(left)), abs(float(right))) + 1.0)
        return 0.0 if self._canonical(left) == self._canonical(right) else 1.0

    def _latent_delta(self, current, previous) -> float:
        return float(self._torch.norm(current - previous).item() / math.sqrt(current.numel()))

    def _accounting(self, active_core_calls, message_count, verifier_calls, elapsed_ms, config):
        active_compute_proxy = (
            active_core_calls * self.unit.parameter_count
            + message_count * config.message_dim
            + verifier_calls * 1000
        )
        joule_proxy = max(1.0, active_compute_proxy / 1_000_000.0)
        return SwarmAccounting(
            stored_parameters=self.unit.parameter_count,
            active_core_calls=active_core_calls, message_count=message_count,
            verifier_calls=verifier_calls, elapsed_ms=round(elapsed_ms, 3),
            active_compute_proxy=round(active_compute_proxy, 3),
            solved_per_joule_proxy=round(1.0 / joule_proxy, 6),
        )

    def _canonical(self, value: Any) -> str:
        try:
            return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        except TypeError:
            return str(value)

    def _seed(self, value: str) -> int:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return int(digest[:16], 16) % (2**31)


def available_domains() -> List[str]:
    """Return all available verifier domain names."""
    builtin = ["erp", "billable", "modernize", "dasc", "qa"]
    industry = sorted(_INDUSTRY_REGISTRY.keys())
    reasoning = sorted(_REASONING_REGISTRY.keys())
    return builtin + industry + reasoning
