from __future__ import annotations

import math

import pytest

from codex_smart_route.config import ConfigError, Weights, parse_config


def test_default_balanced_weights():
    config = parse_config({})
    assert config.policy.weights == Weights(0.5, 0.3, 0.1, 0.1)


def test_weights_are_normalized():
    config = parse_config(
        {
            "policies": {
                "custom": {
                    "weights": {"consumption": 5, "quality_gap": 3, "latency": 1, "switch_cost": 1}
                }
            },
            "active_policy": "custom",
        }
    )
    assert math.isclose(sum(config.policy.weights.__dict__.values()), 1.0)


@pytest.mark.parametrize(
    "weights",
    [{"consumption": -1}, {"consumption": 0, "quality_gap": 0, "latency": 0, "switch_cost": 0}],
)
def test_invalid_weights_rejected(weights):
    with pytest.raises((ConfigError, TypeError)):
        parse_config({"policies": {"bad": {"weights": weights}}, "active_policy": "bad"})


def test_unknown_policy_rejected():
    with pytest.raises(ConfigError):
        parse_config({"active_policy": "missing"})


def test_remote_classifier_disabled_by_default():
    assert parse_config({}).classifier.mode == "disabled"


def test_auto_classifier_recursion_rejected():
    with pytest.raises(ConfigError, match="Auto model"):
        parse_config({"classifier": {"mode": "remote", "model": "codex-smart-route"}})


def test_custom_policy_supported():
    config = parse_config(
        {"active_policy": "mine", "policies": {"mine": {"minimum_quality": 0.55, "version": "7"}}}
    )
    assert config.policy.name == "mine"
    assert config.policy.version == "7"


def test_allowed_reasoning_validates_type():
    with pytest.raises(ConfigError):
        parse_config({"allowed_reasoning": "high"})
