"""Dynamic model discovery from Codex App Server or user catalogs."""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .config import RouterConfig
from .models import ModelCapability, fingerprint


class DiscoveryError(RuntimeError):
    pass


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


@dataclass(frozen=True)
class DiscoveryDiagnostics:
    protocol: str = "unknown"
    server_version: str | None = None
    pages: int = 0
    provider_capabilities: str = "not-attempted"
    partial: bool = False
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "server_version": self.server_version,
            "pages": self.pages,
            "provider_capabilities": self.provider_capabilities,
            "partial": self.partial,
            "warnings": list(self.warnings),
        }


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row:
            return row[key]
    return None


def _optional_positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _evidence(
    values: dict[str, Any], source: str, confidence: str
) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (field, source, confidence)
        for field, value in sorted(values.items())
        if value is not None and value != ()
    )


def _parse_efforts(row: dict[str, Any]) -> tuple[str, ...]:
    efforts = _first(row, "supportedReasoningEfforts", "supported_reasoning_efforts")
    parsed: list[str] = []
    if isinstance(efforts, list):
        for item in efforts:
            value = item.get("reasoningEffort") if isinstance(item, dict) else item
            if isinstance(value, str) and value and value not in parsed:
                parsed.append(value)
    default_effort = _first(row, "defaultReasoningEffort", "default_reasoning_effort")
    if not parsed and isinstance(default_effort, str) and default_effort:
        parsed.append(default_effort)
    return tuple(parsed)


def _merge_capability(left: ModelCapability, right: ModelCapability) -> ModelCapability:
    """Merge duplicate records. Earlier model evidence wins; missing values are filled."""
    evidence = {item[0]: item for item in left.field_evidence}
    evidence.update({item[0]: item for item in right.field_evidence if item[0] not in evidence})
    return replace(
        left,
        display_name=min(left.display_name, right.display_name),
        reasoning_efforts=tuple(sorted(set(left.reasoning_efforts) | set(right.reasoning_efforts))),
        input_modalities=tuple(sorted(set(left.input_modalities) | set(right.input_modalities))),
        supports_tools=(
            left.supports_tools if left.supports_tools is not None else right.supports_tools
        ),
        supports_parallel_tools=(
            left.supports_parallel_tools
            if left.supports_parallel_tools is not None
            else right.supports_parallel_tools
        ),
        context_window=(
            left.context_window if left.context_window is not None else right.context_window
        ),
        auth_mode=left.auth_mode if left.auth_mode is not None else right.auth_mode,
        available=left.available if left.available is not None else right.available,
        provider_capabilities=tuple(
            sorted(set(left.provider_capabilities) | set(right.provider_capabilities))
        ),
        source=left.source if right.source in left.source else f"{left.source}+{right.source}",
        field_evidence=tuple(evidence[key] for key in sorted(evidence)),
    )


