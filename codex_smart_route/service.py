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
from .state import DecisionCache


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
        capability_version = fingerprint(
            [
                (
                    model.model,
                    model.capability_version,
                    model.prior_version,
                    model.relative_quality,
                    model.relative_consumption,
                    model.relative_latency,
                    model.tie_break_priority,
                    model.prior_strengths,
                    model.prior_weaknesses,
                )
                for model in self.catalog
            ]
        )
        availability = fingerprint([(model.model, model.available) for model in self.catalog])
        key = DecisionCache.key(
            task, self.config.policy.version, capability_version, availability, "auto"
        )
        if not dry_run and self.cache is None:
            self.cache = DecisionCache(self.cache_path, self.config.cache_ttl_seconds)
        if (
            self.cache is not None
            and task.event in {"tool_loop", "continuation"}
            and not task.force_reevaluation
            and not task.significant_model_failure
        ):
            cached = self.cache.get(key)
            if cached:
                self.audit.decision(task, cached)
                return cached
        signals = evaluate_task(task)
        assessments = None
        if self.config.classifier.mode == "remote" and signals.confidence < 0.8:
            assessments = RemoteJsonClassifier(self.config.classifier).score(task, self.profiles)
        decision = self.router.route(task, self.profiles, signals, assessments)
        if self.cache is not None and decision.status == "selected":
            self.cache.put(key, decision)
        if not dry_run:
            self.audit.decision(task, decision)
        return decision

    def clear_cache(self) -> None:
        if self.cache_path.exists():
            DecisionCache(self.cache_path, self.config.cache_ttl_seconds).clear()
