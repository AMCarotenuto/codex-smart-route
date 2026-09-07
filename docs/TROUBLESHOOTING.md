# Troubleshooting

`Codex App Server discovery failed`: run `codex --version`, `codex login status`, and `codex app-server --help` in normal user shell. Ensure Codex can resolve its home directory. Use `--catalog` for offline routing.

`model/list schema changed`, `malformed nextCursor`, or `pagination cursor loop detected`: run
`smart-route doctor`, record `codex_version` and `app_server_protocol`, then update Codex and
codex-smart-route together. `provider capability discovery unavailable` is partial discovery: models
remain usable, but unreported tool, context, auth, or availability fields stay unknown.

`no eligible profile`: inspect `excluded` and `hard_gates`; add verified capability overrides, relax explicit allowlist/budget, or choose different policy. Router does not select cheapest profile silently.

`manual profile rejected by hard gate`: manual choice cannot override unavailable model, auth, modality, tool, context, policy, or Auto-recursion rules.

Skill uninstall refusal means installed files changed after installation. Preserve changes manually before removing target; tool intentionally avoids overwriting them.
