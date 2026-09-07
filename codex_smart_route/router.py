"""Hard gates followed by normalized weighted optimization."""

from __future__ import annotations

from dataclasses import replace

from .config import RouterConfig
from .evaluation import local_scores
from .models import CandidateScore, Decision, ModelProfile, TaskContext, TaskSignals


class RoutingError(RuntimeError):
    pass


class Router:
    def __init__(self, config: RouterConfig):
        self.config = config

    def route(
        self,
        task: TaskContext,
        profiles: tuple[ModelProfile, ...],
        signals: TaskSignals,
        assessments: dict[str, tuple[float, float]] | None = None,
    ) -> Decision:
        if not self.config.enabled:
            raise RoutingError("router is disabled")
        scores = assessments or local_scores(signals, profiles)
        excluded: dict[str, tuple[str, ...]] = {}
        candidates: list[CandidateScore] = []
        weights = self.config.policy.weights.normalized()
        for profile in profiles:
            gates = self._gates(task, profile)
            score = scores.get(profile.id)
            if score is None:
                gates.append("missing-assessment")
            elif score[0] < self.config.policy.minimum_quality:
                gates.append("below-minimum-quality")
            if gates:
                excluded[profile.id] = tuple(gates)
                continue
            assert score is not None
            consumption, latency, verified_metrics = self._relative_metrics(profile)
            components = {
                "consumption": consumption,
                "quality_gap": 1 - score[0],
                "latency": latency,
                "switch_cost": float(
                    task.current_profile is not None and task.current_profile != profile.id
                ),
            }
            penalty = (
                weights.consumption * components["consumption"]
                + weights.quality_gap * components["quality_gap"]
                + weights.latency * components["latency"]
                + weights.switch_cost * components["switch_cost"]
            )
            verified = (
                verified_metrics and profile.capability.confidence == "verified" and score[1] >= 0.8
            )
            candidates.append(
                CandidateScore(
                    profile.id,
                    score[0],
                    score[1],
                    penalty,
                    components,
                    (profile.capability.source, signals.source),
                    verified,
                )
            )
        if task.manual_profile:
            return self._manual(task, profiles, excluded, candidates)
        if not candidates:
            fallback = self._explicit_fallback(task, profiles, excluded, scores, signals)
            if fallback:
                return fallback
            return Decision(
                "blocked",
                None,
                None,
                None,
                self.config.auto_slug,
                excluded=excluded,
                hard_gates=self._all_gates(excluded),
                reason="no eligible profile; no implicit fallback",
                policy_name=self.config.active_policy,
                policy_version=self.config.policy.version,
            )
        candidates.sort(key=lambda item: (item.penalty, item.profile_id))
        winner = candidates[0]
        if (
            task.current_profile
            and task.current_profile != winner.profile_id
            and not task.significant_model_failure
        ):
            current = next(
                (item for item in candidates if item.profile_id == task.current_profile), None
            )
            if current and current.penalty - winner.penalty < self.config.switch_hysteresis:
                winner = current
        profile = next(item for item in profiles if item.id == winner.profile_id)
        return Decision(
            "selected",
            profile.model,
            profile.effort,
            profile.id,
            self.config.auto_slug,
            candidates=tuple(candidates),
            excluded=excluded,
            hard_gates=self._all_gates(excluded),
            reason="minimum-quality satisfied; lowest normalized weighted penalty",
            confidence=winner.confidence,
            verified=winner.verified,
            policy_name=self.config.active_policy,
            policy_version=self.config.policy.version,
        )

    def _gates(self, task: TaskContext, profile: ModelProfile) -> list[str]:
        cap = profile.capability
        gates: list[str] = []
        if cap.model == self.config.auto_slug:
            gates.append("auto-recursion")
        if cap.available is False:
            gates.append("model-unavailable")
        if self.config.allowed_models and cap.model not in self.config.allowed_models:
            gates.append("model-not-allowed")
        if cap.model in self.config.blocked_models:
            gates.append("model-blocked")
        if self.config.allowed_reasoning and profile.effort not in self.config.allowed_reasoning:
            gates.append("reasoning-not-allowed")
        if not task.required_modalities.issubset(cap.input_modalities):
            gates.append("missing-input-modality")
        strict = self.config.unknown_capabilities == "exclude-required"
        if task.tools_required and (
            cap.supports_tools is False or strict and cap.supports_tools is None
        ):
            gates.append("tools-unsupported" if cap.supports_tools is False else "tools-unverified")
        if task.parallel_tools_required and (
            cap.supports_parallel_tools is False or strict and cap.supports_parallel_tools is None
        ):
            gates.append(
                "parallel-tools-unsupported"
                if cap.supports_parallel_tools is False
                else "parallel-tools-unverified"
            )
        if cap.context_window is not None and task.estimated_context_tokens > cap.context_window:
            gates.append("context-limit")
        if task.auth_modes and cap.auth_mode not in task.auth_modes:
            gates.append("authentication-unavailable")
        consumption = cap.relative_consumption
        if task.max_relative_consumption is not None and (
            consumption is None or consumption > task.max_relative_consumption
        ):
            gates.append("budget-policy")
        return gates

    @staticmethod
    def _relative_metrics(profile: ModelProfile) -> tuple[float, float, bool]:
        cap = profile.capability
        effort_order = {
            "none": 0.05,
            "minimal": 0.10,
            "low": 0.20,
            "medium": 0.45,
            "high": 0.68,
            "xhigh": 0.82,
            "max": 0.93,
            "ultra": 1.0,
        }
        heuristic = effort_order.get(profile.effort.lower(), 0.5)
        consumption = (
            cap.relative_consumption if cap.relative_consumption is not None else heuristic
        )
        latency = cap.relative_latency if cap.relative_latency is not None else heuristic
        return (
            consumption,
            latency,
            cap.relative_consumption is not None and cap.relative_latency is not None,
        )

    def _manual(
        self,
        task: TaskContext,
        profiles: tuple[ModelProfile, ...],
        excluded: dict[str, tuple[str, ...]],
        candidates: list[CandidateScore],
    ) -> Decision:
        profile = next((item for item in profiles if item.id == task.manual_profile), None)
        if profile is None:
            raise RoutingError(f"manual profile not found: {task.manual_profile}")
        gates = self._gates(task, profile)
        if gates:
            raise RoutingError(f"manual profile rejected by hard gate: {', '.join(gates)}")
        candidate = next((item for item in candidates if item.profile_id == profile.id), None)
        confidence = candidate.confidence if candidate else 1.0
        return Decision(
            "selected",
            profile.model,
            profile.effort,
            profile.id,
            profile.model,
            candidates=tuple(candidates),
            excluded=excluded,
            hard_gates=self._all_gates(excluded),
            reason="explicit manual override",
            confidence=confidence,
            verified=profile.capability.confidence == "verified",
            policy_name=self.config.active_policy,
            policy_version=self.config.policy.version,
        )

    def _explicit_fallback(
        self,
        task: TaskContext,
        profiles: tuple[ModelProfile, ...],
        excluded: dict[str, tuple[str, ...]],
        scores: dict[str, tuple[float, float]],
        signals: TaskSignals,
    ) -> Decision | None:
        fallback = self.config.fallback_profile
        if not fallback:
            return None
        profile = next((item for item in profiles if item.id == fallback), None)
        if not profile or self._gates(task, profile):
            return None
        score = scores.get(profile.id)
        if not score or score[0] < self.config.policy.minimum_quality:
            return None
        return Decision(
            "selected",
            profile.model,
            profile.effort,
            profile.id,
            self.config.auto_slug,
            excluded=excluded,
            hard_gates=self._all_gates(excluded),
            reason="explicit configured fallback",
            confidence=score[1],
            verified=False,
            policy_name=self.config.active_policy,
            policy_version=self.config.policy.version,
        )

    @staticmethod
    def _all_gates(excluded: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
        return tuple(sorted({gate for gates in excluded.values() for gate in gates}))


def mark_forwarded(
    decision: Decision, confirmed_model: str | None = None, confirmed_effort: str | None = None
) -> Decision:
    if decision.status != "selected":
        raise RoutingError("cannot forward blocked decision")
    return replace(
        decision,
        forwarded_model=decision.selected_model,
        forwarded_reasoning_effort=decision.selected_reasoning_effort,
        confirmed_model=confirmed_model,
        confirmed_reasoning_effort=confirmed_effort,
    )
