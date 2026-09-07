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

Manual override precedes optimization but never bypasses hard gates. No eligible profile produces explicit error unless a configured fallback also passes every gate and quality floor.

