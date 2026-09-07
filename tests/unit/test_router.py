from __future__ import annotations

from dataclasses import replace

import pytest

from codex_smart_route.config import parse_config
from codex_smart_route.evaluation import evaluate_task
from codex_smart_route.models import ModelCapability, TaskContext
from codex_smart_route.router import Router, RoutingError


def route(config, catalog, task):
    profiles = tuple(profile for model in catalog for profile in model.profiles())
    return Router(config).route(task, profiles, evaluate_task(task))


def test_balanced_selects_pair(config, catalog):
    decision = route(config, catalog, TaskContext("Fix small typo"))
    assert decision.status == "selected"
    assert (
        decision.selected_profile
        == f"{decision.selected_model}@{decision.selected_reasoning_effort}"
    )


def test_short_but_coupled_debugging_routes_capable(config, catalog):
    decision = route(config, catalog, TaskContext("Investigate subtle regression across modules"))
    assert decision.selected_model == "local-capable"


def test_unavailable_hard_gate(config, catalog):
    catalog[0] = replace(catalog[0], available=False)
    decision = route(config, catalog, TaskContext("rename symbol"))
    assert "model-unavailable" in decision.excluded[next(iter(catalog[0].profiles())).id]


def test_reasoning_allowlist_hard_gate(catalog):
    config = parse_config({"allowed_reasoning": ["high"]})
    decision = route(config, catalog, TaskContext("investigate root cause"))
    assert "reasoning-not-allowed" in decision.hard_gates
    assert decision.selected_reasoning_effort == "high"


def test_image_gate(config, catalog):
    catalog[0] = replace(catalog[0], input_modalities=("text",))
    task = TaskContext("Inspect screenshot", required_modalities=frozenset({"text", "image"}))
    decision = route(config, catalog, task)
    assert "missing-input-modality" in decision.hard_gates


def test_tools_false_gate(config, catalog):
    catalog[:] = [replace(model, supports_tools=False) for model in catalog]
    decision = route(config, catalog, TaskContext("Use tools", tools_required=True))
    assert decision.status == "blocked"
    assert "tools-unsupported" in decision.hard_gates


def test_unknown_tools_strict_gate(catalog):
    catalog[:] = [replace(model, supports_tools=None) for model in catalog]
    config = parse_config({"unknown_capabilities": "exclude-required"})
    decision = route(config, catalog, TaskContext("Use tools", tools_required=True))
    assert decision.status == "blocked"
    assert "tools-unverified" in decision.hard_gates


def test_context_limit_gate(config, catalog):
    catalog[:] = [replace(model, context_window=10) for model in catalog]
    decision = route(config, catalog, TaskContext("Summarize", estimated_context_tokens=11))
    assert decision.status == "blocked"
    assert "context-limit" in decision.hard_gates


def test_authentication_gate(config, catalog):
    decision = route(config, catalog, TaskContext("Task", auth_modes=frozenset({"unavailable"})))
    assert decision.status == "blocked"
    assert "authentication-unavailable" in decision.hard_gates


def test_blocked_model_gate(catalog):
    config = parse_config({"blocked_models": [model.model for model in catalog]})
    assert route(config, catalog, TaskContext("Task")).status == "blocked"


def test_budget_is_hard_gate(config, catalog):
    decision = route(config, catalog, TaskContext("Task", max_relative_consumption=0.01))
    assert decision.status == "blocked"
    assert "budget-policy" in decision.hard_gates


def test_auto_profile_never_candidate(config, catalog):
    catalog.append(ModelCapability(config.auto_slug, "Auto", ("medium",), available=True))
    decision = route(config, catalog, TaskContext("Task"))
    assert "auto-recursion" in decision.excluded[f"{config.auto_slug}@medium"]


def test_manual_override_wins(config, catalog):
    profile = catalog[-1].profiles()[-1]
    decision = route(config, catalog, TaskContext("typo", manual_profile=profile.id))
    assert decision.selected_profile == profile.id
    assert decision.requested_reasoning_effort == profile.effort
    assert decision.reason == "explicit manual override"


def test_manual_override_cannot_bypass_gate(config, catalog):
    profile = catalog[0].profiles()[0]
    catalog[0] = replace(catalog[0], available=False)
    with pytest.raises(RoutingError, match="hard gate"):
        route(config, catalog, TaskContext("Task", manual_profile=profile.id))


def test_no_quality_floor_fallback(config, catalog):
    profiles = tuple(profile for model in catalog for profile in model.profiles())
    assessments = {profile.id: (0.1, 1.0) for profile in profiles}
    decision = Router(config).route(
        TaskContext("Task"), profiles, evaluate_task(TaskContext("Task")), assessments
    )
    assert decision.status == "blocked"
    assert "no implicit fallback" in decision.reason


def test_explicit_fallback_still_meets_quality(catalog):
    profile = catalog[0].profiles()[0]
    config = parse_config({"fallback_profile": profile.id})
    profiles = tuple(item for model in catalog for item in model.profiles())
    scores = {item.id: (0.1, 1.0) for item in profiles}
    assert (
        Router(config)
        .route(TaskContext("Task"), profiles, evaluate_task(TaskContext("Task")), scores)
        .status
        == "blocked"
    )


def test_hysteresis_preserves_current(config, catalog):
    profiles = tuple(profile for model in catalog for profile in model.profiles())
    current = profiles[-1]
    scores = {profile.id: (0.9, 0.9) for profile in profiles}
    decision = Router(replace(config, switch_hysteresis=1.0)).route(
        TaskContext("Task", current_profile=current.id),
        profiles,
        evaluate_task(TaskContext("Task")),
        scores,
    )
    assert decision.selected_profile == current.id
    assert decision.hysteresis_applied is True
    assert decision.previous_profile == current.id


def test_significant_failure_disables_hysteresis(config, catalog):
    profiles = tuple(profile for model in catalog for profile in model.profiles())
    current = profiles[-1]
    scores = {profile.id: (0.95 if profile == profiles[0] else 0.71, 0.9) for profile in profiles}
    decision = Router(config).route(
        TaskContext("Task", current_profile=current.id, significant_model_failure=True),
        profiles,
        evaluate_task(TaskContext("Task")),
        scores,
    )
    assert decision.selected_profile != current.id
    assert decision.hysteresis_applied is False
    assert decision.previous_profile == current.id


def test_environment_failure_does_not_raise_difficulty():
    base = evaluate_task(TaskContext("rename symbol"))
    failed = evaluate_task(TaskContext("rename symbol", environment_failure=True))
    assert failed == base


@pytest.mark.parametrize("policy", ["economy", "balanced", "quality"])
def test_built_in_policies_route(policy, catalog):
    config = parse_config({"active_policy": policy})
    assert route(
        config, catalog, TaskContext("Investigate subtle regression across modules")
    ).status in {"selected", "blocked"}


def test_scores_not_called_probabilities(config, catalog):
    decision = route(config, catalog, TaskContext("Task"))
    assert "probability" not in decision.reason
