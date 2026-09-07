"""Adapters that apply selected model and reasoning before Codex execution."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, BinaryIO, TextIO

from .models import Decision
from .router import RoutingError, mark_forwarded

AUTO_SLUG = "codex-smart-route"


def patch_responses_request(
    payload: dict[str, Any], decision: Decision, auto_slug: str = AUTO_SLUG
) -> dict[str, Any]:
    result = copy.deepcopy(payload)
    if result.get("model") != auto_slug:
        return result
    if (
        decision.status != "selected"
        or not decision.selected_model
        or not decision.selected_reasoning_effort
    ):
        raise RoutingError("blocked route cannot be forwarded")
    reasoning = result.get("reasoning") or {}
    if not isinstance(reasoning, dict):
        raise RoutingError("Responses reasoning must be an object")
    result["model"] = decision.selected_model
    result["reasoning"] = {**reasoning, "effort": decision.selected_reasoning_effort}
    return result


def patch_turn_start(params: dict[str, Any], decision: Decision) -> dict[str, Any]:
    if (
        decision.status != "selected"
        or not decision.selected_model
        or not decision.selected_reasoning_effort
    ):
        raise RoutingError("blocked route cannot start a turn")
    result = copy.deepcopy(params)
    result["model"] = decision.selected_model
    result["effort"] = decision.selected_reasoning_effort
    return result


class CodexCliAdapter:
    """Official CLI adapter: applies both flags before `codex exec` starts."""

    def __init__(self, executable: str = "codex"):
        self.executable = executable

    def command(
        self,
        decision: Decision,
        task: str,
        cwd: Path | None = None,
        extra_args: tuple[str, ...] = (),
    ) -> list[str]:
        if (
            decision.status != "selected"
            or not decision.selected_model
            or not decision.selected_reasoning_effort
        ):
            raise RoutingError("blocked route cannot execute")
        command = [
            self.executable,
            "exec",
            "-m",
            decision.selected_model,
            "-c",
            f'model_reasoning_effort="{decision.selected_reasoning_effort}"',
        ]
        if cwd:
            command.extend(["-C", str(cwd)])
        command.extend(extra_args)
        command.append(task)
        return command

    def run(
        self,
        decision: Decision,
        task: str,
        cwd: Path | None = None,
        extra_args: tuple[str, ...] = (),
    ) -> tuple[int, Decision]:
        command = self.command(decision, task, cwd, extra_args)
        completed = subprocess.run(command, check=False)  # noqa: S603
        return completed.returncode, mark_forwarded(decision)


class JsonLineAppServerProxy:
    """Experimental transparent stdio proxy for Codex App Server.

    Caller supplies a callback that returns a Decision for each `turn/start`.
    All unmodified JSON messages, streaming notifications, tool traffic,
    cancellation, and compaction pass byte-for-byte except routed requests.
    """

    def __init__(self, route_callback: Any, executable: str = "codex", auto_slug: str = AUTO_SLUG):
        self.route_callback = route_callback
        self.executable = executable
        self.auto_slug = auto_slug
        self._model_list_ids: set[Any] = set()
        self._pending_auto_starts: set[Any] = set()
        self._auto_threads: set[str] = set()
        self._state_lock = threading.RLock()

    def run(self, source: TextIO = sys.stdin, sink: TextIO = sys.stdout) -> int:
        child = subprocess.Popen(  # noqa: S603
            [self.executable, "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        assert child.stdin is not None and child.stdout is not None
        child_stdout = child.stdout

        def copy_output() -> None:
            for line in child_stdout:
                output = line
                try:
                    message = json.loads(line)
                    if message.get("id") in self._model_list_ids:
                        message = self.inject_auto_model(message)
                        output = json.dumps(message, separators=(",", ":")) + "\n"
                    self.observe_server_message(message)
                except (json.JSONDecodeError, AttributeError):
                    pass
                sink.write(output)
                sink.flush()

        output_thread = threading.Thread(target=copy_output, daemon=True)
        output_thread.start()
        try:
            for line in source:
                message = json.loads(line)
                method = message.get("method")
                if method == "model/list" and "id" in message:
                    self._model_list_ids.add(message["id"])
                message = self.process_client_message(message)
                child.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
                child.stdin.flush()
        except (BrokenPipeError, json.JSONDecodeError) as exc:
            print(f"smart-route app-server proxy stopped: {type(exc).__name__}", file=sys.stderr)
            return 2
        finally:
            child.stdin.close()
            child.wait()
            output_thread.join(timeout=2)
        return child.returncode

    def process_client_message(self, message: dict[str, Any]) -> dict[str, Any]:
        """Route only explicit Auto turns or turns owned by an Auto thread."""
        result = copy.deepcopy(message)
        method = result.get("method")
        if method == "shutdown":
            with self._state_lock:
                self._auto_threads.clear()
                self._pending_auto_starts.clear()
            return result
        params = result.get("params")
        if not isinstance(params, dict):
            return result
        thread_id = params.get("threadId")
        valid_thread_id = thread_id if isinstance(thread_id, str) and thread_id else None

        if method == "thread/start" and params.get("model") == self.auto_slug:
            request_id = result.get("id")
            if request_id is not None:
                with self._state_lock:
                    self._pending_auto_starts.add(request_id)
            params.pop("model", None)
        elif method == "thread/resume":
            model = params.get("model")
            if model == self.auto_slug:
                if valid_thread_id:
                    with self._state_lock:
                        self._auto_threads.add(valid_thread_id)
                params.pop("model", None)
            elif model is not None and valid_thread_id:
                with self._state_lock:
                    self._auto_threads.discard(valid_thread_id)
        elif method == "turn/start":
            model = params.get("model")
            if model == self.auto_slug:
                if valid_thread_id:
                    with self._state_lock:
                        self._auto_threads.add(valid_thread_id)
                result["params"] = patch_turn_start(params, self.route_callback(params))
            elif model is not None:
                if valid_thread_id:
                    with self._state_lock:
                        self._auto_threads.discard(valid_thread_id)
            elif valid_thread_id:
                with self._state_lock:
                    auto_enabled = valid_thread_id in self._auto_threads
                if auto_enabled:
                    result["params"] = patch_turn_start(params, self.route_callback(params))
        elif method in {"thread/archive", "thread/delete"} and valid_thread_id:
            with self._state_lock:
                self._auto_threads.discard(valid_thread_id)
        return result

    def observe_server_message(self, message: dict[str, Any]) -> None:
        """Update thread ownership from correlated responses and lifecycle events."""
        request_id = message.get("id")
        with self._state_lock:
            if request_id in self._pending_auto_starts:
                self._pending_auto_starts.discard(request_id)
                result = message.get("result")
                thread = result.get("thread") if isinstance(result, dict) else None
                thread_id = thread.get("id") if isinstance(thread, dict) else None
                if isinstance(thread_id, str) and thread_id:
                    self._auto_threads.add(thread_id)

            if message.get("method") in {
                "thread/closed",
                "thread/archived",
                "thread/deleted",
            }:
                params = message.get("params")
                thread_id = params.get("threadId") if isinstance(params, dict) else None
                if isinstance(thread_id, str):
                    self._auto_threads.discard(thread_id)

    def inject_auto_model(self, message: dict[str, Any]) -> dict[str, Any]:
        """Add virtual entry with modalities/efforts derived from current catalog."""
        result = copy.deepcopy(message)
        data = result.get("result", {}).get("data")
        if not isinstance(data, list) or any(
            isinstance(item, dict) and item.get("model") == self.auto_slug for item in data
        ):
            return result
        modalities = sorted(
            {
                modality
                for item in data
                if isinstance(item, dict)
                for modality in item.get("inputModalities", [])
                if isinstance(modality, str)
            }
        )
        effort_values: set[str] = set()
        for item in data:
            if not isinstance(item, dict):
                continue
            for option in item.get("supportedReasoningEfforts", []):
                if isinstance(option, dict):
                    effort = option.get("reasoningEffort")
                    if isinstance(effort, str):
                        effort_values.add(effort)
        efforts = sorted(effort_values)
        default_effort = "medium" if "medium" in efforts else (efforts[0] if efforts else "low")
        data.append(
            {
                "id": self.auto_slug,
                "model": self.auto_slug,
                "displayName": "Auto (Smart Route)",
                "description": "Select model and reasoning before each safe turn boundary.",
                "defaultReasoningEffort": default_effort,
                "supportedReasoningEfforts": [
                    {"reasoningEffort": effort, "description": "Routed dynamically"}
                    for effort in efforts or [default_effort]
                ],
                "inputModalities": modalities or ["text"],
                "hidden": False,
                "isDefault": False,
            }
        )
        return result


def stream_copy(source: BinaryIO, sink: BinaryIO, chunk_size: int = 65_536) -> None:
    """Transport helper used by offline streaming tests."""
    while chunk := source.read(chunk_size):
        sink.write(chunk)
        sink.flush()
