# Contributing

Use Python 3.11+, create a virtual environment, then install `.[dev]`. Run `pytest`, `ruff check .`, `ruff format --check .`, `mypy codex_smart_route`, and `python -m build` before opening a pull request.

Use Conventional Commits. Add offline tests for routing behavior. Live tests must remain opt-in and must never require secrets in CI. Document capability data sources and mark estimates unverified. Report vulnerabilities through private process in `SECURITY.md`.

