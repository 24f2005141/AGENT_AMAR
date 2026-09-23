# AGENT AMAR current decision-model baseline

Generated: `2026-09-22T16:25:25.213942+00:00`

## Executive summary

This is the pre-Jev quantitative baseline for AGENT AMAR's current hybrid 
decision path: deterministic rules first, Gemini for uncertain decisions, and 
Groq as the configured fallback. Measurements use synthetic repository fixtures; 
no user email, prompt, response, or credential is stored in this report.

### Routing observation

The bundled synthetic triage set contains **58** messages. With the current local artifacts, **36** messages (**62.1%**) route to an LLM. A trained local ML model is **not present**.

| Routing method | Messages |
|---|---:|
| `deterministic` | 22 |
| `llm` | 36 |

### Gemini repair status

The Gemini configuration is operational. The retired-model `404 NOT_FOUND` and 
depleted-account `402 RESOURCE_EXHAUSTED` blockers are resolved; AGENT AMAR now 
uses `gemini-3.5-flash-lite` with a working key. The benchmark 
captured **10 valid Gemini responses** with provider-reported token usage. The remaining **10** failures were provider-side capacity or deadline responses; they are included in the availability measurements below.

## Live provider measurements

Each provider received the same four production prompts captured from the Triage, Action, Deadline, and Priority agents. Latency is non-streaming wall-clock time around the same SDK request shape used by the application. Token counts come from the provider response metadata.

### Gemini — `gemini-3.5-flash-lite`

| Task | Success | Schema valid | Scenario match* | Latency p50 | Latency p95 | Mean input tokens | Mean output tokens | Mean total tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| triage | 2/5 | 40% | 40% | 33870.9 ms | 35452.1 ms | 457.0 | 58.0 | 515.0 |
| action | 2/5 | 40% | 0% | 33726.5 ms | 34321.8 ms | 458.0 | 12.0 | 470.0 |
| deadline | 2/5 | 40% | 0% | 31996.4 ms | 39113.9 ms | 510.0 | 13.0 | 523.0 |
| priority | 4/5 | 80% | 80% | 33619.3 ms | 44309.1 ms | 220.0 | 32.5 | 252.5 |

Overall: **10/20 successful calls**; p50 **33726.5 ms**, p95 **43074.7 ms**; mean **402.6 total tokens/call**.

Failures: **10** (ServerError=10; HTTP 503=6, HTTP 504=4; UNAVAILABLE=6, DEADLINE_EXCEEDED=4); failure-response p50 **16560.7 ms**, p95 **46414.3 ms**.

### Groq — `openai/gpt-oss-20b`

| Task | Success | Schema valid | Scenario match* | Latency p50 | Latency p95 | Mean input tokens | Mean output tokens | Mean total tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| triage | 4/5 | 80% | 80% | 827.7 ms | 918.3 ms | 536.0 | 224.2 | 760.2 |
| action | 4/5 | 80% | 20% | 892.0 ms | 1310.0 ms | 524.0 | 275.5 | 799.5 |
| deadline | 3/5 | 60% | 60% | 7449.1 ms | 9201.1 ms | 565.0 | 487.3 | 1052.3 |
| priority | 0/5 | 0% | 0% | n/a ms | n/a ms | n/a | n/a | n/a |

Overall: **11/20 successful calls**; p50 **930.7 ms**, p95 **8422.4 ms**; mean **854.2 total tokens/call**.

Failures: **9** (BadRequestError=9; HTTP 400=9); failure-response p50 **1164.6 ms**, p95 **4476.8 ms**.

*Scenario match is a narrow check for these four synthetic cases, not an accuracy benchmark.

## Deterministic fast-path measurements

These measurements isolate local rule execution with no network or model call.

| Agent | Iterations | Mean | p50 | p95 |
|---|---:|---:|---:|---:|
| triage | 500 | 0.163 ms | 0.143 ms | 0.226 ms |
| action | 500 | 0.676 ms | 0.228 ms | 1.337 ms |
| deadline | 500 | 0.472 ms | 0.378 ms | 0.946 ms |
| priority | 500 | 0.195 ms | 0.165 ms | 0.313 ms |

## Test conditions

- Platform: `Windows-11-10.0.26200-SP0`
- Python: `3.13.15`
- Logical CPUs: `8`
- `google-genai`: `2.24.0`
- `openai`: `3.17.0`
- Runs: `5` measured calls per provider/task after `1` warm-up call(s) per provider
- Max output tokens: `512`
- Calls were sequential; concurrency was intentionally excluded.
- Network conditions are those of this laptop at measurement time.
- Groq was measured directly as the configured fallback. Provider calls were measured separately, so this report does not claim an end-to-end failed-primary fallback latency.

## Interpretation for the future Jev comparison

Use the same four task fixtures and report: p50/p95 latency, input/output/total tokens, schema-valid rate, and scenario-match rate. Jev should be compared only on these bounded decision tasks; reply drafting remains a generative-model workload.

The companion JSON file contains the anonymized per-call observations and prompt fingerprints needed to confirm that a future run used the same fixtures.
