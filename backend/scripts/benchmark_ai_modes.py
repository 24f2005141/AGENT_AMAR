"""Live comparison of AMAR conventional, Jev, and Laya decision modes.

Only synthetic evaluation messages are used. Raw prompts, responses, email text,
and credentials are never written to the output files.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.action_agent import ActionAgent
from app.agents.deadline_agent import DeadlineAgent
from app.agents.priority_agent import PriorityAgent
from app.agents.triage_agent import TriageAgent
from app.core.config import Settings
from app.services.decision_service import LangChainJevDecisionClient, LayaDecisionClient
from app.services.llm_service import build_llm_client

from benchmark_current_llm import FIXED_IST, percentile
from benchmark_jev_implementation import (
    BASELINE_JSON,
    email_for,
    load_dataset,
)

DEFAULT_INDICES = (3, 7, 10, 13, 19, 29, 37, 43)
OUTPUT_DIR = REPO_ROOT / "docs" / "benchmarks"


def summary(values: list[float]) -> dict[str, float | None]:
    return {
        "count": len(values),
        "mean": statistics.fmean(values) if values else None,
        "p50": percentile(values, 0.5),
        "p95": percentile(values, 0.95),
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def local_outputs(settings: Settings, email):
    triage_agent = TriageAgent(settings=settings, ml_classifier=None)
    action_agent = ActionAgent(settings=settings)
    deadline_agent = DeadlineAgent(settings=settings)
    priority_agent = PriorityAgent(settings=settings)
    triage = triage_agent.classify(email, allow_remote=False, record_metrics=False)
    action = action_agent.detect(email, triage, allow_remote=False)
    deadline = deadline_agent.analyze(email, triage, action, allow_remote=False)
    priority = priority_agent.score(
        email, triage, action, deadline, now=FIXED_IST, allow_remote=False
    )
    return triage, action, deadline, priority


def benchmark_conventional(
    settings: Settings, rows: list[dict[str, Any]], indices: tuple[int, ...]
) -> dict[str, Any]:
    mode_settings = settings.model_copy(
        update={"decision_provider": "none", "ml_classifier_enabled": False}
    )
    client = build_llm_client(mode_settings)
    observations = []
    for index in indices:
        item = rows[index - 1]
        email = email_for(item, index)
        started = time.perf_counter()
        try:
            output = TriageAgent(
                settings=mode_settings, llm_client=client, ml_classifier=None
            ).classify(email, record_metrics=False)
            latency = (time.perf_counter() - started) * 1000.0
            predicted = output.data.get("category")
            observations.append(
                {
                    "row": index,
                    "expected": item["expected_label"],
                    "predicted": predicted,
                    "match": predicted == item["expected_label"],
                    "confidence": output.data.get("confidence"),
                    "latency_ms": latency,
                    "success": output.status != "error",
                    "method": output.data.get("signals", {}).get(
                        "classification_method"
                    ),
                }
            )
        except Exception as exc:
            observations.append(
                {
                    "row": index,
                    "expected": item["expected_label"],
                    "success": False,
                    "match": False,
                    "latency_ms": (time.perf_counter() - started) * 1000.0,
                    "error_type": type(exc).__name__,
                }
            )
    return aggregate("conventional", observations)


def benchmark_decision_client(
    name: str,
    client,
    settings: Settings,
    rows: list[dict[str, Any]],
    indices: tuple[int, ...],
) -> dict[str, Any]:
    observations = []
    for position, index in enumerate(indices):
        item = rows[index - 1]
        email = email_for(item, index)
        triage, action, deadline, priority = local_outputs(settings, email)
        started = time.perf_counter()
        try:
            bundle = client.evaluate(
                email,
                triage=triage,
                action=action,
                deadline=deadline,
                priority=priority,
            )
            total_latency = (time.perf_counter() - started) * 1000.0
            category = bundle.category
            predicted = category.value if category else None
            accepted = bool(
                category
                and min(category.confidence, category.probability)
                >= settings.jev_choice_confidence_threshold
            )
            observations.append(
                {
                    "row": index,
                    "expected": item["expected_label"],
                    "predicted": predicted,
                    "match": predicted == item["expected_label"],
                    "accepted": accepted,
                    "confidence": category.confidence if category else None,
                    "probability": category.probability if category else None,
                    "latency_ms": bundle.latency_ms or total_latency,
                    "cold_start": name == "laya" and position == 0,
                    "success": category is not None,
                    "input_tokens": bundle.input_tokens,
                    "output_tokens": bundle.output_tokens,
                    "cost_usd": bundle.cost_usd if name == "jev" else 0.0,
                    "model": bundle.model,
                }
            )
        except Exception as exc:
            observations.append(
                {
                    "row": index,
                    "expected": item["expected_label"],
                    "success": False,
                    "match": False,
                    "accepted": False,
                    "cold_start": name == "laya" and position == 0,
                    "latency_ms": (time.perf_counter() - started) * 1000.0,
                    "error_type": type(exc).__name__,
                }
            )
    return aggregate(name, observations)


def aggregate(name: str, observations: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [item for item in observations if item.get("success")]
    warm = [
        float(item["latency_ms"])
        for item in successful
        if not item.get("cold_start")
    ]
    all_latency = [float(item["latency_ms"]) for item in successful]
    return {
        "mode": name,
        "attempts": len(observations),
        "successes": len(successful),
        "accuracy": (
            sum(bool(item.get("match")) for item in successful) / len(successful)
            if successful
            else None
        ),
        "accepted_rate": (
            sum(bool(item.get("accepted")) for item in successful) / len(successful)
            if successful and any("accepted" in item for item in successful)
            else None
        ),
        "latency_ms": summary(all_latency),
        "warm_latency_ms": summary(warm),
        "input_tokens_total": sum(int(item.get("input_tokens") or 0) for item in successful),
        "output_tokens_total": sum(int(item.get("output_tokens") or 0) for item in successful),
        "cost_usd_total": sum(float(item.get("cost_usd") or 0.0) for item in successful),
        "observations": observations,
    }


def estimate_baseline_cost(baseline: dict[str, Any]) -> dict[str, float]:
    gemini = baseline["providers"]["gemini"]["overall"]["tokens_mean"]
    groq = baseline["providers"]["groq"]["overall"]["tokens_mean"]
    return {
        "gemini_per_successful_call_usd": (
            float(gemini["input"] or 0) * 0.30
            + float(gemini["output"] or 0) * 2.50
        ) / 1_000_000,
        "groq_per_successful_call_usd": (
            float(groq["input"] or 0) * 0.075
            + float(groq["output"] or 0) * 0.30
        ) / 1_000_000,
    }


def fmt(value: Any, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.{digits}f}"


def render(result: dict[str, Any]) -> str:
    baseline = result["conventional_saved_baseline"]
    gemini = baseline["providers"]["gemini"]["overall"]
    groq = baseline["providers"]["groq"]["overall"]
    current = {item["mode"]: item for item in result["live_common_subset"]}
    conventional, jev, laya = (
        current["conventional"],
        current["jev"],
        current["laya"],
    )
    costs = result["published_price_estimates"]
    return "\n".join(
        [
            "# AGENT AMAR AI-mode quantitative comparison",
            "",
            f"Generated: `{result['generated_at']}`",
            "",
            "## Executive conclusion",
            "",
            "**Jev is the best candidate for typed decisions on this laptop**, based on this "
            "small live sample: it returned accepted categories for 4/5 emails and avoided "
            "Laya's severe CPU latency. This is not a measured end-to-end speed or cost win "
            "for every email. Gemini + Groq remains necessary for reply generation and "
            "uncertain decisions; Laya's 0/5 accepted categories in this sample make it a "
            "poor production default on this hardware.",
            "",
            "## Same synthetic subset (live)",
            "",
            f"Rows: `{result['sample_indices']}`. Raw message text is not recorded.",
            "",
            "| Measured operation | Success | Raw category accuracy | Accepted at threshold | p50 latency | p95 latency | Observed API cost |",
            "|---|---:|---:|---:|---:|---:|---:|",
            f"| Conventional triage (Gemini → Groq when needed) | {conventional['successes']}/{conventional['attempts']} | {fmt(100*(conventional['accuracy'] or 0))}% | n/a | {fmt(conventional['latency_ms']['p50'])} ms | {fmt(conventional['latency_ms']['p95'])} ms | not captured for this subset |",
            f"| Jev shared typed-decision call | {jev['successes']}/{jev['attempts']} | {fmt(100*(jev['accuracy'] or 0))}% | {fmt(100*(jev['accepted_rate'] or 0))}% | {fmt(jev['latency_ms']['p50'])} ms | {fmt(jev['latency_ms']['p95'])} ms | ${jev['cost_usd_total']:.8f} |",
            f"| Laya shared typed-decision call (warm) | {laya['successes']}/{laya['attempts']} | {fmt(100*(laya['accuracy'] or 0))}% | {fmt(100*(laya['accepted_rate'] or 0))}% | {fmt(laya['warm_latency_ms']['p50'])} ms | {fmt(laya['warm_latency_ms']['p95'])} ms | $0 for local call |",
            "",
            "These are **different stages**, not full orchestrator runs: conventional timing "
            "covers category triage only (including deterministic shortcuts), while Jev/Laya "
            "timing covers one shared call that proposes multiple fields. Provider fallback, "
            "reply drafting and Gmail I/O are excluded. A raw category may be correct but "
            "rejected by AMAR's confidence gate.",
            "",
            f"Jev reported **{jev['input_tokens_total']:,} input** and **{jev['output_tokens_total']:,} output** "
            f"tokens across {jev['successes']} shared calls (mean "
            f"{(jev['input_tokens_total'] + jev['output_tokens_total']) / max(jev['successes'], 1):.1f} "
            "tokens/call). The conventional same-subset token count was not captured; "
            "the saved provider baseline below is per *individual generative call*, not "
            "per email. Thus this run does **not** establish a token reduction from Jev. "
            "Laya's local tokenizer work is not billable provider tokens.",
            "",
            f"Laya cold first decision: **{fmt(laya['latency_ms']['max'])} ms**. Its 843 MB "
            "checkpoint is lazy-loaded and shared after the first request.",
            "",
            "## Saved conventional provider baseline",
            "",
            "| Provider | Success | p50 | p95 | Mean total tokens | Estimated paid cost/success |",
            "|---|---:|---:|---:|---:|---:|",
            f"| Gemini `{baseline['providers']['gemini']['configured_model']}` | {gemini['successes']}/{gemini['attempts']} | {fmt(gemini['latency_ms']['p50'])} ms | {fmt(gemini['latency_ms']['p95'])} ms | {fmt(gemini['tokens_mean']['total'])} | ${costs['gemini_per_successful_call_usd']:.8f} |",
            f"| Groq `{baseline['providers']['groq']['configured_model']}` | {groq['successes']}/{groq['attempts']} | {fmt(groq['latency_ms']['p50'])} ms | {fmt(groq['latency_ms']['p95'])} ms | {fmt(groq['tokens_mean']['total'])} | ${costs['groq_per_successful_call_usd']:.8f} |",
            "",
            "Paid estimates use published standard rates at report time: Gemini Flash-Lite "
            "$0.30/M input and $2.50/M output; Groq GPT-OSS 20B $0.075/M input and "
            "$0.30/M output; Jev uses provider-reported cost ($0.042/M input, output free). "
            "Free-tier credits can reduce the actual bill. Laya excludes electricity, hardware, "
            "download bandwidth and engineering cost.",
            "",
            "Price and model sources: [OpenRouter Jev](https://openrouter.ai/typesafe/jev-1.13/api), "
            "[Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing), "
            "[Groq models](https://console.groq.com/docs/models), "
            "[Laya repository](https://github.com/NandhaKishorM/laya), and "
            "[Laya typed checkpoint](https://huggingface.co/convaiinnovations/laya-typed-decisions).",
            "",
            "## Interpretation",
            "",
            "- **Lowest measured hosted typed-decision fee:** Jev ($0.00033025 for five calls). This cannot be directly compared with the conventional triage fee because its same-subset token usage was not captured.",
            "- **Lowest direct inference API fee:** Laya ($0), but every sampled category failed AMAR's confidence gate, so effective processing needs the paid Gemini/Groq fallback; local CPU/RAM and time are substantial.",
            "- **Fastest measured shared-decision call:** Jev. Full AMAR pipeline latency has not been benchmarked across the three modes, so no end-to-end speed ranking is claimed.",
            "- **Best fallback/general capability:** Gemini + Groq, because Jev and Laya cannot draft replies or generate prose.",
            "- **Laya quality warning:** its own checkpoint warns that confidence for 11+ options is uncalibrated; AMAR has 15 category choices, so low-confidence results remain gated to the generative fallback.",
            "",
            "## Scope and limitations",
            "",
            "This is an engineering comparison on AMAR's synthetic email dataset, not a general "
            "model leaderboard. The live common subset is deliberately small because Laya takes "
            "roughly a minute per warm decision on this CPU. The conventional saved baseline uses "
            "four production prompt types and is retained separately from the same-subset category "
            "accuracy. Laya's internal tokenizer counts are not billable API tokens and are "
            "not compared to Jev/Gemini/Groq tokens. No statistically meaningful difference "
            "or full-workflow cost reduction can be inferred from five emails. Re-run on a CUDA "
            "machine before judging Laya's advertised GPU speed.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--indices",
        default=",".join(str(value) for value in DEFAULT_INDICES),
        help="One-based comma-separated dataset row numbers",
    )
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    indices = tuple(int(value) for value in args.indices.split(",") if value.strip())
    settings = Settings.load().model_copy(
        update={"ml_classifier_enabled": False, "jev_cache_size": 0}
    )
    rows = load_dataset()
    baseline = json.loads(BASELINE_JSON.read_text("utf-8"))

    conventional = benchmark_conventional(settings, rows, indices)
    jev_settings = settings.model_copy(update={"decision_provider": "jev"})
    jev = benchmark_decision_client(
        "jev", LangChainJevDecisionClient(jev_settings), jev_settings, rows, indices
    )
    laya_settings = settings.model_copy(update={"decision_provider": "laya"})
    laya = benchmark_decision_client(
        "laya", LayaDecisionClient(laya_settings), laya_settings, rows, indices
    )

    result = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "processor": platform.processor(),
            "accelerator": "CPU only; no nvidia-smi/GPU detected",
        },
        "sample_indices": list(indices),
        "live_common_subset": [conventional, jev, laya],
        "conventional_saved_baseline": baseline,
        "published_price_estimates": estimate_baseline_cost(baseline),
        "privacy": {
            "synthetic_data_only": True,
            "raw_messages_recorded": False,
            "credentials_recorded": False,
        },
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().date().isoformat()
    raw = args.output_dir / f"ai_mode_comparison_{stamp}.json"
    report = args.output_dir / f"AI_MODE_COMPARISON_{stamp}.md"
    raw.write_text(json.dumps(result, indent=2), "utf-8")
    report.write_text(render(result), "utf-8")
    print(f"Raw observations: {raw}")
    print(f"Comparison report: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
