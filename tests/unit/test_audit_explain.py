from __future__ import annotations

import json

import pytest

from codex_smart_route.audit import (
    AUDIT_SCHEMA_VERSION,
    AuditLogger,
    decision_event,
    format_explanation,
    latest_event,
    redact,
)
from codex_smart_route.cli import main
from codex_smart_route.config import resolve_config
from codex_smart_route.models import CandidateScore, Decision, TaskContext
from codex_smart_route.service import RoutingService


def _decision() -> Decision:
    return Decision(
        "selected",
        "winner",
        "high",
        "winner@high",
        "codex-smart-route",
        requested_reasoning_effort=None,
        forwarded_model="winner",
        forwarded_reasoning_effort="high",
        candidates=(
            CandidateScore(
                "winner@high",
                0.91,
                0.88,
                0.24,
                {
                    "consumption": 0.4,
                    "quality_gap": 0.09,
                    "latency": 0.3,
                    "switch_cost": 0.0,
                },
                ("app-server", "local-rules"),
                True,
            ),
            CandidateScore(
                "alternative@medium",
                0.84,
                0.82,
                0.31,
                {
                    "consumption": 0.3,
                    "quality_gap": 0.16,
                    "latency": 0.2,
                    "switch_cost": 0.0,
                },
                ("app-server", "local-rules"),
                True,
            ),
        ),
        excluded={"blocked@low": ("model-blocked",)},
        hard_gates=("model-blocked",),
        reason="lowest normalized weighted penalty",
        confidence=0.88,
        verified=True,
        policy_name="balanced",
        policy_version="2",
        previous_profile="previous@medium",
        routing_evidence={
            "evaluator": {"source": "local-rules", "version": "1"},
            "catalog": {
                "capability_versions": [{"model": "winner", "version": "2026-09"}],
                "prior_versions": [{"model": "winner", "version": "1"}],
                "card_version": "abc123",
            },
            "cache": {"status": "miss", "identity_prefix": "deadbeef"},
            "policy": {
                "name": "balanced",
                "version": "2",
                "minimum_quality": 0.7,
                "weights": {
                    "consumption": 0.5,
                    "quality_gap": 0.3,
                    "latency": 0.1,
                    "switch_cost": 0.1,
                },
            },
            "runtime": {
                "adapter": "codex-cli",
                "codex_smart_route": "0.1.0",
                "python": "3.13.0",
                "platform": "Windows",
            },
            "reevaluation_trigger": None,
        },
    )


def test_decision_event_contains_complete_versioned_evidence_without_task_text() -> None:
    task = TaskContext("private task text", session_id="secret-session", task_id="private-id")
    event = decision_event(task, _decision())
    serialized = json.dumps(event)

    assert event["schema_version"] == AUDIT_SCHEMA_VERSION
    assert event["models"]["selected"] == {
        "model": "winner",
        "reasoning_effort": "high",
        "profile": "winner@high",
    }
    assert event["models"]["requested"]["reasoning_effort"] == "unverified"
    assert event["models"]["forwarded"]["reasoning_effort"] == "high"
    assert event["models"]["confirmed"] == {
        "model": "unverified",
        "reasoning_effort": "unverified",
    }
    assert event["candidates"][0]["components"]["quality_gap"] == 0.09
    assert event["excluded"] == [{"profile": "blocked@low", "hard_gate_reasons": ["model-blocked"]}]
    assert event["evidence"]["catalog"]["card_version"] == "abc123"
    assert "private task text" not in serialized
    assert "secret-session" not in serialized
    assert "private-id" not in serialized


@pytest.mark.parametrize(
    "payload",
    [
        {"nested": [{"Authorization": "Bearer abcdefghijklmnop"}]},
        {"outer": {"argv": ["tool", "--password", "visible-looking-value"]}},
        {"outer": {"command_line": "safe-tool credential-value"}},
        {"credential": "apparently-safe"},
        {"private_key": "apparently-safe"},
        {"safe": "OPENAI_API_KEY=top-secret-value"},
        {"safe": "eyJabcdefghijk.abcdefghijklmnop.qrstuvwxyz12345"},
        {"safe": "-----BEGIN PRIVATE KEY-----\nmaterial"},
        {"deep": ({"cookie": "session=secret"},)},
    ],
)
def test_redaction_handles_nested_and_adversarial_secret_values(payload: object) -> None:
    serialized = json.dumps(redact(payload))
    assert "top-secret" not in serialized
    assert "visible-looking-value" not in serialized
    assert "credential-value" not in serialized
    assert "apparently-safe" not in serialized
    assert "abcdefghijklmnop" not in serialized
    assert "material" not in serialized
    assert "session=secret" not in serialized


