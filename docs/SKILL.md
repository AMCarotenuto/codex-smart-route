# Codex skill

Skill in `skill/codex-smart-route` is thin control interface. It calls CLI for status, diagnostics, policy, overrides, enable/disable, and safe-boundary reevaluation. It never pretends prompt instructions change model.

Install/remove:

```bash
smart-route install-skill --dry-run
smart-route install-skill
smart-route uninstall-skill
```

Installer manages only `$CODEX_HOME/skills/codex-smart-route` (or default Codex home), records content hash, refuses unmanaged overwrite, and refuses removal after external modification.

