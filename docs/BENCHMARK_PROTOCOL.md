# Benchmark and calibration protocol

Benchmark suite is provider-neutral, versioned, reproducible, and opt-in for live execution. Public
manifest lives in `codex_smart_route/benchmark_data/manifest.v1.json`; result contract lives beside
it in `result.v1.schema.json`. Manifest digest, suite version, policy version, and priors version make
comparisons explicit across policy, prior, and task-suite revisions.

## Offline CI path

Mock mode exercises manifest validation, acceptance evaluators, aggregation, regression comparison,
redaction contract, calibration, and result serialization. It never starts external processes:

```bash
python -m codex_smart_route.benchmark mock --profile fixed-fast@low --profile fixed-capable@high --policy-version balanced@1 --priors-version families@1 --report benchmark-mock.json
python -m codex_smart_route.benchmark validate benchmark-mock.json
```

Each task declares public fixtures, language, task class, requirements, and machine-checkable
acceptance. Suite covers mechanical edits, standard implementation, multi-file refactoring,
debugging/investigation, architecture/reasoning, tool-heavy work, and image input where runner
supports it. English and Italian prompts are both present.

## Live runner protocol

Live mode requires environment `SMART_ROUTE_BENCHMARK_LIVE=1` plus `--consent-live`. It rejects
fewer than two distinct fixed profiles and always adds `auto`. It can consume account quota.
Benchmark core has no provider SDK: operator supplies executable command as JSON string array.
Harness starts command once per task and variant, writes one JSON request to stdin, reads one JSON
outcome from stdout, and never persists command, prompt, stdout, stderr, environment, or repository
path.

Windows PowerShell example (replace runner with reviewed local adapter):

```powershell
$env:SMART_ROUTE_BENCHMARK_LIVE = "1"
python -m codex_smart_route.benchmark live --consent-live --runner-command-json '["python","path/to/reviewed_runner.py"]' --profile MODEL_A@EFFORT --profile MODEL_B@EFFORT --policy-version balanced@1 --priors-version families@1 --report benchmark-live.json
```

Request on stdin:

```json
{"protocol_version":"1.0","variant":"auto","task":{"id":"...","prompt":"...","fixtures":{},"requirements":{}}}
```

Runner response:

```json
{
  "status": "completed",
  "answer": "candidate answer used locally then discarded",
  "profile_evidence": {"requested":"auto","selected":"MODEL@EFFORT","forwarded":"MODEL@EFFORT","confirmed":"unverified"},
  "attempts": [
    {"kind":"classification","status":"completed","latency_ms":12,"usage":{"input_tokens":8,"output_tokens":3}},
    {"kind":"execution","status":"completed","latency_ms":240,"usage":{"input_tokens":120,"output_tokens":30,"reasoning_tokens":20}},
    {"kind":"correction","status":"completed","latency_ms":80,"usage":{"input_tokens":40,"output_tokens":10}},
    {"kind":"verification","status":"completed","latency_ms":15,"usage":{}}
  ],
  "suitability_score": 0.72,
  "prior": "family-registry-v1"
}
```

`status` may be `unsupported` for optional image capability. Attempt kinds are `classification`,
`execution`, `correction`, and `verification`. Usage signals (`input_tokens`, `output_tokens`,
`reasoning_tokens`, cache tokens, `request_count`, and `tool_calls`) remain separate from latency and
cover whole run. Currency estimates are forbidden because access-mode-specific trustworthy rates are not
part of protocol.

Runner must keep requested, selected, forwarded, and runtime-confirmed profile evidence distinct.
`confirmed` stays `unverified` when provider exposes no trustworthy runtime metadata. Harness rejects
a requested profile that differs from benchmark variant.

## Interpretation and privacy

Result stores completion, independent acceptance, retries, accepted-to-failed regressions, usage
signals, runner-reported latency, and harness wall time. Pass prior result using `--baseline` to mark
regressions. Suitability buckets and prior groups report observed acceptance frequency. They are
descriptive calibration evidence, not probabilities.

Report contains task IDs and public metadata, never prompts, answers, artifacts, logs, paths,
commands, environment, credentials, or monetary guesses. Keep provider/account-specific live reports
outside repository unless intentionally publishing a reviewed, reproducible result set. Benchmark
code never reads reports as router configuration and never changes policy or priors automatically.

Any README performance or savings claim must link exact manifest/result set, runner methodology,
acceptance process, provider/access context, policy/prior versions, and benchmark commit.
