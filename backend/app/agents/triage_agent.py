"""Triage Agent — "What kind of email is this?"

Implements ``01-Agents/Triage Agent.md`` + ``03-Memory/Classification Rules.md``.

Hybrid design:
  * **Layer 1 (deterministic)** — keyword / sender / structure scoring, then the
    documented precedence rules. Always runs.
  * **Layer 2 (LLM)** — only when the deterministic confidence is below
    ``TRIAGE_LLM_THRESHOLD`` *and* an LLM provider is configured. The LLM picks a
    category; the deterministic precedence rules are still applied on top of its
    answer (the LLM cannot override explicit precedence).

The agent classifies. It does **not** compute priority, extract deadlines,
detect actions, send notifications, or touch Gmail.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.agents import triage_rules as rules
from app.core.config import Settings, get_settings
from app.models.agent_output import AgentError, AgentOutput, AgentStatus
from app.models.email import NormalizedEmail
from app.models.triage import (
    CATEGORY_PRIORITY_BAND,
    LOW_BAND_CATEGORIES,
    OPPORTUNITY_CATEGORIES,
    ClassificationMethod,
    ImportanceEstimate,
    LLMClassification,
    TriageCategory,
    TriageData,
    TriageSignals,
)
from app.services.llm_service import (
    LLMClient,
    LLMResponseError,
    LLMUnavailableError,
    NullLLMClient,
)

AGENT_NAME = "Triage Agent"
AGENT_VERSION = "0.1.0"

_IMPORTANCE_RANK = {ImportanceEstimate.LOW: 0, ImportanceEstimate.MEDIUM: 1, ImportanceEstimate.HIGH: 2}
_RANK_IMPORTANCE = {v: k for k, v in _IMPORTANCE_RANK.items()}

_DATE_HINT_RE = re.compile(
    r"\b(deadline|last date|due date|by (mon|tue|wed|thu|fri|sat|sun)|"
    r"tomorrow|today|tonight|\d{1,2}(st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)|"
    r"\d{1,2}[/-]\d{1,2}|\d{4}-\d{2}-\d{2})\b",
    re.IGNORECASE,
)
_TASK_HINT_RE = re.compile(
    r"\b(submit|fill|complete|upload|register|apply|confirm|acknowledge|pay)\b", re.IGNORECASE
)
_URGENCY_RE = re.compile(r"\b(urgent|immediately|asap|act now|important)\b", re.IGNORECASE)

_SYSTEM_PROMPT = """You are the Triage Agent for an email-intelligence system used by a college student.
Your ONLY job: decide what KIND of email this is. Do not judge priority, deadlines, or actions.

Choose EXACTLY ONE category from this closed list (return it verbatim):
INTERNSHIP - internship opportunity or its application
PLACEMENT - campus placement drive / recruitment / company hiring visit
JOB_OPPORTUNITY - full or part-time job opening, off-campus role
ASSIGNMENT - coursework to submit (assignment, lab record, problem set)
EXAM - exam schedule, hall ticket / admit card, results, revaluation
FACULTY_ANNOUNCEMENT - official notice/circular from faculty or department
REPLY_REQUIRED - the sender is explicitly waiting on the student's reply
ACADEMIC_INFORMATION - academic content with no urgent task (syllabus, notes, class change)
PROJECT_UPDATE - team / group / project status
EVENT - workshop, webinar, hackathon, club or cultural event, registration
PROMOTIONAL - selling something (discounts, offers, marketing)
NEWSLETTER - recurring subscribed digest
SPAM - unsolicited / suspicious / phishing
SOCIAL - social-network notification
OTHER - genuine email that fits nothing above

Precedence when several fit:
1. Opportunity (INTERNSHIP/PLACEMENT/JOB_OPPORTUNITY) beats EVENT, NEWSLETTER, ACADEMIC_INFORMATION.
2. EXAM/ASSIGNMENT beats FACULTY_ANNOUNCEMENT when there is a concrete task + date.
3. REPLY_REQUIRED is only the primary category when nothing else dominates.
4. Never label a @college.edu sender as SPAM or PROMOTIONAL.

