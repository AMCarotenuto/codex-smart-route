# Architecture

`discovery` creates capability records from Codex App Server `model/list` or explicit files. Each advertised reasoning effort becomes one `model@effort` profile. Unknown fields remain `None`.

`evaluation` extracts general task signals. Clear cases use deterministic rules; optional remote classification returns structured suitability scores and never sees economic weights. `router` runs hard gates, quality floor, normalized weighted penalty, and switch hysteresis in that order.

`state` caches by session, task, phase, relevant-context fingerprint, policy version, capability version, availability, and mode. Tool-loop continuations reuse matching decisions. `audit` stores identifiers and hashes, not task text.

`adapters` apply decisions. `CodexCliAdapter` starts `codex exec` with both model and reasoning configuration. Experimental JSONL App Server proxy patches `turn/start` while forwarding all other traffic.

Core has no transport, credential, RTK, Caveman, CaveCrew, scheduler, backlog, or subagent dependency.

