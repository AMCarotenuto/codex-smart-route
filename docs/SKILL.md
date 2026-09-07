# Codex skill

Skill in `skill/codex-smart-route` is thin control interface. It calls CLI for status, diagnostics, policy, overrides, enable/disable, and safe-boundary reevaluation. It never pretends prompt instructions change model.

Codex currently documents personal skills at `$HOME/.agents/skills` and repository skills at
`<repo>/.agents/skills`. It scans repository roots from the working directory to repository root.
See [official skill locations](https://developers.openai.com/codex/skills#where-codex-loads-local-skills).

User scope: dry-run, install, update (same install command), uninstall:

```bash
smart-route install-skill --scope user --dry-run
smart-route install-skill --scope user
smart-route install-skill --scope user
smart-route uninstall-skill --scope user
```

Repository scope, visible only while Codex runs within that repository hierarchy:

```bash
smart-route install-skill --scope repo --repo . --dry-run
smart-route install-skill --scope repo --repo .
smart-route install-skill --scope repo --repo .
smart-route uninstall-skill --scope repo --repo .
```

`legacy` is explicit compatibility mode for pre-scoped installs under
`$CODEX_HOME/skills`. Current official documentation does not list this root, so discovery is
reported as unverified:

```bash
smart-route install-skill --scope legacy --codex-home ~/.codex --dry-run
smart-route install-skill --scope legacy --codex-home ~/.codex
smart-route uninstall-skill --scope legacy --codex-home ~/.codex
```

Inspect Codex version, roots, selected scope, and legacy status:

```bash
smart-route doctor --scope user
smart-route doctor --scope repo --repo .
smart-route doctor --scope legacy --codex-home ~/.codex
```

Installer records scope, target, ownership, and content hash. It refuses unmanaged overwrite,
cross-scope manifests, symlink targets, and removal after external modification. Repo uninstall
removes only managed `codex-smart-route` directory; it leaves `.agents`, sibling skills, and every
`AGENTS.md` untouched. `agents/openai.yaml` disables implicit invocation, so invoke control skill
explicitly as `$codex-smart-route`.