Respond with ONLY a JSON object:
{"category": "<ONE_CATEGORY>", "subcategory": "<short_snake_case_or_null>",
 "importance_estimate": "HIGH|MEDIUM|LOW", "confidence": 0.0-1.0,
 "reasoning": "<one sentence>"}
Never invent a category outside the list."""


@dataclass
class _Assessment:
    category: TriageCategory
    subcategory: str | None
    importance: ImportanceEstimate
    confidence: float
    reasoning: str
    method: ClassificationMethod
    keywords: list[str] = field(default_factory=list)
    category_scores: dict[str, float] = field(default_factory=dict)
    precedence_applied: list[str] = field(default_factory=list)
    sender_importance: str | None = None
    sender_in_important_list: bool = False
    has_form_link: bool = False
    conflicting_signals: bool = False


class TriageAgent:
    """Classifies a :class:`NormalizedEmail` into one category."""

    def __init__(
        self,
        settings: Settings | None = None,
        llm_client: LLMClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._llm = llm_client or NullLLMClient()
        self._tz = self._resolve_tz(self.settings.default_timezone)

    # -- public API -----------------------------------------------------

    def classify(self, email: NormalizedEmail) -> AgentOutput:
        started_at = self._now()
        errors: list[AgentError] = []

        det = self._deterministic(email)
        assessment = det

        if det.confidence < self.settings.triage_llm_threshold and self._llm.is_available:
            try:
                llm = self._llm_classify(email, det)
                assessment = self._merge(det, llm, email)
            except LLMResponseError as exc:
                errors.append(AgentError(code="invalid_llm_response", message=str(exc)))
                assessment = self._as_fallback(det)
            except LLMUnavailableError as exc:
                errors.append(AgentError(code="llm_unavailable", message=str(exc)))
                assessment = self._as_fallback(det)

        needs_review = self._needs_human_review(assessment)
        data = self._build_data(assessment, needs_review)
        status = AgentStatus.PARTIAL if errors else AgentStatus.OK

        return AgentOutput(
            agent=AGENT_NAME,
            agent_version=AGENT_VERSION,
            email_id=email.email_id,
            run_id=self._run_id(),
            status=status,
            confidence=round(assessment.confidence, 4),
            needs_human_review=needs_review,
            reasoning_summary=assessment.reasoning,
            data=data.model_dump(),
            errors=errors,
            started_at=started_at,
            finished_at=self._now(),
        )

    # -- layer 1: deterministic --------------------------------------

    def _deterministic(self, email: NormalizedEmail) -> _Assessment:
        subject = (email.subject or "").lower()
        body = (email.body or "").lower()
        text = f" {subject} \n {body} "
        addr = (email.sender.email or "").lower()
        domain = addr.split("@")[-1] if "@" in addr else ""
        is_college = domain == rules.COLLEGE_DOMAIN or domain.endswith("." + rules.COLLEGE_DOMAIN)

        pattern, sender_importance = rules.match_sender_importance(addr)
        sender_in_list = sender_importance in {"CRITICAL", "HIGH"}
        expected = rules.sender_expected_categories(addr)

        # --- score every category ---
        scores: dict[TriageCategory, float] = {}
        matched: dict[TriageCategory, list[str]] = {}
        for category, phrases in rules.CATEGORY_KEYWORDS.items():
            total = 0.0
            hits: list[str] = []
            for phrase, weight in phrases:
                if phrase in text:
                    factor = 1.6 if phrase.strip() in subject else 1.0
                    total += weight * factor
                    hits.append(phrase.strip())
            if total:
                scores[category] = round(total, 2)
                matched[category] = hits

        # structural signals
        has_form_link = any(ind in text for ind in rules.FORM_INDICATORS) or any(
            "form" in link.lower() for link in email.links
        )
        phishing_hits = [p for p in rules.PHISHING_PHRASES if p in text]
        phishing_strong = len(phishing_hits) >= 2 or (
            len(phishing_hits) >= 1 and email.has_links and not is_college and bool(_URGENCY_RE.search(text))
        )
        is_ics = any(
            (a.mime_type or "").lower() in {"text/calendar", "application/ics"}
            or (a.filename or "").lower().endswith(".ics")
            for a in email.attachments
        )

        # sender-domain based nudges
        if domain in rules.SOCIAL_SENDER_DOMAINS and not addr.startswith("jobs@"):
            scores[TriageCategory.SOCIAL] = scores.get(TriageCategory.SOCIAL, 0.0) + 3.5
            matched.setdefault(TriageCategory.SOCIAL, []).append(f"social domain:{domain}")
        if sender_importance == "LOW_TRUST":
            for c in (TriageCategory.PROMOTIONAL, TriageCategory.NEWSLETTER):
                if c in scores:
                    scores[c] += 1.5

        if is_ics:
            scores[TriageCategory.EVENT] = scores.get(TriageCategory.EVENT, 0.0) + 4.0
            matched.setdefault(TriageCategory.EVENT, []).append("calendar invite (.ics)")

        # --- pick top ---
        if not scores:
            category = TriageCategory.OTHER
            top = second = 0.0
            keywords: list[str] = []
        else:
            ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
            category, top = ranked[0]
            second = ranked[1][1] if len(ranked) > 1 else 0.0
            keywords = matched.get(category, [])

        # --- precedence ---
        ctx = _PrecedenceContext(
            scores=scores,
            is_college_domain=is_college,
            phishing_strong=phishing_strong,
            has_date_hint=bool(_DATE_HINT_RE.search(text)),
            has_task_hint=bool(_TASK_HINT_RE.search(text)),
            has_academic_signal=any(
                c in scores
                for c in (
                    TriageCategory.ASSIGNMENT,
                    TriageCategory.EXAM,
                    TriageCategory.ACADEMIC_INFORMATION,
                    TriageCategory.FACULTY_ANNOUNCEMENT,
                )
            ),
        )
        category, precedence_notes = _apply_precedence(category, ctx)
        if category in scores:
            keywords = matched.get(category, keywords)

        # --- confidence ---
        confidence = self._score_to_confidence(top, second, len(keywords))
        if category in expected and top > 0:
            confidence = min(0.95, confidence + 0.06)
        if precedence_notes:
            confidence = min(0.9, confidence + 0.03)
        if (
            category in OPPORTUNITY_CATEGORIES
            and not sender_in_list
            and not is_college
        ):
            confidence = min(confidence, self.settings.triage_unknown_opportunity_cap)
        if sender_importance == "LOW_TRUST" and CATEGORY_PRIORITY_BAND[category] == ImportanceEstimate.HIGH:
            # A low-trust bulk sender claiming a high-stakes category is
            # suspicious — force it below the review / LLM threshold.
            confidence = min(confidence, self.settings.triage_review_threshold - 0.05)
        if category == TriageCategory.OTHER:
            confidence = min(confidence, 0.5)
        if category == TriageCategory.SPAM and phishing_strong:
            confidence = max(confidence, 0.8)

        importance = self._importance_for(category, sender_importance, is_college)
        subcategory = self._subcategory_for(category, text, has_form_link, is_ics)
        reasoning = self._deterministic_reasoning(category, keywords, precedence_notes, sender_importance)

        return _Assessment(
            category=category,
            subcategory=subcategory,
            importance=importance,
            confidence=round(confidence, 4),
            reasoning=reasoning,
            method=ClassificationMethod.DETERMINISTIC,
            keywords=keywords[:8],
            category_scores={c.value: s for c, s in scores.items()},
            precedence_applied=precedence_notes,
            sender_importance=sender_importance,
            sender_in_important_list=sender_in_list,
            has_form_link=has_form_link,
        )

    def _score_to_confidence(self, top: float, second: float, hit_count: int) -> float:
        """Blend absolute signal strength with the margin over the runner-up.

        A single weak keyword hit is deliberately not enough to be confident —
        that is exactly when Layer 2 (LLM) should take over, or the result
        should be flagged for review.
        """
        if top <= 0:
            return 0.30
        strength = min(top, 6.0) / 6.0
        margin = (top - second) / top
        raw = 0.30 + 0.42 * strength + 0.16 * margin
        if hit_count <= 1 and top < 4.5:
            raw = min(raw, 0.52)
        return max(0.28, min(0.95, raw))

    # -- layer 2: LLM ------------------------------------------------

    def _llm_classify(self, email: NormalizedEmail, det: _Assessment) -> LLMClassification:
        body = (email.body or "").strip()
        if len(body) > 4000:
            body = body[:4000] + "\n...[truncated]"
        user = (
            f"Sender: {email.sender.email}\n"
            f"Sender importance: {det.sender_importance or 'none'}\n"
            f"Subject: {email.subject or '(empty)'}\n\n"
            f"Body:\n{body or '(empty)'}"
        )
        raw = self._llm.complete_json(
            _SYSTEM_PROMPT, user, max_tokens=self.settings.llm_max_tokens
        )
        try:
            return LLMClassification.model_validate(raw)
        except ValidationError as exc:
            raise LLMResponseError(f"LLM returned an invalid classification: {exc}") from exc

    def _merge(self, det: _Assessment, llm: LLMClassification, email: NormalizedEmail) -> _Assessment:
        addr = (email.sender.email or "").lower()
        domain = addr.split("@")[-1] if "@" in addr else ""
        is_college = domain == rules.COLLEGE_DOMAIN or domain.endswith("." + rules.COLLEGE_DOMAIN)

        # The LLM was consulted precisely because the deterministic signals were
        # weak, so only the *hard* safety rules (never SPAM/PROMOTIONAL for a
        # @college.edu sender; SPAM needs strong phishing signals) constrain its
        # answer here. The competitive tie-breakers already had their turn in the
        # deterministic layer.
        ctx = _PrecedenceContext(
            scores={llm.category: 5.0},
            is_college_domain=is_college,
            phishing_strong=det.category == TriageCategory.SPAM and det.confidence >= 0.8,
            has_date_hint=False,
            has_task_hint=False,
            has_academic_signal=True,
        )
        final_category, precedence_notes = _apply_hard_precedence(llm.category, ctx)
        agreement = final_category == det.category

        if agreement:
            confidence = min(0.97, max(det.confidence, llm.confidence) + 0.05)
            conflicting = False
        else:
            confidence = min(0.80, llm.confidence)
            conflicting = True

        importance = self._importance_for(final_category, det.sender_importance, is_college)
        if llm.importance_estimate is not None:
            importance = _RANK_IMPORTANCE[
                max(_IMPORTANCE_RANK[importance], _IMPORTANCE_RANK[llm.importance_estimate])
            ]

        reasoning = (llm.reasoning or "").strip() or f"LLM classified this email as {final_category.value}."
        if precedence_notes:
            reasoning += f" Deterministic precedence applied: {', '.join(precedence_notes)}."

        notes = list(dict.fromkeys(det.precedence_applied + precedence_notes))
        return _Assessment(
            category=final_category,
            subcategory=(llm.subcategory or det.subcategory),
            importance=importance,
            confidence=round(confidence, 4),
            reasoning=reasoning,
            method=ClassificationMethod.LLM,
            keywords=det.keywords,
            category_scores=det.category_scores,
            precedence_applied=notes,
            sender_importance=det.sender_importance,
            sender_in_important_list=det.sender_in_important_list,
            has_form_link=det.has_form_link,
            conflicting_signals=conflicting,
        )

    @staticmethod
    def _as_fallback(det: _Assessment) -> _Assessment:
        det.method = ClassificationMethod.LLM_FALLBACK_DETERMINISTIC
        return det

    # -- shared helpers --------------------------------------------

    def _importance_for(
        self, category: TriageCategory, sender_importance: str | None, is_college: bool
    ) -> ImportanceEstimate:
        band = CATEGORY_PRIORITY_BAND[category]
        floor: ImportanceEstimate | None = None
        if sender_importance == "CRITICAL":
            floor = ImportanceEstimate.HIGH
        elif sender_importance == "HIGH":
            floor = ImportanceEstimate.MEDIUM
        elif is_college:
            floor = ImportanceEstimate.MEDIUM  # User Preferences §6 rule 2
        if floor is None:
            return band
        return _RANK_IMPORTANCE[max(_IMPORTANCE_RANK[band], _IMPORTANCE_RANK[floor])]

    @staticmethod
    def _subcategory_for(
        category: TriageCategory, text: str, has_form_link: bool, is_ics: bool
    ) -> str | None:
        if category == TriageCategory.EVENT and is_ics:
            return "calendar_invite"
        if has_form_link and category in {
            TriageCategory.INTERNSHIP,
            TriageCategory.PLACEMENT,
            TriageCategory.JOB_OPPORTUNITY,
            TriageCategory.FACULTY_ANNOUNCEMENT,
        }:
            return "application_form"
        if category == TriageCategory.EVENT and ("register" in text or has_form_link):
            return "registration"
        if category == TriageCategory.EXAM:
            if "hall ticket" in text or "admit card" in text:
                return "hall_ticket"
            if "result" in text:
                return "results"
            if "time table" in text or "timetable" in text or "schedule" in text:
                return "time_table"
        if category == TriageCategory.EVENT:
            for kw in ("webinar", "workshop", "hackathon", "seminar", "guest lecture"):
                if kw in text:
                    return kw.replace(" ", "_")
        if category == TriageCategory.NEWSLETTER and ("internship" in text or "job" in text):
            return "job_digest"
        return None

    @staticmethod
    def _deterministic_reasoning(
        category: TriageCategory,
        keywords: list[str],
        precedence_notes: list[str],
        sender_importance: str | None,
    ) -> str:
        parts: list[str] = []
        if keywords:
            parts.append(f"Matched {', '.join(keywords[:4])}")
        parts.append(f"classified as {category.value}")
        if precedence_notes:
            parts.append(f"after precedence ({', '.join(precedence_notes)})")
        if sender_importance:
            parts.append(f"sender importance {sender_importance}")
        if not keywords and not precedence_notes:
            return f"No strong keyword signals; classified as {category.value}."
        return "; ".join(parts).capitalize() + "."

    def _needs_human_review(self, a: _Assessment) -> bool:
        if a.confidence < self.settings.triage_review_threshold:
            return True
        if a.category == TriageCategory.OTHER and a.confidence < 0.6:
            return True
        if a.conflicting_signals and a.confidence < 0.7:
            return True
        return False

    def _build_data(self, a: _Assessment, needs_review: bool) -> TriageData:
        signals = TriageSignals(
            keywords=a.keywords,
            sender_in_important_list=a.sender_in_important_list,
            sender_importance=a.sender_importance,
            has_form_link=a.has_form_link,
            classification_method=a.method,
            category_scores=a.category_scores,
            precedence_applied=a.precedence_applied,
            conflicting_signals=a.conflicting_signals,
        )
        return TriageData(
            category=a.category,
            subcategory=a.subcategory,
            importance_estimate=a.importance,
            further_analysis_required=(a.category not in LOW_BAND_CATEGORIES) or needs_review,
            confidence=round(a.confidence, 4),
            reasoning_summary=a.reasoning,
            signals=signals,
        )

    # -- runtime plumbing -----------------------------------------

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


# ---------------------------------------------------------------------------
# Precedence (Classification Rules.md "Precedence") — deterministic, never
# overridden by the LLM.
# ---------------------------------------------------------------------------


@dataclass
class _PrecedenceContext:
    scores: dict[TriageCategory, float]
    is_college_domain: bool
    phishing_strong: bool
    has_date_hint: bool
    has_task_hint: bool
    has_academic_signal: bool


def _best_other_category(
    scores: dict[TriageCategory, float], exclude: set[TriageCategory]
) -> TriageCategory | None:
    candidates = [(c, s) for c, s in scores.items() if c not in exclude and s > 0]
    if not candidates:
        return None
    return max(candidates, key=lambda kv: kv[1])[0]


def _apply_hard_precedence(
    category: TriageCategory, ctx: _PrecedenceContext
) -> tuple[TriageCategory, list[str]]:
    """Safety rules that always apply — even over a confident LLM answer."""
    notes: list[str] = []

    # Never PROMOTIONAL / SPAM for a @college.edu sender
    # (User Preferences §6 rule 2, Classification Rules precedence 5).
    if ctx.is_college_domain and category in rules.FORBIDDEN_FOR_COLLEGE_DOMAIN:
        replacement = _best_other_category(ctx.scores, set(rules.FORBIDDEN_FOR_COLLEGE_DOMAIN))
        category = replacement or (
            TriageCategory.ACADEMIC_INFORMATION if ctx.has_academic_signal else TriageCategory.OTHER
        )
        notes.append("college_domain_not_promotional_or_spam")

    # SPAM only wins with strong phishing signals (precedence 5).
    if category == TriageCategory.SPAM and not ctx.phishing_strong:
        replacement = _best_other_category(ctx.scores, {TriageCategory.SPAM})
        category = replacement or TriageCategory.OTHER
        notes.append("spam_requires_strong_phishing")

    return category, notes


def _apply_precedence(
    category: TriageCategory, ctx: _PrecedenceContext
) -> tuple[TriageCategory, list[str]]:
    """Hard rules + the competitive tie-breakers (deterministic layer only)."""
    category, notes = _apply_hard_precedence(category, ctx)
    top_score = max(ctx.scores.values(), default=0.0)

    # Rule 2: opportunity beats EVENT / NEWSLETTER / ACADEMIC_INFORMATION.
    if category in {
        TriageCategory.EVENT,
        TriageCategory.NEWSLETTER,
        TriageCategory.ACADEMIC_INFORMATION,
    }:
        opp = [
            (c, ctx.scores[c]) for c in OPPORTUNITY_CATEGORIES if ctx.scores.get(c, 0.0) > 0
        ]
        if opp:
            best_opp, best_score = max(opp, key=lambda kv: kv[1])
            # Only flip when the opportunity signal is genuinely competitive with
            # the current pick — a clear subscribed digest that merely *mentions*
            # internships stays NEWSLETTER (Classification Rules example B).
            if best_score >= 1.5 and best_score >= 0.9 * top_score:
                category = best_opp
                notes.append("opportunity_precedence")

    # Rule 3: EXAM / ASSIGNMENT beats FACULTY_ANNOUNCEMENT with a concrete task+date.
    if (
        category == TriageCategory.FACULTY_ANNOUNCEMENT
        and ctx.has_date_hint
        and ctx.has_task_hint
    ):
        exam_s = ctx.scores.get(TriageCategory.EXAM, 0.0)
        asg_s = ctx.scores.get(TriageCategory.ASSIGNMENT, 0.0)
        if max(exam_s, asg_s) > 0:
            category = TriageCategory.EXAM if exam_s >= asg_s else TriageCategory.ASSIGNMENT
            notes.append("exam_assignment_beats_faculty")

    # Rule 4: REPLY_REQUIRED is additive — demote if another category is close.
    if category == TriageCategory.REPLY_REQUIRED:
        reply_s = ctx.scores.get(TriageCategory.REPLY_REQUIRED, 0.0)
        other = _best_other_category(ctx.scores, {TriageCategory.REPLY_REQUIRED})
        if other is not None and ctx.scores[other] >= 0.8 * max(reply_s, 0.01):
            category = other
            notes.append("reply_required_additive_demoted")

    return category, notes
