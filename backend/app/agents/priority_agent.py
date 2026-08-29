"""Priority Agent — "How important and urgent is this email right now?"

Implements ``01-Agents/Priority Agent.md`` + ``03-Memory/Priority Rules.md``.

Design (per the phase brief):
  * **Deterministic core** (`app/utils/priority_scoring.py`) — the factor table
    from Priority Rules §2, the proximity buckets from §4, the level bands from
    §1. This produces the score and the level. Always runs.
  * **Bounded LLM adjustment** (reuses `llm_service.py`) — used *only* when the
    deterministic signals conflict (e.g. "URGENT!!!" wording in a promo, an
    important sender on a social notification). Returns a nudge in [-10, +10]
    applied to the score. It never invents deadlines/actions, never overrides a
    user preference, and its failure never breaks the pipeline.
  * **Overrides** applied in the documented precedence:
    important-sender floor → category band → explicit user preference (§6) →
    safety bias for time-sensitive ambiguity.

It only scores priority. It does not reclassify, re-detect actions, re-extract
deadlines, notify, schedule, or touch Gmail.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.models.agent_output import AgentError, AgentOutput, AgentStatus
from app.models.priority import (
    LLMPriorityAdjustment,
    PriorityData,
    PriorityLevel,
    ProximityBucket,
    ScoringMethod,
)
from app.models.email import NormalizedEmail
from app.services.llm_service import (
    LLMClient,
    LLMResponseError,
    LLMUnavailableError,
    NullLLMClient,
)
from app.services.priority_context import PriorityContext, StaticPriorityContext
from app.utils import priority_scoring as ps

AGENT_NAME = "Priority Agent"
AGENT_VERSION = "0.1.0"

_URGENCY_RE = re.compile(
    r"\b(urgent|immediately|asap|as soon as possible|time[- ]sensitive|"
    r"last chance|final (?:reminder|call|notice)|don'?t miss|act now|"
    r"expires soon|closing soon|important reminder)\b",
    re.I,
)
_FORM_MIME = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_LEVEL_MIN_SCORE = {
    PriorityLevel.CRITICAL: 90, PriorityLevel.URGENT: 75, PriorityLevel.HIGH: 55,
    PriorityLevel.MEDIUM: 30, PriorityLevel.LOW: 0,
}
_LEVEL_MAX_SCORE = {
    PriorityLevel.CRITICAL: 100, PriorityLevel.URGENT: 89, PriorityLevel.HIGH: 74,
    PriorityLevel.MEDIUM: 54, PriorityLevel.LOW: 29,
}


@dataclass
class _Signals:
    category: str
    action_required: bool
    reply_requested: bool
    event_registration: bool
    has_form_attachment: bool
    urgency_language: bool
    deadline_present: bool
    deadline_ambiguous: bool
    deadline_unresolved: bool
    normalized_deadline: str | None
    sender_importance: str | None
    triage_conf: float
    action_conf: float
    deadline_conf: float
    upstream_review: bool


class PriorityAgent:
    """Scores the priority of a fully-analysed email."""

    def __init__(
        self,
        settings: Settings | None = None,
        llm_client: LLMClient | None = None,
        context: PriorityContext | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._llm = llm_client or NullLLMClient()
        self._ctx = context or StaticPriorityContext()
        self._tz = self._resolve_tz(self.settings.default_timezone)

    # -- public API -----------------------------------------------------

    def score(
        self,
        email: NormalizedEmail,
        triage: AgentOutput | None = None,
        action: AgentOutput | None = None,
        deadline: AgentOutput | None = None,
        *,
        now: datetime | None = None,
    ) -> AgentOutput:
        started_at = self._now()
        errors: list[AgentError] = []

        now = self._ensure_aware(now) if now else self._now()
        sig = self._extract_signals(email, triage, action, deadline)

        bucket, remaining, is_past = ps.compute_proximity(
            sig.normalized_deadline, now, self.settings.default_timezone
        )

        scoring_inputs = ps.ScoringInputs(
            category=sig.category,
            action_required=sig.action_required,
            reply_requested=sig.reply_requested,
            event_registration=sig.event_registration,
            has_form_attachment=sig.has_form_attachment,
            urgency_language=sig.urgency_language,
            proximity=bucket,
            deadline_present=sig.deadline_present,
            deadline_ambiguous=sig.deadline_ambiguous,
            deadline_unresolved=sig.deadline_unresolved,
            deadline_is_past=is_past,
            sender_importance=sig.sender_importance,
        )
        result = ps.score(
            scoring_inputs,
            ambiguous_deadline_factor=self.settings.priority_ambiguous_deadline_factor,
        )
        base_score = result.base_score
        breakdown = list(result.breakdown)

        # --- bounded LLM adjustment (only on conflicting signals) ---
        scoring_method = ScoringMethod.DETERMINISTIC
        adjustment = 0
        conflict = self._signals_conflict(sig, base_score)
        llm_ran = False
        if conflict and self._llm.is_available:
            llm_ran = True
            try:
                adjustment = self._llm_adjust(email, sig, base_score, bucket)
                if adjustment:
                    breakdown.append(
                        ps.ScoreFactor(
                            factor="llm_context_adjustment", points=adjustment,
                            detail="bounded [-10, +10] nudge for conflicting signals",
                        )
                    )
                    scoring_method = ScoringMethod.LLM_ADJUSTED
            except (LLMResponseError, LLMUnavailableError) as exc:
                errors.append(AgentError(code="llm_unavailable", message=str(exc)))
                scoring_method = ScoringMethod.LLM_UNAVAILABLE

        final_score = int(max(0, min(100, base_score + adjustment)))
        level = ps.score_to_level(final_score)

        # --- overrides (documented precedence) ---
        level, final_score, overrides, forced_notify, forced_monitor, safety_review = (
            self._apply_overrides(email, sig, level, final_score, bucket)
        )

        notify = self._decide_notify(level, sig, forced_notify)
        monitor = self._decide_monitor(sig, is_past, forced_monitor)
        confidence = self._confidence(sig, conflict, safety_review, scoring_method)
        needs_review = self._needs_human_review(sig, level, conflict, llm_ran, safety_review)
        reasoning = self._reasoning(sig, level, final_score, bucket, breakdown, overrides)

        data = PriorityData(
            priority_score=final_score,
            priority_level=level,
            score_breakdown=breakdown,
            notify=notify,
            monitor=monitor,
            reasoning_summary=reasoning,
            confidence=round(confidence, 4),
            proximity_bucket=bucket,
            time_remaining_seconds=remaining,
            deadline_is_past=is_past,
            factors=self._factor_summary(sig, bucket),
            overrides_applied=overrides,
            scoring_method=scoring_method,
            reference_time_used=now.isoformat(),
        )
        status = AgentStatus.PARTIAL if errors else AgentStatus.OK
        return AgentOutput(
            agent=AGENT_NAME,
            agent_version=AGENT_VERSION,
            email_id=email.email_id,
            run_id=self._run_id(),
            status=status,
            confidence=round(confidence, 4),
            needs_human_review=needs_review,
            reasoning_summary=reasoning,
            data=data.model_dump(),
            errors=errors,
            started_at=started_at,
            finished_at=self._now(),
        )

    # -- signal extraction --------------------------------------

    def _extract_signals(
        self,
        email: NormalizedEmail,
        triage: AgentOutput | None,
        action: AgentOutput | None,
        deadline: AgentOutput | None,
    ) -> _Signals:
        t = triage.data if (triage and isinstance(triage.data, dict)) else {}
        a = action.data if (action and isinstance(action.data, dict)) else {}
        d = deadline.data if (deadline and isinstance(deadline.data, dict)) else {}

        category = t.get("category") or "OTHER"
        action_types = [x.get("action_type") for x in a.get("actions", [])]
        reply_requested = category == "REPLY_REQUIRED" or "REPLY" in action_types
        event_registration = category == "EVENT" and "REGISTRATION" in action_types
        has_form_attachment = category in ps.HIGH_VALUE_CATEGORIES and any(
            (att.mime_type or "") in _FORM_MIME for att in email.attachments
        )
        text = f"{email.subject or ''}\n{email.body or ''}"

        deadline_present = bool(d.get("deadline_detected"))
        normalized = d.get("normalized_deadline")
        ambiguous = bool(d.get("ambiguity_flag"))

        upstream_review = any(
            x is not None and x.needs_human_review for x in (triage, action, deadline)
        )
        return _Signals(
            category=category,
            action_required=bool(a.get("action_required")),
            reply_requested=reply_requested,
            event_registration=event_registration,
            has_form_attachment=has_form_attachment,
            urgency_language=bool(_URGENCY_RE.search(text)),
            deadline_present=deadline_present,
            deadline_ambiguous=ambiguous,
            deadline_unresolved=ambiguous and normalized is None,
            normalized_deadline=normalized,
            sender_importance=self._ctx.sender_importance(email.sender.email),
            triage_conf=float(t.get("confidence", 0.9)),
            action_conf=float(a.get("confidence", 0.9)),
            deadline_conf=float(d.get("confidence", 0.9)),
            upstream_review=upstream_review,
        )

    # -- overrides (STEP 7 precedence) ---------------------------

    def _apply_overrides(
        self,
        email: NormalizedEmail,
        sig: _Signals,
        level: PriorityLevel,
        score_value: int,
        bucket: ProximityBucket,
    ) -> tuple[PriorityLevel, int, list[str], bool | None, bool | None, bool]:
        applied: list[str] = []
        forced_notify: bool | None = None
        forced_monitor: bool | None = None

        # 1. important-sender CRITICAL -> floor at HIGH (Important Senders.md)
        if sig.sender_importance == "CRITICAL":
            new = ps.clamp_level(level, floor=PriorityLevel.HIGH)
            if new != level:
                applied.append("important_sender_critical_floor_high")
            level = new

        # 2. category priority band (User Preferences §2 / Priority Rules §6)
        band = self._ctx.category_band(sig.category)
        if band == "HIGH":
            new = ps.clamp_level(level, floor=PriorityLevel.MEDIUM)
            if new != level:
                applied.append("category_band_floor_medium")
            level = new
        elif band == "LOW":
            new = ps.clamp_level(level, ceiling=PriorityLevel.MEDIUM)
            if new != level:
                applied.append("category_band_ceiling_medium")
            level = new

        # 3. explicit user overrides (§6) — HIGHEST precedence
        for m in self._ctx.user_overrides(
            category=sig.category, subject=email.subject or "",
            body=email.body or "", sender_email=email.sender.email,
        ):
            ov = m.override
            if ov.floor_level is not None:
                level = ps.clamp_level(level, floor=ov.floor_level)
            if ov.ceiling_level is not None:
                level = ps.clamp_level(level, ceiling=ov.ceiling_level)
            if ov.force_notify is not None:
                forced_notify = ov.force_notify
            if ov.force_monitor is not None:
                forced_monitor = ov.force_monitor
            applied.append(ov.name)

        # 4. an already-overdue deadline should stay visible but not escalate to
        #    CRITICAL (escalating reminders on a passed deadline are pointless).
        if bucket == ProximityBucket.OVERDUE:
            new = ps.clamp_level(level, ceiling=PriorityLevel.URGENT)
            if new != level:
                applied.append("overdue_deadline_ceiling_urgent")
            level = new

        # 5. safety bias for time-sensitive ambiguity (STEP 8)
        safety_review = False
        if (
            sig.category in ps.HIGH_VALUE_CATEGORIES
            and sig.action_required
            and (sig.deadline_unresolved or (sig.deadline_present and sig.deadline_ambiguous))
            and not ps.level_at_least(level, PriorityLevel.HIGH)
        ):
            level = PriorityLevel.HIGH
            safety_review = True
            applied.append("safety_bias_ambiguous_time_sensitive")

        # keep the score consistent with any forced level change
        score_value = max(score_value, _LEVEL_MIN_SCORE[level])
        score_value = min(score_value, _LEVEL_MAX_SCORE[level])
        return level, score_value, applied, forced_notify, forced_monitor, safety_review

    # -- LLM adjustment ----------------------------------------

    def _signals_conflict(self, sig: _Signals, base_score: int) -> bool:
        low_band = sig.category in {"PROMOTIONAL", "NEWSLETTER", "SPAM", "SOCIAL"}
        if sig.urgency_language and low_band:
            return True
        if sig.sender_importance == "CRITICAL" and low_band:
            return True
        if (
            sig.category in ps.HIGH_VALUE_CATEGORIES
            and not sig.action_required
            and not sig.deadline_present
            and 25 <= base_score < 55
        ):
            return True
        if min(sig.triage_conf, sig.action_conf, sig.deadline_conf) < 0.55 and 25 <= base_score < 75:
            return True
        return False

    def _llm_adjust(
        self, email: NormalizedEmail, sig: _Signals, base_score: int, bucket: ProximityBucket
    ) -> int:
        body = (email.body or "").strip()
        if len(body) > 3000:
            body = body[:3000] + "\n...[truncated]"
        user = (
            f"Deterministic score so far: {base_score}/100\n"
            f"Category: {sig.category}\n"
            f"Action required: {sig.action_required}\n"
            f"Deadline proximity: {bucket.value}\n"
            f"Sender importance: {sig.sender_importance or 'none'}\n"
            f"Sender: {email.sender.email}\n"
            f"Subject: {email.subject or '(empty)'}\n\n"
            f"Body:\n{body or '(empty)'}"
        )
        raw = self._llm.complete_json(_SYSTEM_PROMPT, user, max_tokens=self.settings.llm_max_tokens)
        try:
            adj = LLMPriorityAdjustment.model_validate(raw)
        except ValidationError as exc:
            raise LLMResponseError(f"LLM returned an invalid adjustment: {exc}") from exc
        cap = self.settings.priority_llm_max_adjustment
        return int(max(-cap, min(cap, adj.score_adjustment)))

    # -- notify / monitor -------------------------------------

    def _decide_notify(self, level: PriorityLevel, sig: _Signals, forced: bool | None) -> bool:
        if forced is not None:
            return forced
        if sig.category in {"PROMOTIONAL", "NEWSLETTER", "SPAM", "SOCIAL"}:
            return False
        return level in self._ctx.notify_levels()

    def _decide_monitor(self, sig: _Signals, is_past: bool, forced: bool | None) -> bool:
        if forced is not None:
            return forced
        if sig.category in {"PROMOTIONAL", "NEWSLETTER", "SPAM", "SOCIAL"}:
            return False
        if sig.action_required:
            return True
        if sig.deadline_ambiguous:
            return True
        if sig.deadline_present and not is_past:
            return True
        return False

    # -- confidence / review -------------------------------

    def _confidence(
        self, sig: _Signals, conflict: bool, safety_review: bool, method: ScoringMethod
    ) -> float:
        conf = 0.9
        conf = min(conf, min(sig.triage_conf, sig.action_conf, sig.deadline_conf) + 0.05)
        if sig.deadline_ambiguous:
            conf -= 0.08
        if conflict:
            conf -= 0.08
        if safety_review:
            conf -= 0.12
        if method == ScoringMethod.LLM_ADJUSTED:
            conf = min(conf, 0.85)
        return max(0.3, min(0.95, conf))

    def _needs_human_review(
        self, sig: _Signals, level: PriorityLevel, conflict: bool, llm_ran: bool, safety: bool
    ) -> bool:
        if safety:
            return True
        # a time-sensitive high-value email whose deadline could not be pinned down
        if (
            sig.deadline_unresolved
            and sig.action_required
            and sig.category in ps.HIGH_VALUE_CATEGORIES
        ):
            return True
        if (
            sig.upstream_review
            and sig.category in ps.HIGH_VALUE_CATEGORIES
            and ps.level_at_least(level, PriorityLevel.MEDIUM)
        ):
            return True
        return False

    # -- explainability -----------------------------------

    @staticmethod
    def _factor_summary(sig: _Signals, bucket: ProximityBucket) -> dict:
        return {
            "category": sig.category,
            "action_required": sig.action_required,
            "reply_requested": sig.reply_requested,
            "deadline_present": sig.deadline_present,
            "deadline_proximity": bucket.value,
            "deadline_ambiguous": sig.deadline_ambiguous,
            "important_sender": sig.sender_importance,
            "explicit_urgency_language": sig.urgency_language,
        }

    @staticmethod
    def _reasoning(
        sig: _Signals,
        level: PriorityLevel,
        score_value: int,
        bucket: ProximityBucket,
        breakdown: list[ps.ScoreFactor],
        overrides: list[str],
    ) -> str:
        top = sorted(breakdown, key=lambda f: abs(f.points), reverse=True)[:3]
        drivers = ", ".join(f.factor.replace("_", " ") for f in top) or "no strong signals"
        parts = [f"{sig.category} email — {drivers}"]
        if sig.deadline_present and bucket != ProximityBucket.NONE:
            parts.append(f"deadline {bucket.value.replace('_', ' ').lower()}")
        if sig.sender_importance in ("CRITICAL", "HIGH"):
            parts.append(f"{sig.sender_importance.lower()} sender")
        if overrides:
            parts.append("overrides: " + ", ".join(overrides))
        return f"{'; '.join(parts)} → {level.value} (score {score_value})."

    # -- runtime plumbing ------------------------------

    def _now(self) -> datetime:
        return datetime.now(self._tz)

    def _ensure_aware(self, dt: datetime) -> datetime:
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=self._tz)

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


_SYSTEM_PROMPT = """You assist a deterministic email-priority scorer for a college student.
A rule engine has ALREADY produced a score from category, action, deadline proximity, sender
importance and user preferences. Your ONLY job: return a SMALL bounded adjustment when the
rules clearly miss the email's real urgency or lack thereof.

You must NOT: invent a deadline or action, change the category, or override a user preference.
Base your nudge only on wording/tone the rules cannot see (e.g. a genuine personal plea, or
marketing "URGENT!!!" that is obviously noise).

Respond with ONLY:
{"score_adjustment": <integer between -10 and 10>, "reasoning": "<one sentence>"}
Use 0 if the deterministic score already looks right."""
