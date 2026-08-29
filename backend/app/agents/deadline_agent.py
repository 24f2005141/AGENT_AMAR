"""Deadline Agent — "Does this email contain a deadline, and if so, when?"

Implements ``01-Agents/Deadline Agent.md``.

Hybrid, mirroring the other agents:
  * **Layer 1 (deterministic)** — ``app/utils/deadline_parsing.py`` extracts
    candidate temporal phrases, decides DEADLINE vs EVENT_DATE vs IGNORE, and
    normalises against the email's ``received_at``. Always runs.
  * **Layer 2 (LLM)** — only when Layer 1 is under-confident, a numeric date is
    ambiguous, or nothing could be normalised — and a provider is configured.
    Every LLM deadline must be backed by text that actually appears in the email.

It only detects / extracts / normalises / flags ambiguity, and reports whether a
resolved deadline is already in the past (``is_past``). It does NOT compute time
remaining, classify, decide actions, score priority, or notify.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.models.agent_output import AgentError, AgentOutput, AgentStatus
from app.models.deadline import (
    DeadlineData,
    DeadlineItem,
    DeadlineKind,
    EventDate,
    LLMDeadlineResult,
)
from app.models.email import NormalizedEmail
from app.models.triage import ClassificationMethod
from app.services.llm_service import (
    LLMClient,
    LLMResponseError,
    LLMUnavailableError,
    NullLLMClient,
)
from app.utils import deadline_parsing as dp

AGENT_NAME = "Deadline Agent"
AGENT_VERSION = "0.1.0"

_LOW_BAND_CATEGORIES = {"PROMOTIONAL", "NEWSLETTER", "SPAM", "SOCIAL"}

# action_type -> verbs that, near a deadline, suggest it belongs to that action
_ACTION_VERBS: dict[str, tuple[str, ...]] = {
    "FORM_SUBMISSION": ("apply", "fill", "form", "application", "submit the form"),
    "COMPLETE_ASSIGNMENT": ("assignment", "coursework", "lab record", "submit the assignment"),
    "DOCUMENT_UPLOAD": ("upload", "resume", "cv", "document", "attach"),
    "REGISTRATION": ("register", "registration", "sign up", "enrol", "enroll"),
    "PAYMENT": ("pay", "payment", "fee"),
    "REPLY": ("reply", "respond", "confirm", "revert", "rsvp"),
    "ATTEND_EVENT": ("attend", "join", "be present", "report to"),
    "READ_AND_ACKNOWLEDGE": ("read", "acknowledge", "review"),
}


@dataclass
class _Analysis:
    deadlines: list[DeadlineItem] = field(default_factory=list)
    event_dates: list[EventDate] = field(default_factory=list)
    method: ClassificationMethod = ClassificationMethod.DETERMINISTIC
    reasoning: str = ""
    overall_confidence: float = 0.0
    conflicting: bool = False
    unresolved_cue: bool = False  # deadline language present but nothing extracted


class DeadlineAgent:
    """Extracts and normalises deadlines from a :class:`NormalizedEmail`."""

    def __init__(self, settings: Settings | None = None, llm_client: LLMClient | None = None) -> None:
        self.settings = settings or get_settings()
        self._llm = llm_client or NullLLMClient()
        self._tz = self._resolve_tz(self.settings.default_timezone)

    # -- public API -----------------------------------------------------

    def analyze(
        self,
        email: NormalizedEmail,
        triage: AgentOutput | None = None,
        action: AgentOutput | None = None,
    ) -> AgentOutput:
        started_at = self._now()
        errors: list[AgentError] = []

        reference_dt = self._reference_time(email)
        category = triage.data.get("category") if (triage and isinstance(triage.data, dict)) else None
        actions = (
            action.data.get("actions", []) if (action and isinstance(action.data, dict)) else []
        )
        primary_action_type = (
            action.data.get("action_type") if (action and isinstance(action.data, dict)) else None
        )

        det = self._deterministic(email, reference_dt, category, actions)
        analysis = det

        if self._should_use_llm(det) and self._llm.is_available:
            try:
                analysis = self._llm_analyze(email, reference_dt, category, actions, det)
            except LLMResponseError as exc:
                errors.append(AgentError(code="invalid_llm_response", message=str(exc)))
                analysis = self._as_fallback(det)
            except LLMUnavailableError as exc:
                errors.append(AgentError(code="llm_unavailable", message=str(exc)))
                analysis = self._as_fallback(det)

        data = self._build_data(email, reference_dt, analysis, primary_action_type)
        needs_review = self._needs_human_review(analysis, data)
        status = AgentStatus.PARTIAL if errors else AgentStatus.OK

        return AgentOutput(
            agent=AGENT_NAME,
            agent_version=AGENT_VERSION,
            email_id=email.email_id,
            run_id=self._run_id(),
            status=status,
            confidence=round(analysis.overall_confidence, 4),
            needs_human_review=needs_review,
            reasoning_summary=analysis.reasoning,
            data=data.model_dump(),
            errors=errors,
            started_at=started_at,
            finished_at=self._now(),
        )

    # -- reference time (STEP 4) -----------------------------------

    def _reference_time(self, email: NormalizedEmail) -> datetime:
        rt = email.received_at
        if isinstance(rt, datetime) and rt.tzinfo is not None:
            return rt
        return self._now()

    # -- layer 1: deterministic ----------------------------------

    def _deterministic(
        self,
        email: NormalizedEmail,
        reference_dt: datetime,
        category: str | None,
        actions: list[dict],
    ) -> _Analysis:
        text = f"{email.subject or ''}\n{email.body or ''}"
        low_priority = (category in _LOW_BAND_CATEGORIES) and not _any_action(actions)

        candidates = dp.extract_candidates(text, low_priority_context=low_priority)
        # also consider deadline hints the Action Agent already vouched for
        for a in actions:
            hint = a.get("raw_deadline_hint")
            if hint:
                for pm in dp.extract_candidates(f"Please {hint}."):
                    candidates.append(dp.PhraseMatch(pm.text, hint, "DEADLINE"))

        deadlines: list[DeadlineItem] = []
        event_dates: list[EventDate] = []
        by_sentence: dict[str, list[tuple[dp.PhraseMatch, dp.NormalizedResult]]] = {}

        for pm in candidates:
            res = dp.normalize_phrase(
                pm.text, pm.sentence, reference_dt,
                self.settings.default_timezone, self.settings.deadline_date_locale,
            )
            if pm.kind == "EVENT_DATE":
                event_dates.append(
                    EventDate(
                        raw_text=pm.text,
                        normalized=res.dt.isoformat() if res.dt else None,
                        reason="phrased as a scheduled event, not a submission deadline",
                    )
                )
                continue
            if pm.kind == "IGNORE":
                continue
            by_sentence.setdefault(pm.sentence, []).append((pm, res))

        # merge phrases within one sentence that describe the same instant
        idx = 0
        for sentence, group in by_sentence.items():
            for pm, res in self._merge_group(group):
                idx += 1
                deadlines.append(self._make_item(f"dl_{idx:03d}", pm, res, reference_dt, actions, sentence))

        deadlines = self._dedupe(deadlines)
        event_dates = self._dedupe_events(event_dates)

        if not deadlines:
            text_l = text.lower()
            unresolved = (
                not low_priority
                and any(c in text_l for c in dp.DEADLINE_CUES)
                and not event_dates
            )
            conf = 0.6 if unresolved else (0.85 if (low_priority or event_dates) else 0.72)
            reason = "No deadline expression found in the email."
            if event_dates:
                reason = f"No deadline; {len(event_dates)} event date(s) mentioned but not a cutoff."
            elif low_priority:
                reason = f"No deadline; {category} email — dates are informational."
            elif unresolved:
                reason = "The email uses deadline language but no concrete date could be extracted."
            return _Analysis(
                [], event_dates, reasoning=reason, overall_confidence=conf, unresolved_cue=unresolved
            )

        overall = max(d.confidence for d in deadlines)
        conflicting = self._detect_conflict(deadlines)
        reason = self._reasoning(deadlines, event_dates, conflicting)
        return _Analysis(deadlines, event_dates, reasoning=reason,
                         overall_confidence=overall, conflicting=conflicting)

    def _merge_group(
        self, group: list[tuple[dp.PhraseMatch, dp.NormalizedResult]]
    ) -> list[tuple[dp.PhraseMatch, dp.NormalizedResult]]:
        """Within one sentence, collapse phrases that resolve to the same day."""
        resolved = [(pm, r) for pm, r in group if r.dt is not None]
        unresolved = [(pm, r) for pm, r in group if r.dt is None]
        kept: list[tuple[dp.PhraseMatch, dp.NormalizedResult]] = []
        for pm, r in sorted(resolved, key=lambda x: (x[1].date_only, -len(x[0].text))):
            if any(abs((r.dt - k[1].dt).total_seconds()) < 86400 for k in kept):
                continue
            kept.append((pm, r))
        if not kept and unresolved:
            kept.append(unresolved[0])
        return kept

    def _make_item(
        self,
        deadline_id: str,
        pm: dp.PhraseMatch,
        res: dp.NormalizedResult,
        reference_dt: datetime,
        actions: list[dict],
        sentence: str,
    ) -> DeadlineItem:
        is_past = bool(res.dt and res.dt < reference_dt)
        conf = self._confidence(pm, res)
        act_type, act_id = self._link_action(sentence, pm.text, actions)
        if act_type:
            conf = min(0.97, conf + 0.05)
        return DeadlineItem(
            deadline_id=deadline_id,
            raw_deadline_text=pm.text,
            normalized_deadline=res.dt.isoformat() if res.dt else None,
            timezone=res.tz_name,
            date_only=res.date_only,
            ambiguity_flag=res.ambiguous,
            ambiguity_reason=res.reason,
            is_past=is_past,
            confidence=round(conf, 4),
            action_context=act_type,
            related_action_id=act_id,
            source=ClassificationMethod.DETERMINISTIC,
            evidence=sentence[:180],
        )

    @staticmethod
    def _confidence(pm: dp.PhraseMatch, res: dp.NormalizedResult) -> float:
        if res.dt is None:
            return 0.5
        base = 0.9
        if res.date_only:
            base = 0.8
        if res.ambiguous:
            base = 0.62
        s = pm.sentence.lower()
        if "deadline" in s or "last date" in s or "last day" in s:
            base = min(0.97, base + 0.05)
        return max(0.4, min(0.97, base))

    @staticmethod
    def _link_action(sentence: str, phrase: str, actions: list[dict]) -> tuple[str | None, str | None]:
        if not actions:
            return None, None
        if len(actions) == 1:
            return actions[0].get("action_type"), actions[0].get("action_id")

        s = sentence.lower()

        # 1) position-aware: the action verb closest *before* this phrase wins
        #    (handles "Register by Sep 1 and submit your resume by Sep 3")
        idx = s.find(phrase.lower())
        prefix = s[: idx if idx != -1 else len(s)]
        best_pos, best = -1, None
        for a in actions:
            for verb in _ACTION_VERBS.get(a.get("action_type", ""), ()):
                pos = prefix.rfind(verb)
                if pos > best_pos:
                    best_pos, best = pos, a
        if best is not None:
            return best.get("action_type"), best.get("action_id")

        # 2) exact deadline-hint match from the Action Agent
        for a in actions:
            hint = (a.get("raw_deadline_hint") or "").lower()
            if hint and phrase.lower() in hint and len(hint) < 40:
                return a.get("action_type"), a.get("action_id")

        # 3) any verb anywhere in the sentence
        for a in actions:
            if any(v in s for v in _ACTION_VERBS.get(a.get("action_type", ""), ())):
                return a.get("action_type"), a.get("action_id")
        return None, None

    @staticmethod
    def _dedupe(items: list[DeadlineItem]) -> list[DeadlineItem]:
        out: list[DeadlineItem] = []
        for it in sorted(items, key=lambda x: (x.normalized_deadline or "z", -x.confidence)):
            dup = next(
                (
                    o for o in out
                    if o.normalized_deadline == it.normalized_deadline
                    and (o.action_context or None) == (it.action_context or None)
                ),
                None,
            )
            if dup is None:
                out.append(it)
        for i, it in enumerate(out, start=1):
            it.deadline_id = f"dl_{i:03d}"
        return out

    @staticmethod
    def _dedupe_events(items: list[EventDate]) -> list[EventDate]:
        seen: set[tuple[str | None, str]] = set()
        out: list[EventDate] = []
        for it in items:
            key = (it.normalized, it.raw_text.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append(it)
        return out

    @staticmethod
    def _detect_conflict(items: list[DeadlineItem]) -> bool:
        by_action: dict[str, set[str]] = {}
        for it in items:
            if it.action_context and it.normalized_deadline:
                by_action.setdefault(it.action_context, set()).add(it.normalized_deadline[:10])
        return any(len(v) > 1 for v in by_action.values())

    @staticmethod
    def _reasoning(deadlines: list[DeadlineItem], events: list[EventDate], conflict: bool) -> str:
        parts = [f"Found {len(deadlines)} deadline(s)"]
        clear = [d for d in deadlines if not d.ambiguity_flag]
        if clear:
            parts.append(f"{len(clear)} clearly resolved")
        amb = [d for d in deadlines if d.ambiguity_flag]
        if amb:
            parts.append(f"{len(amb)} flagged ambiguous")
        if any(d.is_past for d in deadlines):
            parts.append("at least one already in the past")
        if events:
            parts.append(f"{len(events)} event date(s) ignored")
        if conflict:
            parts.append("conflicting dates for the same action")
        return "; ".join(parts) + "."

    # -- layer 2: LLM ------------------------------------------------

    def _should_use_llm(self, det: _Analysis) -> bool:
        if det.conflicting or det.unresolved_cue:
            return True
        if det.deadlines and det.overall_confidence < self.settings.deadline_llm_threshold:
            return True
        if det.deadlines and all(d.normalized_deadline is None for d in det.deadlines):
            return True
        return any("DD/MM vs MM/DD" in (d.ambiguity_reason or "") for d in det.deadlines)

    def _llm_analyze(
        self,
        email: NormalizedEmail,
        reference_dt: datetime,
        category: str | None,
        actions: list[dict],
        det: _Analysis,
    ) -> _Analysis:
        body = (email.body or "").strip()
        if len(body) > 4000:
            body = body[:4000] + "\n...[truncated]"
        action_lines = "; ".join(
            f"{a.get('action_type')}: {a.get('evidence') or a.get('action_description') or ''}"
            for a in actions
        ) or "none detected"
        user = (
            f"Reference time (email received): {reference_dt.isoformat()}\n"
            f"Default timezone: {self.settings.default_timezone}\n"
            f"Sender: {email.sender.email}\n"
            f"Triage category: {category or 'unknown'}\n"
            f"Detected actions: {action_lines}\n"
            f"Subject: {email.subject or '(empty)'}\n\n"
            f"Body:\n{body or '(empty)'}"
        )
        raw = self._llm.complete_json(_SYSTEM_PROMPT, user, max_tokens=self.settings.llm_max_tokens)
        try:
            parsed = LLMDeadlineResult.model_validate(raw)
        except ValidationError as exc:
            raise LLMResponseError(f"LLM returned an invalid deadline result: {exc}") from exc

        haystack = f"{email.subject or ''}\n{email.body or ''}".lower()
        deadlines: list[DeadlineItem] = []
        event_dates: list[EventDate] = list(det.event_dates)
        idx = 0
        for ld in parsed.deadlines:
            if not _text_supported(ld.raw_deadline_text, haystack):
                continue  # STEP 11 — never invent a deadline
            norm = self._llm_normalized(ld.normalized_deadline) or dp.normalize_phrase(
                ld.raw_deadline_text, ld.evidence or ld.raw_deadline_text, reference_dt,
                self.settings.default_timezone, self.settings.deadline_date_locale,
            ).dt
            if ld.kind == DeadlineKind.EVENT_DATE:
                event_dates.append(
                    EventDate(raw_text=ld.raw_deadline_text,
                             normalized=norm.isoformat() if norm else None,
                             reason="LLM classified as an event date")
                )
                continue
            idx += 1
            act_id = self._match_action_id(ld.action_context, actions)
            deadlines.append(
                DeadlineItem(
                    deadline_id=f"dl_{idx:03d}",
                    raw_deadline_text=ld.raw_deadline_text,
                    normalized_deadline=norm.isoformat() if norm else None,
                    timezone=self.settings.default_timezone,
                    date_only=ld.date_only,
                    ambiguity_flag=ld.is_ambiguous or norm is None,
                    ambiguity_reason=ld.ambiguity_reason or (None if norm else "LLM could not resolve"),
                    is_past=bool(norm and norm < reference_dt),
                    confidence=round(max(0.3, min(0.95, ld.confidence)), 4),
                    action_context=ld.action_context if act_id or ld.action_context else None,
                    related_action_id=act_id,
                    source=ClassificationMethod.LLM,
                    evidence=(ld.evidence or ld.raw_deadline_text)[:180],
                )
            )

        if not parsed.has_deadline or not deadlines:
            return _Analysis(
                [], self._dedupe_events(event_dates), method=ClassificationMethod.LLM,
                reasoning="LLM found no deadline in the email.",
                overall_confidence=max(0.6, det.overall_confidence),
            )
        deadlines = self._dedupe(deadlines)
        return _Analysis(
            deadlines, self._dedupe_events(event_dates), method=ClassificationMethod.LLM,
            reasoning=self._reasoning(deadlines, event_dates, self._detect_conflict(deadlines)),
            overall_confidence=max(d.confidence for d in deadlines),
            conflicting=self._detect_conflict(deadlines),
        )

    def _llm_normalized(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self._tz)
        return dt

    @staticmethod
    def _match_action_id(action_context: str | None, actions: list[dict]) -> str | None:
        if not action_context:
            return None
        for a in actions:
            if a.get("action_type") == action_context:
                return a.get("action_id")
        return None

    @staticmethod
    def _as_fallback(det: _Analysis) -> _Analysis:
        det.method = ClassificationMethod.LLM_FALLBACK_DETERMINISTIC
        return det

    # -- assemble --------------------------------------------------

    def _build_data(
        self,
        email: NormalizedEmail,
        reference_dt: datetime,
        analysis: _Analysis,
        primary_action_type: str | None,
    ) -> DeadlineData:
        tz_default = self.settings.default_timezone
        if not analysis.deadlines:
            return DeadlineData(
                deadline_detected=False,
                raw_deadline_text=None,
                normalized_deadline=None,
                timezone=tz_default,
                ambiguity_flag=False,
                ambiguity_reason=None,
                monitoring_required=False,
                confidence=round(analysis.overall_confidence, 4),
                reference_time_used=reference_dt.isoformat(),
                is_past=False,
                deadlines=[],
                event_dates=analysis.event_dates,
                detection_method=analysis.method,
            )

        primary = self._pick_primary(analysis.deadlines, primary_action_type, reference_dt)
        return DeadlineData(
            deadline_detected=True,
            raw_deadline_text=primary.raw_deadline_text,
            normalized_deadline=primary.normalized_deadline,
            timezone=primary.timezone or tz_default,
            ambiguity_flag=primary.ambiguity_flag,
            ambiguity_reason=primary.ambiguity_reason,
            monitoring_required=True,
            confidence=round(analysis.overall_confidence, 4),
            reference_time_used=reference_dt.isoformat(),
            is_past=primary.is_past,
            deadlines=analysis.deadlines,
            event_dates=analysis.event_dates,
            detection_method=analysis.method,
        )

    @staticmethod
    def _pick_primary(
        deadlines: list[DeadlineItem], primary_action_type: str | None, reference_dt: datetime
    ) -> DeadlineItem:
        if primary_action_type:
            linked = [d for d in deadlines if d.action_context == primary_action_type]
            if linked:
                return min(linked, key=lambda d: d.normalized_deadline or "z")
        future = [d for d in deadlines if d.normalized_deadline and not d.is_past]
        if future:
            return min(future, key=lambda d: d.normalized_deadline)
        resolved = [d for d in deadlines if d.normalized_deadline]
        if resolved:
            return min(resolved, key=lambda d: d.normalized_deadline)
        return max(deadlines, key=lambda d: d.confidence)

    def _needs_human_review(self, analysis: _Analysis, data: DeadlineData) -> bool:
        if analysis.conflicting:
            return True
        if data.deadline_detected and data.ambiguity_flag and data.normalized_deadline is None:
            return True
        if data.deadline_detected and analysis.overall_confidence < self.settings.deadline_review_threshold:
            return True
        # an ambiguous DD/MM vs MM/DD date resolved only by locale guess, with no
        # LLM available to check it -> surface it (STEP 8)
        if analysis.method != ClassificationMethod.LLM and any(
            "DD/MM vs MM/DD" in (d.ambiguity_reason or "") for d in analysis.deadlines
        ):
            return True
        return False

    # -- runtime plumbing ---------------------------------------

    def _now(self) -> datetime:
        return datetime.now(self._tz)

    @staticmethod
    def _run_id() -> str:
        stamp = datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"run_{stamp}_{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _resolve_tz(tz_name: str) -> ZoneInfo:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            return ZoneInfo("UTC")


def _any_action(actions: list[dict]) -> bool:
    return bool(actions)


def _overlap(a: str, b: str) -> bool:
    aw = {w for w in re.findall(r"[a-z]{4,}", a)}
    bw = {w for w in re.findall(r"[a-z]{4,}", b)}
    return len(aw & bw) >= 3


def _text_supported(phrase: str, haystack_lower: str) -> bool:
    p = phrase.lower().strip()
    if not p:
        return False
    if p in haystack_lower:
        return True
    words = [w for w in re.findall(r"[a-z0-9]+", p) if len(w) > 2]
    if not words:
        return False
    hits = sum(1 for w in words if w in haystack_lower)
    return hits / len(words) >= 0.6


_SYSTEM_PROMPT = """You are the Deadline Agent for an email-intelligence system used by a college student.
Your ONLY job: find dates/times that are DEADLINES the user must meet, and normalise them.

