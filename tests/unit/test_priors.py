from __future__ import annotations

import json
from dataclasses import replace

import pytest

from codex_smart_route.cli import main
from codex_smart_route.config import parse_config
from codex_smart_route.discovery import DiscoveryError, apply_overrides
from codex_smart_route.evaluation import evaluate_task, local_scores
from codex_smart_route.models import ModelCapability, TaskContext, fingerprint
from codex_smart_route.priors import BUNDLED_PRIORS, PriorRegistry, apply_priors
from codex_smart_route.router import Router
from codex_smart_route.service import RoutingService


def _model(name: str, **changes: object) -> ModelCapability:
    values: dict[str, object] = {
        "model": name,
        "display_name": name,
        "reasoning_efforts": ("medium",),
        "available": True,
        "capability_version": "catalog-1",
    }
    values.update(changes)
    return ModelCapability(**values)  # type: ignore[arg-type]


def test_known_families_get_distinct_versioned_operational_priors():
    astra, luna = apply_priors([_model("gpt-6-astra"), _model("gpt-5.6-luna")])
    assert astra.relative_quality != luna.relative_quality
    assert astra.relative_consumption != luna.relative_consumption
    assert astra.relative_latency != luna.relative_latency
    assert astra.prior_version == BUNDLED_PRIORS.version
    assert astra.prior_confidence == "unverified"
    assert astra.prior_strengths and astra.prior_weaknesses


def test_unknown_future_family_remains_routable_and_explicitly_unverified():
    model = apply_priors([_model("gpt-99-future")])[0]
    task = TaskContext("routine task")
    decision = Router(parse_config({})).route(
        task,
        model.profiles(),
        evaluate_task(task),
        local_scores(evaluate_task(task), model.profiles()),
    )
    assert model.prior_family == "unknown"
    assert model.prior_version == f"{BUNDLED_PRIORS.version}:unknown"
    assert model.relative_quality is None
    assert decision.status == "selected"
    assert decision.verified is False


def test_repository_values_then_user_override_take_precedence_without_mutation():
    discovered = _model(
        "gpt-5.6-sol",
        relative_quality=0.31,
        prior_source="repository-catalog",
        prior_confidence="reported",
        prior_version="repo-4",
    )
    enriched = apply_priors([discovered])
    config = parse_config({"capability_overrides": {"gpt-5.6-sol": {"relative_quality": 0.47}}})
    overridden = apply_overrides(enriched, config)[0]
    assert discovered.relative_quality == 0.31
    assert enriched[0].relative_quality == 0.31
    assert overridden.relative_quality == 0.47
    assert overridden.prior_source == "user-config"
    assert overridden.prior_version.startswith("user-override:")


def test_partial_repository_prior_discloses_mixed_unverified_source():
    model = apply_priors([_model("gpt-5.6-sol", relative_quality=0.42, source="file:repo.json")])[0]
    assert model.relative_quality == 0.42
    assert model.relative_consumption is not None
    assert model.prior_source == "file:repo.json+bundled-operational-prior"
    assert model.prior_confidence == "unverified"


def test_user_override_cannot_self_mark_prior_verified():
    config = parse_config(
        {"capability_overrides": {"gpt-5.6-sol": {"prior_confidence": "verified"}}}
    )
    with pytest.raises(DiscoveryError, match="cannot mark prior data verified"):
        apply_overrides(apply_priors([_model("gpt-5.6-sol")]), config)


def test_registry_resolution_is_specific_and_order_independent():
    model = _model("gpt-6-astra")
    forward = BUNDLED_PRIORS.resolve(model.model)
    reverse = PriorRegistry(BUNDLED_PRIORS.version, tuple(reversed(BUNDLED_PRIORS.priors))).resolve(
        model.model
    )
    assert forward == reverse


def test_tie_break_uses_priority_then_hash_not_alphabetical_order():
    high = _model(
        "z",
        relative_quality=0.8,
        relative_consumption=0.4,
        relative_latency=0.4,
        prior_version="test-1",
        tie_break_priority=10,
    )
    low = replace(high, model="a", display_name="a", tie_break_priority=1)
    task = TaskContext("routine task")
    signals = evaluate_task(task)
    assessments = {profile.id: (0.9, 0.7) for model in (high, low) for profile in model.profiles()}
    for catalog in ((low, high), (high, low)):
        profiles = tuple(profile for model in catalog for profile in model.profiles())
        decision = Router(parse_config({})).route(task, profiles, signals, assessments)
        assert decision.selected_model == "z"
        assert decision.candidates[0].tie_break_priority == 10

    no_priority = (replace(low, tie_break_priority=0), replace(high, tie_break_priority=0))
    profiles = tuple(profile for model in no_priority for profile in model.profiles())
    assessments = {profile.id: (0.9, 0.7) for profile in profiles}
    decision = Router(parse_config({})).route(task, profiles, signals, assessments)
    expected = min((profile.id for profile in profiles), key=fingerprint)
    assert decision.selected_profile == expected
    assert expected != min(profile.id for profile in profiles)


def test_prior_version_invalidates_service_cache(tmp_path):
    task = TaskContext("routine task", session_id="s", task_id="t")
    first_model = _model("future", prior_version="prior-1")
    first = RoutingService(parse_config({}), [first_model], tmp_path)
    first.route(task)
    changed = RoutingService(
        parse_config({}), [replace(first_model, prior_version="prior-2")], tmp_path
    )
    decision = changed.route(replace(task, event="tool_loop"))
    assert decision.cached is False


def test_cli_models_and_profiles_show_prior_cards(tmp_path, capsys):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps([{"model": "gpt-5.6-terra", "reasoning_efforts": ["medium"]}]),
        encoding="utf-8",
    )
    assert main(["models", "--catalog", str(catalog), "--json"]) == 0
    model = json.loads(capsys.readouterr().out)[0]
    assert model["prior_family"] == "terra"
    assert model["prior_version"] == BUNDLED_PRIORS.version
    assert model["prior_strengths"]
    assert main(["profiles", "--catalog", str(catalog), "--json"]) == 0
    profile = json.loads(capsys.readouterr().out)[0]
    assert profile["prior_source"] == "bundled-operational-prior"


def test_decision_explanation_contains_capability_card():
    model = apply_priors([_model("gpt-5.6-sol")])[0]
    task = TaskContext("debug regression across modules")
    decision = Router(parse_config({})).route(task, model.profiles(), evaluate_task(task))
    candidate = decision.candidates[0]
    assert candidate.prior_family == "sol"
    assert candidate.prior_confidence == "unverified"
    assert candidate.prior_version == BUNDLED_PRIORS.version
    assert candidate.prior_strengths and candidate.prior_weaknesses
