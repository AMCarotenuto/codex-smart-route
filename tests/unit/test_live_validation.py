from __future__ import annotations

import json
from pathlib import Path

import pytest

from codex_smart_route.live_validation import (
    EventSummary,
    ExecutionEvidence,
    LiveValidationError,
    ProcessResult,
    main,
    run_app_server_suite,
    run_cli_suite,
    validate_report_safe,
)
from codex_smart_route.models import Decision


def decision(model: str, effort: str) -> Decision:
    return Decision("selected", model, effort, f"{model}@{effort}", "codex-smart-route")


def test_event_summary_detects_streaming_tool_round_trip_without_content():
    summary = EventSummary()
    summary.observe({"type": "thread.started", "thread_id": "thread-1"})
    summary.observe({"type": "item.started", "item": {"id": "item-1", "type": "command_execution"}})
    summary.observe(
        {"type": "item.completed", "item": {"id": "item-1", "type": "command_execution"}}
    )
    assert summary.streaming is True
    assert summary.tool_round_trip is True
    assert summary.thread_id == "thread-1"
    assert "content" not in summary.to_dict()


def test_evidence_keeps_selected_requested_forwarded_confirmed_distinct():
    evidence = ExecutionEvidence.from_decision("case", decision("real", "high"), EventSummary(), 0)
    assert evidence.selected_profile == "real@high"
    assert evidence.requested_model == "codex-smart-route"
    assert evidence.forwarded_model == "real"
    assert evidence.forwarded_reasoning_effort == "high"
    assert evidence.confirmed_model == "unverified"
    assert evidence.confirmed_reasoning_effort == "unverified"


def test_cli_suite_uses_read_only_ephemeral_jsonl_and_distinct_profiles():
    class Service:
        calls = 0

        def route(self, _task, dry_run=False):
            assert dry_run is True
            self.calls += 1
            if self.calls == 1:
                return decision("fast", "low")
            return decision("deep", "high")

    commands = []

    def runner(command, *, timeout, cancel_after_first_event=False):
        commands.append(command)
        summary = EventSummary()
        summary.observe({"type": "thread.started", "thread_id": "t"})
        summary.observe({"type": "item.started", "item": {"id": "x", "type": "command_execution"}})
        summary.observe(
            {"type": "item.completed", "item": {"id": "x", "type": "command_execution"}}
        )
        code = -15 if cancel_after_first_event else 0
        return ProcessResult(code, summary, cancel_after_first_event)

    result = run_cli_suite(Service(), "codex", Path("."), 10, runner)  # type: ignore[arg-type]
    assert len(result) == 3
    assert {item.selected_profile for item in result[:2]} == {"fast@low", "deep@high"}
    assert all("--ephemeral" in command and "read-only" in command for command in commands)
    assert result[2].cancelled is True


def test_cli_suite_rejects_one_profile():
    class Service:
        def route(self, _task, dry_run=False):
            return decision("same", "medium")

    def runner(_command, **_kwargs):
        summary = EventSummary(
            event_count=2, streaming=True, tool_started=True, tool_completed=True
        )
        return ProcessResult(0, summary)

    with pytest.raises(LiveValidationError, match="selected one profile"):
        run_cli_suite(Service(), "codex", Path("."), 10, runner)  # type: ignore[arg-type]


def test_app_server_suite_forwards_auto_continues_compacts_and_cancels(monkeypatch):
    class Service:
        def route(self, _task, dry_run=False):
            assert dry_run is True
            return decision("real", "high")

    class Session:
        def __init__(self, _executable, _timeout):
            self.summary = EventSummary(event_count=2, streaming=True)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def send(self, method, params):
            if method == "thread/start":
                assert "model" not in params
                return {"result": {"thread": {"id": "thread-1"}}}
            if method == "turn/start":
                assert params["model"] == "real"
                assert params["effort"] == "high"
                return {"result": {"turn": {"id": "turn-1"}}}
            return {"result": {}}

        def wait_for(self, methods):
            return {"method": sorted(methods)[0]}

    monkeypatch.setattr("codex_smart_route.live_validation.AppServerSession", Session)
    result = run_app_server_suite(Service(), "codex", Path("."), 10)  # type: ignore[arg-type]
    assert result["status"] == "passed"
    assert result["continuation"] == "passed"
    assert result["compaction"] == "passed"
    assert result["cancellation"] == "passed"
    assert result["evidence"]["requested_model"] == "codex-smart-route"
    assert result["evidence"]["forwarded_model"] == "real"
    assert result["evidence"]["confirmed_model"] == "unverified"


def test_report_schema_rejects_secrets_and_raw_output():
    with pytest.raises(LiveValidationError, match="credential-like"):
        validate_report_safe({"value": "sk-abcdefgh12345678"})
    with pytest.raises(LiveValidationError, match="sensitive field"):
        validate_report_safe({"output": "harmless"})


def test_main_refuses_without_double_consent(monkeypatch, capsys):
    monkeypatch.setenv("SMART_ROUTE_LIVE", "1")
    assert main([]) == 2
    assert "--consent-live" in capsys.readouterr().err


def test_safe_report_serializes_without_task_or_output():
    report = {
        "cli": [
            {
                "selected_profile": "m@low",
                "requested_model": "codex-smart-route",
                "forwarded_model": "m",
                "confirmed_model": "unverified",
            }
        ]
    }
    validate_report_safe(report)
    value = json.dumps(report)
    assert "task" not in value and "prompt" not in value
