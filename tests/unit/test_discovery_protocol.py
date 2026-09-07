from __future__ import annotations

import io
import json
from typing import Any

import pytest

from codex_smart_route.config import parse_config
from codex_smart_route.discovery import (
    AppServerDiscovery,
    DiscoveryDiagnostics,
    DiscoveryError,
    apply_overrides,
    capabilities_from_app_server,
)
from codex_smart_route.doctor import run_doctor
from codex_smart_route.evaluation import evaluate_task
from codex_smart_route.models import TaskContext
from codex_smart_route.router import Router


def _row(model: str, **extra: Any) -> dict[str, Any]:
    return {
        "model": model,
        "displayName": model,
        "supportedReasoningEfforts": [{"reasoningEffort": "medium"}],
        **extra,
    }


class _FakeProcess:
    def __init__(self, messages: list[dict[str, Any]]):
        self.stdin = io.StringIO()
        self.stdout = iter(json.dumps(message) + "\n" for message in messages)
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: int) -> int:
        return 0

    def kill(self) -> None:
        self.terminated = True


def _discovery(monkeypatch: pytest.MonkeyPatch, messages: list[dict[str, Any]]):
    process = _FakeProcess(messages)
    monkeypatch.setattr("codex_smart_route.discovery.subprocess.Popen", lambda *a, **k: process)
    discovery = AppServerDiscovery(timeout=0.1)
    return discovery, process


def test_current_catalog_paginates_merges_duplicates_and_provider_evidence(monkeypatch):
    discovery, process = _discovery(
        monkeypatch,
        [
            {"id": 1, "result": {"userAgent": "codex-cli/1.2.3"}},
            {
                "id": 2,
                "result": {
                    "data": [
                        _row("z-model", inputModalities=["text"], supportsTools=False),
                        _row("Auto"),
                    ],
                    "nextCursor": "page-2",
                    "futureField": {"safe": "ignored"},
                },
            },
            {
                "id": 3,
                "result": {
                    "data": [
                        _row(
                            "z-model",
                            supportedReasoningEfforts=[{"reasoningEffort": "high"}],
                            contextWindow=200000,
                        ),
                        _row("a-model", inputModalities=["text", "image"]),
                    ],
                    "nextCursor": None,
                },
            },
            {
                "id": 4,
                "result": {
                    "supportsTools": True,
                    "supportsParallelTools": True,
                    "imageGeneration": False,
                    "namespaceTools": True,
                    "webSearch": True,
                    "authMode": "chatgpt",
                    "available": True,
                    "unknownFutureCapability": 7,
                },
            },
        ],
    )

    models = discovery.discover()

    assert [model.model for model in models] == ["a-model", "z-model"]
    z_model = models[1]
    assert z_model.reasoning_efforts == ("high", "medium")
    assert z_model.supports_tools is False  # model evidence outranks provider evidence
    assert z_model.supports_parallel_tools is True
    assert z_model.context_window == 200000
    assert z_model.auth_mode == "chatgpt"
    assert z_model.available is True
    assert z_model.provider_capabilities == (
        ("imageGeneration", False),
        ("namespaceTools", True),
        ("webSearch", True),
    )
    evidence = {field: (source, confidence) for field, source, confidence in z_model.field_evidence}
    assert evidence["supports_tools"] == ("codex-app-server:model/list", "verified")
    assert evidence["auth_mode"] == (
        "codex-app-server:modelProvider/capabilities/read",
        "reported",
    )
    assert discovery.last_diagnostics.to_dict() == {
        "protocol": "v2-model-list",
        "server_version": "codex-cli/1.2.3",
        "pages": 2,
        "provider_capabilities": "available",
        "partial": False,
        "warnings": [],
    }
    requests = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
    assert requests[2]["params"]["cursor"] == "page-2"


def test_legacy_catalog_keeps_missing_modalities_and_capabilities_unknown():
    models = capabilities_from_app_server(
        {
            "data": [
                {
                    "id": "legacy-model",
                    "display_name": "Legacy",
                    "supported_reasoning_efforts": ["low"],
                    "future": "ignored",
                }
            ]
        }
    )

    assert models[0].model == "legacy-model"
    assert models[0].input_modalities == ()
    assert models[0].supports_tools is None
    assert models[0].available is None
    assert "input_modalities" not in {item[0] for item in models[0].field_evidence}


@pytest.mark.parametrize("cursor", ["", 7, [], {}])
def test_malformed_pagination_cursor_is_actionable(monkeypatch, cursor):
    discovery, _ = _discovery(
        monkeypatch,
        [
            {"id": 1, "result": {}},
            {"id": 2, "result": {"data": [_row("model")], "nextCursor": cursor}},
        ],
    )

    with pytest.raises(DiscoveryError, match="malformed nextCursor"):
        discovery.discover()


