# Releases and installation

GitHub Releases is the supported package distribution channel for the current alpha. Every tagged
release is a **prerelease** with an exact wheel, source distribution, and `SHA256SUMS`. PyPI
publishing is intentionally absent until maintainers establish package-name ownership, release
policy, and trusted-publishing configuration. No repository secret or PyPI token is required by the
current workflow.

Official OpenAI documentation distinguishes local/repository skills from reusable distribution:
Codex discovers user skills under `$HOME/.agents/skills`, repository skills under
`.agents/skills`, and recommends plugins for reusable distribution beyond one repository. Codex
Smart Route therefore ships a version-aligned skill inside the Python package and installs it only
after an explicit `smart-route install-skill` command. It does not claim plugin-directory
publication and does not install an undocumented plugin workaround. A skill-only plugin would
expose commands without ensuring the separate Python executable exists, so plugin packaging is
deferred until its dependency and upgrade contract can be tested. See
[official OpenAI skill distribution guidance](https://learn.chatgpt.com/docs/build-skills#distribute-skills-with-plugins).

## Verify downloaded assets

Download wheel, source distribution, and `SHA256SUMS` from the same GitHub release. From their
directory, verify before installation:

```bash
sha256sum --check SHA256SUMS
```

Windows PowerShell equivalent:

```powershell
Get-Content SHA256SUMS | ForEach-Object {
  $hash, $file = $_ -split "  ", 2
  if ((Get-FileHash -Algorithm SHA256 -LiteralPath $file).Hash.ToLowerInvariant() -ne $hash) {
    throw "Checksum mismatch: $file"
  }
}
```

## Install package

Replace `<version>` and paths with verified release assets.

Isolated CLI with `pipx`:

```bash
pipx install ./codex_smart_route-<version>-py3-none-any.whl
smart-route --version
```

Virtual environment on macOS/Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install ./codex_smart_route-<version>-py3-none-any.whl
.venv/bin/smart-route --version
```

Virtual environment on Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install .\codex_smart_route-<version>-py3-none-any.whl
.\.venv\Scripts\smart-route --version
```

Direct installation into an already managed Python environment:

```bash
python -m pip install ./codex_smart_route-<version>-py3-none-any.whl
smart-route --version
```

Package installation is side-effect free: it installs Python files and the `smart-route` entry
point only. It does not modify Codex configuration, create `.agents`, edit `AGENTS.md`, or install
the control skill.

## Install, update, or remove control skill

Explicit user-scope installation uses Codex''s documented location:

```bash
smart-route install-skill --scope user --dry-run
smart-route install-skill --scope user
smart-route uninstall-skill --scope user --dry-run
smart-route uninstall-skill --scope user
```

Updating package does not silently update skill. After package upgrade, rerun the explicit install
command; managed-file integrity checks refuse overwriting local modifications:

```bash
smart-route install-skill --scope user
smart-route doctor --scope user
```

Repository and explicit legacy scopes are documented in [Codex skill](SKILL.md).

## Upgrade and rollback

Keep the last verified wheel until upgrade passes local checks. For `pipx`:

```bash
pipx install --force ./codex_smart_route-<new-version>-py3-none-any.whl
smart-route install-skill --scope user
smart-route doctor --scope user
```

For a virtual or managed environment:

```bash
python -m pip install --upgrade ./codex_smart_route-<new-version>-py3-none-any.whl
smart-route install-skill --scope user
smart-route doctor --scope user
```

Rollback package and bundled skill together:

```bash
python -m pip install --force-reinstall ./codex_smart_route-<old-version>-py3-none-any.whl
smart-route install-skill --scope user
smart-route doctor --scope user
```

For `pipx`, replace first command with:

```bash
pipx install --force ./codex_smart_route-<old-version>-py3-none-any.whl
```

Remove both managed skill and package:

```bash
smart-route uninstall-skill --scope user
python -m pip uninstall codex-smart-route
```

For a `pipx` installation, remove package environment with `pipx uninstall codex-smart-route`
after uninstalling managed skill.

## Maintainer prerelease procedure

1. Update version in `pyproject.toml`, `codex_smart_route/__init__.py`, and both `SKILL.md` copies.
2. Add matching `CHANGELOG.md` section and refresh `THIRD_PARTY_NOTICES.md`.
3. Run full offline tests, lint, type checks, and `python -m build`.
4. Run `scripts/verify_release_artifacts.py --version <version> --dist dist` locally.
5. Merge release change, create signed or annotated `v<version>` tag on reviewed commit, and push
   tag. Do not retag or move a published version.
6. Wait for `Release prerelease artifacts`; inspect exact-wheel smoke output and downloaded assets.
7. Recompute checksums after download. Promote prerelease manually only after validation policy is
   satisfied.

Workflow uses only job-scoped `GITHUB_TOKEN`: build has `contents: read`; publishing alone has
`contents: write`. It never calls PyPI. A failed version, changelog, reproducibility, archive,
license, bundled-skill, installation-side-effect, or smoke check prevents release creation.
