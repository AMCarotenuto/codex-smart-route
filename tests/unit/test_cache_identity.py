from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import replace

import pytest

from codex_smart_route.config import parse_config
from codex_smart_route.models import TaskContext
from codex_smart_route.service import RoutingService
from codex_smart_route.state import DecisionCache


def _identity(service: RoutingService, task: TaskContext, classifier_used: bool = False) -> str:
    return service._cache_identity(task, classifier_used).key


@pytest.mark.parametrize(
    "change",
    [
        {"required_modalities": frozenset({"text", "image"})},
        {"tools_required": True},
        {"parallel_tools_required": True},
        {"estimated_context_tokens": 12_345},
        {"auth_modes": frozenset({"local"})},
        {"max_relative_consumption": 0.5},
        {"manual_profile": "local-capable@high"},
        {"current_profile": "local-capable@high"},
    ],
)
def test_every_task_constraint_changes_identity(config, catalog, tmp_path, change):
    service = RoutingService(config, catalog, tmp_path)
    task = TaskContext("same task")
    assert _identity(service, task) != _identity(service, replace(task, **change))


def test_policy_classifier_and_adapter_changes_invalidate(catalog, tmp_path):
    task = TaskContext("same task")
    base = RoutingService(parse_config({}), catalog, tmp_path / "base")
    changed_configs = [
        parse_config({"active_policy": "economy"}),
        parse_config({"adapter": "app-server"}),
        parse_config(
            {
                "policies": {
                    "balanced": {
                        "version": "2",
                        "minimum_quality": 0.71,
                        "weights": {
                            "consumption": 0.4,
                            "quality_gap": 0.4,
                            "latency": 0.1,
                            "switch_cost": 0.1,
                        },
                    }
                }
            }
        ),
    ]
    base_key = _identity(base, task)
    for index, config in enumerate(changed_configs):
        changed = RoutingService(config, catalog, tmp_path / str(index))
        assert _identity(changed, task) != base_key

    first_classifier = RoutingService(
        parse_config(
            {
                "classifier": {
                    "mode": "remote",
                    "model": "classifier",
                    "version": "v1",
                    "endpoint": "https://one.invalid",
                }
            }
        ),
        catalog,
        tmp_path / "classifier-one",
    )
    second_classifier = RoutingService(
        parse_config(
            {
                "classifier": {
                    "mode": "remote",
                    "model": "classifier",
                    "version": "v2",
                    "endpoint": "https://two.invalid",
                }
            }
        ),
        catalog,
        tmp_path / "classifier-two",
    )
    assert _identity(first_classifier, task, True) != _identity(second_classifier, task, True)


@pytest.mark.parametrize(
    "change",
    [
        {"available": False},
        {"capability_version": "new-capability"},
        {"prior_version": "new-prior"},
        {"relative_quality": 0.123},
        {"context_window": 999},
    ],
)
def test_catalog_availability_versions_and_cards_invalidate(config, catalog, tmp_path, change):
    task = TaskContext("same task")
    base = RoutingService(config, catalog, tmp_path / "base")
    changed_catalog = [replace(catalog[0], **change), *catalog[1:]]
    changed = RoutingService(config, changed_catalog, tmp_path / "changed")
    assert _identity(base, task) != _identity(changed, task)


def test_catalog_order_does_not_invalidate(config, catalog, tmp_path):
    task = TaskContext("same task")
    forward = RoutingService(config, catalog, tmp_path / "forward")
    reverse = RoutingService(config, list(reversed(catalog)), tmp_path / "reverse")
    assert _identity(forward, task) == _identity(reverse, task)


def test_identical_continuation_reuses_with_redacted_explanation(config, catalog, tmp_path):
    task_text = "private task sk-abcdefghijk"
    context_text = "private context Bearer abcdefghijk"
    service = RoutingService(config, catalog, tmp_path)
    first = service.route(
        TaskContext(task_text, session_id="s", task_id="t", relevant_context=context_text)
    )
    cached = service.route(
        TaskContext(
            task_text,
            session_id="s",
            task_id="t",
            event="continuation",
            relevant_context=context_text,
        )
    )
    assert first.cached is False
    assert cached.cached is True
    assert "matching session/task/phase" in cached.reason
    assert "hard-gate constraints" in cached.reason
    assert task_text not in cached.reason

    database = sqlite3.connect(tmp_path / "cache.sqlite3")
    try:
        key, scope_key, payload = database.execute(
            "SELECT cache_key, scope_key, payload FROM decisions"
        ).fetchone()
    finally:
        database.close()
    serialized = json.dumps([key, scope_key, payload])
    assert task_text not in serialized
    assert context_text not in serialized
    assert "sk-abcdefghijk" not in serialized

    events = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert events[-1]["reason"].startswith("cache hit:")
    assert task_text not in json.dumps(events)
    assert context_text not in json.dumps(events)