def test_human_explanation_answers_winner_alternatives_exclusions_and_state() -> None:
    text = format_explanation(decision_event(TaskContext("safe"), _decision()))
    assert "Winner: winner@high" in text
    assert "effort=high" in text
    assert "alternative@medium" in text
    assert "blocked@low: model-blocked" in text
    assert "Cache: miss" in text
    assert "Hysteresis: not applied (previous=previous@medium)" in text
    assert "Policy: balanced v2" in text


def test_latest_event_ignores_interrupted_trailing_line(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text(
        '{"schema_version":"1.0","selected":"winner@high"}\n{"broken"', encoding="utf-8"
    )
    assert latest_event(path) == {
        "schema_version": "1.0",
        "selected": "winner@high",
    }


def test_explain_command_supports_human_and_json(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_SMART_ROUTE_HOME", str(tmp_path))
    runtime_home = resolve_config(
        working_directory=tmp_path,
        home=tmp_path,
        environ={"CODEX_SMART_ROUTE_HOME": str(tmp_path)},
    ).runtime_home
    AuditLogger(runtime_home / "audit.jsonl").decision(TaskContext("private"), _decision())

    assert main(["explain"]) == 0
    assert "Winner: winner@high" in capsys.readouterr().out
    assert main(["explain", "--json"]) == 0
    event = json.loads(capsys.readouterr().out)
    assert event["schema_version"] == AUDIT_SCHEMA_VERSION
    assert event["models"]["selected"]["reasoning_effort"] == "high"


def test_audit_rotation_enforces_backup_limit(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    logger = AuditLogger(path, max_bytes=1, backup_count=2)
    task = TaskContext("private")
    logger.decision(task, _decision())
    logger.decision(task, _decision())
    logger.decision(task, _decision())
    logger.decision(task, _decision())

    assert path.exists()
    assert path.with_name("audit.jsonl.1").exists()
    assert path.with_name("audit.jsonl.2").exists()
    assert not path.with_name("audit.jsonl.3").exists()


def test_service_records_cache_classifier_catalog_policy_and_runtime_evidence(
    config, catalog, tmp_path
) -> None:
    service = RoutingService(config, catalog, tmp_path)
    task = TaskContext("safe", session_id="s", task_id="t")
    initial = service.route(task)
    cached = service.route(TaskContext("safe", session_id="s", task_id="t", event="continuation"))

    evidence = initial.routing_evidence
    assert evidence["evaluator"] == {"source": "local-rules", "version": "1"}
    assert evidence["catalog"]["capability_versions"]
    assert evidence["catalog"]["prior_versions"]
    assert len(evidence["catalog"]["card_version"]) == 64
    assert evidence["cache"]["status"] == "not-eligible"
    assert len(evidence["cache"]["identity_prefix"]) == 16
    assert evidence["policy"]["minimum_quality"] == config.policy.minimum_quality
    assert evidence["policy"]["weights"]
    assert evidence["runtime"]["adapter"] == config.adapter
    assert cached.routing_evidence["cache"]["status"] == "hit"


def test_service_records_hysteresis_and_reevaluation_triggers(config, catalog, tmp_path) -> None:
    service = RoutingService(config, catalog, tmp_path)
    first = service.route(TaskContext("safe"))
    assert first.selected_profile is not None
    reevaluated = service.route(
        TaskContext(
            "safe",
            current_profile=first.selected_profile,
            force_reevaluation=True,
            significant_model_failure=True,
        )
    )
    assert reevaluated.previous_profile == first.selected_profile
    assert reevaluated.routing_evidence["cache"]["status"] == "bypassed"
    assert reevaluated.routing_evidence["reevaluation_trigger"] is None
    assert reevaluated.routing_evidence["reevaluation_triggers"] == [
        "significant-model-failure",
        "explicit",
    ]
