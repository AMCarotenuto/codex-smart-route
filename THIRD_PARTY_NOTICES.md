# Third-party notices

Codex Smart Route has no runtime dependencies and does not bundle third-party source code,
model weights, or binary components in its wheel or source distribution.

Development and release tooling is installed separately and is not redistributed as part of the
package. Its licensing remains governed by the corresponding upstream projects:

- [build](https://github.com/pypa/build)
- [Hatchling](https://github.com/pypa/hatch)
- [mypy](https://github.com/python/mypy)
- [pytest](https://github.com/pytest-dev/pytest)
- [pytest-cov](https://github.com/pytest-dev/pytest-cov)
- [Ruff](https://github.com/astral-sh/ruff)

Regenerate and review this file before any release that adds a runtime dependency, vendors code,
or bundles a binary asset.

## Attribution

Architecture was informed by `sybil-solutions/codex-shim` and
`orange-the-weak/codex-auto-model-router`, both MIT-licensed when reviewed on 2026-09-07. No source
code was copied. Their separation of virtual entry, classification, request rewriting, and cache
informed terminology and design boundaries.

Code of conduct is adapted from Contributor Covenant 2.1 under its published reuse terms.