def test_pagination_loop_is_rejected(monkeypatch):
    discovery, _ = _discovery(
        monkeypatch,
        [
            {"id": 1, "result": {}},
            {"id": 2, "result": {"data": [_row("one")], "nextCursor": "again"}},
            {"id": 3, "result": {"data": [_row("two")], "nextCursor": "again"}},
        ],
    )

    with pytest.raises(DiscoveryError, match="cursor loop"):
        discovery.discover()


def test_unavailable_provider_keeps_models_and_marks_partial(monkeypatch):
    discovery, _ = _discovery(
        monkeypatch,
        [
            {"id": 1, "result": {}},
            {"id": 2, "result": {"data": [_row("model")]}},
            {"id": 3, "error": {"code": -32601, "message": "Method not found"}},
        ],
    )

    models = discovery.discover()

    assert models[0].supports_tools is None
    assert discovery.last_diagnostics.partial is True
    assert discovery.last_diagnostics.provider_capabilities == "unavailable"
    assert "Method not found" in discovery.last_diagnostics.warnings[0]


def test_provider_does_not_overwrite_model_level_evidence():
    model = capabilities_from_app_server(
        {"data": [_row("model", supportsTools=False, authMode="api-key")]},
        {"supportsTools": True, "authMode": "chatgpt", "contextWindow": 1234},
    )[0]

    assert model.supports_tools is False
    assert model.auth_mode == "api-key"
    assert model.context_window == 1234


def test_namespace_tools_false_does_not_claim_general_tools_are_unsupported():
    model = capabilities_from_app_server({"data": [_row("model")]}, {"namespaceTools": False})[0]

    assert model.supports_tools is None
    assert model.provider_capabilities == (("namespaceTools", False),)


def test_user_override_replaces_field_evidence():
    original = capabilities_from_app_server({"data": [_row("model", supportsTools=True)]})
    config = parse_config({"capability_overrides": {"model": {"supports_tools": False}}})

    changed = apply_overrides(original, config)[0]

    evidence = {field: (source, confidence) for field, source, confidence in changed.field_evidence}
    assert changed.supports_tools is False
    assert evidence["supports_tools"] == ("user-config", "reported")


def test_changed_model_list_schema_is_actionable(monkeypatch):
    discovery, _ = _discovery(
        monkeypatch,
        [
            {"id": 1, "result": {}},
            {"id": 2, "result": {"models": [_row("future-shape")]}},
        ],
    )

    with pytest.raises(DiscoveryError, match="schema changed.*smart-route doctor"):
        discovery.discover()


def test_provider_timeout_keeps_models_and_marks_partial(monkeypatch):
    discovery, _ = _discovery(
        monkeypatch,
        [
            {"id": 1, "result": {}},
            {"id": 2, "result": {"data": [_row("model")]}},
        ],
    )

    assert discovery.discover()[0].model == "model"
    assert discovery.last_diagnostics.provider_capabilities == "unavailable"
    assert "timed out" in discovery.last_diagnostics.warnings[0]


def test_unknown_legacy_modality_obeys_unknown_capability_policy():
    capability = capabilities_from_app_server({"data": [_row("legacy", inputModalities=None)]})[0]
    task = TaskContext("text task")
    profiles = capability.profiles()
    signals = evaluate_task(task)

    allowed = Router(parse_config({})).route(task, profiles, signals)
    strict = Router(parse_config({"unknown_capabilities": "exclude-required"})).route(
        task, profiles, signals
    )

    assert allowed.status == "selected"
    assert strict.status == "blocked"
    assert "input-modality-unverified" in strict.hard_gates


def test_doctor_reports_protocol_and_partial_warning(monkeypatch, tmp_path):
    class FakeDiscovery:
        def __init__(self):
            self.last_diagnostics = DiscoveryDiagnostics(
                protocol="v2-model-list",
                pages=2,
                provider_capabilities="unavailable",
                partial=True,
                warnings=("provider unavailable",),
            )

        def discover(self):
            return capabilities_from_app_server({"data": [_row("model")]})

    monkeypatch.setattr("codex_smart_route.doctor.AppServerDiscovery", FakeDiscovery)
    monkeypatch.setattr("codex_smart_route.doctor.shutil.which", lambda _: "codex")
    monkeypatch.setattr("codex_smart_route.doctor._version", lambda _: "codex-cli 1.2.3")

    report = run_doctor(
        parse_config({}),
        codex_home=tmp_path / "codex",
        user_home=tmp_path / "home",
    )

    assert report["app_server_protocol"]["pages"] == 2
    assert report["app_server_protocol"]["partial"] is True
    assert "provider unavailable" in report["warnings"]
