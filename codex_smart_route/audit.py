"""Versioned, bounded, privacy-preserving routing audit events."""

from __future__ import annotations

import dataclasses
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from .models import Decision, TaskContext, fingerprint

AUDIT_SCHEMA_VERSION = "1.0"
SECRET_KEY = re.compile(
    r"(?i)(authorization|api[_-]?key|access[_-]?key|private[_-]?key|"
    r"credential|client[_-]?secret|token|cookie|password|secret)"
)
SECRET_VALUE = re.compile(
    r"(?i)(bearer\s+[^\s,;]+|sk-[a-z0-9_-]{8,}|gh[opusr]_[a-z0-9]{8,}|"
    r"-----begin [^-]*(?:private|secret)[^-]*-----|"
    r"(?:api[_-]?key|token|password|cookie|secret)\s*[:=]\s*[^\s,;]+|"
    r"eyj[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,})"
)
SENSITIVE_CONTENT_KEYS = frozenset(
    {
        "args",
        "arguments",
        "argv",
        "body",
        "command",
        "command_line",
        "commands",
        "content",
        "input",
        "model_output",
        "output",
        "prompt",
        "raw_task",
        "shell_command",
        "stderr",
        "stdin",
        "stdout",
        "task",
        "task_text",
    }
)

# Compatibility aliases retained for 0.x callers.
SECRET = SECRET_KEY


