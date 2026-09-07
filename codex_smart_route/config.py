"""Versioned TOML configuration with no secret fields."""

from __future__ import annotations

import dataclasses
import hashlib
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Weights:
    consumption: float
    quality_gap: float
    latency: float
    switch_cost: float

    def normalized(self) -> Weights:
        values = (self.consumption, self.quality_gap, self.latency, self.switch_cost)
        if any(value < 0 for value in values) or sum(values) <= 0:
            raise ConfigError("policy weights must be non-negative with a positive total")
        total = sum(values)
        return Weights(*(value / total for value in values))


@dataclass(frozen=True)
class PolicyConfig:
    name: str
    weights: Weights
    minimum_quality: float
    version: str = "1"


@dataclass(frozen=True)
class ClassifierConfig:
    mode: str = "disabled"
    model: str | None = None
    endpoint: str | None = None
    timeout_seconds: float = 4.0
    max_output_bytes: int = 32_768
    version: str = "1"


@dataclass(frozen=True)
class RouterConfig:
    version: int = 1
    enabled: bool = True
    active_policy: str = "balanced"
    auto_slug: str = "codex-smart-route"
    adapter: str = "codex-cli"
    cache_ttl_seconds: int = 900
    switch_hysteresis: float = 0.05
    allowed_models: frozenset[str] = frozenset()
    blocked_models: frozenset[str] = frozenset()
    allowed_reasoning: frozenset[str] = frozenset()
    fallback_profile: str | None = None
    unknown_capabilities: str = "allow-unverified"
    classifier: ClassifierConfig = ClassifierConfig()
    policies: dict[str, PolicyConfig] = field(default_factory=dict)
    capability_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    log_enabled: bool = True
    log_redact: bool = True
    log_max_bytes: int = 5_000_000
    log_backup_count: int = 3
    experimental_app_server: bool = False
    catalog_strategy: str = "app-server"
    catalog_path: str | None = None

    @property
    def policy(self) -> PolicyConfig:
        try:
            return self.policies[self.active_policy]
        except KeyError as exc:
            raise ConfigError(f"unknown active policy: {self.active_policy}") from exc


DEFAULT_POLICIES: dict[str, PolicyConfig] = {
    "economy": PolicyConfig("economy", Weights(0.65, 0.20, 0.10, 0.05), 0.60),
    "balanced": PolicyConfig("balanced", Weights(0.50, 0.30, 0.10, 0.10), 0.70),
    "quality": PolicyConfig("quality", Weights(0.15, 0.65, 0.10, 0.10), 0.82),
}


def default_home() -> Path:
    override = os.environ.get("CODEX_SMART_ROUTE_HOME")
    return Path(override) if override else Path.home() / ".codex-smart-route"


def default_config_path() -> Path:
    return default_home() / "config.toml"


REPOSITORY_CONFIG_NAME = ".codex-smart-route.toml"


@dataclass(frozen=True)
class ConfigSource:
    kind: str
    path: str | None


@dataclass(frozen=True)
class ResolvedConfig:
    config: RouterConfig
    sources: tuple[ConfigSource, ...]
    project_root: Path | None
    project_id: str | None
    runtime_home: Path


def find_project_root(start: Path) -> Path | None:
    """Find enclosing Git root without invoking Git or reading repository contents."""
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _repository_config(start: Path, project_root: Path | None) -> Path | None:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    stop = project_root or current
    while True:
        candidate = current / REPOSITORY_CONFIG_NAME
        if candidate.is_file():
            return candidate
        if current == stop or current.parent == current:
            return None
        current = current.parent


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc
    catalog = raw.get("catalog")
    if isinstance(catalog, dict) and isinstance(catalog.get("path"), str):
        value = Path(catalog["path"])
        if not value.is_absolute():
            raw = _deep_merge(raw, {"catalog": {"path": str((path.parent / value).resolve())}})
    return raw