def capabilities_from_app_server(
    data: dict[str, Any], provider_data: dict[str, Any] | None = None
) -> list[ModelCapability]:
    """Parse current and legacy model/list shapes without inventing absent fields."""
    rows = data.get("data")
    if not isinstance(rows, list):
        raise DiscoveryError("model/list response has no data array")
    version = fingerprint({"models": rows, "provider": provider_data})[:16]
    by_model: dict[str, ModelCapability] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        model = _first(row, "model", "id")
        if not isinstance(model, str) or not model or model.casefold() == "auto":
            continue
        parsed_efforts = _parse_efforts(row)
        if not parsed_efforts:
            continue
        raw_modalities = _first(row, "inputModalities", "input_modalities")
        modalities = (
            tuple(dict.fromkeys(item for item in raw_modalities if isinstance(item, str)))
            if isinstance(raw_modalities, list)
            else ()
        )
        auth = _first(row, "authMode", "auth_mode")
        values: dict[str, Any] = {
            "reasoning_efforts": parsed_efforts,
            "input_modalities": modalities,
            "supports_tools": _optional_bool(_first(row, "supportsTools", "supports_tools")),
            "supports_parallel_tools": _optional_bool(
                _first(row, "supportsParallelTools", "supports_parallel_tools")
            ),
            "context_window": _optional_positive_int(
                _first(row, "contextWindow", "contextWindowTokens", "context_window")
            ),
            "auth_mode": str(auth) if auth is not None else None,
            "available": _optional_bool(_first(row, "available", "isAvailable")),
        }
        field_evidence = list(_evidence(values, "codex-app-server:model/list", "verified"))
        if isinstance(raw_modalities, list) and not modalities:
            field_evidence.append(("input_modalities", "codex-app-server:model/list", "verified"))
        capability = ModelCapability(
            model=model,
            display_name=str(_first(row, "displayName", "display_name") or model),
            reasoning_efforts=parsed_efforts,
            input_modalities=modalities,
            supports_tools=values["supports_tools"],
            supports_parallel_tools=values["supports_parallel_tools"],
            context_window=values["context_window"],
            auth_mode=values["auth_mode"],
            available=values["available"],
            source="codex-app-server:model/list",
            confidence="verified",
            capability_version=version,
            field_evidence=tuple(sorted(field_evidence)),
        )
        by_model[model] = (
            _merge_capability(by_model[model], capability) if model in by_model else capability
        )
    if not by_model:
        raise DiscoveryError("model/list returned no routable model profiles")

    provider = provider_data if isinstance(provider_data, dict) else {}
    provider_auth = _first(provider, "authMode", "auth_mode")
    explicit_provider_tools = _optional_bool(_first(provider, "supportsTools", "supports_tools"))
    reported_provider_capabilities = tuple(
        sorted(
            (field, value)
            for field, value in provider.items()
            if field in {"imageGeneration", "namespaceTools", "webSearch"}
            and isinstance(value, bool)
        )
    )
    provider_values: dict[str, Any] = {
        "supports_tools": explicit_provider_tools,
        "supports_parallel_tools": _optional_bool(
            _first(provider, "supportsParallelTools", "supports_parallel_tools")
        ),
        "context_window": _optional_positive_int(
            _first(provider, "contextWindow", "contextWindowTokens", "context_window")
        ),
        "auth_mode": str(provider_auth) if provider_auth is not None else None,
        "available": _optional_bool(_first(provider, "available", "isAvailable")),
        "provider_capabilities": reported_provider_capabilities,
    }
    if any(value is not None for value in provider_values.values()):
        provider_record = ModelCapability(
            model="provider",
            display_name="provider",
            reasoning_efforts=(),
            input_modalities=(),
            supports_tools=provider_values["supports_tools"],
            supports_parallel_tools=provider_values["supports_parallel_tools"],
            context_window=provider_values["context_window"],
            auth_mode=provider_values["auth_mode"],
            available=provider_values["available"],
            provider_capabilities=provider_values["provider_capabilities"],
            source="codex-app-server:modelProvider/capabilities/read",
            confidence="reported",
            capability_version=version,
            field_evidence=_evidence(
                provider_values,
                "codex-app-server:modelProvider/capabilities/read",
                "reported",
            ),
        )
        by_model = {
            name: _merge_capability(model, replace(provider_record, model=name))
            for name, model in by_model.items()
        }
    return [by_model[name] for name in sorted(by_model)]


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
                prior_family=str(row["prior_family"])
                if row.get("prior_family") is not None
                else None,
                prior_strengths=tuple(str(item) for item in row.get("prior_strengths", [])),
                prior_weaknesses=tuple(str(item) for item in row.get("prior_weaknesses", [])),
                prior_source=str(row.get("prior_source", "none")),
                prior_confidence=str(row.get("prior_confidence", "unverified")),  # type: ignore[arg-type]
                prior_version=str(row.get("prior_version", "none")),
                tie_break_priority=int(row.get("tie_break_priority", 0)),
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
    allowed.remove("field_evidence")
    allowed.remove("provider_capabilities")
    result = []
    for model in models:
        changes = dict(config.capability_overrides.get(model.model, {}))
        unknown = set(changes) - allowed
        if unknown:
            raise DiscoveryError(f"unknown capability override fields: {sorted(unknown)}")
        if "reasoning_efforts" in changes:
            changes = {**changes, "reasoning_efforts": tuple(changes["reasoning_efforts"])}
        if "input_modalities" in changes:
            changes = {**changes, "input_modalities": tuple(changes["input_modalities"])}
        if "prior_strengths" in changes:
            changes = {**changes, "prior_strengths": tuple(changes["prior_strengths"])}
        if "prior_weaknesses" in changes:
            changes = {**changes, "prior_weaknesses": tuple(changes["prior_weaknesses"])}
        if changes.get("prior_confidence") == "verified":
            raise DiscoveryError("user overrides cannot mark prior data verified")
        if "prior_confidence" in changes and changes["prior_confidence"] not in {
            "reported",
            "unverified",
        }:
            raise DiscoveryError("prior_confidence must be reported or unverified")
        if changes.get("prior_confidence") == "verified":
            raise DiscoveryError("user overrides cannot mark prior data verified")
        if "prior_confidence" in changes and changes["prior_confidence"] not in {
            "reported",
            "unverified",
        }:
            raise DiscoveryError("prior_confidence must be reported or unverified")
        if changes:
            override_version = fingerprint(changes)[:12]
            overridden_fields = set(changes) & {
                "reasoning_efforts",
                "input_modalities",
                "supports_tools",
                "supports_parallel_tools",
                "context_window",
                "auth_mode",
                "available",
            }
            if overridden_fields:
                evidence = {item[0]: item for item in model.field_evidence}
                evidence.update(
                    {field: (field, "user-config", "reported") for field in overridden_fields}
                )
                changes["field_evidence"] = tuple(evidence[field] for field in sorted(evidence))
            changes.setdefault(
                "capability_version", f"{model.capability_version}+override:{override_version}"
            )
            if set(changes) & {
                "relative_quality",
                "relative_consumption",
                "relative_latency",
                "prior_family",
                "prior_strengths",
                "prior_weaknesses",
                "prior_source",
                "prior_confidence",
                "tie_break_priority",
            }:
                changes.setdefault("prior_version", f"user-override:{override_version}")
                changes.setdefault("prior_source", "user-config")
                changes.setdefault("prior_confidence", "reported")
        result.append(replace(model, **changes))
    return result


