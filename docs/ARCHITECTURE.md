# Architecture

`discovery` creates capability records from Codex App Server `model/list` or explicit files. Each advertised reasoning effort becomes one `model@effort` profile. Unknown fields remain `None`.

App Server discovery follows every opaque `nextCursor`, rejects repeated or malformed cursors, and
deduplicates models deterministically. Model-level values take precedence over provider-level values
from `modelProvider/capabilities/read`; provider data only fills missing fields. Each populated runtime
field records `(field, source, confidence)` in `field_evidence`. Older catalogs without
`inputModalities` retain an empty tuple meaning unknown, not an inferred text-only capability. Default
policy permits that unknown evidence; `unknown_capabilities = "exclude-required"` rejects required
modalities as unverified. Virtual `Auto` entries never become concrete candidates.
Current provider flags (`imageGeneration`, `namespaceTools`, `webSearch`) remain distinct in
`provider_capabilities`; they do not imply generic or parallel tool support.

`evaluation` extracts general task signals. Clear cases use deterministic rules; optional remote classification returns structured suitability scores and never sees economic weights. `router` runs hard gates, quality floor, normalized weighted penalty, and switch hysteresis in that order.

`state` caches by session, task, phase, relevant-context fingerprint, policy version, capability version, availability, and mode. Tool-loop continuations reuse matching decisions. `audit` stores identifiers and hashes, not task text.

`adapters` apply decisions. `CodexCliAdapter` starts `codex exec` with both model and reasoning configuration. Experimental JSONL App Server proxy patches `turn/start` while forwarding all other traffic.

Core has no transport, credential, RTK, Caveman, CaveCrew, scheduler, backlog, or subagent dependency.

`config` deep-merges built-in, user, nearest repository, and environment layers. Explicit
`--config-file` selects isolated single-file mode. Repository identity namespaces runtime state,
cache, and audit below shared Smart Route home; CLI catalog flags override persistent catalog source.
