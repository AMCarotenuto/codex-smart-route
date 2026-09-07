"""SQLite decision cache and small runtime control state."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .models import CandidateScore, Decision, TaskContext, fingerprint


class DecisionCache:
    def __init__(self, path: Path, ttl_seconds: int):
        self.path = path
        self.ttl_seconds = ttl_seconds
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._database() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS decisions "
                "(cache_key TEXT PRIMARY KEY, created REAL NOT NULL, payload TEXT NOT NULL)"
            )

    @contextmanager
    def _database(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def key(
        task: TaskContext,
        policy_version: str,
        capability_version: str,
        availability: str,
        mode: str,
    ) -> str:
        return fingerprint(
            {
                "session": task.session_id,
                "task": task.task_id,
                "phase": task.phase_id,
                "context": task.context_fingerprint,
                "policy": policy_version,
                "capabilities": capability_version,
                "availability": availability,
                "mode": mode,
            }
        )

    def get(self, key: str) -> Decision | None:
        with self._lock, self._database() as db:
            row = db.execute(
                "SELECT created, payload FROM decisions WHERE cache_key = ?", (key,)
            ).fetchone()
            if not row:
                return None
            if time.time() - float(row[0]) > self.ttl_seconds:
                db.execute("DELETE FROM decisions WHERE cache_key = ?", (key,))
                return None
            data = json.loads(row[1])
            data["candidates"] = tuple(
                CandidateScore(**item) for item in data.get("candidates", [])
            )
            data["excluded"] = {
                key: tuple(value) for key, value in data.get("excluded", {}).items()
            }
            data["hard_gates"] = tuple(data.get("hard_gates", []))
            data["cached"] = True
            return Decision(**data)

    def put(self, key: str, decision: Decision) -> None:
        payload = json.dumps(dataclasses.asdict(decision), separators=(",", ":"))
        with self._lock, self._database() as db:
            db.execute(
                "INSERT OR REPLACE INTO decisions(cache_key, created, payload) VALUES (?, ?, ?)",
                (key, time.time(), payload),
            )

    def clear(self) -> None:
        with self._lock, self._database() as db:
            db.execute("DELETE FROM decisions")


class RuntimeState:
    def __init__(self, path: Path):
        self.path = path

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "enabled": True,
                "policy": "balanced",
                "manual_override": None,
                "reevaluate_next": False,
            }
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("runtime state must be a JSON object")
        return value

    def update(self, **changes: Any) -> dict[str, Any]:
        data = {**self.read(), **changes}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)
        return data