class AppServerDiscovery:
    """Small JSONL client. Never reads auth files or environment secrets."""

    def __init__(self, executable: str = "codex", timeout: float = 8.0):
        self.executable = executable
        self.timeout = timeout
        self.last_diagnostics = DiscoveryDiagnostics()

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
            initialized = self._response(output, 1)
            if "error" in initialized:
                raise DiscoveryError(
                    f"App Server initialize failed: {self._error_message(initialized)}"
                )
            initialize_result = initialized.get("result", {})
            if not isinstance(initialize_result, dict):
                raise DiscoveryError("App Server initialize returned malformed result")
            user_agent = initialize_result.get("userAgent")
            self.last_diagnostics = DiscoveryDiagnostics(
                protocol="v2-model-list",
                server_version=str(user_agent) if isinstance(user_agent, str) else None,
            )

            rows: list[Any] = []
            cursor: str | None = None
            seen_cursors: set[str] = set()
            pages = 0
            request_id = 2
            while True:
                params: dict[str, Any] = {"includeHidden": True}
                if cursor is not None:
                    params["cursor"] = cursor
                response = self._request(process, output, request_id, "model/list", params)
                if "error" in response:
                    raise DiscoveryError(
                        f"App Server model/list failed: {self._error_message(response)}"
                    )
                page = response.get("result")
                if not isinstance(page, dict) or not isinstance(page.get("data"), list):
                    raise DiscoveryError(
                        "App Server model/list schema changed: expected result.data array; "
                        "run `smart-route doctor` and update codex-smart-route"
                    )
                rows.extend(page["data"])
                pages += 1
                self.last_diagnostics = replace(self.last_diagnostics, pages=pages)
                next_cursor = page.get("nextCursor")
                if next_cursor is None:
                    break
                if not isinstance(next_cursor, str) or not next_cursor:
                    raise DiscoveryError(
                        "App Server model/list returned malformed nextCursor; "
                        "run `smart-route doctor` and update Codex"
                    )
                if next_cursor in seen_cursors:
                    raise DiscoveryError("App Server model/list pagination cursor loop detected")
                if pages >= 100:
                    raise DiscoveryError("App Server model/list exceeded 100 pagination pages")
                seen_cursors.add(next_cursor)
                cursor = next_cursor
                request_id += 1

            warnings: list[str] = []
            request_id += 1
            provider_data: dict[str, Any] | None = None
            try:
                provider_response = self._request(
                    process,
                    output,
                    request_id,
                    "modelProvider/capabilities/read",
                    {},
                )
            except queue.Empty:
                provider_status = "unavailable"
                warnings.append(
                    "provider capability discovery timed out; provider fields remain unknown"
                )
            else:
                if "error" in provider_response:
                    provider_status = "unavailable"
                    warnings.append(
                        "provider capability discovery unavailable: "
                        f"{self._error_message(provider_response)}"
                    )
                elif isinstance(provider_response.get("result"), dict):
                    provider_status = "available"
                    provider_data = provider_response["result"]
                else:
                    provider_status = "malformed"
                    warnings.append(
                        "provider capability response malformed; provider fields remain unknown"
                    )

            self.last_diagnostics = DiscoveryDiagnostics(
                protocol="v2-model-list",
                server_version=str(user_agent) if isinstance(user_agent, str) else None,
                pages=pages,
                provider_capabilities=provider_status,
                partial=bool(warnings),
                warnings=tuple(warnings),
            )
            return capabilities_from_app_server({"data": rows}, provider_data)
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
            try:
                message = json.loads(output.get(timeout=self.timeout))
            except json.JSONDecodeError as exc:
                raise DiscoveryError("App Server emitted malformed JSON") from exc
            if isinstance(message, dict) and message.get("id") == request_id:
                return message

    def _request(
        self,
        process: subprocess.Popen[str],
        output: queue.Queue[str],
        request_id: int,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        self._send(
            process,
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        )
        return self._response(output, request_id)

    @staticmethod
    def _error_message(response: dict[str, Any]) -> str:
        error = response.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str):
                return message
        return "unknown error"
