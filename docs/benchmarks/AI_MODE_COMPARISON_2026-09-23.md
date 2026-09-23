# AGENT AMAR AI-mode quantitative comparison

Generated: `2026-09-22T20:01:20.559931+00:00`

## Executive conclusion

**Jev is the best candidate for typed decisions on this laptop**, based on this small live sample: it returned accepted categories for 4/5 emails and avoided Laya's severe CPU latency. This is not a measured end-to-end speed or cost win for every email. Gemini + Groq remains necessary for reply generation and uncertain decisions; Laya's 0/5 accepted categories in this sample make it a poor production default on this hardware.

## Same synthetic subset (live)

Rows: `[3, 10, 19, 37, 43]`. Raw message text is not recorded.

| Measured operation | Success | Raw category accuracy | Accepted at threshold | p50 latency | p95 latency | Observed API cost |
|---|---:|---:|---:|---:|---:|---:|
| Conventional triage (Gemini → Groq when needed) | 5/5 | 60.0% | n/a | 2135.4 ms | 3444.1 ms | not captured for this subset |
| Jev shared typed-decision call | 5/5 | 100.0% | 80.0% | 418.5 ms | 1269.8 ms | $0.00033025 |
| Laya shared typed-decision call (warm) | 5/5 | 60.0% | 0.0% | 85216.7 ms | 93418.3 ms | $0 for local call |

These are **different stages**, not full orchestrator runs: conventional timing covers category triage only (including deterministic shortcuts), while Jev/Laya timing covers one shared call that proposes multiple fields. Provider fallback, reply drafting and Gmail I/O are excluded. A raw category may be correct but rejected by AMAR's confidence gate.

Jev reported **7,863 input** and **2,431 output** tokens across five shared calls (mean 2,058.8 tokens/call). The conventional same-subset token count was not captured; the saved provider baseline below is per *individual generative call*, not per email. Thus this run does **not** establish a token reduction from Jev. Laya's local tokenizer work is not billable provider tokens.

Laya cold first decision: **159142.5 ms**. Its 843 MB checkpoint is lazy-loaded and shared after the first request.

## Saved conventional provider baseline

| Provider | Success | p50 | p95 | Mean total tokens | Estimated paid cost/success |
|---|---:|---:|---:|---:|---:|
| Gemini `gemini-3.5-flash-lite` | 10/20 | 33726.5 ms | 43074.7 ms | 402.6 | $0.00018590 |
| Groq `openai/gpt-oss-20b` | 11/20 | 930.7 ms | 8422.4 ms | 854.2 | $0.00013486 |

Paid estimates use published standard rates at report time: Gemini Flash-Lite $0.30/M input and $2.50/M output; Groq GPT-OSS 20B $0.075/M input and $0.30/M output; Jev uses provider-reported cost ($0.042/M input, output free). Free-tier credits can reduce the actual bill. Laya excludes electricity, hardware, download bandwidth and engineering cost.

Price and model sources: [OpenRouter Jev](https://openrouter.ai/typesafe/jev-1.13/api), [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing), [Groq models](https://console.groq.com/docs/models), [Laya repository](https://github.com/NandhaKishorM/laya), and [Laya typed checkpoint](https://huggingface.co/convaiinnovations/laya-typed-decisions).

## Interpretation

- **Lowest measured hosted typed-decision fee:** Jev ($0.00033025 for five calls). This cannot be directly compared with the conventional triage fee because its same-subset token usage was not captured.
- **Lowest direct inference API fee:** Laya ($0), but every sampled category failed AMAR's confidence gate, so effective processing needs the paid Gemini/Groq fallback; local CPU/RAM and time are substantial.
- **Fastest measured shared-decision call:** Jev. Full AMAR pipeline latency has not been benchmarked across the three modes, so no end-to-end speed ranking is claimed.
- **Best fallback/general capability:** Gemini + Groq, because Jev and Laya cannot draft replies or generate prose.
- **Laya quality warning:** its own checkpoint warns that confidence for 11+ options is uncalibrated; AMAR has 15 category choices, so low-confidence results remain gated to the generative fallback.

## Scope and limitations

This is an engineering comparison on AMAR's synthetic email dataset, not a general model leaderboard. The live common subset is deliberately small because Laya takes roughly a minute per warm decision on this CPU. The conventional saved baseline uses four production prompt types and is retained separately from the same-subset category accuracy. Laya's internal tokenizer counts are not billable API tokens and are not compared to Jev/Gemini/Groq tokens. No statistically meaningful difference or full-workflow cost reduction can be inferred from five emails. Re-run on a CUDA machine before judging Laya's advertised GPU speed.
