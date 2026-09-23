# AGENT AMAR pre-Jev vs post-Jev comparison

Generated: `2026-09-22T18:31:48.884369+00:00`

## Bottom line

The implementation changes the uncertain-email path from as many as **4 sequential generative decisions** to at most **2 sequential remote decisions** (one shared Jev call plus an occasional generative fallback) in the high-confidence typed harness. Across the 58-message census, generative calls fell from **83** to **7**.

This confirms the architectural call reduction. It does not yet establish real Jev latency, token usage, or accuracy because the TypeSafe key was absent.

## Previous live provider baseline

| Provider | Success | p50 latency | p95 latency | Mean total tokens/success |
|---|---:|---:|---:|---:|
| Gemini `gemini-3.5-flash-lite` | 10/20 | 33726.5 ms | 43074.7 ms | 402.6 |
| Groq `openai/gpt-oss-20b` | 11/20 | 930.7 ms | 8422.4 ms | 854.2 |

These are the saved live measurements from the pre-Jev report and are not rerun here.

## Architecture comparison on the same 58 messages

| Metric | Previous path | Current shared-Jev path | Change |
|---|---:|---:|---:|
| Generative decision calls | 83 | 7 | -91.6% |
| All remote decision calls | 83 | 55 | -33.7% |
| Max sequential remote calls/email | 4 | 2 | 2 fewer |
| Messages with zero remote decision | 10 | 10 | +0 |
| Serialized request characters | 152675 | 223442 | +46.4% |

## Local-only latency comparison

| Mode | p50 | p95 | Interpretation |
|---|---:|---:|---|
| Previous offline harness | 1.408 ms | 2.848 ms | Python/rules/schema cost only |
| Current shared typed harness | 3.082 ms | 5.607 ms | Includes local preview + request construction |

These local timings cannot be subtracted from the previous live Gemini/Groq latency. The current end-to-end network comparison remains pending.

## Token comparison

- Previous Gemini successes averaged **402.6 total tokens/call**.
- Previous Groq successes averaged **854.2 total tokens/call**.
- Current Jev provider-reported tokens: **not measured** (key absent).
- Current exact serialized request volume: **223442 characters** across 48 shared requests.

It would be invalid to convert the character count into an authoritative token count or claim a token percentage before observing TypeSafe's usage metadata.
The current typed schema increased serialized characters even though it reduced remote calls, so shortening/reusing question definitions is the clearest next payload optimization.

## What is proven vs pending

Proven:

- **91.6% fewer generative decision calls** in the typed high-confidence routing harness.
- One shared typed request is reused by Triage, Action, Deadline and Priority.
- Fully local messages still consume zero provider calls.
- Cache reuse consumes zero additional decision calls.

Pending:

- Real Jev p50/p95 and availability.
- Provider-reported Jev tokens and actual monetary cost.
- Accuracy, calibration and real fallback rate on the labeled dataset.
- End-to-end comparison against Gemini and Groq under the same network conditions.
