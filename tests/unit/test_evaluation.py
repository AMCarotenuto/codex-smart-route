from __future__ import annotations

import pytest

from codex_smart_route.evaluation import (
    LanguagePack,
    evaluate_task,
    normalize_text,
    parse_routing_tags,
    strip_routing_tags,
)
from codex_smart_route.models import TaskContext
from codex_smart_route.router import Router


def test_equivalent_english_and_italian_tasks_have_comparable_profiles():
    english = evaluate_task(
        TaskContext("Investigate the root cause of a security regression across multiple files")
    )
    italian = evaluate_task(
        TaskContext("Indaga la causa principale di una regressione di sicurezza su più file")
    )
    compared = (
        "reasoning_complexity",
        "debugging",
        "cross_cutting",
        "consequence",
        "verification_difficulty",
    )
    for name in compared:
        assert getattr(english, name) == pytest.approx(getattr(italian, name), abs=0.25)


def test_short_italian_authentication_task_is_not_trivial():
    signals = evaluate_task(TaskContext("Correggi l'autenticazione: errore di sicurezza"))
    assert signals.consequence >= 1.0
    assert signals.debugging >= 0.5
    assert signals.difficulty >= 0.5


def test_accents_case_and_punctuation_are_normalized():
    assert normalize_text("PIÙ, ambiguità!") == "piu ambiguita"
    signals = evaluate_task(TaskContext("È un'attività con AMBIGUITÀ; su più file."))
    assert signals.ambiguity >= 0.5
    assert signals.cross_cutting >= 0.5


@pytest.mark.parametrize("text", ["illegal value", "sortilege", "contest", "bugle", "designerly"])
def test_terms_are_not_matched_as_substrings(text):
    signals = evaluate_task(TaskContext(text))
    assert signals.debugging == 0
    assert signals.consequence == 0
    assert signals.mechanical == 0
    assert signals.creative == 0


def test_mixed_language_prompt_combines_signals():
    signals = evaluate_task(TaskContext("Debug the regression e verifica il protocollo"))
    assert signals.debugging == 1.0
    assert signals.coupling >= 0.5
    assert signals.acceptance_criteria >= 0.5


def test_explicit_tags_enrich_signals_deterministically():
    signals = evaluate_task(
        TaskContext("Do the task [cross-cutting] [HIGH CONSEQUENCE] [strict-verification]")
    )
    assert signals.cross_cutting == 1.0
    assert signals.coupling == 1.0
    assert signals.consequence == 1.0
    assert signals.verification_difficulty == 1.0
    assert parse_routing_tags("[debugging] [not-a-routing-tag]") == frozenset({"debugging"})


def test_structured_tags_do_not_require_prompt_markup():
    signals = evaluate_task(TaskContext("Do it", routing_tags=frozenset({"creative"})))
    assert signals.creative == 1.0


def test_explicit_tags_do_not_bypass_capability_gates(config, catalog):
    profiles = tuple(profile for model in catalog for profile in model.profiles())
    task = TaskContext(
        "Handle it [high-consequence]", required_modalities=frozenset({"text", "image"})
    )
    decision = Router(config).route(task, profiles, evaluate_task(task))
    assert "missing-input-modality" in decision.hard_gates


def test_tags_remain_downstream_unless_explicitly_removed():
    text = "Fix it [debugging] and retain [project-note]."
    assert strip_routing_tags(text) == text
    stripped = strip_routing_tags(text, enabled=True)
    assert "[debugging]" not in stripped
    assert "[project-note]" in stripped


def test_additional_language_pack_is_extensible():
    spanish = LanguagePack("es", {"debugging": ("depurar",)})
    signals = evaluate_task(TaskContext("depurar"), language_packs=(spanish,))
    assert signals.debugging == 0.5


def test_prompt_length_does_not_change_difficulty():
    short = evaluate_task(TaskContext("ordinary task"))
    long = evaluate_task(TaskContext("ordinary task " + "filler " * 10_000))
    assert long.difficulty == short.difficulty
