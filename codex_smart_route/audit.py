"""Redacted JSONL audit events. Task text and credentials are never logged."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .models import Decision, TaskContext, fingerprint

SECRET = re.compile(r"(?i)(authorization|api[_-]?key|token|cookie|password)")
SECRET_VALUE = re.compile(
    r"(?i)(bearer\s+[a-z0-9._~+/=-]{8,}|sk-[a-z0-9_-]{8,}|gh[opusr]_[a-z0-9]{8,})"
)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if SECRET.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str) and (SECRET.search(value) or SECRET_VALUE.search(value)):
        return "[REDACTED]"
    return value


class AuditLogger:
    def __init__(self, path: Path, enabled: bool = True):
        self.path = path
        self.enabled = enabled

    def decision(self, task: TaskContext, decision: Decision) -> None:
        if not self.enabled:
            return
        event = redact(
            {
                "timestamp": time.time(),
                "session_hash": fingerprint(task.session_id)[:16],
                "task_hash": fingerprint(task.task_id)[:16],
                "phase_id": task.phase_id,
                "context_fingerprint": task.context_fingerprint,
                "status": decision.status,
                "selected": decision.selected_profile,
                "requested": decision.requested_model,
                "forwarded": decision.forwarded_model,
                "confirmed": decision.confirmed_model or "unverified",
                "reason": decision.reason,
                "policy": decision.policy_name,
                "policy_version": decision.policy_version,
                "verified": decision.verified,
            }
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, separators=(",", ":")) + "\n")
