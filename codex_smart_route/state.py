"""SQLite decision cache and small runtime control state."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import CandidateScore, Decision, TaskContext, fingerprint


@dataclass(frozen=True)
class CacheIdentity:
    """Opaque cache key plus redacted evidence about inputs that matched."""

    key: str
    scope_key: str
    reuse_factors: tuple[str, ...]

    @classmethod
    def build(cls, task: TaskContext, routing_inputs: dict[str, Any]) -> CacheIdentity:
        scope_key = fingerprint(
            {
                "session": task.session_id,
                "task": task.task_id,
                "phase": task.phase_id,
            }
        )
        return cls(
            key=fingerprint(
                {
                    "identity_schema": 2,
                    "scope": scope_key,
                    "context": task.context_fingerprint,
                    "routing_inputs": routing_inputs,
                }
            ),
            scope_key=scope_key,
            reuse_factors=(
                "session/task/phase",
                "relevant-context fingerprint",
                "policy/weights/quality floor",
                "capability/prior/card versions",
                "availability and hard-gate constraints",
                "budget and profile/hysteresis inputs",
                "classifier identity",
                "adapter/routing mode",
            ),
        )

    @property
    def explanation(self) -> str:
        return "matching " + ", ".join(self.reuse_factors)


class DecisionCache:
    def __init__(self, path: Path, ttl_seconds: int):
        self.path = path
        self.ttl_seconds = ttl_seconds
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._database() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS decisions "
                "(cache_key TEXT PRIMARY KEY, scope_key TEXT NOT NULL DEFAULT '', "
                "created REAL NOT NULL, payload TEXT NOT NULL)"
            )
            columns = {str(row[1]) for row in db.execute("PRAGMA table_info(decisions)").fetchall()}
            if "scope_key" not in columns:
                try:
                    db.execute(
                        "ALTER TABLE decisions ADD COLUMN scope_key TEXT NOT NULL DEFAULT ''"
                    )
                except sqlite3.OperationalError:
                    refreshed = {
                        str(row[1]) for row in db.execute("PRAGMA table_info(decisions)").fetchall()
                    }
                    if "scope_key" not in refreshed:
                        raise

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
        return CacheIdentity.build(
            task,
            {
                "policy": policy_version,
                "capabilities": capability_version,
                "availability": availability,
                "mode": mode,
            },
        ).key

    def get(self, key: str, explanation: str | None = None) -> Decision | None:
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
                CandidateScore(
                    **{
                        **item,
                        "prior_strengths": tuple(item.get("prior_strengths", [])),
                        "prior_weaknesses": tuple(item.get("prior_weaknesses", [])),
                    }
                )
                for item in data.get("candidates", [])
            )
            data["excluded"] = {
                key: tuple(value) for key, value in data.get("excluded", {}).items()
            }
            data["hard_gates"] = tuple(data.get("hard_gates", []))
            data["cached"] = True
            if explanation:
                data["reason"] = f"cache hit: {explanation}; original: {data['reason']}"
            return Decision(**data)

    def put(self, key: str, decision: Decision, scope_key: str = "") -> None:
        payload = json.dumps(dataclasses.asdict(decision), separators=(",", ":"))
        with self._lock, self._database() as db:
            db.execute(
                "INSERT OR REPLACE INTO decisions(cache_key, scope_key, created, payload) "
                "VALUES (?, ?, ?, ?)",
                (key, scope_key, time.time(), payload),
            )

    def invalidate_scope(self, scope_key: str) -> None:
        with self._lock, self._database() as db:
            db.execute("DELETE FROM decisions WHERE scope_key = ?", (scope_key,))

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
                "policy": None,
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
