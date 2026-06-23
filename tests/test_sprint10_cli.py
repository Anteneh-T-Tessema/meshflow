from __future__ import annotations

import argparse
import asyncio
import sys

import pytest


def test_top_level_exports_workflow_event_bus():
    import meshflow
    from meshflow.core.events import WorkflowEventBus

    assert meshflow.WorkflowEventBus is WorkflowEventBus
    assert "WorkflowEventBus" in meshflow.__all__


def test_watch_parser_accepts_run_id_without_yaml(monkeypatch):
    from meshflow.cli import main as cli

    seen: dict[str, object] = {}

    def fake_watch(args: argparse.Namespace) -> None:
        seen["run_id"] = args.run_id
        seen["timeout"] = args.timeout

    monkeypatch.setattr(cli, "_cmd_watch", fake_watch)
    monkeypatch.setattr(sys, "argv", ["meshflow", "watch", "run-123", "--timeout", "0.01"])

    cli.main()

    assert seen == {"run_id": "run-123", "timeout": 0.01}


def test_resume_parser_accepts_run_id_without_yaml(monkeypatch):
    from meshflow.cli import main as cli

    seen: dict[str, object] = {}

    def fake_resume(args: argparse.Namespace) -> None:
        seen["run_id"] = args.run_id
        seen["yaml"] = args.yaml
        seen["reject"] = args.reject

    monkeypatch.setattr(cli, "_cmd_resume", fake_resume)
    monkeypatch.setattr(sys, "argv", ["meshflow", "resume", "run-123"])

    cli.main()

    assert seen == {"run_id": "run-123", "yaml": "", "reject": False}


def test_watch_tails_replayed_live_events(monkeypatch, capsys):
    from meshflow.cli.main import _async_watch
    from meshflow.core.events import EventKind, WorkflowEvent, WorkflowEventBus
    import meshflow.core.events as events_mod

    bus = WorkflowEventBus()
    run_id = "watch-run"
    asyncio.run(
        bus.emit(
            WorkflowEvent(
                kind=EventKind.STEP_START,
                run_id=run_id,
                node_id="planner",
                data={"kind": "python"},
            )
        )
    )
    asyncio.run(
        bus.emit(
            WorkflowEvent(
                kind=EventKind.WORKFLOW_COMPLETE,
                run_id=run_id,
                data={"completed": True},
            )
        )
    )
    monkeypatch.setattr(events_mod, "global_event_bus", bus)

    args = argparse.Namespace(run_id=run_id, db=":memory:", sse=False, timeout=0.1)
    asyncio.run(_async_watch(args))

    out = capsys.readouterr().out
    assert "[step_start] run_id=watch-run node=planner" in out
    assert "[workflow_complete] run_id=watch-run" in out


def test_watch_tails_ledger_records_when_no_in_process_events(tmp_path, capsys):
    from meshflow.cli.main import _async_watch
    from meshflow.core.ledger import ReplayLedger
    from meshflow.core.runtime import StepRecord

    db_path = tmp_path / "runs.db"
    run_id = "ledger-watch-run"
    ledger = ReplayLedger(str(db_path))
    asyncio.run(
        ledger.write(
            StepRecord(
                run_id=run_id,
                step_id="step-1",
                node_id="planner",
                node_kind="python",
                input_task="task",
                output_content="done",
                verdict="commit",
                blocked=False,
                block_reason="",
                uncertainty=0.1,
                cost_usd=0.0,
                tokens_used=3,
                carbon_gco2=0.0,
                duration_ms=1.0,
                timestamp="2026-05-22T00:00:00+00:00",
            )
        )
    )

    args = argparse.Namespace(run_id=run_id, db=str(db_path), sse=False, timeout=0.1)
    asyncio.run(_async_watch(args))

    out = capsys.readouterr().out
    assert "[step_complete] run_id=ledger-watch-run node=planner" in out


