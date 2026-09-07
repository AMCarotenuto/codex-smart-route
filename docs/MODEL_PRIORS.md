# Model priors and capability cards

Runtime discovery remains authoritative for model availability, reasoning efforts, and input
modalities. It often supplies no comparable quality, consumption, or latency data. Smart Route
therefore fills missing values from a transport-independent family registry in
`codex_smart_route/priors.py`.

Bundled values are **operational priors**: versioned routing hypotheses, not benchmark truth,
calibrated probabilities, prices, or measured savings. Their confidence is `unverified`. Each
card exposes family, relative estimates, concrete strengths and weaknesses, source, confidence,
version, and tie-break priority through `smart-route models`, `smart-route profiles`, and routing
decision candidates.

Family patterns do not form an allowlist. A newly discovered model without a matching card stays
routable under `unknown_capabilities = "allow-unverified"`; its family is `unknown`, metrics stay
unset, and effort heuristics remain visible as unverified evidence.

## Precedence

Values supplied by a repository catalog take precedence over bundled priors. Per-model
`capability_overrides` from the selected user config take final precedence and never mutate the
bundled registry. Metric overrides receive `reported` confidence and a content-derived
`user-override:<hash>` version unless metadata is explicitly supplied.

```toml
[capability_overrides."gpt-5.6-terra"]
relative_quality = 0.81
relative_consumption = 0.46
relative_latency = 0.40
prior_strengths = ["repository-specific integration work"]
prior_weaknesses = ["unmeasured on long-context tasks"]
tie_break_priority = 55
```

Changing either `capability_version` or `prior_version` changes the routing cache key. Override
content also derives a new version, preventing stale decisions after local edits.

## Deterministic ties

Eligible profiles sort by normalized penalty. Exact ties use higher declared
`tie_break_priority`, then SHA-256 of the complete profile ID. This rule is deterministic,
independent of discovery order, and does not silently privilege alphabetical model IDs.

