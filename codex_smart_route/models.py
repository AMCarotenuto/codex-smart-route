"""Transport-independent routing records."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

UnknownBool = bool | None


def fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True)
class ModelCapability:
    model: str
    display_name: str
    reasoning_efforts: tuple[str, ...]
    input_modalities: tuple[str, ...] = ("text",)
    supports_tools: UnknownBool = None
    supports_parallel_tools: UnknownBool = None
    context_window: int | None = None
    auth_mode: str | None = None
    available: UnknownBool = None
    source: str = "unknown"
    confidence: Literal["verified", "reported", "unverified"] = "unverified"
    capability_version: str = "unknown"
    relative_quality: float | None = None
    relative_consumption: float | None = None
    relative_latency: float | None = None

    def profiles(self) -> tuple[ModelProfile, ...]:
        return tuple(ModelProfile(self, effort) for effort in self.reasoning_efforts)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class ModelProfile:
    capability: ModelCapability
    effort: str

    @property
    def id(self) -> str:
        return f"{self.capability.model}@{self.effort}"

    @property
    def model(self) -> str:
        return self.capability.model

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "model": self.model,
            "effort": self.effort,
            **self.capability.to_dict(),
        }


@dataclass(frozen=True)
class TaskContext:
    task: str
    session_id: str = "local"
    task_id: str = "task"
    phase_id: str = "initial"
    event: Literal["new_task", "new_phase", "tool_loop", "continuation"] = "new_task"
    relevant_context: str = ""
    required_modalities: frozenset[str] = frozenset({"text"})
    tools_required: bool = False
    parallel_tools_required: bool = False
    estimated_context_tokens: int = 0
    auth_modes: frozenset[str] = frozenset()
    max_relative_consumption: float | None = None
    manual_profile: str | None = None
    current_profile: str | None = None
    significant_model_failure: bool = False
    environment_failure: bool = False
    force_reevaluation: bool = False
    recent_failures: tuple[str, ...] = ()

    @property
    def context_fingerprint(self) -> str:
        return fingerprint(
            {
                "task": self.task,
                "context": self.relevant_context,
                "modalities": sorted(self.required_modalities),
                "tools": self.tools_required,
                "parallel_tools": self.parallel_tools_required,
            }
        )


@dataclass(frozen=True)
class TaskSignals:
    reasoning_complexity: float
    ambiguity: float
    coupling: float
    consequence: float
    verification_difficulty: float
    context_need: float
    creative: float
    mechanical: float
    debugging: float
    cross_cutting: float
    acceptance_criteria: float
    confidence: float
    source: str = "local-rules"

    @property
    def difficulty(self) -> float:
        values = (
            self.reasoning_complexity,
            self.ambiguity,
            self.coupling,
            self.consequence,
            self.verification_difficulty,
            self.context_need,
            self.debugging,
            self.cross_cutting,
        )
        average = sum(values) / len(values)
        # One severe dimension can make a short task hard; prompt length is irrelevant.
        return min(1.0, 0.55 * max(values) + 0.45 * average)


@dataclass(frozen=True)
class CandidateScore:
    profile_id: str
    suitability: float
    confidence: float
    penalty: float
    components: dict[str, float]
    data_sources: tuple[str, ...]
    verified: bool


@dataclass(frozen=True)
class Decision:
    status: Literal["selected", "blocked"]
    selected_model: str | None
    selected_reasoning_effort: str | None
    selected_profile: str | None
    requested_model: str | None
    forwarded_model: str | None = None
    forwarded_reasoning_effort: str | None = None
    confirmed_model: str | None = None
    confirmed_reasoning_effort: str | None = None
    candidates: tuple[CandidateScore, ...] = ()
    excluded: dict[str, tuple[str, ...]] = field(default_factory=dict)
    hard_gates: tuple[str, ...] = ()
    reason: str = ""
    confidence: float = 0.0
    verified: bool = False
    policy_name: str = ""
    policy_version: str = ""
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        if data["confirmed_model"] is None:
            data["confirmed_model"] = "unverified"
        if data["confirmed_reasoning_effort"] is None:
            data["confirmed_reasoning_effort"] = "unverified"
        return data
