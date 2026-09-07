from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

from codex_smart_route.audit import AuditLogger, redact
from codex_smart_route.models import Decision, TaskContext
from codex_smart_route.state import DecisionCache


def decision():
    return Decision(
        "selected",
        "model",
        "medium",
        "model@medium",
        "codex-smart-route",
        policy_name="balanced",
        policy_version="1",
    )


def test_cache_isolated_by_session(tmp_path):
    cache = DecisionCache(tmp_path / "cache.db", 60)
    first = TaskContext("same", session_id="one")
    second = TaskContext("same", session_id="two")
    assert cache.key(first, "1", "1", "1", "auto") != cache.key(second, "1", "1", "1", "auto")


def test_cache_changes_by_phase_and_versions(tmp_path):
    cache = DecisionCache(tmp_path / "cache.db", 60)
    task = TaskContext("same")
    keys = {
        cache.key(task, "1", "1", "1", "auto"),
        cache.key(TaskContext("same", phase_id="next"), "1", "1", "1", "auto"),
        cache.key(task, "2", "1", "1", "auto"),
        cache.key(task, "1", "2", "1", "auto"),
    }
    assert len(keys) == 4


def test_cache_round_trip(tmp_path):
    cache = DecisionCache(tmp_path / "cache.db", 60)
    key = "key"
    cache.put(key, decision())
    assert cache.get(key).cached is True


def test_cache_ttl(tmp_path):
    cache = DecisionCache(tmp_path / "cache.db", 1)
    cache.put("key", decision())
    with cache._connect() as db:
        db.execute("UPDATE decisions SET created = ?", (time.time() - 2,))
    assert cache.get("key") is None


def test_cache_concurrency(tmp_path):
    cache = DecisionCache(tmp_path / "cache.db", 60)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda index: cache.put(str(index), decision()), range(20)))
    assert cache.get("19") is not None


def test_redaction():
    assert redact({"Authorization": "Bearer secret", "safe": "ok"}) == {
        "Authorization": "[REDACTED]",
        "safe": "ok",
    }
    assert redact({"safe": "sk-abcdefghijk"})["safe"] == "[REDACTED]"


def test_audit_does_not_log_task_text(tmp_path):
    path = tmp_path / "audit.jsonl"
    task = TaskContext("private unique task body")
    AuditLogger(path).decision(task, decision())
    text = path.read_text()
    assert "private unique" not in text
    event = json.loads(text)
    assert event["confirmed"] == "unverified"
    assert "session_id" not in event and "task_id" not in event
