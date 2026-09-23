"""Shared typed decision layer for remote Jev and local Laya.

The service intentionally does *one* bounded classification request for an
email and shares the typed answers with Triage, Action, Deadline and Priority.
It is not an LLM client: it never generates prose and it never executes an
action.  Provider imports are lazy so deterministic-only deployments keep
working without the optional packages, model weights, or OpenRouter key.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.core.logging_setup import secure_logger
from app.models.action import ActionType
from app.models.agent_output import AgentOutput
from app.models.email import NormalizedEmail
from app.models.triage import TriageCategory

logger = secure_logger(__name__)

_SCHEMA_VERSION = "amar-jev-bundle-v1"
_LAYA_LOAD_LOCK = threading.RLock()
_LAYA_AGENTS: dict[tuple[str, str, str], Any] = {}
_LAYA_INFERENCE_LOCKS: dict[tuple[str, str, str], threading.RLock] = {}
_SHARED_CLIENT_LOCK = threading.RLock()
_SHARED_CLIENTS: OrderedDict[str, Any] = OrderedDict()


class DecisionServiceError(RuntimeError):
    """A Jev decision could not be obtained or validated."""


class ChoiceJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    confidence: float = Field(ge=0.0, le=1.0)
    probability: float = Field(ge=0.0, le=1.0)
    probabilities: dict[str, float] = Field(default_factory=dict)


class NoulJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    probability: float = Field(ge=0.0, le=1.0)


class DecisionQuestion(BaseModel):
    """Provider-neutral subset shared by Jev and Laya."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["choice", "noul"]
    instructions: str
    criteria: dict[str, str] | None = None


class JevDecisionBundle(BaseModel):
    """All bounded decisions returned for one email."""

    model_config = ConfigDict(extra="forbid")

    category: ChoiceJudgment | None = None
    importance: ChoiceJudgment | None = None
    actions: dict[str, NoulJudgment] = Field(default_factory=dict)
    deadline_types: dict[str, ChoiceJudgment] = Field(default_factory=dict)
    priority_adjustment: ChoiceJudgment | None = None
    human_review: NoulJudgment | None = None
    model: str = ""
    request_id: str | None = None
    latency_ms: float = 0.0
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    state_chars: int = 0
    cache_hit: bool = False

    def accepted_choice(self, name: str, threshold: float) -> str | None:
        answer = getattr(self, name, None)
        if not isinstance(answer, ChoiceJudgment):
            return None
        if min(answer.confidence, answer.probability) < threshold:
            return None
        return answer.value

    def action_decision(self, action_type: str, threshold: float) -> bool | None:
        answer = self.actions.get(action_type)
        if answer is None:
            return None
        if answer.probability >= threshold:
            return True
        if answer.probability <= 1.0 - threshold:
            return False
        return None

    def deadline_decision(self, raw_text: str, threshold: float) -> str | None:
        answer = self.deadline_types.get(raw_text)
        if answer is None or min(answer.confidence, answer.probability) < threshold:
            return None
        return answer.value


class DecisionClient:
    """Small provider-neutral interface used by the orchestrator."""

    provider: str = "none"

    @property
    def is_available(self) -> bool:
        return False

    def evaluate(
        self,
        email: NormalizedEmail,
        *,
        triage: AgentOutput,
        action: AgentOutput,
        deadline: AgentOutput,
        priority: AgentOutput,
    ) -> JevDecisionBundle:
        raise DecisionServiceError("No decision provider is configured.")


class NullDecisionClient(DecisionClient):
    pass


