# Security model

Trust boundaries: task text and relevant context are untrusted; capability discovery, configuration, adapter metadata, and auth availability come from trusted local sources. Task text cannot change policy, authorize models, set upstream URLs, or mark model switching safe.

Threats and controls:

- Classifier prompt injection: task is bounded data under fixed instruction; classifier cannot see costs; strict JSON and complete profile set required.
- Auto recursion: Auto is hard-gated and invalid as classifier model.
- Credential leakage: no credential file reads; stderr suppressed during discovery; logs omit task body and redact secret-shaped fields.
- Cache isolation: SQLite stores opaque SHA-256 identity/scope keys and decision payloads only. Raw
  task/context/session text, classifier endpoint, authentication material, and credentials are not
  stored. Cache-hit audit evidence names matched input categories without revealing their values.
- Config poisoning: typed fields, allowlisted overrides, explicit endpoint, no `eval`, no shell interpolation.
- Path traversal: skill target is fixed child of supplied Codex home; unmanaged/modified content blocks deletion.
- Network exposure: current adapters use subprocess stdio. Any future HTTP control plane must bind `127.0.0.1`, authenticate, validate Host/Origin, limit bodies, and allowlist upstreams.
- Global configuration: package install and routing do not edit Codex configuration or `AGENTS.md`.
- Supply chain: standard-library runtime; bounded and pinned-range build/dev tools; Dependabot monitors Actions and Python metadata.

Residual risk: App Server is experimental; local processes with same-user access can manipulate files; heuristic quality is uncalibrated; explicit remote classifier endpoint can receive task excerpts.
