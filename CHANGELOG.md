# Changelog

All notable changes are recorded here. Releases follow semantic version tags (`vX.Y.Z`), and the
release workflow extracts the matching section as GitHub release notes.

## [0.2.0] - 2026-09-07

### Added

- Version-aware, paginated model discovery with provider capability evidence and diagnostics.
- Versioned privacy-preserving audit evidence and human/JSON `explain` output.
- Optional RTK, Caveman, and CaveCrew compatibility contracts and smoke tests.
- Provider-neutral EN/IT routing benchmark, result schema, and calibration workflow.
- Reproducible GitHub prerelease pipeline with wheel, source distribution, and checksums.

### Changed

- Cache identities now cover every routing eligibility, ranking, failure, and reevaluation input.
- Configuration, CLI execution, skill scope management, evaluation, and live validation are hardened.

### Security

- Secret scanning receives only the automatic job-scoped GitHub token.
- Release publishing uses least-privilege `contents: write` and never publishes to PyPI.

## [0.1.0] - 2026-09-07

### Added

- Dynamic App Server/file model discovery and model+effort profiles.
- Hard gates, deterministic evaluator, weighted policies, quality floor, hysteresis, persistent
  cache, and redacted audit.
- Codex CLI execution adapter and experimental App Server request adapter.
- CLI, scoped reversible control skill, offline tests, documentation, and cross-platform CI.

### Known limitations

- App Server and live model confirmation remain version-dependent and opt-in.
- No calibrated cost-saving claim is made.

[0.2.0]: https://github.com/AMCarotenuto/codex-smart-route/releases/tag/v0.2.0
[0.1.0]: https://github.com/AMCarotenuto/codex-smart-route/releases/tag/v0.1.0
