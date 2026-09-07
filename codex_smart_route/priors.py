"""Versioned operational priors for dynamically discovered model families.

Values here are routing hypotheses, not benchmark results or calibrated probabilities.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from fnmatch import fnmatchcase
from typing import Literal

from .models import ModelCapability


@dataclass(frozen=True)
class ModelPrior:
    family: str
    patterns: tuple[str, ...]
    relative_quality: float
    relative_consumption: float
    relative_latency: float
    strengths: tuple[str, ...]
    weaknesses: tuple[str, ...]
    source: str
    confidence: Literal["verified", "reported", "unverified"]
    version: str
    tie_break_priority: int


@dataclass(frozen=True)
class PriorRegistry:
    version: str
    priors: tuple[ModelPrior, ...]

    def resolve(self, model: str) -> ModelPrior | None:
        """Return most-specific matching family prior, independent of registry order."""
        matches = [
            prior
            for prior in self.priors
            if any(fnmatchcase(model.casefold(), pattern.casefold()) for pattern in prior.patterns)
        ]
        if not matches:
            return None
        return max(
            matches,
            key=lambda prior: (
                max(len(pattern.replace("*", "")) for pattern in prior.patterns),
                prior.tie_break_priority,
                prior.family,
            ),
        )


_SOURCE = "bundled-operational-prior"
_VERSION = "2026-09-07.1"

BUNDLED_PRIORS = PriorRegistry(
    version=_VERSION,
    priors=(
        ModelPrior(
            "astra",
            ("gpt-6-astra*",),
            0.96,
            0.95,
            0.88,
            ("complex reasoning", "cross-cutting architecture", "high-consequence review"),
            ("high relative consumption", "high relative latency"),
            _SOURCE,
            "unverified",
            _VERSION,
            70,
        ),
        ModelPrior(
            "sol",
            ("gpt-5.6-sol*",),
            0.88,
            0.68,
            0.62,
            ("agentic implementation", "debugging", "multi-file changes"),
            ("higher overhead than balanced families",),
            _SOURCE,
            "unverified",
            _VERSION,
            60,
        ),
        ModelPrior(
            "terra",
            ("gpt-5.6-terra*",),
            0.78,
            0.48,
            0.42,
            ("balanced implementation", "routine repository work"),
            ("less headroom for highest-complexity work",),
            _SOURCE,
            "unverified",
            _VERSION,
            50,
        ),
        ModelPrior(
            "luna",
            ("gpt-5.6-luna*",),
            0.68,
            0.25,
            0.20,
            ("fast iteration", "mechanical changes"),
            ("less suited to complex or high-consequence reasoning",),
            _SOURCE,
            "unverified",
            _VERSION,
            40,
        ),
        ModelPrior(
            "gpt-5.5",
            ("gpt-5.5*",),
            0.80,
            0.60,
            0.55,
            ("general coding", "reasoning"),
            ("less specialized than current agentic families",),
            _SOURCE,
            "unverified",
            _VERSION,
            45,
        ),
        ModelPrior(
            "mini",
            ("gpt-5.4-mini*",),
            0.62,
            0.18,
            0.15,
            ("low-overhead mechanical work",),
            ("limited fit for complex cross-cutting work",),
            _SOURCE,
            "unverified",
            _VERSION,
            30,
        ),
        ModelPrior(
            "codex-spark",
            ("gpt-5.3-codex-spark*",),
            0.70,
            0.22,
            0.10,
            ("fast coding iterations",),
            ("limited fit for highest-complexity reasoning",),
            _SOURCE,
            "unverified",
            _VERSION,
            35,
        ),
    ),
)


def apply_priors(
    models: Iterable[ModelCapability], registry: PriorRegistry = BUNDLED_PRIORS
) -> list[ModelCapability]:
    """Fill missing metrics from family priors; discovered/reported values always win."""
    result: list[ModelCapability] = []
    for model in models:
        if model.prior_version != "none":
            result.append(model)
            continue
        supplied_metrics = sum(
            value is not None
            for value in (
                model.relative_quality,
                model.relative_consumption,
                model.relative_latency,
            )
        )
        prior = registry.resolve(model.model)
        if prior is None:
            result.append(
                replace(
                    model,
                    prior_family=model.prior_family or "unknown",
                    prior_source=(
                        model.prior_source
                        if model.prior_source != "none"
                        else (model.source if supplied_metrics else "none")
                    ),
                    prior_confidence=(model.confidence if supplied_metrics else "unverified"),
                    prior_version=(
                        f"{model.capability_version}:reported-prior"
                        if supplied_metrics
                        else f"{registry.version}:unknown"
                    ),
                )
            )
            continue
        mixed = 0 < supplied_metrics < 3
        result.append(
            replace(
                model,
                relative_quality=(
                    model.relative_quality
                    if model.relative_quality is not None
                    else prior.relative_quality
                ),
                relative_consumption=(
                    model.relative_consumption
                    if model.relative_consumption is not None
                    else prior.relative_consumption
                ),
                relative_latency=(
                    model.relative_latency
                    if model.relative_latency is not None
                    else prior.relative_latency
                ),
                prior_family=model.prior_family or prior.family,
                prior_strengths=model.prior_strengths or prior.strengths,
                prior_weaknesses=model.prior_weaknesses or prior.weaknesses,
                prior_source=(
                    model.prior_source
                    if model.prior_source != "none"
                    else (
                        f"{model.source}+{prior.source}"
                        if mixed
                        else (model.source if supplied_metrics else prior.source)
                    )
                ),
                prior_confidence=(
                    "unverified"
                    if mixed
                    else (model.confidence if supplied_metrics else prior.confidence)
                ),
                prior_version=(
                    f"{model.capability_version}+{prior.version}"
                    if mixed
                    else (
                        f"{model.capability_version}:reported-prior"
                        if supplied_metrics
                        else prior.version
                    )
                ),
                tie_break_priority=model.tie_break_priority or prior.tie_break_priority,
            )
        )
    return result