def redact(value: Any) -> Any:
    """Recursively remove secret-shaped and raw-content values."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            normalized_key = text_key.lower().replace("-", "_")
            if SECRET_KEY.search(text_key) or normalized_key in SENSITIVE_CONTENT_KEYS:
                result[text_key] = "[REDACTED]"
            else:
                result[text_key] = redact(item)
        return result
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [redact(item) for item in sorted(value, key=repr)]
    if isinstance(value, str) and (SECRET_KEY.search(value) or SECRET_VALUE.search(value)):
        return "[REDACTED]"
    return value


def _pair(model: str | None, effort: str | None, profile: str | None = None) -> dict[str, Any]:
    pair: dict[str, Any] = {
        "model": model or "unverified",
        "reasoning_effort": effort or "unverified",
    }
    if profile is not None:
        pair["profile"] = profile
    return pair


def decision_event(task: TaskContext, decision: Decision) -> dict[str, Any]:
    """Build stable schema without including task, context, failure, or command text."""
    candidates = [dataclasses.asdict(candidate) for candidate in decision.candidates]
    exclusions = [
        {"profile": profile, "hard_gate_reasons": list(reasons)}
        for profile, reasons in sorted(decision.excluded.items())
    ]
    evidence = {
        **decision.routing_evidence,
        "hysteresis": {
            "applied": decision.hysteresis_applied,
            "previous_profile": decision.previous_profile,
            **decision.routing_evidence.get("hysteresis", {}),
        },
    }
    event = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "event_type": "routing.decision",
        "timestamp": time.time(),
        "session_hash": fingerprint(task.session_id)[:16],
        "task_hash": fingerprint(task.task_id)[:16],
        "phase_hash": fingerprint(task.phase_id)[:16],
        "context_fingerprint": task.context_fingerprint,
        "status": decision.status,
        # 0.x compatibility fields.
        "selected": decision.selected_profile,
        "requested": decision.requested_model,
        "forwarded": decision.forwarded_model,
        "confirmed": decision.confirmed_model or "unverified",
        "reason": decision.reason,
        "cached": decision.cached,
        "policy": decision.policy_name,
        "policy_version": decision.policy_version,
        "verified": decision.verified,
        # Complete structured evidence.
        "models": {
            "selected": _pair(
                decision.selected_model,
                decision.selected_reasoning_effort,
                decision.selected_profile,
            ),
            "requested": _pair(
                decision.requested_model,
                decision.requested_reasoning_effort,
            ),
            "forwarded": _pair(
                decision.forwarded_model,
                decision.forwarded_reasoning_effort,
            ),
            "confirmed": _pair(
                decision.confirmed_model,
                decision.confirmed_reasoning_effort,
            ),
        },
        "candidates": candidates,
        "excluded": exclusions,
        "hard_gates": list(decision.hard_gates),
        "decision": {
            "reason": decision.reason,
            "confidence": decision.confidence,
            "verified": decision.verified,
        },
        "evidence": evidence,
    }
    clean = redact(event)
    assert isinstance(clean, dict)
    return clean


class AuditLogger:
    def __init__(
        self,
        path: Path,
        enabled: bool = True,
        max_bytes: int = 5_000_000,
        backup_count: int = 3,
    ):
        if max_bytes < 1:
            raise ValueError("audit max_bytes must be positive")
        if backup_count < 0:
            raise ValueError("audit backup_count must be non-negative")
        self.path = path
        self.enabled = enabled
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self._lock = threading.RLock()

    def decision(self, task: TaskContext, decision: Decision) -> None:
        if not self.enabled:
            return
        line = json.dumps(decision_event(task, decision), separators=(",", ":")) + "\n"
        encoded_size = len(line.encode("utf-8"))
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            current_size = self.path.stat().st_size if self.path.exists() else 0
            if current_size and current_size + encoded_size > self.max_bytes:
                self._rotate()
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)

    def _rotate(self) -> None:
        if self.backup_count == 0:
            self.path.unlink(missing_ok=True)
            return
        oldest = self.path.with_name(f"{self.path.name}.{self.backup_count}")
        oldest.unlink(missing_ok=True)
        for index in range(self.backup_count - 1, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{index}")
            if source.exists():
                source.replace(self.path.with_name(f"{self.path.name}.{index + 1}"))
        self.path.replace(self.path.with_name(f"{self.path.name}.1"))


def latest_event(path: Path) -> dict[str, Any] | None:
    """Return newest valid audit object, tolerating an interrupted trailing write."""
    if not path.exists():
        return None
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            return event
    return None


def format_explanation(event: dict[str, Any], alternative_limit: int = 3) -> str:
    """Render concise answer to why model and effort were selected."""
    models = event.get("models", {})
    selected = models.get("selected", {})
    requested = models.get("requested", {})
    forwarded = models.get("forwarded", {})
    confirmed = models.get("confirmed", {})
    evidence = event.get("evidence", {})
    cache = evidence.get("cache", {})
    hysteresis = evidence.get("hysteresis", {})
    policy = evidence.get("policy", {})
    lines = [
        f"Decision: {event.get('status', 'unknown')}",
        (
            "Winner: "
            f"{selected.get('profile') or event.get('selected') or 'none'} "
            f"(model={selected.get('model', 'unverified')}, "
            f"effort={selected.get('reasoning_effort', 'unverified')})"
        ),
        f"Why: {event.get('reason', 'unavailable')}",
        (
            "Requested / forwarded / confirmed: "
            f"{requested.get('model', event.get('requested', 'unverified'))}"
            f"@{requested.get('reasoning_effort', 'unverified')} / "
            f"{forwarded.get('model', event.get('forwarded') or 'unverified')}"
            f"@{forwarded.get('reasoning_effort', 'unverified')} / "
            f"{confirmed.get('model', event.get('confirmed', 'unverified'))}"
            f"@{confirmed.get('reasoning_effort', 'unverified')}"
        ),
    ]
    candidates = event.get("candidates", [])
    winner_profile = selected.get("profile") or event.get("selected")
    alternatives = [item for item in candidates if item.get("profile_id") != winner_profile][
        :alternative_limit
    ]
    if alternatives:
        lines.append("Nearest viable alternatives:")
        lines.extend(
            "  - "
            f"{item.get('profile_id')}: suitability={item.get('suitability'):.3f}, "
            f"confidence={item.get('confidence'):.3f}, penalty={item.get('penalty'):.3f}"
            for item in alternatives
        )
    else:
        lines.append("Nearest viable alternatives: none")
    exclusions = event.get("excluded", [])
    if exclusions:
        lines.append("Excluded:")
        lines.extend(
            f"  - {item.get('profile')}: {', '.join(item.get('hard_gate_reasons', []))}"
            for item in exclusions
        )
    else:
        lines.append("Excluded: none")
    lines.extend(
        [
            (
                "Cache: "
                f"{cache.get('status', 'unknown')}"
                f" (identity={cache.get('identity_prefix', 'unavailable')})"
            ),
            (
                "Hysteresis: "
                f"{'kept previous profile' if hysteresis.get('applied') else 'not applied'}"
                f" (previous={hysteresis.get('previous_profile') or 'none'})"
            ),
            (
                "Reevaluation trigger: "
                + (
                    ", ".join(evidence.get("reevaluation_triggers", []))
                    or evidence.get("reevaluation_trigger")
                    or "none"
                )
            ),
            (
                "Policy: "
                f"{policy.get('name', event.get('policy', 'unknown'))}"
                f" v{policy.get('version', event.get('policy_version', 'unknown'))}, "
                f"minimum_quality={policy.get('minimum_quality', 'unknown')}, "
                f"weights={json.dumps(policy.get('weights', {}), sort_keys=True)}"
            ),
        ]
    )
    return "\n".join(lines)