@pytest.mark.parametrize(
    "change",
    [
        {"estimated_context_tokens": 65_000},
        {"auth_modes": frozenset({"unavailable"})},
        {"max_relative_consumption": 0.01},
        {"required_modalities": frozenset({"text", "audio"})},
        {"tools_required": True},
        {"parallel_tools_required": True},
    ],
)
def test_changed_hard_gate_or_budget_never_hits_cache(config, catalog, tmp_path, change):
    service = RoutingService(config, catalog, tmp_path)
    task = TaskContext("same task", session_id="s", task_id="t")
    service.route(task)
    decision = service.route(replace(task, event="continuation", **change))
    assert decision.cached is False


def test_identical_prompts_in_different_sessions_do_not_share(config, catalog, tmp_path):
    service = RoutingService(config, catalog, tmp_path)
    service.route(TaskContext("same task", session_id="one"))
    second = service.route(TaskContext("same task", session_id="two", event="tool_loop"))
    assert second.cached is False


def test_manual_override_and_removal_take_effect_immediately(config, catalog, tmp_path):
    service = RoutingService(config, catalog, tmp_path)
    task = TaskContext("same task", session_id="s", task_id="t")
    automatic = service.route(task)
    manual_profile = next(
        profile.id for profile in service.profiles if profile.id != automatic.selected_profile
    )
    manual = service.route(replace(task, event="continuation", manual_profile=manual_profile))
    removed = service.route(replace(task, event="continuation"))
    assert manual.cached is False
    assert manual.selected_profile == manual_profile
    assert removed.cached is True
    assert removed.selected_profile == automatic.selected_profile


@pytest.mark.parametrize("signal", ["force_reevaluation", "significant_model_failure"])
def test_reevaluation_signals_invalidate_scope_and_are_not_cached(
    config, catalog, tmp_path, signal
):
    service = RoutingService(config, catalog, tmp_path)
    task = TaskContext("same task", session_id="s", task_id="t")
    service.route(task)
    bypass = service.route(replace(task, event="continuation", **{signal: True}))
    following = service.route(replace(task, event="continuation"))
    assert bypass.cached is False
    assert following.cached is False


def test_environment_failure_reuses_without_escalation(config, catalog, tmp_path):
    service = RoutingService(config, catalog, tmp_path)
    task = TaskContext("rename symbol", session_id="s", task_id="t")
    first = service.route(task)
    failed = service.route(replace(task, event="tool_loop", environment_failure=True))
    assert failed.cached is True
    assert failed.selected_profile == first.selected_profile


def test_availability_change_cannot_reuse_incompatible_profile(config, catalog, tmp_path):
    task = TaskContext("same task", session_id="s", task_id="t")
    first_service = RoutingService(config, catalog, tmp_path)
    first = first_service.route(task)
    changed_catalog = [
        replace(model, available=model.model != first.selected_model) for model in catalog
    ]
    changed_service = RoutingService(config, changed_catalog, tmp_path)
    changed = changed_service.route(replace(task, event="continuation"))
    assert changed.cached is False
    assert changed.selected_model != first.selected_model


def test_ttl_expiry_forces_reassessment(config, catalog, tmp_path):
    config = replace(config, cache_ttl_seconds=1)
    service = RoutingService(config, catalog, tmp_path)
    task = TaskContext("same task", session_id="s", task_id="t")
    service.route(task)
    assert service.cache is not None
    with service.cache._database() as database:
        database.execute("UPDATE decisions SET created = ?", (time.time() - 2,))
    expired = service.route(replace(task, event="continuation"))
    assert expired.cached is False


def test_legacy_database_schema_is_migrated(tmp_path):
    path = tmp_path / "cache.sqlite3"
    database = sqlite3.connect(path)
    try:
        database.execute(
            "CREATE TABLE decisions "
            "(cache_key TEXT PRIMARY KEY, created REAL NOT NULL, payload TEXT NOT NULL)"
        )
        database.commit()
    finally:
        database.close()
    DecisionCache(path, 60)
    database = sqlite3.connect(path)
    try:
        columns = {row[1] for row in database.execute("PRAGMA table_info(decisions)")}
    finally:
        database.close()
    assert "scope_key" in columns