A DEADLINE is a cut-off for the user to do something ("submit by Friday", "applications close
tomorrow", "last date 5 Sept", "within 24 hours"). It is NOT:
  - an event/meeting/interview date ("the interview will be held on Monday") -> kind EVENT_DATE
  - a date mentioned in an advertisement, newsletter, or confirmation
Only report a deadline that the email text actually supports. Never invent one.

Use the given reference time to resolve relative expressions ("today", "tomorrow", "within 2
hours", "this Friday"). Output ISO 8601 with an explicit offset. If a date has no time, use
23:59:59 and set is_ambiguous. If a numeric date is DD/MM vs MM/DD ambiguous, or the phrase is
vague ("soon", "next week"), set is_ambiguous and (for vague) normalized_deadline = null.

Respond with ONLY this JSON:
{"has_deadline": true|false,
 "deadlines": [
   {"raw_deadline_text": "<verbatim phrase from the email>",
    "normalized_deadline": "<ISO 8601 with offset, or null>",
    "kind": "DEADLINE" | "EVENT_DATE",
    "date_only": true|false, "is_ambiguous": true|false,
    "ambiguity_reason": "<short reason or null>",
    "action_context": "<one of the detected action types, or null>",
    "confidence": 0.0-1.0, "evidence": "<short quote>"}
 ]}
If there is no deadline: {"has_deadline": false, "deadlines": []}"""
