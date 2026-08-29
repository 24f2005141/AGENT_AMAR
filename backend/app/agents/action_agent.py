"""Action Agent — "What does the user need to do because of this email?"

Implements ``01-Agents/Action Agent.md`` + ``04-Schemas/Action Schema.md``.

Hybrid design, mirroring the Triage Agent:
  * **Layer 1 (deterministic)** — explicit action-phrase matching on the
    *cleaned* body + subject, with per-clause negation / completion /
    conditional handling. Always runs.
  * **Layer 2 (LLM)** — only when Layer 1 is under-confident, its signals
    conflict, or the actions are all merely implied *and* an LLM provider is
    configured. Constrained to the 9 schema action types; validated before use.

The agent only *detects and structures* required actions. It never classifies
the email, computes priority/urgency, normalises deadlines, schedules anything,
touches Gmail, or performs an action.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.agents import action_rules as rules
from app.core.config import Settings, get_settings
from app.models.action import (
    ActionData,
    ActionItem,
    ActionStatus,
    ActionType,
    LLMActionResult,
)
from app.models.agent_output import AgentError, AgentOutput, AgentStatus
from app.models.email import NormalizedEmail
from app.models.triage import ClassificationMethod
from app.services.llm_service import (
    LLMClient,
    LLMResponseError,
    LLMUnavailableError,
    NullLLMClient,
)

AGENT_NAME = "Action Agent"
AGENT_VERSION = "0.1.0"

# Triage category -> action types that category tends to support (soft bonus
# only; email content is always the primary evidence — STEP 8).
_CATEGORY_SUPPORT: dict[str, set[ActionType]] = {
    "INTERNSHIP": {ActionType.FORM_SUBMISSION, ActionType.DOCUMENT_UPLOAD, ActionType.REGISTRATION, ActionType.REPLY},
    "PLACEMENT": {ActionType.REGISTRATION, ActionType.FORM_SUBMISSION, ActionType.DOCUMENT_UPLOAD, ActionType.ATTEND_EVENT},
    "JOB_OPPORTUNITY": {ActionType.FORM_SUBMISSION, ActionType.DOCUMENT_UPLOAD, ActionType.REPLY},
    "ASSIGNMENT": {ActionType.COMPLETE_ASSIGNMENT, ActionType.DOCUMENT_UPLOAD},
    "EXAM": {ActionType.READ_AND_ACKNOWLEDGE, ActionType.FORM_SUBMISSION, ActionType.PAYMENT},
    "FACULTY_ANNOUNCEMENT": {ActionType.READ_AND_ACKNOWLEDGE, ActionType.FORM_SUBMISSION, ActionType.REPLY},
    "REPLY_REQUIRED": {ActionType.REPLY},
    "ACADEMIC_INFORMATION": {ActionType.READ_AND_ACKNOWLEDGE},
    "PROJECT_UPDATE": {ActionType.REPLY, ActionType.READ_AND_ACKNOWLEDGE},
    "EVENT": {ActionType.REGISTRATION, ActionType.ATTEND_EVENT},
}

_LOW_BAND_CATEGORIES = {"PROMOTIONAL", "NEWSLETTER", "SPAM", "SOCIAL"}

_DESCRIPTIONS: dict[ActionType, str] = {
    ActionType.FORM_SUBMISSION: "Fill and submit the form",
    ActionType.REPLY: "Reply to this email",
    ActionType.REGISTRATION: "Register / sign up",
    ActionType.DOCUMENT_UPLOAD: "Upload the requested document(s)",
    ActionType.PAYMENT: "Make the required payment",
    ActionType.ATTEND_EVENT: "Attend the event / session",
    ActionType.COMPLETE_ASSIGNMENT: "Complete and submit the assignment",
    ActionType.READ_AND_ACKNOWLEDGE: "Read and acknowledge this email",
    ActionType.OTHER: "Take the action described in the email",
}

_ALWAYS_BLOCKING = {
    ActionType.FORM_SUBMISSION,
    ActionType.COMPLETE_ASSIGNMENT,
    ActionType.DOCUMENT_UPLOAD,
    ActionType.REGISTRATION,
    ActionType.PAYMENT,
    ActionType.REPLY,
}


@dataclass
class _Candidate:
    action_type: ActionType
    weight: float = 0.0
    explicit: bool = False
    evidence: str = ""
    category_support: bool = False
    conflicting: bool = False
    conditional_only: bool = False


@dataclass
class _Detection:
    actions: list[ActionItem] = field(default_factory=list)
    method: ClassificationMethod = ClassificationMethod.DETERMINISTIC
    reasoning: str = ""
    overall_confidence: float = 0.0
    conflicting: bool = False
    no_action_marker: bool = False


class ActionAgent:
    """Detects the discrete actions a :class:`NormalizedEmail` requires of the user."""

    def __init__(self, settings: Settings | None = None, llm_client: LLMClient | None = None) -> None:
        self.settings = settings or get_settings()
        self._llm = llm_client or NullLLMClient()
        self._tz = self._resolve_tz(self.settings.default_timezone)

    # -- public API -----------------------------------------------------

    def detect(self, email: NormalizedEmail, triage: AgentOutput | None = None) -> AgentOutput:
        started_at = self._now()
        errors: list[AgentError] = []

        category = None
        if triage is not None and isinstance(triage.data, dict):
            category = triage.data.get("category")

        det = self._deterministic(email, category)
        detection = det

        if self._should_use_llm(det) and self._llm.is_available:
            try:
                detection = self._llm_detect(email, category, det)
            except LLMResponseError as exc:
                errors.append(AgentError(code="invalid_llm_response", message=str(exc)))
                detection = self._as_fallback(det)
            except LLMUnavailableError as exc:
                errors.append(AgentError(code="llm_unavailable", message=str(exc)))
                detection = self._as_fallback(det)

        data = self._build_data(email, detection)
        needs_review = self._needs_human_review(detection, data)
        status = AgentStatus.PARTIAL if errors else AgentStatus.OK

        return AgentOutput(
            agent=AGENT_NAME,
            agent_version=AGENT_VERSION,
            email_id=email.email_id,
            run_id=self._run_id(),
            status=status,
            confidence=round(detection.overall_confidence, 4),
            needs_human_review=needs_review,
            reasoning_summary=detection.reasoning,
            data=data.model_dump(),
            errors=errors,
            started_at=started_at,
            finished_at=self._now(),
        )

    # -- layer 1: deterministic --------------------------------------

    def _deterministic(self, email: NormalizedEmail, category: str | None) -> _Detection:
        subject = (email.subject or "").strip()
        body = (email.body or "").strip()
        full_lower = f"{subject}\n{body}".lower()

        if any(p.search(full_lower) for p in rules.NO_ACTION_MARKERS):
            return _Detection(
                actions=[],
                reasoning="Email explicitly states that no action is required.",
                overall_confidence=0.9,
                no_action_marker=True,
            )

        deadline_hint = self._deadline_hint(body) or self._deadline_hint(subject)
        supported = _CATEGORY_SUPPORT.get(category or "", set())

        clauses = rules.split_clauses(f"{subject}. {body}")
        subject_lower = subject.lower()
        candidates: dict[ActionType, _Candidate] = {}
        suppressed: set[ActionType] = set()

        for clause in clauses:
            cl = clause.lower()
            negated = any(p.search(cl) for p in rules.NEGATION_PATTERNS)
            completed = any(p.search(cl) for p in rules.COMPLETION_PATTERNS)
            conditional = any(p.search(cl) for p in rules.CONDITIONAL_PATTERNS)

            # a negated / completed clause with no exact phrase still tells us
            # which action(s) are being cancelled or reported as already done
            if negated or completed:
                for kw, hinted in rules.CONTEXT_TYPE_HINTS:
                    if kw in cl:
                        for ht in hinted:
                            suppressed.add(ht)
                            if ht in candidates:
                                candidates[ht].conflicting = True

            matched_types_here: set[ActionType] = set()
            for atype, phrases in rules.ACTION_PHRASES.items():
                for phrase, weight, explicit in phrases:
                    if phrase not in cl:
                        continue
                    matched_types_here.add(atype)
                    if negated or completed:
                        suppressed.add(atype)
                        if atype in candidates:
                            candidates[atype].conflicting = True
                        continue
                    if conditional:
                        # not a mandatory action — record but don't promote
                        cand = candidates.setdefault(atype, _Candidate(atype))
                        cand.conditional_only = cand.conditional_only or not cand.evidence
                        continue
                    w = weight * (1.6 if phrase in subject_lower else 1.0)
                    self._add_candidate(candidates, atype, w, explicit, clause, atype in supported)

            # generic imperative verbs — only for clauses with no explicit phrase
            if not matched_types_here and not (negated or completed or conditional):
                for verb, atype, weight in rules.GENERIC_IMPERATIVES:
                    if re.search(rf"\b{verb}\b", cl):
                        self._add_candidate(candidates, atype, weight, False, clause, atype in supported)

        # drop conditional-only / fully-suppressed candidates
        live = {
            t: c
            for t, c in candidates.items()
            if c.weight > 0 and not (c.conditional_only and not c.evidence)
        }
        for t in list(live):
            if t in suppressed:
                if not live[t].explicit and live[t].weight < 2.5:
                    del live[t]
                else:
                    # explicit ask + a "done/cancelled" mention elsewhere = conflict
                    live[t].conflicting = True

        if not live:
            reason = "No explicit action language found in the cleaned email body."
            conf = 0.6
            if suppressed:
                reason = "The only action language present is negated or already completed."
                conf = 0.82
            elif category in _LOW_BAND_CATEGORIES:
                reason = f"No action language; {category} email — informational only."
                conf = 0.85
            return _Detection(actions=[], reasoning=reason, overall_confidence=conf)

        actions: list[ActionItem] = []
        any_conflict = False
        for idx, (atype, cand) in enumerate(
            sorted(live.items(), key=lambda kv: kv[1].weight, reverse=True), start=1
        ):
            conf = self._candidate_confidence(cand)
            any_conflict = any_conflict or cand.conflicting
            actions.append(
                ActionItem(
                    action_id=f"act_{idx:03d}",
                    action_type=atype,
                    action_description=self._describe(atype, subject),
                    target_link=self._pick_link(email.links, atype),
                    related_email=email.email_id,
                    blocking=self._is_blocking(atype, cand, deadline_hint),
                    raw_deadline_hint=deadline_hint,
                    confidence=round(conf, 4),
                    status=ActionStatus.OPEN,
                    evidence=self._trim_evidence(cand.evidence),
                )
            )

        overall = max(a.confidence for a in actions)
        explicit_n = sum(1 for a in actions if _has_explicit(live[ActionType(a.action_type)]))
        reasoning = self._reasoning(actions, explicit_n, any_conflict)
        return _Detection(
            actions=actions,
            reasoning=reasoning,
            overall_confidence=overall,
            conflicting=any_conflict,
        )

    @staticmethod
    def _add_candidate(
        candidates: dict[ActionType, _Candidate],
        atype: ActionType,
        weight: float,
        explicit: bool,
        evidence: str,
        category_support: bool,
    ) -> None:
        cand = candidates.get(atype)
        if cand is None:
            candidates[atype] = _Candidate(
                action_type=atype,
                weight=weight,
                explicit=explicit,
                evidence=evidence,
                category_support=category_support,
            )
            return
        # keep the strongest single occurrence (STEP 5: repeated -> one, strongest evidence)
        if weight > cand.weight:
            cand.weight = weight
            cand.evidence = evidence
        cand.explicit = cand.explicit or explicit
        cand.category_support = cand.category_support or category_support

    def _candidate_confidence(self, cand: _Candidate) -> float:
        if cand.explicit:
            raw = 0.72 + min(cand.weight, 4.0) / 4.0 * 0.23
        else:
            raw = 0.46 + min(cand.weight, 3.0) / 3.0 * 0.19
        if cand.category_support:
            raw += 0.06
        if cand.conflicting:
            raw *= 0.5
        return max(0.2, min(0.97, raw))

    @staticmethod
    def _is_blocking(atype: ActionType, cand: _Candidate, deadline_hint: str | None) -> bool:
        if atype == ActionType.READ_AND_ACKNOWLEDGE:
            return "acknowledge receipt" in cand.evidence.lower() or "must" in cand.evidence.lower()
        if not cand.explicit:
            return bool(deadline_hint)
        return atype in _ALWAYS_BLOCKING or bool(deadline_hint)

    @staticmethod
    def _deadline_hint(text: str) -> str | None:
        if not text:
            return None
        m = rules.DEADLINE_HINT_RE.search(text)
        if not m:
            return None
        return re.sub(r"\s+", " ", m.group(0)).strip()[:80]

    @staticmethod
    def _pick_link(links: list[str], atype: ActionType) -> str | None:
        hints = rules.LINK_HINTS.get(atype)
        if not hints:
            return links[0] if len(links) == 1 else None
        for link in links:
            low = link.lower()
            if any(h in low for h in hints):
                return link
        return None

    @staticmethod
    def _describe(atype: ActionType, subject: str) -> str:
        base = _DESCRIPTIONS[atype]
        subj = subject.strip()
        if subj:
            return f"{base} (re: {subj[:80]})"
        return base + "."

    @staticmethod
    def _trim_evidence(evidence: str) -> str | None:
        if not evidence:
            return None
        ev = re.sub(r"\s+", " ", evidence).strip()
        return ev[:180]

    @staticmethod
    def _reasoning(actions: list[ActionItem], explicit_n: int, conflict: bool) -> str:
        types = ", ".join(dict.fromkeys(a.action_type for a in actions))
        kind = "explicit" if explicit_n == len(actions) else ("mixed" if explicit_n else "implied")
        note = " Signals conflict (an action is also negated/confirmed elsewhere)." if conflict else ""
        return f"Detected {len(actions)} {kind} action(s): {types}.{note}"

    # -- layer 2: LLM ------------------------------------------------

    def _should_use_llm(self, det: _Detection) -> bool:
        if det.no_action_marker:
            return False
        if det.conflicting:
            return True
        if det.overall_confidence < self.settings.action_llm_threshold:
            return True
        # all actions are merely implied -> let the LLM confirm
        return bool(det.actions) and all(a.confidence < 0.7 for a in det.actions)

    def _llm_detect(self, email: NormalizedEmail, category: str | None, det: _Detection) -> _Detection:
        body = (email.body or "").strip()
        if len(body) > 4000:
            body = body[:4000] + "\n...[truncated]"
        user = (
            f"Sender: {email.sender.email}\n"
            f"Triage category: {category or 'unknown'}\n"
            f"Subject: {email.subject or '(empty)'}\n\n"
            f"Body:\n{body or '(empty)'}"
        )
        raw = self._llm.complete_json(_SYSTEM_PROMPT, user, max_tokens=self.settings.llm_max_tokens)
        try:
            parsed = LLMActionResult.model_validate(raw)
        except ValidationError as exc:
            raise LLMResponseError(f"LLM returned an invalid action result: {exc}") from exc

        if not parsed.action_required or not parsed.actions:
            return _Detection(
                actions=[],
                method=ClassificationMethod.LLM,
                reasoning="LLM determined no user action is required.",
                overall_confidence=max(0.6, min(0.9, _avg_conf(parsed))),
            )

        deadline_hint = self._deadline_hint(email.body or "") or self._deadline_hint(email.subject or "")
        actions: list[ActionItem] = []
        seen: set[ActionType] = set()
        for idx, la in enumerate(parsed.actions, start=1):
            if la.action_type in seen:
                continue
            seen.add(la.action_type)
            actions.append(
                ActionItem(
                    action_id=f"act_{idx:03d}",
                    action_type=la.action_type,
                    action_description=(la.action_description.strip() or self._describe(la.action_type, email.subject or "")),
                    target_link=la.target_link or self._pick_link(email.links, la.action_type),
                    related_email=email.email_id,
                    blocking=bool(la.blocking),
                    raw_deadline_hint=(la.raw_deadline_hint or deadline_hint),
                    confidence=round(max(0.2, min(0.97, la.confidence)), 4),
                    status=ActionStatus.OPEN,
                    evidence=self._trim_evidence(la.evidence),
                )
            )

        overall = max((a.confidence for a in actions), default=0.5)
        return _Detection(
            actions=actions,
            method=ClassificationMethod.LLM,
            reasoning=f"LLM detected {len(actions)} action(s): "
            + ", ".join(dict.fromkeys(a.action_type for a in actions)),
            overall_confidence=overall,
        )

    @staticmethod
    def _as_fallback(det: _Detection) -> _Detection:
        det.method = ClassificationMethod.LLM_FALLBACK_DETERMINISTIC
        return det

    # -- assemble --------------------------------------------------

    def _build_data(self, email: NormalizedEmail, det: _Detection) -> ActionData:
        if not det.actions:
            return ActionData(
                action_required=False,
                actions=[],
                action_type=None,
                action_description=None,
                related_email=email.email_id,
                confidence=round(det.overall_confidence, 4),
                detection_method=det.method,
            )

        primary = max(
            det.actions,
            key=lambda a: (a.blocking, a.confidence),
        )
        if len(det.actions) == 1:
            summary = det.actions[0].action_description
        else:
            summary = "Multiple actions required: " + ", ".join(
                dict.fromkeys(a.action_type for a in det.actions)
            )
        return ActionData(
            action_required=True,
            actions=det.actions,
            action_type=ActionType(primary.action_type),
            action_description=summary,
            related_email=email.email_id,
            confidence=round(det.overall_confidence, 4),
            detection_method=det.method,
        )

    def _needs_human_review(self, det: _Detection, data: ActionData) -> bool:
        if det.overall_confidence < self.settings.action_review_threshold:
            return True
        if det.conflicting and det.overall_confidence < 0.7:
            return True
        if data.action_required and det.actions and all(
            a.confidence < 0.65 for a in det.actions
        ):
            return True
        return False

    # -- runtime plumbing ----------------------------------------

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


def _has_explicit(cand: _Candidate) -> bool:
    return cand.explicit


def _avg_conf(parsed: LLMActionResult) -> float:
    if not parsed.actions:
        return 0.75
    return sum(a.confidence for a in parsed.actions) / len(parsed.actions)


_SYSTEM_PROMPT = """You are the Action Agent for an email-intelligence system used by a college student.
Your ONLY job: decide what the USER must DO because of this email. Do not classify the email,
judge priority/urgency, or parse dates.