def test_resume_uses_checkpoint_yaml_path(tmp_path, capsys):
    from meshflow.cli.main import _async_resume
    from meshflow.core.ledger import ReplayLedger

    workflow_yaml = tmp_path / "workflow.yaml"
    workflow_yaml.write_text(
        """
name: sprint10_resume
policy:
  mode: dev
  enable_guardian: false
  enable_uncertainty: false
  enable_collusion_audit: false
  human_approval_tier: none
nodes:
  gate:
    kind: human
edges: []
""".lstrip()
    )

    db_path = tmp_path / "runs.db"
    ledger = ReplayLedger(str(db_path))
    run_id = "resume-run"
    asyncio.run(
        ledger.save_checkpoint(
            run_id,
            {
                "run_id": run_id,
                "workflow_name": "sprint10_resume",
                "task": "approve release",
                "workflow_yaml": str(workflow_yaml),
                "paused_at_node": "gate",
                "context": {"task": "approve release"},
                "completed_nodes": [],
                "skipped_nodes": [],
                "node_outputs": {},
            },
        )
    )

    args = argparse.Namespace(
        run_id=run_id,
        yaml="",
        db=str(db_path),
        reject=False,
        comment="ship it",
        decided_by="tester",
    )

    asyncio.run(_async_resume(args))

    assert asyncio.run(ledger.load_checkpoint_data(run_id)) is None
    out = capsys.readouterr().out
    assert "Resume approved: run_id=resume-run" in out
    assert "Status   : COMPLETED" in out


def test_resume_reports_missing_yaml_for_legacy_checkpoint(tmp_path, capsys):
    from meshflow.cli.main import _async_resume
    from meshflow.core.ledger import ReplayLedger

    db_path = tmp_path / "runs.db"
    ledger = ReplayLedger(str(db_path))
    asyncio.run(
        ledger.save_checkpoint(
            "legacy-run",
            {
                "run_id": "legacy-run",
                "workflow_name": "legacy",
                "task": "task",
                "paused_at_node": "gate",
                "context": {},
                "completed_nodes": [],
                "skipped_nodes": [],
                "node_outputs": {},
            },
        )
    )

    args = argparse.Namespace(
        run_id="legacy-run",
        yaml="",
        db=str(db_path),
        reject=False,
        comment="",
        decided_by="tester",
    )

    with pytest.raises(SystemExit) as exc:
        asyncio.run(_async_resume(args))

    assert exc.value.code == 1
    assert (
        "Checkpoint does not include the original workflow YAML path"
        in capsys.readouterr().out
    )


def test_playground_cli_parser():
    from meshflow.cli.main import build_parser
    parser = build_parser()
    args = parser.parse_args(["playground", "--model", "gpt-4o", "--mode", "standard", "--budget", "5.0", "--agents", "3", "--db", "test.db"])
    assert args.model == "gpt-4o"
    assert args.mode == "standard"
    assert args.budget == 5.0
    assert args.agents == 3
    assert args.db == "test.db"


def test_playground_cli_execution(monkeypatch, capsys):
    from meshflow.cli import main as cli

    # Mock input to run all slash commands, tasks, and then quit
    inputs = [
        "/help",
        "/ledger",
        "/skills",
        "/skills optimize SQL queries",
        "/guardian",
        "/cost",
        "/mode standard",
        "/model gpt-4o-mini",
        "/agents 3",
        "/history",
        "/clear",
        "/scan hello",
        "test query task",
        "/skills",   # should detect skills for the last task
        "/history",  # should show the task in history
        "/guardian", # should show status of guardian alerts
        "/quit"
    ]
    def mock_input(*args, **kwargs):
        if not inputs:
            raise KeyboardInterrupt()
        return inputs.pop(0)

    monkeypatch.setattr("builtins.input", mock_input)
    monkeypatch.setattr(sys, "argv", ["meshflow", "playground", "--mode", "sandbox"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "MeshFlow Playground" in out
    assert "Playground Commands" in out
    assert "No ledger entries yet." in out
    assert "Detected skills" in out
    assert "sql" in out
    assert "Cost summary" in out
    assert "standard" in out
    assert "gpt-4o-mini" in out
    assert "planner" in out
    assert "executor" in out
    assert "critic" in out
    assert "Injection scan" in out
    assert "Running" in out
    assert "Session summary" in out




