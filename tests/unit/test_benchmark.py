from __future__ import annotations

import json
from pathlib import Path

import pytest

from codex_smart_route.benchmark import (
    BenchmarkError,
    build_report,
    check_acceptance,
    load_manifest,
    main,
    manifest_digest,
    validate_manifest,
    validate_report,
)


def test_public_manifest_is_versioned_bilingual_and_covers_task_classes():
    manifest = load_manifest()
    classes = {task["class"] for task in manifest["tasks"]}
    assert manifest["schema_version"] == "1.0"
    assert {task["language"] for task in manifest["tasks"]} == {"en", "it"}
    assert classes == {
        "mechanical_edit",
        "standard_implementation",
        "multi_file_refactor",
        "debugging_investigation",
        "architecture_reasoning",
        "tool_heavy",
        "image_capable",
    }
    assert len(manifest_digest(manifest)) == 64


def test_manifest_rejects_duplicate_and_missing_coverage():
    manifest = load_manifest()
    manifest["tasks"] = [manifest["tasks"][0], manifest["tasks"][0]]
    with pytest.raises(BenchmarkError, match="duplicate"):
        validate_manifest(manifest)


@pytest.mark.parametrize(
    ("operator", "expected", "answer"),
    [
        ("equals", "ok", " ok\n"),
        ("contains_all", ["alpha", "beta"], "beta then alpha"),
        ("json_subset", {"a": {"b": 2}}, '{"a":{"b":2,"c":3}}'),
    ],
)
def test_machine_checkable_acceptance(operator, expected, answer):
    task = {"acceptance": {"operator": operator, "expected": expected}}
    assert check_acceptance(task, answer) is True


def test_mock_report_has_auto_two_baselines_whole_run_and_calibration():
    report = build_report(
        load_manifest(),
        ["auto", "fast@low", "capable@high"],
        mode="mock",
        policy_version="balanced@1",
        priors_version="families@1",
    )
    assert report["variants"] == ["auto", "fast@low", "capable@high"]
    assert all(run["accepted"] for run in report["runs"])
    assert all(
        {"execution", "verification"} <= {item["kind"] for item in run["attempts"]}
        for run in report["runs"]
    )
    assert all(
        "classification" in {item["kind"] for item in run["attempts"]}
        for run in report["runs"]
        if run["variant"] == "auto"
    )
    assert {run["usage"]["input_tokens"] for run in report["runs"]} == {10, 15}
    assert report["summary"]["accepted"] == len(report["runs"])
    assert report["summary"]["total_usage"]["request_count"] == len(report["runs"])
    assert report["calibration"]["interpretation"].endswith("not_probability")
    assert report["configuration_effect"].startswith("none;")


def test_baseline_marks_accepted_to_failed_as_regression():
    manifest = load_manifest()
    baseline = build_report(manifest, ["auto", "a", "b"], mode="mock")
    baseline["runs"][0]["accepted"] = True

    def rejected(_task, _variant):
        return {
            "status": "completed",
            "answer": "wrong",
            "profile_evidence": {
                "requested": _variant,
                "selected": _variant,
                "forwarded": _variant,
            },
            "attempts": [
                {"kind": "classification", "status": "completed"},
                {"kind": "execution", "status": "completed"},
                {"kind": "verification", "status": "completed"},
            ],
        }

    from codex_smart_route import benchmark

    original = benchmark._mock_outcome
    benchmark._mock_outcome = rejected
    try:
        current = build_report(manifest, ["auto", "a", "b"], mode="mock", baseline=baseline)
    finally:
        benchmark._mock_outcome = original
    assert current["runs"][0]["regression"] is True


def test_report_rejects_prompts_and_monetary_estimates():
    report = build_report(load_manifest(), ["auto", "a", "b"], mode="mock")
    report["prompt"] = "private"
    with pytest.raises(BenchmarkError, match="unsafe"):
        validate_report(report)
    report.pop("prompt")
    report["estimated_cost_usd"] = 1
    with pytest.raises(BenchmarkError, match="monetary"):
        validate_report(report)
    report.pop("estimated_cost_usd")
    report["variants"][1] = "C:\\private\\model"
    with pytest.raises(BenchmarkError, match="private-path"):
        validate_report(report)


def test_bundled_result_schema_covers_generated_root_fields():
    from importlib.resources import files

    report = build_report(load_manifest(), ["auto", "a", "b"], mode="mock")
    schema = json.loads(
        files("codex_smart_route.benchmark_data")
        .joinpath("result.v1.schema.json")
        .read_text(encoding="utf-8")
    )
    assert set(report) == set(schema["properties"])
    assert set(schema["required"]) == set(report)


def test_mock_cli_writes_valid_report(tmp_path: Path):
    output = tmp_path / "result.json"
    assert main(["mock", "--report", str(output), "--profile", "a", "--profile", "b"]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    validate_report(report)
    assert "prompt" not in output.read_text(encoding="utf-8")


def test_live_cli_requires_double_consent_and_two_profiles(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("SMART_ROUTE_BENCHMARK_LIVE", "1")
    args = [
        "live",
        "--report",
        str(tmp_path / "live.json"),
        "--profile",
        "a",
        "--profile",
        "b",
        "--runner-command-json",
        '["never-run"]',
    ]
    assert main(args) == 2
    assert "--consent-live" in capsys.readouterr().err
    assert main(["mock", "--report", str(tmp_path / "x"), "--profile", "a"]) == 1


def test_live_runner_protocol_is_provider_neutral_and_strips_answer(tmp_path: Path, monkeypatch):
    manifest = load_manifest()
    manifest["tasks"] = manifest["tasks"][:1]
    # Retain required classes only for this focused runner test after initial validation.
    monkeypatch.setattr("codex_smart_route.benchmark.validate_manifest", lambda _value: None)
    seen = []

    def runner(command, request, timeout):
        seen.append((command, request, timeout))
        return {
            "status": "completed",
            "answer": "alpha\nbeta",
            "profile_evidence": {
                "requested": request["variant"],
                "selected": "selected@medium",
                "forwarded": "selected@medium",
                "confirmed": "unverified",
            },
            "attempts": [
                {"kind": "classification", "status": "completed", "latency_ms": 1},
                {
                    "kind": "execution",
                    "status": "completed",
                    "latency_ms": 5,
                    "usage": {"input_tokens": 3, "output_tokens": 2},
                },
                {"kind": "verification", "status": "completed", "latency_ms": 1},
            ],
        }

    monkeypatch.setattr("codex_smart_route.benchmark._runner_outcome", runner)
    report = build_report(
        manifest,
        ["auto", "vendor-a@low", "vendor-b@high"],
        mode="live",
        runner_command=["custom-runner"],
    )
    assert len(seen) == 3
    assert seen[0][1]["protocol_version"] == "1.0"
    assert seen[0][1]["variant"] == "auto"
    assert report["runs"][0]["profile_evidence"]["requested"] == "auto"
    serialized = json.dumps(report)
    assert "alpha\\nbeta" not in serialized
    assert "custom-runner" not in serialized
