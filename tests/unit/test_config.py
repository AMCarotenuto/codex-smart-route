from __future__ import annotations

import math

import pytest

from codex_smart_route.config import ConfigError, Weights, parse_config, resolve_config


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


def test_layered_config_merges_user_repo_and_environment(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        """
active_policy = "economy"
allowed_models = ["user-model"]
[policies.team]
minimum_quality = 0.5
[policies.team.weights]
consumption = 4
quality_gap = 3
latency = 2
switch_cost = 1
[capability_overrides.user-model]
relative_quality = 0.4
relative_latency = 0.8
""",
        encoding="utf-8",
    )
    repo = tmp_path / "repo"
    nested = repo / "src" / "package"
    nested.mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / ".codex-smart-route.toml").write_text(
        """
active_policy = "team"
allowed_models = ["repo-model"]
[policies.team]
minimum_quality = 0.75
[capability_overrides.user-model]
relative_quality = 0.9
""",
        encoding="utf-8",
    )

    resolved = resolve_config(
        working_directory=nested,
        home=home,
        environ={"CODEX_SMART_ROUTE_ACTIVE_POLICY": "quality"},
    )

    assert resolved.config.active_policy == "quality"
    assert resolved.config.allowed_models == frozenset({"repo-model"})
    assert resolved.config.policies["team"].minimum_quality == 0.75
    assert resolved.config.policies["team"].weights == Weights(0.4, 0.3, 0.2, 0.1)
    assert resolved.config.capability_overrides["user-model"] == {
        "relative_quality": 0.9,
        "relative_latency": 0.8,
    }
    assert [source.kind for source in resolved.sources] == [
        "built-in",
        "user",
        "repository",
        "environment",
    ]
    assert resolved.project_root == repo
    assert resolved.project_id
    assert resolved.runtime_home == home / "projects" / resolved.project_id


def test_nearest_repository_config_wins(tmp_path):
    repo = tmp_path / "repo"
    nested = repo / "packages" / "one"
    nested.mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / ".codex-smart-route.toml").write_text('active_policy = "economy"')
    (repo / "packages" / ".codex-smart-route.toml").write_text('active_policy = "quality"')

    resolved = resolve_config(working_directory=nested, home=tmp_path / "home", environ={})

    assert resolved.config.active_policy == "quality"
    assert resolved.sources[-1].path == str(
        (repo / "packages" / ".codex-smart-route.toml").resolve()
    )


def test_explicit_config_is_single_file_override(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text('active_policy = "economy"')
    explicit = tmp_path / "automation.toml"
    explicit.write_text('active_policy = "quality"')
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".codex-smart-route.toml").write_text('active_policy = "balanced"')

    resolved = resolve_config(
        working_directory=repo,
        config_file=explicit,
        home=home,
        environ={"CODEX_SMART_ROUTE_ACTIVE_POLICY": "economy"},
    )

    assert resolved.config.active_policy == "quality"
    assert [source.kind for source in resolved.sources] == ["built-in", "explicit"]


def test_catalog_path_is_relative_to_owning_config(tmp_path):
    repo = tmp_path / "repo"
    nested = repo / "nested"
    nested.mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / ".codex-smart-route.toml").write_text(
        '[catalog]\nstrategy = "file"\npath = "data/models.json"\n'
    )

    resolved = resolve_config(working_directory=nested, home=tmp_path / "home", environ={})

    assert resolved.config.catalog_path == str((repo / "data" / "models.json").resolve())


def test_file_catalog_requires_path():
    with pytest.raises(ConfigError, match="catalog.path"):
        parse_config({"catalog": {"strategy": "file"}})


def test_audit_retention_configuration_and_validation():
    config = parse_config({"logging": {"max_bytes": 1234, "backup_count": 0}})
    assert config.log_max_bytes == 1234
    assert config.log_backup_count == 0
    with pytest.raises(ConfigError, match="logging.max_bytes"):
        parse_config({"logging": {"max_bytes": 0}})
    with pytest.raises(ConfigError, match="logging.backup_count"):
        parse_config({"logging": {"backup_count": -1}})


def test_two_repositories_have_independent_policy_and_runtime_namespaces(tmp_path):
    home = tmp_path / "home"
    first = tmp_path / "first"
    second = tmp_path / "second"
    for repo, policy in ((first, "economy"), (second, "quality")):
        repo.mkdir()
        (repo / ".git").mkdir()
        (repo / ".codex-smart-route.toml").write_text(
            f'active_policy = "{policy}"', encoding="utf-8"
        )

    first_config = resolve_config(working_directory=first, home=home, environ={})
    second_config = resolve_config(working_directory=second, home=home, environ={})

    assert first_config.config.active_policy == "economy"
    assert second_config.config.active_policy == "quality"
    assert first_config.project_id != second_config.project_id
    assert first_config.runtime_home != second_config.runtime_home


def test_environment_catalog_path_is_relative_to_working_directory(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    resolved = resolve_config(
        working_directory=repo,
        home=tmp_path / "home",
        environ={
            "CODEX_SMART_ROUTE_CATALOG_STRATEGY": "file",
            "CODEX_SMART_ROUTE_CATALOG_PATH": "local/models.json",
        },
    )

    assert resolved.config.catalog_path == str((repo / "local" / "models.json").resolve())