Ignore imperative words that appear in advertisements, email signatures, quoted previous
messages, or "do not reply" footers. Only report an action the email genuinely asks THIS user
to perform, and that is NOT already completed ("your application has been submitted" => none)
and NOT merely conditional ("reply if you have questions" => none).

Choose action_type ONLY from this closed list (verbatim):
FORM_SUBMISSION - fill in / submit a form (incl. an application form or Google Form)
REPLY - send a reply / confirmation by email
REGISTRATION - register or sign up for something
DOCUMENT_UPLOAD - upload or attach a document (resume, marksheet, ...)
PAYMENT - make a payment / pay a fee
ATTEND_EVENT - be present at a scheduled event, session or meeting
COMPLETE_ASSIGNMENT - do and submit coursework
READ_AND_ACKNOWLEDGE - read carefully / acknowledge receipt (no other action)
OTHER - a genuine required action that fits none of the above

Split "do X and Y" into separate actions. Do not repeat the same action_type.

Respond with ONLY this JSON:
{"action_required": true|false,
 "actions": [
   {"action_type": "<ONE_TYPE>", "action_description": "<short imperative>",
    "target_link": "<url or null>", "raw_deadline_hint": "<verbatim deadline phrase or null>",
    "blocking": true|false, "confidence": 0.0-1.0, "evidence": "<short quote from the email>"}
 ]}
If nothing is required: {"action_required": false, "actions": []}
Never invent an action_type outside the list."""
