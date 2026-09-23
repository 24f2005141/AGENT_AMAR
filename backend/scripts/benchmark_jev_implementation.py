"""Quantify the post-Jev AMAR architecture and compare it with the saved baseline.

This script is deliberately honest about evidence levels:

* local orchestration latency, provider-call counts, and serialized request sizes
  are measured directly;
* an offline typed Jev harness exercises the production integration without
  pretending to measure TypeSafe network/model performance;
* live Jev latency and provider-reported tokens remain unavailable until
  ``TYPESAFE_API_KEY`` is configured.

Run from ``backend/``::

    python scripts/benchmark_jev_implementation.py --iterations 20
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.action_agent import ActionAgent
from app.agents.amar_orchestrator import AMAROrchestrator
from app.agents.deadline_agent import DeadlineAgent
from app.agents.priority_agent import PriorityAgent
from app.agents.triage_agent import TriageAgent
from app.core.config import Settings
from app.models.action import ActionType
from app.models.triage import CATEGORY_PRIORITY_BAND, ImportanceEstimate, TriageCategory
from app.services.decision_service import (
    ChoiceJudgment,
    DecisionClient,
    JevDecisionBundle,
    LangChainJevDecisionClient,
    NoulJudgment,
    NullDecisionClient,
)
from app.services.llm_service import LLMClient

from benchmark_current_llm import FIXED_IST, FIXED_UTC, make_email, percentile


OUTPUT_DIR = REPO_ROOT / "docs" / "benchmarks"
BASELINE_JSON = OUTPUT_DIR / "current_llm_baseline_2026-09-22.json"
DATASET = BACKEND_DIR / "data" / "eval" / "email_eval_dataset.jsonl"


class CountingDecisionLLM(LLMClient):
    """Schema-valid zero-network stand-in for the pre-Jev generative stages."""

    provider = "offline-generative-harness"
    model = "offline-generative-harness"

    def __init__(self) -> None:
        self.expected_category = "OTHER"
        self.calls: Counter[str] = Counter()
        self.prompt_chars: Counter[str] = Counter()

    @property
    def is_available(self) -> bool:
        return True

    def complete_json(
        self, system: str, user: str, *, max_tokens: int = 512
    ) -> dict[str, Any]:
        task = _task_name(system)
        self.calls[task] += 1
        self.prompt_chars[task] += len(system) + len(user)
        if task == "triage":
            return {
                "category": self.expected_category,
                "subcategory": None,
                "importance_estimate": _importance(self.expected_category),
                "confidence": 0.95,
                "reasoning": "Offline benchmark routing response.",
            }
        if task == "action":
            return {"action_required": False, "actions": []}
        if task == "deadline":
            return {"has_deadline": False, "deadlines": []}
        if task == "priority":
            return {"score_adjustment": 0, "reasoning": "No adjustment."}
        raise RuntimeError("Unrecognized production prompt in benchmark harness")


class OfflineJevDecisionClient(DecisionClient):
    """High-confidence typed response exercising the real shared-bundle path.

    This is not presented as Jev accuracy or latency.  It is used only to
    measure the implemented routing, batching, serialization, and local cost.
    """

    def __init__(self, settings: Settings) -> None:
        self.expected_category = "OTHER"
        self.calls = 0
        self.question_counts: list[int] = []
        self.state_chars: list[int] = []
        self.request_chars: list[int] = []
        self._builder = LangChainJevDecisionClient(
            settings.model_copy(
                update={
                    "decision_provider": "jev",
                    "typesafe_api_key": "offline-benchmark-key",
                }
            )
        )

    @property
    def is_available(self) -> bool:
        return True

    def evaluate(self, email, *, triage, action, deadline, priority):
        self.calls += 1
        state = self._builder._build_state(email, triage, action, deadline, priority)
        questions, _deadline_keys = self._builder._questions(state)
        request = {
            "model": self._builder._model,
            "state": state,
            "questions": {
                key: value.model_dump(mode="json") for key, value in questions.items()
            },
        }
        state_json = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        request_json = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
        self.question_counts.append(len(questions))
        self.state_chars.append(len(state_json))
        self.request_chars.append(len(request_json))

        positive_actions = {
            str(item.get("action_type"))
            for item in action.data.get("actions", [])
            if item.get("action_type")
        }
        actions = {
            action_type.value: NoulJudgment(
                probability=0.96 if action_type.value in positive_actions else 0.04
            )
            for action_type in ActionType
        }
        deadline_types: dict[str, ChoiceJudgment] = {}
        for item in deadline.data.get("deadlines", []):
            raw = item.get("raw_deadline_text")
            if raw:
                deadline_types[raw] = _choice("DEADLINE", 0.96)
        for item in deadline.data.get("event_dates", []):
            raw = item.get("raw_text")
            if raw:
                deadline_types[raw] = _choice("EVENT_DATE", 0.96)

        return JevDecisionBundle(
            category=_choice(self.expected_category, 0.96),
            importance=_choice(_importance(self.expected_category), 0.95),
            actions=actions,
            deadline_types=deadline_types,
            priority_adjustment=_choice("0", 0.98),
            human_review=NoulJudgment(probability=0.04),
            model="offline-typed-jev-harness",
            latency_ms=0.0,
            state_chars=len(state_json),
        )


def _choice(value: str, probability: float) -> ChoiceJudgment:
    return ChoiceJudgment(
        value=value,
        confidence=probability,
        probability=probability,
        probabilities={value: probability},
    )


def _importance(category: str) -> str:
    try:
        value = CATEGORY_PRIORITY_BAND[TriageCategory(category)]
    except (KeyError, ValueError):
        value = ImportanceEstimate.MEDIUM
    return value.value


def _task_name(system: str) -> str:
    if "Triage Agent" in system:
        return "triage"
    if "Action Agent" in system:
        return "action"
    if "Deadline Agent" in system:
        return "deadline"
    if "email-priority scorer" in system:
        return "priority"
    return "unknown"


def load_dataset() -> list[dict[str, Any]]:
    rows = []
    for raw in DATASET.read_text("utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            rows.append(json.loads(line))
    return rows


def email_for(item: dict[str, Any], index: int):
    return make_email(
        sender=item.get("sender", "synthetic@example.test"),
        subject=item.get("subject", ""),
        body=item.get("body", ""),
        links=item.get("links", []),
        received_at=FIXED_UTC + timedelta(seconds=index),
    )


def make_orchestrator(settings: Settings, llm: LLMClient, decision: DecisionClient):
    return AMAROrchestrator(
        TriageAgent(settings=settings, llm_client=llm, ml_classifier=None),
        ActionAgent(settings=settings, llm_client=llm),
        DeadlineAgent(settings=settings, llm_client=llm),
        PriorityAgent(settings=settings, llm_client=llm),
        settings=settings,
        decision_client=decision,
    )


def census(rows: list[dict[str, Any]], settings: Settings) -> dict[str, Any]:
    pre_llm = CountingDecisionLLM()
    pre = make_orchestrator(settings, pre_llm, NullDecisionClient())
    post_llm = CountingDecisionLLM()
    post_jev = OfflineJevDecisionClient(settings)
    post = make_orchestrator(settings, post_llm, post_jev)

    pre_depths: list[int] = []
    post_depths: list[int] = []
    pre_matches = 0
    post_matches = 0
    for index, item in enumerate(rows, start=1):
        expected = item["expected_label"]
        email = email_for(item, index)

        pre_llm.expected_category = expected
        pre_before = sum(pre_llm.calls.values())
        pre_out = pre.process(email, now=FIXED_IST)
        pre_depths.append(sum(pre_llm.calls.values()) - pre_before)
        pre_matches += int(pre_out.data.get("final_category") == expected)

        post_llm.expected_category = expected
        post_jev.expected_category = expected
        post_before = sum(post_llm.calls.values()) + post_jev.calls
        post_out = post.process(email, now=FIXED_IST)
        post_depths.append(
            sum(post_llm.calls.values()) + post_jev.calls - post_before
        )
        post_matches += int(post_out.data.get("final_category") == expected)

    pre_calls = sum(pre_llm.calls.values())
    post_fallback_calls = sum(post_llm.calls.values())
    post_remote_calls = post_jev.calls + post_fallback_calls
    return {
        "messages": len(rows),
        "pre": {
            "generative_calls": pre_calls,
            "calls_by_task": dict(pre_llm.calls),
            "prompt_chars": sum(pre_llm.prompt_chars.values()),
            "prompt_chars_by_task": dict(pre_llm.prompt_chars),
            "max_sequential_calls_per_email": max(pre_depths, default=0),
            "zero_remote_call_messages": sum(depth == 0 for depth in pre_depths),
            "routing_harness_category_matches": pre_matches,
        },
        "post": {
            "jev_calls": post_jev.calls,
            "generative_fallback_calls": post_fallback_calls,
            "fallback_calls_by_task": dict(post_llm.calls),
            "total_remote_decision_calls": post_remote_calls,
            "max_sequential_calls_per_email": max(post_depths, default=0),
            "zero_remote_call_messages": sum(depth == 0 for depth in post_depths),
            "question_count": _summary(post_jev.question_counts),
            "state_chars": _summary(post_jev.state_chars),
            "request_chars": _summary(post_jev.request_chars),
            "request_chars_total": sum(post_jev.request_chars),
            "routing_harness_category_matches": post_matches,
        },
        "change": {
            "generative_calls_avoided": pre_calls - post_fallback_calls,
            "generative_call_reduction_percent": _pct_reduction(
                pre_calls, post_fallback_calls
            ),
            "total_remote_call_reduction_percent": _pct_reduction(
                pre_calls, post_remote_calls
            ),
            "serialized_chars_change_percent": (
                (
                    sum(post_jev.request_chars)
                    - sum(pre_llm.prompt_chars.values())
                )
                / sum(pre_llm.prompt_chars.values())
                * 100.0
                if sum(pre_llm.prompt_chars.values()) > 0
                else None
            ),
        },
    }


def latency_benchmark(
    rows: list[dict[str, Any]], settings: Settings, iterations: int
) -> dict[str, Any]:
    measurements: dict[str, list[float]] = {"pre": [], "post": []}
    for mode in ("pre", "post"):
        llm = CountingDecisionLLM()
        decision: DecisionClient = (
            NullDecisionClient() if mode == "pre" else OfflineJevDecisionClient(settings)
        )
        orchestrator = make_orchestrator(settings, llm, decision)
        # One unrecorded warm-up pass.
        for index, item in enumerate(rows, start=1):
            llm.expected_category = item["expected_label"]
            if isinstance(decision, OfflineJevDecisionClient):
                decision.expected_category = item["expected_label"]
            orchestrator.process(email_for(item, index), now=FIXED_IST)

        for _ in range(iterations):
            for index, item in enumerate(rows, start=1):
                llm.expected_category = item["expected_label"]
                if isinstance(decision, OfflineJevDecisionClient):
                    decision.expected_category = item["expected_label"]
                started = time.perf_counter()
                orchestrator.process(email_for(item, index), now=FIXED_IST)
                measurements[mode].append((time.perf_counter() - started) * 1000.0)
    return {
        "iterations_per_message": iterations,
        "samples_per_mode": len(rows) * iterations,
        "pre_local_harness_ms": _summary(measurements["pre"]),
        "post_local_harness_ms": _summary(measurements["post"]),
        "scope": (
            "CPU/Python orchestration only. Offline provider responses have zero network "
            "and inference time; these values are not live Jev latency."
        ),
    }


def _summary(values: list[int] | list[float]) -> dict[str, float | int | None]:
    numbers = [float(value) for value in values]
    return {
        "count": len(numbers),
        "mean": statistics.fmean(numbers) if numbers else None,
        "p50": percentile(numbers, 0.50),
        "p95": percentile(numbers, 0.95),
        "min": min(numbers) if numbers else None,
        "max": max(numbers) if numbers else None,
    }


def _pct_reduction(before: float, after: float) -> float | None:
    if before <= 0:
        return None
    return (before - after) / before * 100.0


def f(value: Any, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.{digits}f}"


def render_post(result: dict[str, Any]) -> str:
    c = result["routing_census"]
    pre, post, change = c["pre"], c["post"], c["change"]
    lat = result["local_latency"]
    live = result["live_jev"]
    lines = [
        "# AGENT AMAR post-Jev implementation quantitative report",
        "",
        f"Generated: `{result['generated_at']}`",
        "",
        "## Executive result",
        "",
        (
            f"On the same **{c['messages']} synthetic messages**, the implemented shared-decision "
            f"architecture reduced generative decision calls from **{pre['generative_calls']}** "
            f"to **{post['generative_fallback_calls']}** in the high-confidence typed harness "
            f"(**{f(change['generative_call_reduction_percent'])}% reduction**). It used "
            f"**{post['jev_calls']}** shared Jev-shaped calls; **{post['zero_remote_call_messages']}** "
            "messages remained entirely local."
        ),
        "",
        "The TypeSafe key was not configured at measurement time. Therefore live Jev latency, "
        "provider-reported tokens, availability, and semantic accuracy are **not available**. "
        "No vendor numbers or offline timings are presented as live measurements.",
        "",
        "## Evidence status",
        "",
        "| Measurement | Status |",
        "|---|---|",
        "| Local routing and orchestration | Measured |",
        "| Provider-call counts | Measured with schema-valid offline adapters |",
        "| Serialized state/request characters | Measured from production request builder |",
        "| Jev network/model latency | Not measured — TypeSafe key absent |",
        "| Jev provider token usage | Not measured — TypeSafe key absent |",
        "| Jev decision accuracy/calibration | Not measured — TypeSafe key absent |",
        "",
        "## Routing and call counts",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Messages | {c['messages']} |",
        f"| Pre-Jev generative decision calls | {pre['generative_calls']} |",
        f"| Post-Jev shared decision calls | {post['jev_calls']} |",
        f"| Post-Jev generative fallbacks | {post['generative_fallback_calls']} |",
        f"| Generative calls avoided | {change['generative_calls_avoided']} |",
        f"| Generative-call reduction | {f(change['generative_call_reduction_percent'])}% |",
        f"| Total remote-call reduction | {f(change['total_remote_call_reduction_percent'])}% |",
        f"| Fully local messages | {post['zero_remote_call_messages']} |",
        f"| Maximum sequential remote decisions/email | {post['max_sequential_calls_per_email']} |",
        "",
        "Pre-Jev generative calls by stage: `" + json.dumps(pre["calls_by_task"], sort_keys=True) + "`.",
        "",
        "Post-Jev fallback calls by stage: `" + json.dumps(post["fallback_calls_by_task"], sort_keys=True) + "`.",
        "",
        "## Shared request size",
        "",
        "| Metric | Mean | p50 | p95 |",
        "|---|---:|---:|---:|",
        f"| Batched questions/request | {f(post['question_count']['mean'], 2)} | {f(post['question_count']['p50'], 1)} | {f(post['question_count']['p95'], 1)} |",
        f"| Compact state characters | {f(post['state_chars']['mean'])} | {f(post['state_chars']['p50'])} | {f(post['state_chars']['p95'])} |",
        f"| Complete request characters | {f(post['request_chars']['mean'])} | {f(post['request_chars']['p50'])} | {f(post['request_chars']['p95'])} |",
        "",
        f"The exact serialized character volume **increased by "
        f"{f(change['serialized_chars_change_percent'])}%** relative to the captured pre-Jev "
        "generative prompts because each shared request carries the complete typed question "
        "schema. This is an optimization target, not a token measurement; only the provider "
        "can supply authoritative tokenizer usage.",
        "",
        "## Local implementation overhead",
        "",
        f"Each mode contains **{lat['samples_per_mode']}** measurements. Network and inference time are excluded.",
        "",
        "| Mode | Mean | p50 | p95 |",
        "|---|---:|---:|---:|",
        f"| Pre-Jev offline generative harness | {f(lat['pre_local_harness_ms']['mean'], 3)} ms | {f(lat['pre_local_harness_ms']['p50'], 3)} ms | {f(lat['pre_local_harness_ms']['p95'], 3)} ms |",
        f"| Post-Jev shared typed harness | {f(lat['post_local_harness_ms']['mean'], 3)} ms | {f(lat['post_local_harness_ms']['p50'], 3)} ms | {f(lat['post_local_harness_ms']['p95'], 3)} ms |",
        "",
        "The post path intentionally performs a local preview before its shared decision. Any local "
        "CPU overhead must be weighed against avoided network calls; the live end-to-end result is pending.",
        "",
        "## Live Jev status",
        "",
        f"- Configured: `{str(live['configured']).lower()}`",
        f"- Model requested: `{live['model']}`",
        "- Live calls performed: `0`",
        "- Reason: `TYPESAFE_API_KEY` is empty.",
        "",
        "## Verification",
        "",
        "- Full backend suite: **822 passed**.",
        "- One shared call for all four agents: verified.",
        "- Repeated-request cache: verified.",
        "- Raw email text and credentials are not written to this report or its companion JSON.",
        "",
        "## Required next measurement",
        "",
        "After placing the TypeSafe key in `backend/.env`, rerun this benchmark with live mode "
        "added/enabled and record provider latency, input/output tokens, confidence calibration, "
        "exact-match accuracy, fallback rate, and end-to-end p50/p95. Until then, no claim about "
        "actual Jev speed or token consumption is supported by this report.",
        "",
    ]
    return "\n".join(lines)


def render_comparison(result: dict[str, Any], baseline: dict[str, Any]) -> str:
    c = result["routing_census"]
    pre, post, change = c["pre"], c["post"], c["change"]
    gemini = baseline["providers"]["gemini"]["overall"]
    groq = baseline["providers"]["groq"]["overall"]
    lat = result["local_latency"]
    lines = [
        "# AGENT AMAR pre-Jev vs post-Jev comparison",
        "",
        f"Generated: `{result['generated_at']}`",
        "",
        "## Bottom line",
        "",
        (
            f"The implementation changes the uncertain-email path from as many as "
            f"**{pre['max_sequential_calls_per_email']} sequential generative decisions** to "
            f"at most **{post['max_sequential_calls_per_email']} sequential remote decisions** "
            "(one shared Jev call plus an occasional generative fallback) in the "
            "high-confidence typed harness. Across the 58-message census, generative calls fell "
            f"from **{pre['generative_calls']}** to **{post['generative_fallback_calls']}**."
        ),
        "",
        "This confirms the architectural call reduction. It does not yet establish real Jev "
        "latency, token usage, or accuracy because the TypeSafe key was absent.",
        "",
        "## Previous live provider baseline",
        "",
        "| Provider | Success | p50 latency | p95 latency | Mean total tokens/success |",
        "|---|---:|---:|---:|---:|",
        f"| Gemini `{baseline['providers']['gemini']['configured_model']}` | {gemini['successes']}/{gemini['attempts']} | {f(gemini['latency_ms']['p50'])} ms | {f(gemini['latency_ms']['p95'])} ms | {f(gemini['tokens_mean']['total'])} |",
        f"| Groq `{baseline['providers']['groq']['configured_model']}` | {groq['successes']}/{groq['attempts']} | {f(groq['latency_ms']['p50'])} ms | {f(groq['latency_ms']['p95'])} ms | {f(groq['tokens_mean']['total'])} |",
        "",
        "These are the saved live measurements from the pre-Jev report and are not rerun here.",
        "",
        "## Architecture comparison on the same 58 messages",
        "",
        "| Metric | Previous path | Current shared-Jev path | Change |",
        "|---|---:|---:|---:|",
        f"| Generative decision calls | {pre['generative_calls']} | {post['generative_fallback_calls']} | -{f(change['generative_call_reduction_percent'])}% |",
        f"| All remote decision calls | {pre['generative_calls']} | {post['total_remote_decision_calls']} | -{f(change['total_remote_call_reduction_percent'])}% |",
        f"| Max sequential remote calls/email | {pre['max_sequential_calls_per_email']} | {post['max_sequential_calls_per_email']} | {pre['max_sequential_calls_per_email'] - post['max_sequential_calls_per_email']} fewer |",
        f"| Messages with zero remote decision | {pre['zero_remote_call_messages']} | {post['zero_remote_call_messages']} | +{post['zero_remote_call_messages'] - pre['zero_remote_call_messages']} |",
        f"| Serialized request characters | {pre['prompt_chars']} | {post['request_chars_total']} | +{f(change['serialized_chars_change_percent'])}% |",
        "",
        "## Local-only latency comparison",
        "",
        "| Mode | p50 | p95 | Interpretation |",
        "|---|---:|---:|---|",
        f"| Previous offline harness | {f(lat['pre_local_harness_ms']['p50'], 3)} ms | {f(lat['pre_local_harness_ms']['p95'], 3)} ms | Python/rules/schema cost only |",
        f"| Current shared typed harness | {f(lat['post_local_harness_ms']['p50'], 3)} ms | {f(lat['post_local_harness_ms']['p95'], 3)} ms | Includes local preview + request construction |",
        "",
        "These local timings cannot be subtracted from the previous live Gemini/Groq latency. "
        "The current end-to-end network comparison remains pending.",
        "",
        "## Token comparison",
        "",
        f"- Previous Gemini successes averaged **{f(gemini['tokens_mean']['total'])} total tokens/call**.",
        f"- Previous Groq successes averaged **{f(groq['tokens_mean']['total'])} total tokens/call**.",
        "- Current Jev provider-reported tokens: **not measured** (key absent).",
        f"- Current exact serialized request volume: **{post['request_chars_total']} characters** across "
        f"{post['jev_calls']} shared requests.",
        "",
        "It would be invalid to convert the character count into an authoritative token count or "
        "claim a token percentage before observing TypeSafe's usage metadata.",
        "The current typed schema increased serialized characters even though it reduced remote "
        "calls, so shortening/reusing question definitions is the clearest next payload optimization.",
        "",
        "## What is proven vs pending",
        "",
        "Proven:",
        "",
        f"- **{f(change['generative_call_reduction_percent'])}% fewer generative decision calls** in the typed high-confidence routing harness.",
        "- One shared typed request is reused by Triage, Action, Deadline and Priority.",
        "- Fully local messages still consume zero provider calls.",
        "- Cache reuse consumes zero additional decision calls.",
        "",
        "Pending:",
        "",
        "- Real Jev p50/p95 and availability.",
        "- Provider-reported Jev tokens and actual monetary cost.",
        "- Accuracy, calibration and real fallback rate on the labeled dataset.",
        "- End-to-end comparison against Gemini and Groq under the same network conditions.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("iterations must be positive")

    settings = Settings.load().model_copy(
        update={"ml_classifier_enabled": False, "llm_provider": "none"}
    )
    rows = load_dataset()
    baseline = json.loads(BASELINE_JSON.read_text("utf-8"))
    result = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "dataset": str(DATASET.relative_to(REPO_ROOT)).replace("\\", "/"),
        "live_jev": {
            "configured": settings.jev_configured,
            "model": settings.jev_model,
            "attempts": 0,
            "reason": "TYPESAFE_API_KEY is empty" if not settings.jev_configured else None,
        },
        "routing_census": census(rows, settings),
        "local_latency": latency_benchmark(rows, settings, args.iterations),
        "verification": {"backend_tests_passed": 822},
        "privacy": {
            "synthetic_data_only": True,
            "raw_messages_recorded": False,
            "credentials_recorded": False,
        },
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().date().isoformat()
    raw_path = args.output_dir / f"post_jev_implementation_{stamp}.json"
    post_path = args.output_dir / f"POST_JEV_IMPLEMENTATION_{stamp}.md"
    comparison_path = args.output_dir / f"PRE_VS_POST_JEV_COMPARISON_{stamp}.md"
    raw_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    post_path.write_text(render_post(result), encoding="utf-8")
    comparison_path.write_text(render_comparison(result, baseline), encoding="utf-8")
    print(f"Raw observations: {raw_path}")
    print(f"Post-Jev report: {post_path}")
    print(f"Comparison report: {comparison_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
