# Configuration

Default path: `~/.codex-smart-route/config.toml`; override with `CODEX_SMART_ROUTE_HOME`. Copy `examples/config.example.toml` as a starting point.

Top-level fields select policy, Auto slug, adapter, cache TTL, hysteresis, model/reasoning allowlists and blocklists, explicit fallback, unknown-capability behavior, logging, and experimental App Server support. `policies.<name>` supports custom normalized weights, quality floor, and version. `capability_overrides."MODEL"` may set only fields defined by `ModelCapability`.

Repository catalog values override bundled family priors; `capability_overrides."MODEL"` in the
selected user config override both. Prior fields include relative metrics, `prior_strengths`,
`prior_weaknesses`, `prior_source`, `prior_confidence`, `prior_version`, and
`tie_break_priority`. See [Model priors and capability cards](MODEL_PRIORS.md).

Classifier modes: `disabled` (default), `local`, and `remote`. Remote mode requires explicit fixed model and endpoint; credentials remain transport/environment responsibility and must not be stored in this file. Auto slug is rejected as classifier model.

`smart-route config validate` parses without mutation. `smart-route config show` prints parsed non-secret settings.
