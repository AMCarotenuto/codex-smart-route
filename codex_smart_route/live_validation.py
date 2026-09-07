"""Explicit, opt-in validation against a real Codex installation.

Importing this module and running offline tests never starts Codex or contacts a model provider.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import platform
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, TextIO

from .adapters import AUTO_SLUG, CodexCliAdapter, JsonLineAppServerProxy
from .audit import SECRET, SECRET_VALUE, redact
from .config import ClassifierConfig, RouterConfig, load_config
from .discovery import AppServerDiscovery, capabilities_from_file
from .models import Decision, ModelCapability, TaskContext
from .router import mark_forwarded
from .service import RoutingService

LIVE_ENV = "SMART_ROUTE_LIVE"
REPORT_VERSION = 1
MECHANICAL_TASK = (
    "Read pyproject.toml only, then return the package name in one line. "
    "Do not write files or reveal configuration values."
)
TOOL_TASK = (
    "Use the normal Codex shell tool to read only the project name from pyproject.toml, "
    "then report that name. Do not write files, inspect environment variables, credentials, "
    "home-directory configuration, or network resources."
)
CANCELLATION_TASK = (
    "Explain the repository architecture in detail using read-only inspection. "
    "Do not write files, inspect credentials, or access network resources."
)
CONTINUATION_TASK = (
    "Continue in the same thread and state only the package name already observed. "
    "Do not use another tool."
)


class LiveValidationError(RuntimeError):
    """Safe user-facing live validation failure."""


@dataclass
class EventSummary:
    event_count: int = 0
    event_types: dict[str, int] = field(default_factory=dict)
    streaming: bool = False
    tool_started: bool = False
    tool_completed: bool = False
    thread_id: str | None = None
    turn_id: str | None = None
    terminal: bool = False

    @property
    def tool_round_trip(self) -> bool:
        return self.tool_started and self.tool_completed

    def observe(self, message: dict[str, Any]) -> None:
        event_type = str(message.get("type") or message.get("method") or "unknown")
        self.event_count += 1
        self.event_types[event_type] = self.event_types.get(event_type, 0) + 1
        self.streaming = self.event_count >= 2
        thread_id = message.get("thread_id")
        if not isinstance(thread_id, str):
            params = message.get("params")
            thread_id = params.get("threadId") if isinstance(params, dict) else None
        if isinstance(thread_id, str) and thread_id:
            self.thread_id = thread_id
        turn_id = message.get("turn_id")
        if not isinstance(turn_id, str):
            params = message.get("params")
            turn = params.get("turn") if isinstance(params, dict) else None
            turn_id = turn.get("id") if isinstance(turn, dict) else None
        if isinstance(turn_id, str) and turn_id:
            self.turn_id = turn_id
        item = message.get("item")
        if not isinstance(item, dict):
            params = message.get("params")
            item = params.get("item") if isinstance(params, dict) else None
        item_type = str(item.get("type", "")) if isinstance(item, dict) else ""
        toolish = any(part in item_type.lower() for part in ("command", "tool", "function"))
        lowered = event_type.lower()
        if toolish and ("started" in lowered or "start" in lowered):
            self.tool_started = True
        if toolish and ("completed" in lowered or "complete" in lowered):
            self.tool_completed = True
        if event_type in {"turn.completed", "turn/completed", "turn.cancelled", "turn/cancelled"}:
            self.terminal = True

    def to_dict(self) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        data["event_types"] = [
            {"name": name, "count": count} for name, count in sorted(self.event_types.items())
        ]
        return data | {"tool_round_trip": self.tool_round_trip}


@dataclass(frozen=True)
class ExecutionEvidence:
    name: str
    selected_profile: str
    requested_model: str
    forwarded_model: str
    forwarded_reasoning_effort: str
    confirmed_model: str = "unverified"
    confirmed_reasoning_effort: str = "unverified"
    return_code: int = 0
    cancelled: bool = False
    events: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_decision(
        cls,
        name: str,
        decision: Decision,
        summary: EventSummary,
        return_code: int,
        cancelled: bool = False,
    ) -> ExecutionEvidence:
        forwarded = mark_forwarded(decision)
        if not forwarded.selected_profile:
            raise LiveValidationError("route did not select a profile")
        if not forwarded.forwarded_model or not forwarded.forwarded_reasoning_effort:
            raise LiveValidationError("adapter did not record forwarded model and effort")
        return cls(
            name=name,
            selected_profile=forwarded.selected_profile,
            requested_model=forwarded.requested_model or AUTO_SLUG,
            forwarded_model=forwarded.forwarded_model,
            forwarded_reasoning_effort=forwarded.forwarded_reasoning_effort,
            confirmed_model="unverified",
            confirmed_reasoning_effort="unverified",
            return_code=return_code,
            cancelled=cancelled,
            events=summary.to_dict(),
        )


@dataclass(frozen=True)
class ProcessResult:
    return_code: int
    summary: EventSummary
    cancelled: bool = False


def _safe_error(kind: str, detail: str = "") -> str:
    clean = redact(detail)
    suffix = f": {clean}" if isinstance(clean, str) and clean else ""
    return f"{kind}{suffix}"


def _read_lines(stream: TextIO, output: queue.Queue[str]) -> None:
    for line in stream:
        output.put(line)


def run_jsonl_command(
    command: Sequence[str], *, timeout: float, cancel_after_first_event: bool = False
) -> ProcessResult:
    """Run Codex JSONL without retaining prompts, model output, or stderr."""
    process = subprocess.Popen(  # noqa: S603
        list(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    if process.stdout is None:
        process.kill()
        raise LiveValidationError("Codex stdout unavailable")
    lines: queue.Queue[str] = queue.Queue()
    threading.Thread(target=_read_lines, args=(process.stdout, lines), daemon=True).start()
    summary = EventSummary()
    cancelled = False
    deadline = time.monotonic() + timeout
    while process.poll() is None or not lines.empty():
        if time.monotonic() >= deadline:
            process.terminate()
            cancelled = True
            break
        try:
            line = lines.get(timeout=0.1)
        except queue.Empty:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict):
            summary.observe(message)
            if cancel_after_first_event and not summary.terminal:
                process.terminate()
                cancelled = True
                break
    if cancelled:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    return ProcessResult(process.wait(), summary, cancelled)


class AppServerSession:
    """Minimal JSON-RPC client used only by explicit App Server live validation."""

    def __init__(self, executable: str, timeout: float):
        self.timeout = timeout
        self.process = subprocess.Popen(  # noqa: S603
            [executable, "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        if self.process.stdin is None or self.process.stdout is None:
            self.close()
            raise LiveValidationError("App Server stdio unavailable")
        self.input = self.process.stdin
        self.output: queue.Queue[str] = queue.Queue()
        threading.Thread(
            target=_read_lines, args=(self.process.stdout, self.output), daemon=True
        ).start()
        self.next_id = 1
        self.summary = EventSummary()
        self.notifications: list[dict[str, str | None]] = []

    @staticmethod
    def _notification_marker(value: dict[str, Any]) -> dict[str, str | None]:
        """Retain only non-sensitive protocol identifiers needed for matching."""
        params = value.get("params")
        item = params.get("item") if isinstance(params, dict) else None
        item_type = item.get("type") if isinstance(item, dict) else None
        return {
            "method": value.get("method") if isinstance(value.get("method"), str) else None,
            "item_type": item_type if isinstance(item_type, str) else None,
        }

    @staticmethod
    def _matches(marker: dict[str, str | None], methods: set[str], item_types: set[str]) -> bool:
        return marker.get("method") in methods or marker.get("item_type") in item_types

    def send(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        self.input.write(
            json.dumps(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
                separators=(",", ":"),
            )
            + "\n"
        )
        self.input.flush()
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                value = json.loads(self.output.get(timeout=0.1))
            except queue.Empty:
                continue
            except json.JSONDecodeError:
                continue
            if not isinstance(value, dict):
                continue
            self.summary.observe(value)
            if value.get("id") == request_id:
                if "error" in value:
                    error = value.get("error")
                    code = error.get("code") if isinstance(error, dict) else "unknown"
                    raise LiveValidationError(f"App Server {method} failed with code {code}")
                return value
            self.notifications.append(self._notification_marker(value))
        raise LiveValidationError(f"App Server {method} timed out")

    def wait_for(
        self, methods: set[str], item_types: set[str] | None = None
    ) -> dict[str, str | None]:
        expected_items = item_types or set()
        for index, value in enumerate(self.notifications):
            if self._matches(value, methods, expected_items):
                return self.notifications.pop(index)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                value = json.loads(self.output.get(timeout=0.1))
            except queue.Empty:
                continue
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                self.summary.observe(value)
                marker = self._notification_marker(value)
                if self._matches(marker, methods, expected_items):
                    return marker
                self.notifications.append(marker)
        expected = sorted(methods | expected_items)
        raise LiveValidationError(f"App Server event timed out: {expected}")

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def __enter__(self) -> AppServerSession:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _selected(service: RoutingService, context: TaskContext) -> Decision:
    decision = service.route(context, dry_run=True)
    if decision.status != "selected":
        raise LiveValidationError(f"routing blocked: {decision.reason}")
    return decision


def _cli_command(executable: str, decision: Decision, task: str, cwd: Path) -> list[str]:
    return CodexCliAdapter(executable).command(
        decision,
        task,
        cwd,
        ("--json", "--ephemeral", "--sandbox", "read-only", "--color", "never"),
    )


def run_cli_suite(
    service: RoutingService,
    executable: str,
    cwd: Path,
    timeout: float,
    runner: Callable[..., ProcessResult] = run_jsonl_command,
) -> list[ExecutionEvidence]:
    scenarios = (
        ("mechanical", TaskContext(MECHANICAL_TASK, task_id="live-mechanical")),
        ("tool-round-trip", TaskContext(TOOL_TASK, task_id="live-tool", tools_required=True)),
    )
    evidence: list[ExecutionEvidence] = []
    for name, context in scenarios:
        decision = _selected(service, context)
        result = runner(
            _cli_command(executable, decision, context.task, cwd),
            timeout=timeout,
            cancel_after_first_event=False,
        )
        evidence.append(
            ExecutionEvidence.from_decision(name, decision, result.summary, result.return_code)
        )
    if len({item.selected_profile for item in evidence}) < 2:
        raise LiveValidationError(
            "live tasks selected one profile; adjust policy/capability overrides and rerun"
        )
    if any(item.return_code != 0 for item in evidence):
        raise LiveValidationError("Codex CLI scenario failed")
    if not all(item.events.get("streaming") for item in evidence):
        raise LiveValidationError("Codex CLI JSONL streaming was not observed")
    if not evidence[1].events.get("tool_round_trip"):
        raise LiveValidationError("Codex CLI tool-call round trip was not observed")
    cancellation = _selected(service, TaskContext(CANCELLATION_TASK, task_id="live-cancellation"))
    result = runner(
        _cli_command(executable, cancellation, CANCELLATION_TASK, cwd),
        timeout=timeout,
        cancel_after_first_event=True,
    )
    if not result.cancelled:
        raise LiveValidationError("Codex CLI cancellation was not observed")
    evidence.append(
        ExecutionEvidence.from_decision(
            "cancellation", cancellation, result.summary, result.return_code, cancelled=True
        )
    )
    return evidence


def run_app_server_suite(
    service: RoutingService, executable: str, cwd: Path, timeout: float
) -> dict[str, Any]:
    """Validate Auto forwarding, continuation, compaction, and cancellation when supported."""
    decision = _selected(
        service, TaskContext(TOOL_TASK, task_id="live-app-server", tools_required=True)
    )
    proxy = JsonLineAppServerProxy(lambda _params: decision, executable=executable)
    with AppServerSession(executable, timeout) as session:
        session.send(
            "initialize", {"clientInfo": {"name": "codex-smart-route-live", "version": "0.1.0"}}
        )
        start_request = proxy.process_client_message(
            {
                "id": 1,
                "method": "thread/start",
                "params": {
                    "model": AUTO_SLUG,
                    "cwd": str(cwd),
                    "ephemeral": True,
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                },
            }
        )
        start = session.send("thread/start", start_request["params"])
        proxy.observe_server_message({"id": 1, "result": start.get("result")})
        result = start.get("result")
        thread = result.get("thread") if isinstance(result, dict) else None
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or not thread_id:
            raise LiveValidationError("App Server did not return thread id")
        raw_turn = {
            "id": 2,
            "method": "turn/start",
            "params": {
                "threadId": thread_id,
                "model": AUTO_SLUG,
                "input": [{"type": "text", "text": TOOL_TASK}],
            },
        }
        forwarded = proxy.process_client_message(raw_turn)["params"]
        if forwarded.get("model") != decision.selected_model:
            raise LiveValidationError("App Server forwarded model mismatch")
        if forwarded.get("effort") != decision.selected_reasoning_effort:
            raise LiveValidationError("App Server forwarded effort mismatch")
        session.send("turn/start", forwarded)
        session.wait_for({"turn/completed"})
        continued = proxy.process_client_message(
            {
                "id": 3,
                "method": "turn/start",
                "params": {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": CONTINUATION_TASK}],
                },
            }
        )["params"]
        if continued.get("model") != decision.selected_model:
            raise LiveValidationError("App Server continuation lost Auto routing")
        session.send("turn/start", continued)
        session.wait_for({"turn/completed"})
        cancel_turn = session.send("turn/start", continued)
        cancel_result = cancel_turn.get("result")
        turn = cancel_result.get("turn") if isinstance(cancel_result, dict) else None
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        if not isinstance(turn_id, str) or not turn_id:
            raise LiveValidationError("App Server did not return cancellable turn id")
        session.send("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
        session.wait_for({"turn/completed", "turn/cancelled"})
        compaction = "unsupported"
        try:
            session.send("thread/compact/start", {"threadId": thread_id})
            session.wait_for({"thread/compacted"}, {"contextCompaction"})
            compaction = "passed"
        except LiveValidationError as exc:
            if "code -32601" not in str(exc):
                raise
        evidence = ExecutionEvidence.from_decision(
            "app-server-auto", decision, session.summary, 0, cancelled=True
        )
        return {
            "status": "passed",
            "evidence": dataclasses.asdict(evidence),
            "continuation": "passed",
            "compaction": compaction,
            "cancellation": "passed",
        }


def _version(executable: str) -> str:
    try:
        result = subprocess.run(  # noqa: S603
            [executable, "--version"], capture_output=True, text=True, check=False, timeout=10
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _safe_error(type(exc).__name__)
    text = result.stdout.strip()
    return text if result.returncode == 0 and text else f"unavailable:exit-{result.returncode}"


def build_report(
    cli: list[ExecutionEvidence], app_server: dict[str, Any], executable: str
) -> dict[str, Any]:
    report = {
        "report_version": REPORT_VERSION,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "codex_cli_version": _version(executable),
        "codex_desktop_version": "unavailable:not-exposed-by-cli",
        "classifier": "disabled",
        "sandbox": "read-only",
        "cli": [dataclasses.asdict(item) for item in cli],
        "app_server": app_server,
        "confirmation_note": (
            "unverified means selection/forwarding observed without trustworthy "
            "runtime model metadata"
        ),
    }
    redacted = redact(report)
    assert isinstance(redacted, dict)
    return redacted


def validate_report_safe(report: dict[str, Any]) -> None:
    serialized = json.dumps(report, sort_keys=True)
    if SECRET_VALUE.search(serialized):
        raise LiveValidationError("report contains credential-like value")
    forbidden = {"task", "prompt", "output", "stderr", "environment", "command"}
    keys: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                keys.add(str(key).lower())
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(report)
    if keys & forbidden or any(SECRET.search(key) for key in keys):
        raise LiveValidationError("report schema contains sensitive field")


def _catalog(path: Path | None, executable: str) -> list[ModelCapability]:
    return capabilities_from_file(path) if path else AppServerDiscovery(executable).discover()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run explicit billable Codex live validation")
    parser.add_argument("--consent-live", action="store_true")
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--include-app-server", action="store_true")
    parser.add_argument("--report", type=Path, default=Path("live-report.json"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if os.environ.get(LIVE_ENV) != "1" or not args.consent_live:
        print(f"refusing live calls: set {LIVE_ENV}=1 and pass --consent-live", file=sys.stderr)
        return 2
    if args.timeout <= 0:
        print("timeout must be positive", file=sys.stderr)
        return 2
    try:
        config: RouterConfig = replace(
            load_config(args.config),
            classifier=ClassifierConfig(mode="disabled"),
            log_enabled=False,
        )
        service = RoutingService(config, _catalog(args.catalog, args.codex))
        cli = run_cli_suite(service, args.codex, args.cwd.resolve(), args.timeout)
        app_server = (
            run_app_server_suite(service, args.codex, args.cwd.resolve(), args.timeout)
            if args.include_app_server
            else {"status": "not-requested"}
        )
        report = build_report(cli, app_server, args.codex)
        validate_report_safe(report)
        args.report.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except (LiveValidationError, OSError, subprocess.SubprocessError) as exc:
        print(_safe_error(type(exc).__name__, str(exc)), file=sys.stderr)
        return 1
    print(str(args.report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
