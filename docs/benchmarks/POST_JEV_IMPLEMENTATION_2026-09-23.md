# AGENT AMAR post-Jev implementation quantitative report

Generated: `2026-09-22T18:31:48.884369+00:00`

## Executive result

On the same **58 synthetic messages**, the implemented shared-decision architecture reduced generative decision calls from **83** to **7** in the high-confidence typed harness (**91.6% reduction**). It used **48** shared Jev-shaped calls; **10** messages remained entirely local.

The TypeSafe key was not configured at measurement time. Therefore live Jev latency, provider-reported tokens, availability, and semantic accuracy are **not available**. No vendor numbers or offline timings are presented as live measurements.

## Evidence status

| Measurement | Status |
|---|---|
| Local routing and orchestration | Measured |
| Provider-call counts | Measured with schema-valid offline adapters |
| Serialized state/request characters | Measured from production request builder |
| Jev network/model latency | Not measured — TypeSafe key absent |
| Jev provider token usage | Not measured — TypeSafe key absent |
| Jev decision accuracy/calibration | Not measured — TypeSafe key absent |

## Routing and call counts

| Metric | Value |
|---|---:|
| Messages | 58 |
| Pre-Jev generative decision calls | 83 |
| Post-Jev shared decision calls | 48 |
| Post-Jev generative fallbacks | 7 |
| Generative calls avoided | 76 |
| Generative-call reduction | 91.6% |
| Total remote-call reduction | 33.7% |
| Fully local messages | 10 |
| Maximum sequential remote decisions/email | 2 |

Pre-Jev generative calls by stage: `{"action": 27, "deadline": 10, "priority": 10, "triage": 36}`.

Post-Jev fallback calls by stage: `{"deadline": 7}`.

## Shared request size

| Metric | Mean | p50 | p95 |
|---|---:|---:|---:|
| Batched questions/request | 13.25 | 13.0 | 14.0 |
| Compact state characters | 780.9 | 753.5 | 1026.2 |
| Complete request characters | 4655.0 | 4555.5 | 5148.1 |

The exact serialized character volume **increased by 46.4%** relative to the captured pre-Jev generative prompts because each shared request carries the complete typed question schema. This is an optimization target, not a token measurement; only the provider can supply authoritative tokenizer usage.

## Local implementation overhead

Each mode contains **1160** measurements. Network and inference time are excluded.

| Mode | Mean | p50 | p95 |
|---|---:|---:|---:|
| Pre-Jev offline generative harness | 1.559 ms | 1.408 ms | 2.848 ms |
| Post-Jev shared typed harness | 3.301 ms | 3.082 ms | 5.607 ms |

The post path intentionally performs a local preview before its shared decision. Any local CPU overhead must be weighed against avoided network calls; the live end-to-end result is pending.

## Live Jev status

- Configured: `false`
- Model requested: `jev-latest`
- Live calls performed: `0`
- Reason: `TYPESAFE_API_KEY` is empty.

## Verification

- Full backend suite: **822 passed**.
- One shared call for all four agents: verified.
- Repeated-request cache: verified.
- Raw email text and credentials are not written to this report or its companion JSON.

## Required next measurement

After placing the TypeSafe key in `backend/.env`, rerun this benchmark with live mode added/enabled and record provider latency, input/output tokens, confidence calibration, exact-match accuracy, fallback rate, and end-to-end p50/p95. Until then, no claim about actual Jev speed or token consumption is supported by this report.