class LangChainJevDecisionClient(DecisionClient):
    """TypeSafe Jev through OpenRouter's native Decisions endpoint.

    The historical class name is retained to avoid breaking imports. The
    LangChain package targets TypeSafe's own host, while AMAR's credential is
    an OpenRouter key, so this client intentionally uses the exact OpenRouter
    endpoint instead.
    """

    provider = "jev"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model = settings.jev_model or "typesafe/jev-1.13"
        self._api_key = settings.jev_api_key
        self._cache_size = max(0, settings.jev_cache_size)
        self._cache: OrderedDict[str, JevDecisionBundle] = OrderedDict()
        self._cache_lock = threading.Lock()
        self._http: Any | None = None

    @property
    def is_available(self) -> bool:
        return self._settings.jev_configured

    def _get_http(self) -> Any:
        if self._http is not None:
            return self._http
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise DecisionServiceError("`httpx` is not installed.") from exc
        self._http = httpx.Client(
            timeout=max(0.1, self._settings.jev_timeout_seconds),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "X-OpenRouter-Title": "Agent AMAR",
            },
        )
        return self._http

    def evaluate(
        self,
        email: NormalizedEmail,
        *,
        triage: AgentOutput,
        action: AgentOutput,
        deadline: AgentOutput,
        priority: AgentOutput,
    ) -> JevDecisionBundle:
        if not self.is_available:
            raise DecisionServiceError("TypeSafe Jev is not configured.")

        state = self._build_state(email, triage, action, deadline, priority)
        questions, deadline_keys = self._questions(state)
        cache_key = self._cache_key(state, questions)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached.model_copy(update={"cache_hit": True, "latency_ms": 0.0})

        started = time.perf_counter()
        try:
            http_response = self._get_http().post(
                self._settings.jev_endpoint_url,
                json={
                    "model": self._model,
                    "state": state,
                    "questions": _question_payload(questions),
                },
            )
            http_response.raise_for_status()
            response = http_response.json()
        except Exception as exc:  # provider packages expose several typed errors
            raise DecisionServiceError(
                f"Jev request failed: {type(exc).__name__}"
            ) from exc

        bundle = self._decode(
            response,
            deadline_keys,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            state_chars=len(json.dumps(state, ensure_ascii=False, separators=(",", ":"))),
        )
        self._cache_put(cache_key, bundle)
        logger.info(
            "Jev shared decision completed model=%s latency_ms=%.1f input_tokens=%s",
            bundle.model,
            bundle.latency_ms,
            bundle.input_tokens,
        )
        return bundle

    def _build_state(
        self,
        email: NormalizedEmail,
        triage: AgentOutput,
        action: AgentOutput,
        deadline: AgentOutput,
        priority: AgentOutput,
    ) -> dict[str, Any]:
        body = _compact_body(email.body or "", self._settings.jev_max_state_chars)
        deadlines = [
            {
                "raw": item.get("raw_deadline_text"),
                "normalized": item.get("normalized_deadline"),
                "ambiguous": bool(item.get("ambiguity_flag")),
                "action": item.get("action_context"),
            }
            for item in deadline.data.get("deadlines", [])[:6]
            if item.get("raw_deadline_text")
        ]
        for item in deadline.data.get("event_dates", [])[:4]:
            raw = item.get("raw_text")
            if raw and not any(d["raw"] == raw for d in deadlines):
                deadlines.append(
                    {"raw": raw, "normalized": item.get("normalized"), "event_hint": True}
                )

        return {
            "email": {
                "sender": email.sender.email,
                "subject": (email.subject or "")[:300],
                "body": body,
                "received_at": email.received_at.isoformat(),
                "attachment_names": [a.filename[:120] for a in email.attachments[:8]],
                "links": [_compact_link(link) for link in email.links[:6]],
                "language": email.language,
            },
            "local_analysis": {
                "category": triage.data.get("category"),
                "category_confidence": triage.data.get("confidence"),
                "importance": triage.data.get("importance_estimate"),
                "actions": [
                    {
                        "type": item.get("action_type"),
                        "confidence": item.get("confidence"),
                        "evidence": (item.get("evidence") or "")[:180],
                    }
                    for item in action.data.get("actions", [])[:8]
                ],
                "deadline_candidates": deadlines,
                "priority_score": priority.data.get("priority_score"),
                "priority_level": priority.data.get("priority_level"),
                "priority_factors": priority.data.get("factors", {}),
            },
        }

    @staticmethod
    def _questions(
        state: dict[str, Any],
    ) -> tuple[dict[str, DecisionQuestion], dict[str, str]]:
        category_criteria = {
            TriageCategory.INTERNSHIP.value: "Internship opportunity or application",
            TriageCategory.PLACEMENT.value: "Campus placement or recruitment drive",
            TriageCategory.JOB_OPPORTUNITY.value: "Off-campus, full-time, or part-time job",
            TriageCategory.ASSIGNMENT.value: "Coursework that the student must complete",
            TriageCategory.EXAM.value: "Exam, result, hall ticket, or revaluation",
            TriageCategory.FACULTY_ANNOUNCEMENT.value: "Official faculty or department notice",
            TriageCategory.REPLY_REQUIRED.value: "Sender primarily waits for a reply",
            TriageCategory.ACADEMIC_INFORMATION.value: "Academic information with no concrete task",
            TriageCategory.PROJECT_UPDATE.value: "Project or team status communication",
            TriageCategory.EVENT.value: "Workshop, webinar, hackathon, club, or cultural event",
            TriageCategory.PROMOTIONAL.value: "Marketing, discount, or sales offer",
            TriageCategory.NEWSLETTER.value: "Recurring subscribed digest",
            TriageCategory.SPAM.value: "Unsolicited, deceptive, or phishing message",
            TriageCategory.SOCIAL.value: "Social-network notification",
            TriageCategory.OTHER.value: "Genuine email fitting no other category",
        }
        questions: dict[str, DecisionQuestion] = {
            "category": DecisionQuestion(
                type="choice",
                instructions="Choose the single best primary email category.",
                criteria=category_criteria,
            ),
            "importance": DecisionQuestion(
                type="choice",
                instructions="Estimate importance, not deadline proximity.",
                criteria={
                    "HIGH": "Material academic, career, financial, or direct-response impact",
                    "MEDIUM": "Useful or relevant but not strongly consequential",
                    "LOW": "Routine, promotional, social, or safely ignorable",
                },
            ),
            "priority_adjustment": DecisionQuestion(
                type="choice",
                instructions=(
                    "Choose a small adjustment to the local priority score only when its "
                    "structured factors miss clear context. Prefer zero."
                ),
                criteria={
                    "-10": "Strong contextual reason to reduce priority",
                    "-5": "Moderate contextual reason to reduce priority",
                    "0": "Local priority score is appropriate",
                    "5": "Moderate contextual reason to increase priority",
                    "10": "Strong contextual reason to increase priority",
                },
            ),
            "human_review": DecisionQuestion(
                type="noul",
                instructions=(
                    "Is human review needed because the message is ambiguous, conflicting, "
                    "suspicious, or consequential with insufficient evidence?"
                )
            ),
        }
        for action_type in ActionType:
            questions[f"action__{action_type.value}"] = DecisionQuestion(
                type="noul",
                instructions=(
                    f"Does this email genuinely require the recipient to perform "
                    f"{action_type.value}? Ignore completed, negated, quoted, or merely "
                    "conditional actions."
                )
            )

        deadline_keys: dict[str, str] = {}
        candidates = state["local_analysis"].get("deadline_candidates", [])
        for idx, candidate in enumerate(candidates):
            raw = str(candidate.get("raw") or "")
            if not raw:
                continue
            key = f"deadline__{idx}"
            deadline_keys[key] = raw
            questions[key] = DecisionQuestion(
                type="choice",
                instructions=f"Classify the date phrase {raw!r} in this email.",
                criteria={
                    "DEADLINE": "Cutoff or due date for an action",
                    "EVENT_DATE": "Scheduled occurrence, not a completion cutoff",
                    "IRRELEVANT": "Historical, quoted, promotional, or unrelated date",
                },
            )
        return questions, deadline_keys

    @staticmethod
    def _decode(
        response: Any,
        deadline_keys: dict[str, str],
        *,
        latency_ms: float,
        state_chars: int,
    ) -> JevDecisionBundle:
        answers = _field(response, "answers", {}) or {}

        def choice(name: str) -> ChoiceJudgment | None:
            answer = answers.get(name)
            if answer is None or _field(answer, "type") != "choice":
                return None
            probabilities = {
                str(k): float(v)
                for k, v in (_field(answer, "probabilities", {}) or {}).items()
            }
            value = str(_field(answer, "choice", ""))
            confidence = float(_field(answer, "confidence", probabilities.get(value, 0.0)))
            return ChoiceJudgment(
                value=value,
                confidence=confidence,
                probability=float(probabilities.get(value, confidence)),
                probabilities=probabilities,
            )

        def noul(name: str) -> NoulJudgment | None:
            answer = answers.get(name)
            if answer is None or _field(answer, "type") != "noul":
                return None
            return NoulJudgment(probability=float(_field(answer, "noul", 0.0)))

        actions: dict[str, NoulJudgment] = {}
        for action_type in ActionType:
            answer = noul(f"action__{action_type.value}")
            if answer is not None:
                actions[action_type.value] = answer

        deadline_types: dict[str, ChoiceJudgment] = {}
        for key, raw in deadline_keys.items():
            answer = choice(key)
            if answer is not None:
                deadline_types[raw] = answer

        usage = _field(response, "usage", {}) or {}
        return JevDecisionBundle(
            category=choice("category"),
            importance=choice("importance"),
            actions=actions,
            deadline_types=deadline_types,
            priority_adjustment=choice("priority_adjustment"),
            human_review=noul("human_review"),
            model=str(_field(response, "model", "")),
            request_id=_field(response, "request_id", _field(response, "id")),
            latency_ms=round(latency_ms, 3),
            input_tokens=_field(usage, "input_tokens"),
            output_tokens=_field(usage, "output_tokens"),
            cost_usd=_field(usage, "cost"),
            state_chars=state_chars,
        )

    def _cache_key(self, state: dict[str, Any], questions: dict[str, Any]) -> str:
        question_shape = {
            name: question.model_dump(mode="json") for name, question in questions.items()
        }
        payload = json.dumps(
            {
                "schema": _SCHEMA_VERSION,
                "model": self._model,
                "state": state,
                "questions": question_shape,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _cache_get(self, key: str) -> JevDecisionBundle | None:
        if not self._cache_size:
            return None
        with self._cache_lock:
            value = self._cache.pop(key, None)
            if value is not None:
                self._cache[key] = value
            return value

    def _cache_put(self, key: str, value: JevDecisionBundle) -> None:
        if not self._cache_size:
            return
        with self._cache_lock:
            self._cache[key] = value
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)


class LayaDecisionClient(LangChainJevDecisionClient):
    """Local open-source Laya checkpoint using the same typed question bundle."""

    provider = "laya"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._model = settings.laya_model_id
        self._agent: Any | None = None
        self._agent_key = (
            settings.laya_model_id,
            settings.laya_model_subfolder,
            settings.laya_device,
        )
        with _LAYA_LOAD_LOCK:
            self._inference_lock = _LAYA_INFERENCE_LOCKS.setdefault(
                self._agent_key, threading.RLock()
            )

    @property
    def is_available(self) -> bool:
        return self._settings.laya_configured

    def _get_agent(self) -> Any:
        with _LAYA_LOAD_LOCK:
            if self._agent is not None:
                return self._agent
            shared = _LAYA_AGENTS.get(self._agent_key)
            if shared is not None:
                self._agent = shared
                return shared
            os.environ.setdefault("USE_TF", "0")
            # Hugging Face's Xet transport repeatedly stalled on this Windows
            # laptop; the ordinary HTTPS downloader is slower but reliable.
            os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
            os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
            try:
                import laya
            except ImportError as exc:  # pragma: no cover - optional deployment
                raise DecisionServiceError("`laya` is not installed.") from exc
            self._agent = laya.load(
                self._settings.laya_model_id,
                device=self._settings.laya_device or None,
                subfolder=self._settings.laya_model_subfolder or None,
            )
            _LAYA_AGENTS[self._agent_key] = self._agent
            return self._agent

    def evaluate(
        self,
        email: NormalizedEmail,
        *,
        triage: AgentOutput,
        action: AgentOutput,
        deadline: AgentOutput,
        priority: AgentOutput,
    ) -> JevDecisionBundle:
        if not self.is_available:
            raise DecisionServiceError("Laya is not configured.")
        state = self._build_state(email, triage, action, deadline, priority)
        questions, deadline_keys = self._questions(state)
        cache_key = self._cache_key(state, questions)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached.model_copy(update={"cache_hit": True, "latency_ms": 0.0})

        started = time.perf_counter()
        try:
            with self._inference_lock:
                response = self._get_agent().predict(state, _question_payload(questions))
        except Exception as exc:
            raise DecisionServiceError(
                f"Laya request failed: {type(exc).__name__}"
            ) from exc
        latency_ms = (time.perf_counter() - started) * 1000.0
        bundle = self._decode(
            response,
            deadline_keys,
            latency_ms=latency_ms,
            state_chars=len(json.dumps(state, ensure_ascii=False, separators=(",", ":"))),
        )
        bundle = bundle.model_copy(
            update={
                "model": (
                    f"{self._settings.laya_model_id}/{self._settings.laya_model_subfolder}"
                    if self._settings.laya_model_subfolder
                    else self._settings.laya_model_id
                )
            }
        )
        self._cache_put(cache_key, bundle)
        logger.info(
            "Laya shared decision completed model=%s latency_ms=%.1f input_tokens=%s",
            bundle.model,
            bundle.latency_ms,
            bundle.input_tokens,
        )
        return bundle


def build_decision_client(settings: Settings) -> DecisionClient:
    if settings.jev_configured:
        return LangChainJevDecisionClient(settings)
    if settings.laya_configured:
        return LayaDecisionClient(settings)
    return NullDecisionClient()


def get_shared_decision_client(settings: Settings) -> DecisionClient:
    """Reuse bounded-decision caches across HTTP requests and Gmail sync runs."""
    if not (settings.jev_configured or settings.laya_configured):
        return NullDecisionClient()
    key = hashlib.sha256(settings.model_dump_json().encode("utf-8")).hexdigest()
    with _SHARED_CLIENT_LOCK:
        client = _SHARED_CLIENTS.get(key)
        if client is None:
            client = build_decision_client(settings)
            _SHARED_CLIENTS[key] = client
            while len(_SHARED_CLIENTS) > 8:
                _SHARED_CLIENTS.popitem(last=False)
        else:
            _SHARED_CLIENTS.move_to_end(key)
        return client


def _question_payload(questions: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        name: question.model_dump(mode="json", exclude_none=True)
        if hasattr(question, "model_dump")
        else dict(question)
        for name, question in questions.items()
    }


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def decision_context(
    triage: AgentOutput,
    action: AgentOutput,
    deadline: AgentOutput,
    priority: AgentOutput,
) -> dict[str, Any]:
    """Small, content-free routing record suitable for traces and tests."""
    return {
        "triage": _routing_value(triage, "signals", "classification_routing"),
        "action": action.data.get("decision_routing", {}),
        "deadline": deadline.data.get("decision_routing", {}),
        "priority": priority.data.get("decision_routing", {}),
    }


def decision_needed(context: dict[str, Any]) -> bool:
    return any(
        bool(value.get("remote_decision_recommended"))
        for value in context.values()
        if isinstance(value, dict)
    )


def _routing_value(output: AgentOutput, outer: str, inner: str) -> dict[str, Any]:
    value = output.data.get(outer, {})
    if not isinstance(value, dict):
        return {}
    nested = value.get(inner, {})
    return nested if isinstance(nested, dict) else {}


def _compact_body(body: str, max_chars: int) -> str:
    max_chars = max(500, max_chars)
    lines: list[str] = []
    for raw in body.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line or line.startswith(">"):
            continue
        if re.match(r"^on .{0,160}wrote:$", line, re.IGNORECASE):
            break
        if line in {"--", "__"}:
            break
        lines.append(line)
    compact = "\n".join(lines).strip()
    if len(compact) <= max_chars:
        return compact
    # Retain both the opening request and the end, where deadlines and links
    # commonly appear.  The marker is deterministic and becomes part of cache.
    head = int(max_chars * 0.72)
    tail = max_chars - head - 24
    return compact[:head] + "\n...[middle omitted]...\n" + compact[-tail:]


def _compact_link(link: str) -> str:
    try:
        parsed = urlsplit(link)
        return f"{parsed.netloc}{parsed.path}"[:180]
    except Exception:
        return link[:180]
