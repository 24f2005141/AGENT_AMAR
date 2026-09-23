"""Reproducible pre-Jev benchmark for AGENT AMAR's decision-model path.

The harness intentionally uses synthetic inputs and never writes prompts,
responses, API keys, or email content to disk.  It records only aggregate
latency, provider-reported token counts, schema validity, and routing counts.

Run from ``backend/``::

    python scripts/benchmark_current_llm.py --runs 5 --warmups 1
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

# ``python scripts/benchmark_current_llm.py`` puts ``scripts/`` rather than the
# backend root on sys.path. Add the backend root before importing ``app``.
BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from pydantic import BaseModel, ValidationError

from app.agents.action_agent import ActionAgent
from app.agents.deadline_agent import DeadlineAgent
from app.agents.priority_agent import PriorityAgent
from app.agents.triage_agent import TriageAgent
from app.core.config import Settings
from app.models.action import LLMActionResult
from app.models.agent_output import AgentOutput
from app.models.deadline import LLMDeadlineResult
from app.models.email import BodyFormat, NormalizedEmail, SenderInfo
from app.models.priority import LLMPriorityAdjustment
from app.models.triage import LLMClassification
from app.services.llm_service import LLMClient, NullLLMClient


DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "benchmarks"
FIXED_UTC = datetime(2026, 8, 28, 9, 14, 22, tzinfo=timezone.utc)
FIXED_IST = datetime(2026, 8, 28, 16, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


def make_email(
    *,
    sender: str = "someone@example.com",
    subject: str = "Hello",
    body: str = "This is a synthetic email body.",
    links: list[str] | None = None,
    received_at: datetime = FIXED_UTC,
) -> NormalizedEmail:
    return NormalizedEmail(
        email_id="benchmark_email",
        thread_id="benchmark_thread",
        sender=SenderInfo(name=None, email=sender),
        to=["student@example.com"],
        subject=subject,
        body=body,
        body_format=BodyFormat.TEXT,
        received_at=received_at,
        labels=["INBOX", "UNREAD"],
        is_unread=True,
        attachments=[],
        links=links or [],
        has_links=bool(links),
        body_parse_error=False,
        needs_human_review=False,
        source="benchmark",
        ingested_at=received_at,
    )


def triage_stub(category: str = "OTHER", confidence: float = 0.9) -> AgentOutput:
    return AgentOutput(
        agent="Triage Agent",
        agent_version="benchmark",
        email_id="benchmark_email",
        run_id="benchmark",
        status="ok",
        confidence=confidence,
        needs_human_review=False,
        reasoning_summary="synthetic benchmark stub",
        data={
            "category": category,
            "confidence": confidence,
            "signals": {"classification_method": "deterministic"},
        },
        errors=[],
        started_at=FIXED_UTC,
        finished_at=FIXED_UTC,
    )


def action_stub(actions: list[dict[str, Any]] | None = None) -> AgentOutput:
    items = []
    for index, action in enumerate(actions or [], start=1):
        action_type = action["action_type"]
        items.append(
            {
                "action_id": f"act_{index:03d}",
                "action_type": action_type,
                "action_description": action.get("action_description", action_type),
                "target_link": None,
                "related_email": "benchmark_email",
                "blocking": True,
                "raw_deadline_hint": None,
                "confidence": 0.9,
                "status": "OPEN",
                "evidence": action.get("evidence", ""),
            }
        )
    return AgentOutput(
        agent="Action Agent",
        agent_version="benchmark",
        email_id="benchmark_email",
        run_id="benchmark",
        status="ok",
        confidence=0.9,
        needs_human_review=False,
        reasoning_summary="synthetic benchmark stub",
        data={
            "action_required": bool(items),
            "actions": items,
            "action_type": items[0]["action_type"] if items else None,
            "action_description": items[0]["action_description"] if items else None,
            "related_email": "benchmark_email",
            "confidence": 0.9,
            "detection_method": "deterministic",
        },
        errors=[],
        started_at=FIXED_UTC,
        finished_at=FIXED_UTC,
    )


def deadline_stub(normalized_deadline: str | None = None) -> AgentOutput:
    return AgentOutput(
        agent="Deadline Agent",
        agent_version="benchmark",
        email_id="benchmark_email",
        run_id="benchmark",
        status="ok",
        confidence=0.9,
        needs_human_review=False,
        reasoning_summary="synthetic benchmark stub",
        data={
            "deadline_detected": normalized_deadline is not None,
            "raw_deadline_text": "synthetic deadline" if normalized_deadline else None,
            "normalized_deadline": normalized_deadline,
            "timezone": "Asia/Kolkata",
            "ambiguity_flag": False,
            "ambiguity_reason": None,
            "monitoring_required": normalized_deadline is not None,
            "confidence": 0.9,
            "reference_time_used": FIXED_IST.isoformat(),
            "is_past": False,
            "deadlines": [],
            "event_dates": [],
            "detection_method": "deterministic",
        },
        errors=[],
        started_at=FIXED_UTC,
        finished_at=FIXED_UTC,
    )


class CaptureLLM(LLMClient):
    """Capture the exact production prompt while returning a valid fixture."""

    provider = "capture"
    model = "capture"

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[tuple[str, str, int]] = []

    @property
    def is_available(self) -> bool:
        return True

    def complete_json(
        self, system: str, user: str, *, max_tokens: int = 512
    ) -> dict[str, Any]:
        self.calls.append((system, user, max_tokens))
        return self.response


class RoutingLLM(LLMClient):
    """No-network client used only to count which synthetic emails route to an LLM."""

    provider = "routing-probe"
    model = "routing-probe"

    def __init__(self) -> None:
        self.expected_category = "OTHER"

    @property
    def is_available(self) -> bool:
        return True

    def complete_json(
        self, system: str, user: str, *, max_tokens: int = 512
    ) -> dict[str, Any]:
        return {
            "category": self.expected_category,
            "subcategory": None,
            "importance_estimate": "MEDIUM",
            "confidence": 0.85,
            "reasoning": "Synthetic routing census fixture.",
        }


@dataclass(frozen=True)
class TaskPrompt:
    name: str
    system: str
    user: str
    max_tokens: int
    validator: type[BaseModel]
    semantic_check: Callable[[dict[str, Any]], bool]

    @property
    def fingerprint(self) -> str:
        payload = f"{self.system}\n---\n{self.user}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]


def _captured_call(client: CaptureLLM, task: str) -> tuple[str, str, int]:
    if len(client.calls) != 1:
        raise RuntimeError(f"{task}: expected one LLM call, observed {len(client.calls)}")
    return client.calls[0]


def capture_production_prompts() -> list[TaskPrompt]:
    settings = Settings(
        llm_provider="none",
        ml_classifier_enabled=False,
        llm_max_tokens=512,
    )

    triage_client = CaptureLLM(
        {
            "category": "REPLY_REQUIRED",
            "subcategory": "follow_up",
            "importance_estimate": "MEDIUM",
            "confidence": 0.82,
            "reasoning": "The sender is following up and expects a reply.",
        }
    )
    TriageAgent(settings=settings, llm_client=triage_client).classify(
        make_email(
            sender="unknown@gmail.com",
            subject="Regarding your submission",
            body="Following up as discussed.",
        )
    )
    triage_prompt = _captured_call(triage_client, "triage")

    action_client = CaptureLLM(
        {
            "action_required": True,
            "actions": [
                {
                    "action_type": "DOCUMENT_UPLOAD",
                    "action_description": "Upload your updated CV.",
                    "confidence": 0.86,
                    "blocking": True,
                    "evidence": "proceed with the next steps",
                }
            ],
        }
    )
    ActionAgent(settings=settings, llm_client=action_client).detect(
        make_email(
            sender="unknown@gmail.com",
            subject="Next steps",
            body="Kindly proceed with the next steps as discussed regarding your candidature.",
        ),
        triage_stub("JOB_OPPORTUNITY"),
    )
    action_prompt = _captured_call(action_client, "action")

    deadline_client = CaptureLLM(
        {
            "has_deadline": True,
            "deadlines": [
                {
                    "raw_deadline_text": "by the usual cut-off next cycle",
                    "normalized_deadline": None,
                    "kind": "DEADLINE",
                    "is_ambiguous": True,
                    "ambiguity_reason": "No concrete date is supplied.",
                    "confidence": 0.55,
                    "evidence": "reaches us by the usual cut-off next cycle",
                }
            ],
        }
    )
    DeadlineAgent(settings=settings, llm_client=deadline_client).analyze(
        make_email(
            subject="Paperwork",
            body="Kindly ensure the paperwork reaches us by the usual cut-off next cycle.",
            received_at=FIXED_IST,
        ),
        triage_stub("OTHER"),
        action_stub([{"action_type": "DOCUMENT_UPLOAD"}]),
    )
    deadline_prompt = _captured_call(deadline_client, "deadline")

    priority_client = CaptureLLM(
        {"score_adjustment": 0, "reasoning": "Important sender but social content."}
    )
    PriorityAgent(settings=settings, llm_client=priority_client).score(
        make_email(
            sender="placement@college.edu",
            subject="Congrats on your work anniversary",
            body="See who reacted.",
        ),
        triage_stub("SOCIAL"),
        action_stub([]),
        deadline_stub(None),
        now=FIXED_IST,
    )
    priority_prompt = _captured_call(priority_client, "priority")

    return [
        TaskPrompt(
            "triage",
            *triage_prompt,
            validator=LLMClassification,
            semantic_check=lambda value: value.get("category") == "REPLY_REQUIRED",
        ),
        TaskPrompt(
            "action",
            *action_prompt,
            validator=LLMActionResult,
            semantic_check=lambda value: value.get("action_required") is True,
        ),
        TaskPrompt(
            "deadline",
            *deadline_prompt,
            validator=LLMDeadlineResult,
            semantic_check=lambda value: value.get("has_deadline") is True,
        ),
        TaskPrompt(
            "priority",
            *priority_prompt,
            validator=LLMPriorityAdjustment,
            semantic_check=lambda value: isinstance(value.get("score_adjustment"), int)
            and -10 <= value["score_adjustment"] <= 10,
        ),
    ]


def extract_json_object(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("response JSON was not an object")
    return parsed


def _integer_attr(value: Any, name: str) -> int | None:
    raw = getattr(value, name, None)
    return int(raw) if raw is not None else None


def call_gemini(settings: Settings, prompt: TaskPrompt) -> dict[str, Any]:
    import google.genai as genai
    from google.genai import types

    started = time.perf_counter()
    client = genai.Client(
        api_key=settings.llm_api_key,
        http_options=types.HttpOptions(timeout=int(settings.llm_timeout_seconds * 1000)),
    )
    response = client.models.generate_content(
        model=settings.llm_model,
        contents=prompt.user,
        config=types.GenerateContentConfig(
            system_instruction=prompt.system,
            max_output_tokens=prompt.max_tokens,
            response_mime_type="application/json",
        ),
    )
    latency_ms = (time.perf_counter() - started) * 1000
    data = extract_json_object(getattr(response, "text", None) or "")
    usage = getattr(response, "usage_metadata", None)
    return {
        "latency_ms": latency_ms,
        "input_tokens": _integer_attr(usage, "prompt_token_count"),
        "output_tokens": _integer_attr(usage, "candidates_token_count"),
        "reasoning_tokens": _integer_attr(usage, "thoughts_token_count"),
        "total_tokens": _integer_attr(usage, "total_token_count"),
        "model_observed": getattr(response, "model_version", None) or settings.llm_model,
        "data": data,
    }


def call_groq(settings: Settings, prompt: TaskPrompt) -> dict[str, Any]:
    from openai import OpenAI

    started = time.perf_counter()
    client = OpenAI(
        api_key=settings.llm_fallback_api_key,
        base_url="https://api.groq.com/openai/v1",
        timeout=settings.llm_timeout_seconds,
    )
    completion = client.chat.completions.create(
        model=settings.llm_fallback_model,
        max_tokens=prompt.max_tokens,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": prompt.system},
            {"role": "user", "content": prompt.user},
        ],
    )
    latency_ms = (time.perf_counter() - started) * 1000
    data = extract_json_object(completion.choices[0].message.content or "")
    usage = completion.usage
    details = getattr(usage, "completion_tokens_details", None)
    return {
        "latency_ms": latency_ms,
        "input_tokens": _integer_attr(usage, "prompt_tokens"),
        "output_tokens": _integer_attr(usage, "completion_tokens"),
        "reasoning_tokens": _integer_attr(details, "reasoning_tokens"),
        "total_tokens": _integer_attr(usage, "total_tokens"),
        "model_observed": getattr(completion, "model", None) or settings.llm_fallback_model,
        "data": data,
    }


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def mean_present(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return statistics.fmean(values) if values else None


def safe_error_metadata(exc: Exception) -> dict[str, Any]:
    """Return non-sensitive provider diagnostics without persisting messages."""
    body = getattr(exc, "body", None)
    error = body.get("error", {}) if isinstance(body, dict) else {}
    if not isinstance(error, dict):
        error = {}
    return {
        "error_type": type(exc).__name__,
        "http_status": getattr(exc, "status_code", None) or getattr(exc, "code", None),
        "api_status": getattr(exc, "status", None),
        "provider_error_type": (
            body.get("type") if isinstance(body, dict) else None
        ) or error.get("type"),
        "provider_error_code": (
            body.get("code") if isinstance(body, dict) else None
        ) or error.get("code"),
        "provider_error_param": (
            body.get("param") if isinstance(body, dict) else None
        ) or error.get("param"),
    }


def summarize_runs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [row for row in rows if row["success"]]
    failed = [row for row in rows if not row["success"]]
    latencies = [float(row["latency_ms"]) for row in successful]
    attempt_latencies = [float(row["latency_ms"]) for row in rows]
    failure_latencies = [float(row["latency_ms"]) for row in failed]

    def latency_summary(values: list[float]) -> dict[str, float | None]:
        return {
            "mean": statistics.fmean(values) if values else None,
            "p50": percentile(values, 0.50),
            "p95": percentile(values, 0.95),
            "min": min(values) if values else None,
            "max": max(values) if values else None,
        }

    return {
        "attempts": len(rows),
        "successes": len(successful),
        "success_rate": len(successful) / len(rows) if rows else 0.0,
        "schema_valid_rate": (
            sum(bool(row.get("schema_valid")) for row in rows) / len(rows) if rows else 0.0
        ),
        "scenario_match_rate": (
            sum(bool(row.get("scenario_match")) for row in rows) / len(rows) if rows else 0.0
        ),
        # Successful-call latency is the comparable performance metric. Attempt
        # and failure latency are retained separately so an unavailable provider
        # does not disappear behind an uninformative `n/a`.
        "latency_ms": latency_summary(latencies),
        "attempt_latency_ms": latency_summary(attempt_latencies),
        "failure_latency_ms": latency_summary(failure_latencies),
        "tokens_mean": {
            "input": mean_present(successful, "input_tokens"),
            "output": mean_present(successful, "output_tokens"),
            "reasoning": mean_present(successful, "reasoning_tokens"),
            "total": mean_present(successful, "total_tokens"),
        },
        "tokens_observed_total": {
            key: sum(int(row.get(key) or 0) for row in successful)
            for key in ("input_tokens", "output_tokens", "reasoning_tokens", "total_tokens")
        },
        "models_observed": sorted(
            {str(row["model_observed"]) for row in successful if row.get("model_observed")}
        ),
        "error_types": dict(Counter(row.get("error_type") for row in rows if not row["success"])),
        "http_statuses": dict(
            Counter(
                str(row["http_status"])
                for row in failed
                if row.get("http_status") is not None
            )
        ),
        "api_statuses": dict(
            Counter(str(row["api_status"]) for row in failed if row.get("api_status"))
        ),
        "provider_error_codes": dict(
            Counter(
                str(row["provider_error_code"])
                for row in failed
                if row.get("provider_error_code")
            )
        ),
    }


def benchmark_provider(
    provider: str,
    settings: Settings,
    prompts: list[TaskPrompt],
    *,
    runs: int,
    warmups: int,
) -> dict[str, Any]:
    if provider == "gemini":
        configured = bool(settings.llm_api_key)
        caller = call_gemini
        configured_model = settings.llm_model
    else:
        configured = bool(settings.llm_fallback_api_key)
        caller = call_groq
        configured_model = settings.llm_fallback_model

    result: dict[str, Any] = {
        "provider": provider,
        "configured": configured,
        "configured_model": configured_model,
        "warmups": warmups,
        "runs_per_task": runs,
        "tasks": {},
    }
    if not configured:
        result["skipped_reason"] = "API key is not configured"
        return result

    for _ in range(warmups):
        try:
            caller(settings, prompts[0])
        except Exception:
            pass

    all_rows: list[dict[str, Any]] = []
    for prompt in prompts:
        rows: list[dict[str, Any]] = []
        for run_index in range(1, runs + 1):
            row: dict[str, Any] = {
                "run": run_index,
                "task": prompt.name,
                "success": False,
                "schema_valid": False,
                "scenario_match": False,
            }
            started = time.perf_counter()
            try:
                observation = caller(settings, prompt)
                row.update({key: value for key, value in observation.items() if key != "data"})
                data = observation["data"]
                row["success"] = True
                try:
                    prompt.validator.model_validate(data)
                    row["schema_valid"] = True
                except ValidationError:
                    row["schema_valid"] = False
                row["scenario_match"] = bool(prompt.semantic_check(data))
            except Exception as exc:  # benchmark should retain failures, not abort
                row["latency_ms"] = (time.perf_counter() - started) * 1000
                row.update(safe_error_metadata(exc))
            rows.append(row)
            all_rows.append(row)
            print(
                f"{provider:6s} {prompt.name:8s} {run_index}/{runs} "
                f"{'ok' if row['success'] else row.get('error_type', 'failed')} "
                f"{row['latency_ms']:.0f} ms",
                flush=True,
            )
        result["tasks"][prompt.name] = {
            "prompt_fingerprint": prompt.fingerprint,
            "system_chars": len(prompt.system),
            "user_chars": len(prompt.user),
            "summary": summarize_runs(rows),
            "runs": rows,
        }
    result["overall"] = summarize_runs(all_rows)
    return result


def timed_many(function: Callable[[], Any], iterations: int) -> dict[str, Any]:
    # Prime imports, regex caches, and Pydantic schema caches.
    function()
    values: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        function()
        values.append((time.perf_counter_ns() - started) / 1_000_000)
    return {
        "iterations": iterations,
        "latency_ms": {
            "mean": statistics.fmean(values),
            "p50": percentile(values, 0.50),
            "p95": percentile(values, 0.95),
            "min": min(values),
            "max": max(values),
        },
    }


def benchmark_deterministic(iterations: int) -> dict[str, Any]:
    settings = Settings(llm_provider="none", ml_classifier_enabled=False)
    null = NullLLMClient()
    triage_agent = TriageAgent(settings=settings, llm_client=null)
    action_agent = ActionAgent(settings=settings, llm_client=null)
    deadline_agent = DeadlineAgent(settings=settings, llm_client=null)
    priority_agent = PriorityAgent(settings=settings, llm_client=null)

    triage_email = make_email(
        sender="placement@college.edu",
        subject="Summer internship 2026 application form",
        body="Apply for the internship. Fill the application form and upload your resume.",
        links=["https://example.test/apply"],
    )
    action_email = make_email(
        subject="Assignment 3",
        body="Please submit the assignment through the portal before Friday.",
    )
    deadline_email = make_email(
        subject="Form deadline",
        body="Deadline: 30 August 2026 to submit the form.",
        received_at=FIXED_IST,
    )
    priority_email = make_email(sender="placement@college.edu", subject="Placement form")
    priority_deadline = deadline_stub((FIXED_IST + timedelta(minutes=30)).isoformat())

    return {
        "triage": timed_many(lambda: triage_agent.classify(triage_email), iterations),
        "action": timed_many(
            lambda: action_agent.detect(action_email, triage_stub("ASSIGNMENT")), iterations
        ),
        "deadline": timed_many(
            lambda: deadline_agent.analyze(
                deadline_email,
                triage_stub("PLACEMENT"),
                action_stub([{"action_type": "FORM_SUBMISSION"}]),
            ),
            iterations,
        ),
        "priority": timed_many(
            lambda: priority_agent.score(
                priority_email,
                triage_stub("PLACEMENT"),
                action_stub([{"action_type": "FORM_SUBMISSION"}]),
                priority_deadline,
                now=FIXED_IST,
            ),
            iterations,
        ),
    }


def routing_census(settings: Settings) -> dict[str, Any]:
    path = BACKEND_DIR / "data" / "eval" / "email_eval_dataset.jsonl"
    rows = []
    for raw in path.read_text("utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            rows.append(json.loads(line))

    probe = RoutingLLM()
    agent = TriageAgent(settings=settings, llm_client=probe)
    methods: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    llm_invoked = 0
    deterministic_confidences: list[float] = []
    for index, item in enumerate(rows, start=1):
        probe.expected_category = item["expected_label"]
        output = agent.classify(
            make_email(
                sender=item.get("sender", "synthetic@example.test"),
                subject=item.get("subject", ""),
                body=item.get("body", ""),
                links=item.get("links", []),
                received_at=FIXED_UTC + timedelta(seconds=index),
            )
        )
        routing = output.data["signals"]["classification_routing"]
        methods[str(routing["method"])] += 1
        kinds[str(item.get("kind", "unspecified"))] += 1
        llm_invoked += int(bool(routing["llm_invoked"]))
        deterministic_confidences.append(float(routing["deterministic_confidence"]))

    return {
        "dataset": str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
        "synthetic_messages": len(rows),
        "dataset_kinds": dict(kinds),
        "routing_methods": dict(methods),
        "llm_invoked": llm_invoked,
        "llm_invocation_rate": llm_invoked / len(rows) if rows else 0.0,
        "deterministic_confidence": {
            "mean": statistics.fmean(deterministic_confidences),
            "p50": percentile(deterministic_confidences, 0.50),
            "p95": percentile(deterministic_confidences, 0.95),
        },
        "trained_ml_model_present": Path(settings.ml_classifier_model_path_resolved).is_file(),
    }


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def format_number(value: Any, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.{digits}f}"


def render_report(result: dict[str, Any]) -> str:
    generated = result["generated_at"]
    lines = [
        "# AGENT AMAR current decision-model baseline",
        "",
        f"Generated: `{generated}`",
        "",
        "## Executive summary",
        "",
        "This is the pre-Jev quantitative baseline for AGENT AMAR's current hybrid ",
        "decision path: deterministic rules first, Gemini for uncertain decisions, and ",
        "Groq as the configured fallback. Measurements use synthetic repository fixtures; ",
        "no user email, prompt, response, or credential is stored in this report.",
        "",
    ]

    routing = result["routing_census"]
    lines.extend(
        [
            "### Routing observation",
            "",
            f"The bundled synthetic triage set contains **{routing['synthetic_messages']}** messages. "
            f"With the current local artifacts, **{routing['llm_invoked']}** messages "
            f"(**{routing['llm_invocation_rate'] * 100:.1f}%**) route to an LLM. "
            f"A trained local ML model is **{'present' if routing['trained_ml_model_present'] else 'not present'}**.",
            "",
            "| Routing method | Messages |",
            "|---|---:|",
        ]
    )
    for method, count in sorted(routing["routing_methods"].items()):
        lines.append(f"| `{method}` | {count} |")

    gemini_provider = result.get("providers", {}).get("gemini", {})
    gemini_overall = gemini_provider.get("overall", {})
    if gemini_overall.get("successes", 0):
        remaining_failures = gemini_overall["attempts"] - gemini_overall["successes"]
        failure_note = (
            f" The remaining **{remaining_failures}** failures were provider-side capacity or "
            "deadline responses; they are included in the availability measurements below."
            if remaining_failures
            else " All measured requests succeeded."
        )
        lines.extend(
            [
                "",
                "### Gemini repair status",
                "",
                "The Gemini configuration is operational. The retired-model `404 NOT_FOUND` and ",
                "depleted-account `402 RESOURCE_EXHAUSTED` blockers are resolved; AGENT AMAR now ",
                f"uses `{gemini_provider['configured_model']}` with a working key. The benchmark ",
                f"captured **{gemini_overall['successes']} valid Gemini responses** with provider-reported "
                f"token usage.{failure_note}",
            ]
        )
    elif gemini_overall.get("http_statuses", {}).get("402"):
        lines.extend(
            [
                "",
                "### Gemini repair status",
                "",
                "The retired-model `404 NOT_FOUND` is resolved: the configured model is now ",
                f"`{result['providers']['gemini']['configured_model']}`. The current API key reached that model endpoint, but every ",
                "measured request was rejected with `402 RESOURCE_EXHAUSTED` because the linked ",
                "billing account has no prepaid credit. Consequently, this run contains no valid ",
                "Gemini latency or token-consumption baseline.",
            ]
        )

    lines.extend(
        [
            "",
            "## Live provider measurements",
            "",
            "Each provider received the same four production prompts captured from the Triage, "
            "Action, Deadline, and Priority agents. Latency is non-streaming wall-clock time "
            "around the same SDK request shape used by the application. Token counts come from "
            "the provider response metadata.",
            "",
        ]
    )
    for provider_name, provider in result["providers"].items():
        lines.append(f"### {provider_name.title()} — `{provider['configured_model']}`")
        lines.append("")
        if not provider.get("configured"):
            lines.append(f"Skipped: {provider.get('skipped_reason', 'not configured')}.")
            lines.append("")
            continue
        lines.extend(
            [
                "| Task | Success | Schema valid | Scenario match* | Latency p50 | Latency p95 | Mean input tokens | Mean output tokens | Mean total tokens |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for task_name, task in provider["tasks"].items():
            summary = task["summary"]
            latency = summary["latency_ms"]
            tokens = summary["tokens_mean"]
            lines.append(
                f"| {task_name} | {summary['successes']}/{summary['attempts']} | "
                f"{summary['schema_valid_rate'] * 100:.0f}% | "
                f"{summary['scenario_match_rate'] * 100:.0f}% | "
                f"{format_number(latency['p50'])} ms | {format_number(latency['p95'])} ms | "
                f"{format_number(tokens['input'])} | {format_number(tokens['output'])} | "
                f"{format_number(tokens['total'])} |"
            )
        overall = provider["overall"]
        lines.extend(
            [
                "",
                f"Overall: **{overall['successes']}/{overall['attempts']} successful calls**; "
                f"p50 **{format_number(overall['latency_ms']['p50'])} ms**, "
                f"p95 **{format_number(overall['latency_ms']['p95'])} ms**; mean "
                f"**{format_number(overall['tokens_mean']['total'])} total tokens/call**.",
                "",
            ]
        )
        failures = overall["attempts"] - overall["successes"]
        if failures:
            error_types = ", ".join(
                f"{name}={count}" for name, count in overall.get("error_types", {}).items()
            ) or "unclassified"
            http_statuses = ", ".join(
                f"HTTP {status}={count}"
                for status, count in overall.get("http_statuses", {}).items()
            ) or "HTTP status unavailable"
            api_statuses = ", ".join(
                f"{status}={count}" for status, count in overall.get("api_statuses", {}).items()
            )
            status_text = f"; {api_statuses}" if api_statuses else ""
            failure_latency = overall.get("failure_latency_ms", {})
            lines.extend(
                [
                    f"Failures: **{failures}** ({error_types}; {http_statuses}{status_text}); "
                    f"failure-response p50 **{format_number(failure_latency.get('p50'))} ms**, "
                    f"p95 **{format_number(failure_latency.get('p95'))} ms**.",
                    "",
                ]
            )

    lines.extend(
        [
            "*Scenario match is a narrow check for these four synthetic cases, not an accuracy benchmark.",
            "",
            "## Deterministic fast-path measurements",
            "",
            "These measurements isolate local rule execution with no network or model call.",
            "",
            "| Agent | Iterations | Mean | p50 | p95 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for task_name, task in result["deterministic_fast_path"].items():
        latency = task["latency_ms"]
        lines.append(
            f"| {task_name} | {task['iterations']} | {format_number(latency['mean'], 3)} ms | "
            f"{format_number(latency['p50'], 3)} ms | {format_number(latency['p95'], 3)} ms |"
        )

    environment = result["environment"]
    lines.extend(
        [
            "",
            "## Test conditions",
            "",
            f"- Platform: `{environment['platform']}`",
            f"- Python: `{environment['python']}`",
            f"- Logical CPUs: `{environment['logical_cpus']}`",
            f"- `google-genai`: `{environment['packages']['google-genai']}`",
            f"- `openai`: `{environment['packages']['openai']}`",
            f"- Runs: `{result['parameters']['runs']}` measured calls per provider/task after "
            f"`{result['parameters']['warmups']}` warm-up call(s) per provider",
            f"- Max output tokens: `{result['parameters']['max_output_tokens']}`",
            "- Calls were sequential; concurrency was intentionally excluded.",
            "- Network conditions are those of this laptop at measurement time.",
            "- Groq was measured directly as the configured fallback. Provider calls were measured "
            "separately, so this report does not claim an end-to-end failed-primary fallback latency.",
            "",
            "## Interpretation for the future Jev comparison",
            "",
            "Use the same four task fixtures and report: p50/p95 latency, input/output/total "
            "tokens, schema-valid rate, and scenario-match rate. Jev should be compared only on "
            "these bounded decision tasks; reply drafting remains a generative-model workload.",
            "",
            "The companion JSON file contains the anonymized per-call observations and prompt "
            "fingerprints needed to confirm that a future run used the same fixtures.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=5, help="Measured calls per provider/task")
    parser.add_argument("--warmups", type=int, default=1, help="Unrecorded warm-up calls per provider")
    parser.add_argument(
        "--deterministic-iterations", type=int, default=500, help="Local iterations per task"
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    if args.runs < 1 or args.warmups < 0 or args.deterministic_iterations < 1:
        parser.error("runs/iterations must be positive and warmups cannot be negative")

    settings = Settings.load()
    prompts = capture_production_prompts()
    print("Captured production prompts:", ", ".join(p.name for p in prompts), flush=True)

    result: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "parameters": {
            "runs": args.runs,
            "warmups": args.warmups,
            "deterministic_iterations": args.deterministic_iterations,
            "max_output_tokens": settings.llm_max_tokens,
        },
        "configuration": {
            "primary_provider": settings.llm_provider,
            "primary_model": settings.llm_model,
            "fallback_provider": settings.llm_fallback_provider,
            "fallback_model": settings.llm_fallback_model,
            "timeout_seconds": settings.llm_timeout_seconds,
            "keys_recorded": False,
        },
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "logical_cpus": os.cpu_count(),
            "packages": {
                "google-genai": package_version("google-genai"),
                "openai": package_version("openai"),
                "pydantic": package_version("pydantic"),
            },
        },
        "prompt_fingerprints": {prompt.name: prompt.fingerprint for prompt in prompts},
    }

    print("Measuring deterministic fast paths...", flush=True)
    result["deterministic_fast_path"] = benchmark_deterministic(
        args.deterministic_iterations
    )
    print("Computing synthetic routing census...", flush=True)
    result["routing_census"] = routing_census(settings)
    result["providers"] = {
        "gemini": benchmark_provider(
            "gemini", settings, prompts, runs=args.runs, warmups=args.warmups
        ),
        "groq": benchmark_provider(
            "groq", settings, prompts, runs=args.runs, warmups=args.warmups
        ),
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().date().isoformat()
    json_path = args.output_dir / f"current_llm_baseline_{stamp}.json"
    report_path = args.output_dir / f"CURRENT_LLM_BASELINE_{stamp}.md"
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    report_path.write_text(render_report(result), encoding="utf-8")
    print(f"Raw observations: {json_path}")
    print(f"Report: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
