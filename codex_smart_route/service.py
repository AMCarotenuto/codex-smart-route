"""Composition layer shared by CLI and adapters."""

from __future__ import annotations

from pathlib import Path

from .audit import AuditLogger
from .config import RouterConfig, default_home
from .discovery import apply_overrides, capabilities_from_file
from .evaluation import RemoteJsonClassifier, evaluate_task
from .models import Decision, ModelCapability, TaskContext, fingerprint
from .priors import apply_priors
from .router import Router
from .state import CacheIdentity, DecisionCache


class RoutingService:
    def __init__(
        self, config: RouterConfig, catalog: list[ModelCapability], home: Path | None = None
    ):
        self.config = config
        self.catalog = apply_overrides(apply_priors(catalog), config)
        self.profiles = tuple(profile for model in self.catalog for profile in model.profiles())
        self.router = Router(config)
        self.home = home or default_home()
        self.cache_path = self.home / "cache.sqlite3"
        self.cache: DecisionCache | None = None
        self.audit = AuditLogger(self.home / "audit.jsonl", config.log_enabled)

    @classmethod
    def from_catalog_file(
        cls, config: RouterConfig, path: Path, home: Path | None = None
    ) -> RoutingService:
        return cls(config, capabilities_from_file(path), home)

    def route(self, task: TaskContext, dry_run: bool = False) -> Decision:
        signals = evaluate_task(task)
        classifier_used = self.config.classifier.mode == "remote" and signals.confidence < 0.8
        identity = self._cache_identity(task, classifier_used)
        if not dry_run and self.cache is None:
            self.cache = DecisionCache(self.cache_path, self.config.cache_ttl_seconds)
        bypass_cache = task.force_reevaluation or task.significant_model_failure
        if self.cache is not None and bypass_cache:
            self.cache.invalidate_scope(identity.scope_key)
        if (
            self.cache is not None
            and task.event in {"tool_loop", "continuation"}
            and not bypass_cache
        ):
            cached = self.cache.get(identity.key, identity.explanation)
            if cached:
                self.audit.decision(task, cached)
                return cached
        assessments = None
        if classifier_used:
            assessments = RemoteJsonClassifier(self.config.classifier).score(task, self.profiles)
        decision = self.router.route(task, self.profiles, signals, assessments)
        if self.cache is not None and decision.status == "selected" and not bypass_cache:
            self.cache.put(identity.key, decision, identity.scope_key)
        if not dry_run:
            self.audit.decision(task, decision)
        return decision

    def _cache_identity(self, task: TaskContext, classifier_used: bool) -> CacheIdentity:
        policy = self.config.policy
        weights = policy.weights.normalized()
        ordered_catalog = sorted(self.catalog, key=lambda model: model.model)
        capability_cards = [model.to_dict() for model in ordered_catalog]
        classifier = self.config.classifier
        routing_inputs = {
            "config": {
                "version": self.config.version,
                "enabled": self.config.enabled,
                "active_policy": self.config.active_policy,
                "policy_name": policy.name,
                "policy_version": policy.version,
                "normalized_weights": {
                    "consumption": weights.consumption,
                    "quality_gap": weights.quality_gap,
                    "latency": weights.latency,
                    "switch_cost": weights.switch_cost,
                },
                "minimum_quality": policy.minimum_quality,
                "allowed_models": sorted(self.config.allowed_models),
                "blocked_models": sorted(self.config.blocked_models),
                "allowed_reasoning": sorted(self.config.allowed_reasoning),
                "fallback_profile": self.config.fallback_profile,
                "unknown_capabilities": self.config.unknown_capabilities,
                "auto_slug": self.config.auto_slug,
            },
            "catalog": {
                "capability_versions": [
                    (model.model, model.capability_version) for model in ordered_catalog
                ],
                "prior_versions": [(model.model, model.prior_version) for model in ordered_catalog],
                "card_fingerprint": fingerprint(capability_cards),
            },
            "availability": [(model.model, model.available) for model in ordered_catalog],
            "hard_gates": {
                "required_modalities": sorted(task.required_modalities),
                "tools_required": task.tools_required,
                "parallel_tools_required": task.parallel_tools_required,
                "estimated_context_tokens": task.estimated_context_tokens,
                "accepted_auth_modes": sorted(task.auth_modes),
                "max_relative_consumption": task.max_relative_consumption,
            },
            "hysteresis": {
                "manual_profile": task.manual_profile,
                "current_profile": task.current_profile,
                "switch_hysteresis": self.config.switch_hysteresis,
            },
            "classifier": {
                "used": classifier_used,
                "mode": classifier.mode,
                "model": classifier.model if classifier_used else None,
                "version": classifier.version if classifier_used else None,
                "endpoint_fingerprint": (
                    fingerprint(classifier.endpoint) if classifier_used else None
                ),
                "timeout_seconds": classifier.timeout_seconds if classifier_used else None,
                "max_output_bytes": classifier.max_output_bytes if classifier_used else None,
                "recent_failures_fingerprint": (
                    fingerprint(task.recent_failures[-3:]) if classifier_used else None
                ),
            },
            "routing": {"mode": "auto", "adapter": self.config.adapter},
        }
        return CacheIdentity.build(task, routing_inputs)

    def clear_cache(self) -> None:
        if self.cache_path.exists():
            DecisionCache(self.cache_path, self.config.cache_ttl_seconds).clear()
