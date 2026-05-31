# MeshFlow Coding & Development Guide

This guide outlines common development commands, styling rules, and key architectural guidelines for developers and AI agents working on the MeshFlow codebase.

---

## 1. Development Commands

### Running Tests
- **Run all unit tests**: `.venv/bin/pytest`
- **Run specific tests**: `.venv/bin/pytest tests/test_sprint79.py`
- **Run with verbose output**: `.venv/bin/pytest -v`

### Code Quality (Linting & Formatting)
- **Run Ruff linter**: `.venv/bin/ruff check .`
- **Auto-format code**: `.venv/bin/ruff format .`
- **Run MyPy type checker**: `.venv/bin/mypy .`

### Running the Trace Studio
- **Start Trace Studio locally**: `.venv/bin/meshflow studio --port 7788`

### Benchmarking
- **Run micro-benchmarks**: `python benchmarks/bench_core.py --quick`

---

## 2. Key Architecture Rules

- **The StepRuntime Kernel**: All agent steps — regardless of execution framework (LangGraph, CrewAI, AutoGen, native) — must pass through `StepRuntime.run()` or `GovernedStepExecutor.execute()` to enforce governance policies (DascGate, PII blockers, budget trackers, and ledger logging).
- **tamper-Evident Ledger**: Every committed agent execution must write a `StepRecord` to the `ReplayLedger`. The record must contain `prev_hash` and `entry_hash` to maintain the cryptographic hash chain.
- **Synchronous Entry Points**: High-level developer APIs (like `Workflow.run()`) must remain synchronous to keep the prototyping interface frictionless. Use `meshflow.integrations._utils.run_sync` to call underlying async orchestrators.
- **Zero-Dependency Mocking**: Demos, sandbox runs, and local tests must execute offline using `SandboxProvider` or `EchoProvider` without requiring real API keys or external network dependencies.

---

## 3. Code Style & Conventions

- **Type Annotations**: Enforce strict type hints on all public functions and classes. Validate types using MyPy.
- **Imports**: Place future imports (`from __future__ import annotations`) at the top of new modules.
- **Aesthetics & UI**: Ensure Trace Studio or web interfaces are built with high-fidelity, premium dark-mode styling (harmonious colors, Outfit/Inter fonts, CSS variables, and clean micro-animations). Avoid raw or generic layouts.
