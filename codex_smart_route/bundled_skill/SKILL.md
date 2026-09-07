---
name: codex-smart-route
description: "Control an installed Codex Smart Route bridge: route model and reasoning, inspect status or decisions, change policy, set overrides, or run diagnostics. Do not activate for ordinary tasks when no routing control was requested."
metadata:
  version: "0.1.0"
---

# Codex Smart Route controls

Use installed `smart-route` CLI. Engine and adapter perform model changes; never claim this skill prompt changes active model.

- Status or last decision: `smart-route status` or `smart-route explain`.
- Diagnostics: `smart-route doctor`.
- Enable/disable future routing: `smart-route enable` or `smart-route disable`.
- Policy: `smart-route policy economy|balanced|quality`.
- Manual profile: `smart-route override MODEL@EFFORT`; clear with `smart-route override`.
- Safe-boundary reevaluation: `smart-route reevaluate`.
- Compatibility: report `doctor` output and consult project `docs/COMPATIBILITY.md`.

For actual routed execution, use `smart-route exec --task "..."`. This applies both `model` and `model_reasoning_effort` before starting `codex exec`. Do not launch paid/remote classification or live inference unless user explicitly requests execution.

Never delegate routing to subagents, edit global `AGENTS.md`, inspect credentials, or modify unrelated Codex/RTK/Caveman/CaveCrew configuration. Install and removal use explicit scopes, for example `smart-route install-skill --scope user` and `smart-route uninstall-skill --scope user`.
