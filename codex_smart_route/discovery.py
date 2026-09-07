"""Dynamic model discovery from Codex App Server or user catalogs."""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

from .config import RouterConfig
from .models import ModelCapability, fingerprint


class DiscoveryError(RuntimeError):
    pass


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def capabilities_from_app_server(data: dict[str, Any]) -> list[ModelCapability]:
    """Parse documented v2 model/list response without inventing absent fields."""
    rows = data.get("data")
    if not isinstance(rows, list):
        raise DiscoveryError("model/list response has no data array")
    version = fingerprint(rows)[:16]
    result: list[ModelCapability] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("model"), str):
            continue
        efforts = row.get("supportedReasoningEfforts", [])
        parsed_efforts = tuple(
            item["reasoningEffort"]
            for item in efforts
            if isinstance(item, dict) and isinstance(item.get("reasoningEffort"), str)
        )
        default_effort = row.get("defaultReasoningEffort")
        if not parsed_efforts and isinstance(default_effort, str):
            parsed_efforts = (default_effort,)
        if not parsed_efforts:
            continue
        modalities = tuple(item for item in row.get("inputModalities", []) if isinstance(item, str))
        result.append(
            ModelCapability(
                model=row["model"],
                display_name=str(row.get("displayName") or row["model"]),
                reasoning_efforts=parsed_efforts,
                input_modalities=modalities or ("text",),
                supports_tools=None,
                supports_parallel_tools=None,
                context_window=None,
                auth_mode=None,
                available=True,
                source="codex-app-server:model/list",
                confidence="verified",
                capability_version=version,
            )
        )
    if not result:
        raise DiscoveryError("model/list returned no routable model profiles")
    return result


def capabilities_from_file(path: Path) -> list[ModelCapability]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DiscoveryError(f"cannot read catalog: {exc}") from exc
    rows = raw.get("models") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        raise DiscoveryError("catalog must be an array or contain a models array")
    version = fingerprint(rows)[:16]
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise DiscoveryError("catalog model entries must be objects")
        model = row.get("model") or row.get("id")
        efforts = row.get("reasoning_efforts") or row.get("supported_reasoning_efforts")
        if not isinstance(model, str) or not isinstance(efforts, list) or not efforts:
            raise DiscoveryError("catalog entries require model and reasoning_efforts")
        result.append(
            ModelCapability(
                model=model,
                display_name=str(row.get("display_name") or model),
                reasoning_efforts=tuple(str(item) for item in efforts),
                input_modalities=tuple(str(item) for item in row.get("input_modalities", ["text"])),
                supports_tools=_optional_bool(row.get("supports_tools")),
                supports_parallel_tools=_optional_bool(row.get("supports_parallel_tools")),
                context_window=int(row["context_window"])
                if row.get("context_window") is not None
                else None,
                auth_mode=str(row["auth_mode"]) if row.get("auth_mode") is not None else None,
                available=_optional_bool(row.get("available")),
                source=f"file:{path.name}",
                confidence=str(row.get("confidence", "reported")),  # type: ignore[arg-type]
                capability_version=str(row.get("capability_version", version)),
                relative_quality=_optional_float(row.get("relative_quality")),
                relative_consumption=_optional_float(row.get("relative_consumption")),
                relative_latency=_optional_float(row.get("relative_latency")),
            )
        )
    return result


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DiscoveryError("relative metrics must be numbers")
    result = float(value)
    if not 0 <= result <= 1:
        raise DiscoveryError("relative metrics must be between 0 and 1")
    return result


def apply_overrides(
    models: Iterable[ModelCapability], config: RouterConfig
) -> list[ModelCapability]:
    allowed = {field.name for field in ModelCapability.__dataclass_fields__.values()}
    result = []
    for model in models:
        changes = config.capability_overrides.get(model.model, {})
        unknown = set(changes) - allowed
        if unknown:
            raise DiscoveryError(f"unknown capability override fields: {sorted(unknown)}")
        if "reasoning_efforts" in changes:
            changes = {**changes, "reasoning_efforts": tuple(changes["reasoning_efforts"])}
        if "input_modalities" in changes:
            changes = {**changes, "input_modalities": tuple(changes["input_modalities"])}
        result.append(replace(model, **changes))
    return result


class AppServerDiscovery:
    """Small JSONL client. Never reads auth files or environment secrets."""

    def __init__(self, executable: str = "codex", timeout: float = 8.0):
        self.executable = executable
        self.timeout = timeout

    def discover(self) -> list[ModelCapability]:
        process = subprocess.Popen(  # noqa: S603
            [self.executable, "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        output: queue.Queue[str] = queue.Queue()

        def reader() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                output.put(line)

        threading.Thread(target=reader, daemon=True).start()
        try:
            self._send(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"clientInfo": {"name": "codex-smart-route", "version": "0.1.0"}},
                },
            )
            self._response(output, 1)
            self._send(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "model/list",
                    "params": {"includeHidden": True},
                },
            )
            response = self._response(output, 2)
            if "error" in response:
                error = response["error"]
                message = (
                    error.get("message", "unknown error")
                    if isinstance(error, dict)
                    else "unknown error"
                )
                raise DiscoveryError(f"App Server model/list failed: {message}")
            return capabilities_from_app_server(response.get("result", {}))
        except queue.Empty as exc:
            raise DiscoveryError("Codex App Server discovery timed out") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise DiscoveryError(f"Codex App Server discovery failed: {exc}") from exc
        finally:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()

    @staticmethod
    def _send(process: subprocess.Popen[str], value: dict[str, Any]) -> None:
        if process.stdin is None:
            raise DiscoveryError("App Server stdin unavailable")
        process.stdin.write(json.dumps(value, separators=(",", ":")) + "\n")
        process.stdin.flush()

    def _response(self, output: queue.Queue[str], request_id: int) -> dict[str, Any]:
        while True:
            message = json.loads(output.get(timeout=self.timeout))
            if isinstance(message, dict) and message.get("id") == request_id:
                return message
