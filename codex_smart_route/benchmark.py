"""Provider-neutral, reproducible routing benchmark.

Offline mode is deterministic. Live mode starts an external runner only after double consent;
this module never imports a provider SDK or writes benchmark results into router configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from importlib.resources import files
from pathlib import Path
from typing import Any

from .audit import SECRET_VALUE

BENCHMARK_PROTOCOL_VERSION = "1.0"
RESULT_SCHEMA_VERSION = "1.0"
LIVE_ENV = "SMART_ROUTE_BENCHMARK_LIVE"
AUTO_VARIANT = "auto"
USAGE_SIGNALS = (
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "request_count",
    "tool_calls",
)
FORBIDDEN_RESULT_KEYS = frozenset(
    {
        "command",
        "cwd",
        "environment",
        "log",
        "logs",
        "output",
        "path",
        "prompt",
        "repository",
        "stderr",
        "stdin",
        "stdout",
        "task",
    }
)
PRIVATE_PATH = re.compile(
    r"(?i)(?:[a-z]:\\|\\\\[^\\]+\\|/(?:home|users|workspace|repo|private|tmp)/)[^\s]+"
)


class BenchmarkError(RuntimeError):
    """Invalid benchmark input or unsafe execution request."""


def _data_path(name: str) -> Path:
    return Path(str(files("codex_smart_route.benchmark_data").joinpath(name)))


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    source = path or _data_path("manifest.v1.json")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"invalid benchmark manifest: {type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise BenchmarkError("benchmark manifest must be an object")
    validate_manifest(value)
    return value


def _non_empty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkError(f"{label} must be a non-empty string")
    return value


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    if manifest.get("schema_version") != "1.0":
        raise BenchmarkError("unsupported benchmark manifest schema_version")
    _non_empty_string(manifest.get("suite_id"), "suite_id")
    _non_empty_string(manifest.get("suite_version"), "suite_version")
    tasks = manifest.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise BenchmarkError("benchmark manifest requires tasks")
    seen: set[str] = set()
    classes: set[str] = set()
    languages: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise BenchmarkError("each benchmark task must be an object")
        task_id = _non_empty_string(task.get("id"), "task id")
        if task_id in seen:
            raise BenchmarkError(f"duplicate benchmark task: {task_id}")
        seen.add(task_id)
        classes.add(_non_empty_string(task.get("class"), f"{task_id}.class"))
        languages.add(_non_empty_string(task.get("language"), f"{task_id}.language"))
        _non_empty_string(task.get("prompt"), f"{task_id}.prompt")
        acceptance = task.get("acceptance")
        if not isinstance(acceptance, dict) or acceptance.get("operator") not in {
            "equals",
            "contains_all",
            "json_subset",
        }:
            raise BenchmarkError(f"{task_id}.acceptance has unsupported operator")
        if "expected" not in acceptance:
            raise BenchmarkError(f"{task_id}.acceptance.expected is required")
    required = {
        "mechanical_edit",
        "standard_implementation",
        "multi_file_refactor",
        "debugging_investigation",
        "architecture_reasoning",
        "tool_heavy",
        "image_capable",
    }
    if not required <= classes:
        raise BenchmarkError(f"benchmark task classes missing: {sorted(required - classes)}")
    if not {"en", "it"} <= languages:
        raise BenchmarkError("benchmark must include English and Italian prompts")


def manifest_digest(manifest: Mapping[str, Any]) -> str:
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _contains_subset(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _contains_subset(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(
                _contains_subset(left, right) for left, right in zip(actual, expected, strict=True)
            )
        )
    return bool(actual == expected)


def check_acceptance(task: Mapping[str, Any], answer: Any) -> bool:
    acceptance = task["acceptance"]
    operator = acceptance["operator"]
    expected = acceptance["expected"]
    if operator == "equals":
        return isinstance(answer, str) and answer.strip() == str(expected).strip()
    if operator == "contains_all":
        return isinstance(answer, str) and all(str(item) in answer for item in expected)
    if operator == "json_subset":
        if isinstance(answer, str):
            try:
                answer = json.loads(answer)
            except json.JSONDecodeError:
                return False
        return _contains_subset(answer, expected)
    raise BenchmarkError(f"unsupported acceptance operator: {operator}")


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise BenchmarkError(f"{label} must be a non-negative number")
    return float(value)


def _normalize_attempt(attempt: Mapping[str, Any], index: int) -> dict[str, Any]:
    kind = _non_empty_string(attempt.get("kind"), f"attempt[{index}].kind")
    if kind not in {"classification", "execution", "correction", "verification"}:
        raise BenchmarkError(f"attempt[{index}].kind is unsupported")
    usage = attempt.get("usage", {})
    if not isinstance(usage, dict):
        raise BenchmarkError(f"attempt[{index}].usage must be an object")
    unknown = set(usage) - set(USAGE_SIGNALS)
    if unknown:
        raise BenchmarkError(f"attempt[{index}].usage has unknown signals: {sorted(unknown)}")
    return {
        "kind": kind,
        "status": _non_empty_string(attempt.get("status"), f"attempt[{index}].status"),
        "latency_ms": round(_number(attempt.get("latency_ms", 0), "latency_ms"), 3),
        "usage": {key: int(_number(value, key)) for key, value in usage.items()},
    }


def _aggregate_attempts(attempts: Sequence[Mapping[str, Any]]) -> tuple[dict[str, int], float]:
    usage = {key: 0 for key in USAGE_SIGNALS}
    latency = 0.0
    for attempt in attempts:
        latency += float(attempt["latency_ms"])
        for key, value in attempt["usage"].items():
            usage[key] += int(value)
    return usage, round(latency, 3)


def _normalize_outcome(
    task: Mapping[str, Any],
    variant: str,
    raw: Mapping[str, Any],
    measured_ms: float,
) -> dict[str, Any]:
    status = _non_empty_string(raw.get("status", "completed"), "status")
    if status not in {"completed", "failed", "unsupported"}:
        raise BenchmarkError("runner status must be completed, failed, or unsupported")
    attempts_raw = raw.get("attempts", [])
    if not isinstance(attempts_raw, list):
        raise BenchmarkError("runner attempts must be an array")
    attempts = [
        _normalize_attempt(item, index)
        for index, item in enumerate(attempts_raw)
        if isinstance(item, dict)
    ]
    if len(attempts) != len(attempts_raw):
        raise BenchmarkError("runner attempt must be an object")
    kinds = {item["kind"] for item in attempts}
    if status != "unsupported" and not {"execution", "verification"} <= kinds:
        raise BenchmarkError("completed/failed run requires execution and verification attempts")
    if variant == AUTO_VARIANT and status != "unsupported" and "classification" not in kinds:
        raise BenchmarkError("Auto run requires a classification attempt")
    usage, reported_latency = _aggregate_attempts(attempts)
    accepted = check_acceptance(task, raw.get("answer")) if status == "completed" else None
    retry_count = sum(item["kind"] in {"correction", "execution"} for item in attempts) - 1
    retry_count = max(0, retry_count)
    correction_count = sum(item["kind"] == "correction" for item in attempts)
    suitability = raw.get("suitability_score")
    if suitability is not None:
        suitability = _number(suitability, "suitability_score")
        if suitability > 1:
            raise BenchmarkError("suitability_score must be at most 1")
    prior = raw.get("prior")
    if prior is not None:
        prior = _non_empty_string(prior, "prior")
    evidence_raw = raw.get("profile_evidence")
    if not isinstance(evidence_raw, dict):
        raise BenchmarkError("runner profile_evidence must be an object")
    requested = _non_empty_string(evidence_raw.get("requested"), "profile_evidence.requested")
    if requested != variant:
        raise BenchmarkError("runner requested profile does not match benchmark variant")
    profile_evidence = {
        "requested": requested,
        "selected": _non_empty_string(evidence_raw.get("selected"), "profile_evidence.selected"),
        "forwarded": _non_empty_string(evidence_raw.get("forwarded"), "profile_evidence.forwarded"),
        "confirmed": _non_empty_string(
            evidence_raw.get("confirmed", "unverified"), "profile_evidence.confirmed"
        ),
    }
    return {
        "task_id": task["id"],
        "task_class": task["class"],
        "language": task["language"],
        "variant": variant,
        "profile_evidence": profile_evidence,
        "status": status,
        "accepted": accepted,
        "retry_count": retry_count,
        "correction_count": correction_count,
        "regression": False,
        "usage": usage,
        "latency_ms": {
            "runner_reported": reported_latency,
            "wall_clock": round(measured_ms, 3),
        },
        "attempts": attempts,
        "suitability_score": suitability,
        "prior": prior,
    }


def _mock_outcome(task: Mapping[str, Any], variant: str) -> dict[str, Any]:
    expected = task["acceptance"]["expected"]
    answer = (
        " ".join(map(str, expected))
        if task["acceptance"]["operator"] == "contains_all"
        else expected
    )
    score_seed = int(hashlib.sha256(f"{task['id']}:{variant}".encode()).hexdigest()[:4], 16)
    score = round(0.55 + (score_seed % 41) / 100, 2)
    attempts = []
    if variant == AUTO_VARIANT:
        attempts.append(
            {
                "kind": "classification",
                "status": "completed",
                "latency_ms": 1,
                "usage": {"input_tokens": 5, "output_tokens": 2},
            }
        )
    attempts.extend(
        [
            {
                "kind": "execution",
                "status": "completed",
                "latency_ms": 2,
                "usage": {"input_tokens": 10, "output_tokens": 4, "request_count": 1},
            },
            {
                "kind": "verification",
                "status": "completed",
                "latency_ms": 1,
                "usage": {},
            },
        ]
    )
    return {
        "status": "completed",
        "answer": answer,
        "profile_evidence": {
            "requested": variant,
            "selected": "mock-selected@medium" if variant == AUTO_VARIANT else variant,
            "forwarded": "mock-selected@medium" if variant == AUTO_VARIANT else variant,
            "confirmed": "unverified",
        },
        "attempts": attempts,
        "suitability_score": score,
        "prior": "mock-family-v1",
    }


def _runner_outcome(
    command: Sequence[str], request: Mapping[str, Any], timeout: float
) -> dict[str, Any]:
    try:
        completed = subprocess.run(  # noqa: S603
            list(command),
            input=json.dumps(request, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BenchmarkError(f"benchmark runner failed: {type(exc).__name__}") from exc
    if completed.returncode != 0:
        raise BenchmarkError(f"benchmark runner failed: exit-{completed.returncode}")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise BenchmarkError("benchmark runner returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise BenchmarkError("benchmark runner result must be an object")
    return value


def _baseline_index(report: Mapping[str, Any] | None) -> dict[tuple[str, str], bool | None]:
    if report is None:
        return {}
    runs = report.get("runs", [])
    if not isinstance(runs, list):
        raise BenchmarkError("baseline runs must be an array")
    return {
        (str(run.get("task_id")), str(run.get("variant"))): run.get("accepted")
        for run in runs
        if isinstance(run, dict)
    }


def _calibration(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, list[bool]] = defaultdict(list)
    priors: dict[str, list[bool]] = defaultdict(list)
    for run in runs:
        accepted = run.get("accepted")
        score = run.get("suitability_score")
        if isinstance(accepted, bool) and isinstance(score, (int, float)):
            lower = min(int(float(score) * 10) * 10, 90)
            buckets[f"{lower / 100:.1f}-{(lower + 10) / 100:.1f}"].append(accepted)
        prior = run.get("prior")
        if isinstance(accepted, bool) and isinstance(prior, str):
            priors[prior].append(accepted)

    def summarize(values: Mapping[str, list[bool]]) -> list[dict[str, Any]]:
        return [
            {
                "label": label,
                "observations": len(items),
                "accepted": sum(items),
                "observed_acceptance_rate": round(sum(items) / len(items), 4),
            }
            for label, items in sorted(values.items())
        ]

    return {
        "interpretation": "descriptive_observed_frequency_not_probability",
        "suitability_buckets": summarize(buckets),
        "prior_groups": summarize(priors),
    }


def _summary(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    usage = {key: 0 for key in USAGE_SIGNALS}
    accepted = 0
    evaluated = 0
    for run in runs:
        if isinstance(run.get("accepted"), bool):
            evaluated += 1
            accepted += int(run["accepted"])
        for key, value in run["usage"].items():
            usage[key] += int(value)
    return {
        "completed": sum(run["status"] == "completed" for run in runs),
        "failed": sum(run["status"] == "failed" for run in runs),
        "unsupported": sum(run["status"] == "unsupported" for run in runs),
        "evaluated": evaluated,
        "accepted": accepted,
        "observed_acceptance_rate": round(accepted / evaluated, 4) if evaluated else None,
        "retries": sum(int(run["retry_count"]) for run in runs),
        "corrections": sum(int(run["correction_count"]) for run in runs),
        "regressions": sum(bool(run["regression"]) for run in runs),
        "total_usage": usage,
        "total_runner_reported_latency_ms": round(
            sum(float(run["latency_ms"]["runner_reported"]) for run in runs), 3
        ),
        "total_wall_clock_latency_ms": round(
            sum(float(run["latency_ms"]["wall_clock"]) for run in runs), 3
        ),
    }


def build_report(
    manifest: Mapping[str, Any],
    variants: Sequence[str],
    *,
    mode: str,
    runner_command: Sequence[str] = (),
    timeout: float = 300,
    baseline: Mapping[str, Any] | None = None,
    policy_version: str = "unreported",
    priors_version: str = "unreported",
) -> dict[str, Any]:
    previous = _baseline_index(baseline)
    runs: list[dict[str, Any]] = []
    for task in manifest["tasks"]:
        for variant in variants:
            started = time.perf_counter()
            if mode == "mock":
                raw = _mock_outcome(task, variant)
            else:
                request = {
                    "protocol_version": BENCHMARK_PROTOCOL_VERSION,
                    "variant": variant,
                    "task": {
                        "id": task["id"],
                        "prompt": task["prompt"],
                        "fixtures": task.get("fixtures", {}),
                        "requirements": task.get("requirements", {}),
                    },
                }
                raw = _runner_outcome(runner_command, request, timeout)
            measured_ms = (time.perf_counter() - started) * 1000
            run = _normalize_outcome(task, variant, raw, measured_ms)
            old = previous.get((run["task_id"], variant))
            run["regression"] = old is True and run["accepted"] is not True
            runs.append(run)
    report: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "protocol_version": BENCHMARK_PROTOCOL_VERSION,
        "suite": {
            "id": manifest["suite_id"],
            "version": manifest["suite_version"],
            "manifest_sha256": manifest_digest(manifest),
        },
        "mode": mode,
        "variants": list(variants),
        "routing_inputs": {
            "policy_version": policy_version,
            "priors_version": priors_version,
        },
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "runs": runs,
        "summary": _summary(runs),
        "calibration": _calibration(runs),
        "comparison": {
            "baseline_suite": (
                {
                    key: baseline.get("suite", {}).get(key)
                    for key in ("id", "version", "manifest_sha256")
                }
                if baseline is not None and isinstance(baseline.get("suite"), dict)
                else None
            ),
            "baseline_routing_inputs": (
                {
                    key: baseline.get("routing_inputs", {}).get(key)
                    for key in ("policy_version", "priors_version")
                }
                if baseline is not None and isinstance(baseline.get("routing_inputs"), dict)
                else None
            ),
        },
        "configuration_effect": "none; benchmark reports are never loaded as routing configuration",
    }
    validate_report(report)
    return report


def validate_report(report: Mapping[str, Any]) -> None:
    if report.get("schema_version") != RESULT_SCHEMA_VERSION:
        raise BenchmarkError("unsupported result schema_version")
    if report.get("configuration_effect") != (
        "none; benchmark reports are never loaded as routing configuration"
    ):
        raise BenchmarkError("benchmark output must not alter routing configuration")
    variants = report.get("variants")
    if (
        not isinstance(variants, list)
        or AUTO_VARIANT not in variants
        or len(variants) < 3
        or len(set(variants)) != len(variants)
    ):
        raise BenchmarkError("benchmark report requires Auto and two distinct fixed profiles")
    runs = report.get("runs")
    if not isinstance(runs, list) or not runs:
        raise BenchmarkError("benchmark report requires runs")

    def inspect(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in FORBIDDEN_RESULT_KEYS:
                    raise BenchmarkError(f"unsafe benchmark result field: {key}")
                if any(word in str(key).lower() for word in ("cost", "currency", "price")):
                    raise BenchmarkError(f"monetary estimate field is forbidden: {key}")
                inspect(item)
        elif isinstance(value, list):
            for item in value:
                inspect(item)
        elif isinstance(value, str) and (SECRET_VALUE.search(value) or PRIVATE_PATH.search(value)):
            raise BenchmarkError("credential-like or private-path benchmark result value")

    inspect(report)


def _read_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"invalid JSON file: {type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise BenchmarkError("JSON file must contain an object")
    return value


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reproducible provider-neutral routing benchmark")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("mock", "live"):
        command = commands.add_parser(name)
        command.add_argument("--manifest", type=Path)
        command.add_argument("--report", type=Path, required=True)
        command.add_argument("--baseline", type=Path)
        command.add_argument("--policy-version", default="unreported")
        command.add_argument("--priors-version", default="unreported")
        command.add_argument("--profile", action="append", default=[])
    live = commands.choices["live"]
    live.add_argument("--consent-live", action="store_true")
    live.add_argument("--runner-command-json", required=True)
    live.add_argument("--timeout", type=float, default=300)
    validate = commands.add_parser("validate")
    validate.add_argument("report", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "validate":
            report = _read_json(args.report)
            assert report is not None
            validate_report(report)
            print("valid")
            return 0
        profiles = list(dict.fromkeys(args.profile))
        if len(profiles) < 2:
            raise BenchmarkError("benchmark requires at least two distinct fixed profiles")
        variants = [AUTO_VARIANT, *profiles]
        manifest = load_manifest(args.manifest)
        baseline = _read_json(args.baseline)
        if baseline is not None:
            validate_report(baseline)
        runner: list[str] = []
        if args.command == "live":
            if os.environ.get(LIVE_ENV) != "1" or not args.consent_live:
                print(
                    f"refusing live calls: set {LIVE_ENV}=1 and pass --consent-live",
                    file=sys.stderr,
                )
                return 2
            if args.timeout <= 0:
                raise BenchmarkError("timeout must be positive")
            try:
                runner_value = json.loads(args.runner_command_json)
            except json.JSONDecodeError as exc:
                raise BenchmarkError("--runner-command-json must be a JSON array") from exc
            if (
                not isinstance(runner_value, list)
                or not runner_value
                or not all(isinstance(item, str) and item for item in runner_value)
            ):
                raise BenchmarkError("--runner-command-json must be a non-empty string array")
            runner = runner_value
        report = build_report(
            manifest,
            variants,
            mode=args.command,
            runner_command=runner,
            timeout=getattr(args, "timeout", 300),
            baseline=baseline,
            policy_version=args.policy_version,
            priors_version=args.priors_version,
        )
        _write_report(args.report, report)
        print(str(args.report))
        return 0
    except BenchmarkError as exc:
        print(f"benchmark: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