def _environment_layer(environ: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    scalar = {
        "CODEX_SMART_ROUTE_ACTIVE_POLICY": "active_policy",
        "CODEX_SMART_ROUTE_CATALOG_STRATEGY": "catalog_strategy",
        "CODEX_SMART_ROUTE_CATALOG_PATH": "catalog_path",
    }
    for variable, key in scalar.items():
        if variable in environ:
            if key.startswith("catalog_"):
                result.setdefault("catalog", {})[key.removeprefix("catalog_")] = environ[variable]
            else:
                result[key] = environ[variable]
    for variable, key in {
        "CODEX_SMART_ROUTE_ALLOWED_MODELS": "allowed_models",
        "CODEX_SMART_ROUTE_BLOCKED_MODELS": "blocked_models",
        "CODEX_SMART_ROUTE_ALLOWED_REASONING": "allowed_reasoning",
    }.items():
        if variable in environ:
            result[key] = [item.strip() for item in environ[variable].split(",") if item.strip()]
    return result


def resolve_config(
    *,
    working_directory: Path | None = None,
    config_file: Path | None = None,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
) -> ResolvedConfig:
    """Resolve deterministic config layers and repository-scoped runtime location."""
    working = (working_directory or Path.cwd()).resolve()
    router_home = (home or default_home()).resolve()
    project_root = find_project_root(working)
    sources = [ConfigSource("built-in", None)]
    raw: dict[str, Any] = {}
    if config_file is not None:
        target = config_file.resolve()
        raw = _read_toml(target)
        sources.append(ConfigSource("explicit", str(target)))
    else:
        user_path = router_home / "config.toml"
        if user_path.is_file():
            raw = _deep_merge(raw, _read_toml(user_path))
            sources.append(ConfigSource("user", str(user_path)))
        repository_path = _repository_config(working, project_root)
        if repository_path is not None:
            raw = _deep_merge(raw, _read_toml(repository_path))
            sources.append(ConfigSource("repository", str(repository_path)))
        environment = _environment_layer(dict(os.environ if environ is None else environ))
        environment_catalog = environment.get("catalog")
        if isinstance(environment_catalog, dict) and isinstance(
            environment_catalog.get("path"), str
        ):
            environment_path = Path(environment_catalog["path"])
            if not environment_path.is_absolute():
                environment = _deep_merge(
                    environment,
                    {"catalog": {"path": str((working / environment_path).resolve())}},
                )
        if environment:
            raw = _deep_merge(raw, environment)
            sources.append(ConfigSource("environment", None))
    project_id = None
    runtime_home = router_home
    if project_root is not None:
        normalized = os.path.normcase(str(project_root.resolve())).replace("\\", "/")
        project_id = hashlib.sha256(normalized.encode()).hexdigest()[:16]
        runtime_home = router_home / "projects" / project_id
    return ResolvedConfig(parse_config(raw), tuple(sources), project_root, project_id, runtime_home)


def _string_set(value: Any, name: str) -> frozenset[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigError(f"{name} must be an array of strings")
    return frozenset(value)


def _unit(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= float(value) <= 1:
        raise ConfigError(f"{name} must be between 0 and 1")
    return float(value)


def _policies(raw: dict[str, Any]) -> dict[str, PolicyConfig]:
    result = dict(DEFAULT_POLICIES)
    for name, item in raw.items():
        if not isinstance(item, dict):
            raise ConfigError(f"policies.{name} must be a table")
        weights = item.get("weights", {})
        if not isinstance(weights, dict):
            raise ConfigError(f"policies.{name}.weights must be a table")
        result[name] = PolicyConfig(
            name,
            Weights(
                float(weights.get("consumption", 0.5)),
                float(weights.get("quality_gap", 0.3)),
                float(weights.get("latency", 0.1)),
                float(weights.get("switch_cost", 0.1)),
            ).normalized(),
            _unit(item.get("minimum_quality", 0.7), f"policies.{name}.minimum_quality"),
            str(item.get("version", "1")),
        )
    return result


def parse_config(raw: dict[str, Any]) -> RouterConfig:
    classifier_raw = raw.get("classifier", {})
    if not isinstance(classifier_raw, dict):
        raise ConfigError("classifier must be a table")
    mode = classifier_raw.get("mode", "disabled")
    if mode not in {"disabled", "local", "remote"}:
        raise ConfigError("classifier.mode must be disabled, local, or remote")
    classifier = ClassifierConfig(
        mode=mode,
        model=classifier_raw.get("model"),
        version=str(classifier_raw.get("version", "1")),
        endpoint=classifier_raw.get("endpoint"),
        timeout_seconds=float(classifier_raw.get("timeout_seconds", 4.0)),
        max_output_bytes=int(classifier_raw.get("max_output_bytes", 32_768)),
    )
    auto_slug = str(raw.get("auto_slug", "codex-smart-route"))
    if classifier.model == auto_slug:
        raise ConfigError("classifier model cannot be the Auto model")
    unknown = raw.get("unknown_capabilities", "allow-unverified")
    if unknown not in {"allow-unverified", "exclude-required"}:
        raise ConfigError("unknown_capabilities has unsupported value")
    policies = _policies(raw.get("policies", {}))
    active = str(raw.get("active_policy", "balanced"))
    if active not in policies:
        raise ConfigError(f"unknown active policy: {active}")
    overrides = raw.get("capability_overrides", {})
    if not isinstance(overrides, dict):
        raise ConfigError("capability_overrides must be a table")
    catalog_raw = raw.get("catalog", {})
    if not isinstance(catalog_raw, dict):
        raise ConfigError("catalog must be a table")
    catalog_strategy = str(catalog_raw.get("strategy", "app-server"))
    if catalog_strategy not in {"app-server", "file"}:
        raise ConfigError("catalog.strategy must be app-server or file")
    catalog_path = catalog_raw.get("path")
    if catalog_path is not None and not isinstance(catalog_path, str):
        raise ConfigError("catalog.path must be a string")
    if catalog_strategy == "file" and not catalog_path:
        raise ConfigError("catalog.path is required when catalog.strategy is file")
    config = RouterConfig(
        version=int(raw.get("version", 1)),
        enabled=bool(raw.get("enabled", True)),
        active_policy=active,
        auto_slug=auto_slug,
        adapter=str(raw.get("adapter", "codex-cli")),
        cache_ttl_seconds=int(raw.get("cache_ttl_seconds", 900)),
        switch_hysteresis=_unit(raw.get("switch_hysteresis", 0.05), "switch_hysteresis"),
        allowed_models=_string_set(raw.get("allowed_models", []), "allowed_models"),
        blocked_models=_string_set(raw.get("blocked_models", []), "blocked_models"),
        allowed_reasoning=_string_set(raw.get("allowed_reasoning", []), "allowed_reasoning"),
        fallback_profile=raw.get("fallback_profile"),
        unknown_capabilities=unknown,
        classifier=classifier,
        policies=policies,
        capability_overrides={str(key): value for key, value in overrides.items()},
        log_enabled=bool(raw.get("logging", {}).get("enabled", True)),
        log_redact=bool(raw.get("logging", {}).get("redact", True)),
        log_max_bytes=int(raw.get("logging", {}).get("max_bytes", 5_000_000)),
        log_backup_count=int(raw.get("logging", {}).get("backup_count", 3)),
        experimental_app_server=bool(raw.get("experimental", {}).get("app_server", False)),
        catalog_strategy=catalog_strategy,
        catalog_path=catalog_path,
    )
    if config.cache_ttl_seconds < 1:
        raise ConfigError("cache_ttl_seconds must be positive")
    if config.log_max_bytes < 1:
        raise ConfigError("logging.max_bytes must be positive")
    if config.log_backup_count < 0:
        raise ConfigError("logging.backup_count must be non-negative")
    return config


def load_config(path: Path | None = None) -> RouterConfig:
    target = path or default_config_path()
    if not target.exists():
        return parse_config({})
    with target.open("rb") as handle:
        return parse_config(tomllib.load(handle))


def config_as_dict(config: RouterConfig) -> dict[str, Any]:
    return dataclasses.asdict(config)
