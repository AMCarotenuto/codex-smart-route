# Troubleshooting

`Codex App Server discovery failed`: run `codex --version`, `codex login status`, and `codex app-server --help` in normal user shell. Ensure Codex can resolve its home directory. Use `--catalog` for offline routing.

`no eligible profile`: inspect `excluded` and `hard_gates`; add verified capability overrides, relax explicit allowlist/budget, or choose different policy. Router does not select cheapest profile silently.

`manual profile rejected by hard gate`: manual choice cannot override unavailable model, auth, modality, tool, context, policy, or Auto-recursion rules.

Skill uninstall refusal means installed files changed after installation. Preserve changes manually before removing target; tool intentionally avoids overwriting them.

