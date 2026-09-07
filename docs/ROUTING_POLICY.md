# Routing policy

Hard gates exclude unavailable, forbidden, recursive Auto, unsupported reasoning, modality/tool/context/auth, and explicit budget violations. Unknown capabilities are preserved; default policy allows them with unverified evidence, while `unknown_capabilities = "exclude-required"` makes required unknown features fail closed.

Eligible profiles must meet policy quality floor. Remaining penalty is:

```text
consumption_weight * expected_consumption
+ quality_weight * (1 - suitability)
+ latency_weight * expected_latency
+ switch_weight * unnecessary_switch_cost
```

Weights normalize to one. Missing relative consumption/latency use disclosed effort-order heuristics and cannot produce verified decisions. Hysteresis retains adequate current profile when improvement is below configured threshold. Significant model-capability failure permits reassessment; environment, credential, dependency, and transient service failures do not imply escalation.

Missing family metrics first receive versioned operational priors where a family card matches.
These values are unverified routing hypotheses, not calibrated probabilities or benchmark truth;
see [Model priors and capability cards](MODEL_PRIORS.md). Exact penalty ties use declared card
priority, then a stable profile-ID hash, never alphabetical model order.

Manual override precedes optimization but never bypasses hard gates. No eligible profile produces explicit error unless a configured fallback also passes every gate and quality floor.

## Local multilingual evaluation

The default evaluator is deterministic, local, and classifier-free. English and Italian vocabularies cover complexity, debugging, cross-cutting work, consequence, mechanical work, creativity, ambiguity, acceptance criteria, and coupling. Matching folds case and accents, treats punctuation as boundaries, and recognizes common inflections. It never uses prompt length as a difficulty proxy.

Recognized language-neutral tags are `[mechanical]`, `[debugging]`, `[cross-cutting]`, `[high-consequence]`, `[creative]`, `[ambiguous]`, and `[strict-verification]`. They enrich routing signals but cannot bypass capability gates. Integrations may also pass the same values through `TaskContext.routing_tags`, avoiding prompt markup.

Tags remain part of the downstream task by default. An integration may explicitly call `strip_routing_tags(text, enabled=True)` before forwarding; only recognized routing tags are removed. `LanguagePack` plus the optional `language_packs` argument to `evaluate_task` are the extension point for more languages. Vocabularies are heuristics: domain-specific phrasing, negation, and languages without a configured pack may need structured tags or the opt-in remote classifier.
