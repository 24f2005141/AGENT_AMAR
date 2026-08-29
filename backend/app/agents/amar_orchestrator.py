"""AMAR Orchestrator — the deterministic coordinator.

Implements ``01-Agents/AMAR Orchestrator.md`` + ``02-Workflows/New Email
Processing.md``.

It **coordinates**; it does not re-do the agents' work:
  * runs Triage → (gated) Action + Deadline → Priority, in order
  * validates each output, records failures, continues where possible
  * resolves the documented cross-agent conflicts (deterministically)
  * produces one :class:`~app.models.decision.FinalDecision`
  * decides the routing flags (``store`` / ``notify`` / ``monitor`` /
    ``folder_label``) — it decides *what should happen*, it does not act
  * keeps a structured ``agent_trace``
  * computes the final ``needs_human_review`` with explicit reasons

No LLM here. No notification sending, no monitoring, no Gmail writes, no DB.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from app.agents.action_agent import ActionAgent
from app.agents.deadline_agent import DeadlineAgent
from app.agents.priority_agent import PriorityAgent
from app.agents.triage_agent import TriageAgent
from app.core.config import Settings, get_settings
from app.models.agent_output import AgentError, AgentOutput, AgentStatus
from app.models.decision import (
    ConflictResolution,
    DecisionAction,
    DecisionDeadline,
    FinalDecision,
    RoutingDecision,
    TraceEntry,
)
from app.models.email import NormalizedEmail
from app.models.priority import PriorityLevel, ProximityBucket
from app.services.llm_service import build_llm_client
from app.services.priority_context import get_priority_context
from app.utils import priority_scoring as ps

AGENT_NAME = "AMAR Orchestrator"
AGENT_VERSION = "0.1.0"

_LOW_BAND = {"PROMOTIONAL", "NEWSLETTER", "SPAM", "SOCIAL"}
_NEAR_BUCKETS = {"OVERDUE", "WITHIN_1H", "WITHIN_24H"}

# folder_label per final category (Agent Orchestrator.md example: "AMAR/Opportunities").
_FOLDER_LABELS: dict[str, str] = {
    "INTERNSHIP": "AMAR/Opportunities",
    "PLACEMENT": "AMAR/Opportunities",
    "JOB_OPPORTUNITY": "AMAR/Opportunities",
    "ASSIGNMENT": "AMAR/Academics",
    "EXAM": "AMAR/Academics",
    "FACULTY_ANNOUNCEMENT": "AMAR/Academics",
    "ACADEMIC_INFORMATION": "AMAR/Academics",
    "REPLY_REQUIRED": "AMAR/Replies",
    "PROJECT_UPDATE": "AMAR/Projects",
    "EVENT": "AMAR/Events",
    "PROMOTIONAL": "AMAR/Promotions",
    "NEWSLETTER": "AMAR/Newsletters",
    "SOCIAL": "AMAR/Social",
    "SPAM": "AMAR/Spam",
    "OTHER": "AMAR/Other",
}


@dataclass
class _Step:
    output: AgentOutput
    trace: TraceEntry
    ran: bool = True


@dataclass
class _Conflicts:
    reasons: list[str] = field(default_factory=list)
    entries: list[ConflictResolution] = field(default_factory=list)
    force_notify: bool | None = None
    force_monitor: bool | None = None
    review: bool = False

    def add(self, rule: str, detail: str, *, reason: str | None = None) -> None:
        self.entries.append(ConflictResolution(rule=rule, detail=detail))
        if reason:
            self.reasons.append(reason)


class AMAROrchestrator:
    """Deterministic coordination of Triage / Action / Deadline / Priority."""

    def __init__(
        self,
        triage: TriageAgent,
        action: ActionAgent,
        deadline: DeadlineAgent,
        priority: PriorityAgent,
        *,
        settings: Settings | None = None,
    ) -> None:
        self.triage = triage
        self.action = action
        self.deadline = deadline
        self.priority = priority
        self.settings = settings or get_settings()
        self._tz = self._resolve_tz(self.settings.default_timezone)

    # -- public API -----------------------------------------------------

    def process(
        self,
        email: NormalizedEmail,
        intake_output: AgentOutput | None = None,
        *,
        now: datetime | None = None,
    ) -> AgentOutput:
        started_at = self._now()
        run_id = self._run_id()
        errors: list[AgentError] = []
        trace: list[TraceEntry] = [self._intake_trace(intake_output)]

        # --- Triage ---
        tri = self._run(lambda: self.triage.classify(email), "Triage Agent", email, errors)
        trace.append(tri.trace)
        category = self._get(tri.output, "category", "OTHER")
        further = bool(self._get(tri.output, "further_analysis_required", True))
        deep = further or category not in _LOW_BAND or tri.output.status == "error"

        # --- Action + Deadline (gated) ---
        if deep:
            act = self._run(
                lambda: self.action.detect(email, tri.output), "Action Agent", email, errors
            )
            ddl = self._run(
                lambda: self.deadline.analyze(email, tri.output, act.output),
                "Deadline Agent", email, errors,
            )
        else:
            act = self._skipped("Action Agent", email, self._empty_action())
            ddl = self._skipped("Deadline Agent", email, self._empty_deadline())
        trace.append(act.trace)
        trace.append(ddl.trace)

        # --- Priority (always) ---
        pri = self._run(
            lambda: self.priority.score(email, tri.output, act.output, ddl.output, now=now),
            "Priority Agent", email, errors,
        )
        if pri.output.status == "error":
            pri = _Step(
                output=self._fallback_priority(email, category, act.output, ddl.output),
                trace=TraceEntry(agent="Priority Agent", status="partial",
                                 method="deterministic_fallback", fallback_used=True,
                                 error_codes=["agent_exception"]),
            )
        trace.append(pri.trace)

        # --- merge + resolve conflicts ---
        decision = self._build_decision(
            email, run_id, category, tri.output, act.output, ddl.output, pri.output, trace
        )

        status = AgentStatus.PARTIAL if errors or any(
            t.status in ("error", "partial") for t in trace
        ) else AgentStatus.OK
        confidence = self._confidence(tri.output, act.output, ddl.output, pri.output)

        return AgentOutput(
            agent=AGENT_NAME,
            agent_version=AGENT_VERSION,
            email_id=email.email_id,
            run_id=run_id,
            status=status,
            confidence=round(confidence, 4),
            needs_human_review=decision.needs_human_review,
            reasoning_summary=self._summary(decision),
            data=decision.model_dump(),
            errors=errors,
            started_at=started_at,
            finished_at=self._now(),
        )

    # -- decision assembly ----------------------------------------

    def _build_decision(
        self,
        email: NormalizedEmail,
        run_id: str,
        category: str,
        tri: AgentOutput,
        act: AgentOutput,
        ddl: AgentOutput,
        pri: AgentOutput,
        trace: list[TraceEntry],
    ) -> FinalDecision:
        a, d, p = act.data, ddl.data, pri.data

        actions = [
            DecisionAction(
                action_id=x.get("action_id", f"act_{i + 1:03d}"),
                action_type=x.get("action_type", "OTHER"),
                action_description=x.get("action_description"),
                blocking=bool(x.get("blocking")),
                confidence=float(x.get("confidence", 0.0)),
                target_link=x.get("target_link"),
                raw_deadline_hint=x.get("raw_deadline_hint"),
            )
            for i, x in enumerate(a.get("actions", []))
        ]
        deadlines = [
            DecisionDeadline(
                deadline_id=x.get("deadline_id", f"dl_{i + 1:03d}"),
                raw_deadline_text=x.get("raw_deadline_text"),
                normalized_deadline=x.get("normalized_deadline"),
                timezone=x.get("timezone", "UTC"),
                date_only=bool(x.get("date_only")),
                ambiguity_flag=bool(x.get("ambiguity_flag")),
                ambiguity_reason=x.get("ambiguity_reason"),
                is_past=bool(x.get("is_past")),
                confidence=float(x.get("confidence", 0.0)),
                action_context=x.get("action_context"),
                related_action_id=x.get("related_action_id"),
            )
            for i, x in enumerate(d.get("deadlines", []))
        ]

        conflicts = self._resolve_conflicts(category, tri, act, ddl, pri, trace)

        notify = bool(p.get("notify", False))
        monitor = bool(p.get("monitor", False))
        if conflicts.force_notify is not None:
            notify = conflicts.force_notify
        if conflicts.force_monitor is not None:
            monitor = conflicts.force_monitor

        review = self._final_review(category, tri, act, ddl, pri, conflicts)

        return FinalDecision(
            email_id=email.email_id,
            thread_id=email.thread_id,
            source=email.source,
            final_category=category,
            category_confidence=(None if tri.status == "error"
                                 else float(tri.data.get("confidence", 0.0))),
            action_required=bool(a.get("action_required", False)),
            primary_action_type=a.get("action_type"),
            actions=actions,
            deadline=d.get("normalized_deadline"),
            deadline_ambiguous=bool(d.get("ambiguity_flag", False)),
            deadline_is_past=bool(d.get("is_past", False)),
            deadlines=deadlines,
            proximity_bucket=ProximityBucket(p.get("proximity_bucket", "NONE")),
            priority_level=PriorityLevel(p.get("priority_level", "LOW")),
            priority_score=int(p.get("priority_score", 0)),
            routing=RoutingDecision(
                store=True,  # New Email Processing §9 — everything is persisted
                notify=notify,
                monitor=monitor,
                folder_label=_FOLDER_LABELS.get(category, "AMAR/Other"),
            ),
            needs_human_review=review,
            review_reasons=list(dict.fromkeys(conflicts.reasons)),
            conflicts_resolved=conflicts.entries,
            agent_trace=trace,
        )

    # -- conflict resolution (AMAR Orchestrator.md) --------------

    def _resolve_conflicts(
        self,
        category: str,
        tri: AgentOutput,
        act: AgentOutput,
        ddl: AgentOutput,
        pri: AgentOutput,
        trace: list[TraceEntry],
    ) -> _Conflicts:
        c = _Conflicts(review=bool(pri.needs_human_review))
        if pri.needs_human_review:
            c.reasons.append(f"Priority Agent: {pri.reasoning_summary[:140]}")

        ar = bool(act.data.get("action_required"))
        action_conf = float(act.data.get("confidence", 0.0))
        triage_conf = 0.0 if tri.status == "error" else float(tri.data.get("confidence", 0.0))
        dl_present = bool(ddl.data.get("deadline_detected"))
        dl_ambiguous = bool(ddl.data.get("ambiguity_flag"))
        dl_norm = ddl.data.get("normalized_deadline")
        bucket = pri.data.get("proximity_bucket", "NONE")
        plevel = pri.data.get("priority_level", "LOW")
        near = bucket in _NEAR_BUCKETS
        low_band = category in _LOW_BAND

        confs = [triage_conf, action_conf,
                 float(ddl.data.get("confidence", 1.0)), float(pri.data.get("confidence", 1.0))]

        # doc rule 3 — low confidence: flag, don't drop
        if min(confs) < 0.4:
            c.add("low_confidence_flag_dont_drop", "minimum agent confidence < 0.4",
                  reason="very low combined agent confidence")
            c.review = True

        # CASE 2 — low-confidence Triage + confident action / time-sensitive content
        if triage_conf and triage_conf < 0.55 and (ar or near or dl_present):
            c.add(
                "safety_bias_low_triage_confidence",
                f"triage confidence {triage_conf:.2f} but action_required={ar}, proximity={bucket}",
                reason="low-confidence classification with actionable / time-sensitive content",
            )
            c.review = True
            if ar or near:
                c.force_notify = True
                c.force_monitor = True

        # CASE 2 variant — low-priority category vs a confident actionable deadline
        if low_band and ar and action_conf >= 0.7 and near:
            c.add(
                "category_vs_action_conflict",
                f"{category} but action_required (conf {action_conf:.2f}) with proximity {bucket}",
                reason="low-priority category conflicts with a confident actionable deadline",
            )
            c.review = True
            c.force_notify = True
            c.force_monitor = True

        # CASE 4 — ambiguous deadline on an actionable / high-priority email
        if dl_ambiguous and (ar or ps.level_at_least(PriorityLevel(plevel), PriorityLevel.HIGH)):
            c.add(
                "ambiguous_deadline_preserved",
                ddl.data.get("ambiguity_reason") or "deadline flagged ambiguous",
                reason="ambiguous deadline on an actionable / high-priority email",
            )
            c.review = True
            c.force_monitor = True

        # CASE 3 — deadline present but no action: preserve both
        if dl_present and not ar:
            c.add("deadline_without_action_preserved",
                  "deadline detected but no user action — both kept, monitoring on")
            c.force_monitor = True
            if near and dl_norm:
                c.reasons.append("a near-term deadline was detected but no action was — please check")
                c.review = True

        # doc rule 1 — deterministic beats guess
        if dl_present and dl_norm and not dl_ambiguous:
            c.add("deterministic_deadline_authoritative",
                  f"concrete deadline {dl_norm} used as-is")

        # partial / error agents
        for out, nm in ((tri, "Triage Agent"), (act, "Action Agent"),
                        (ddl, "Deadline Agent"), (pri, "Priority Agent")):
            if out.status in ("partial", "error"):
                c.reasons.append(f"{nm} returned status '{out.status}'")
                c.review = True

        # fallback recovery on a consequential email
        if any(t.fallback_used for t in trace) and category in ps.HIGH_VALUE_CATEGORIES and (ar or near):
            c.add("fallback_output_recovered",
                  "an agent fell back to deterministic logic on a high-value actionable email",
                  reason="agent fallback used on a high-value actionable email")
            c.review = True

        return c

    def _final_review(
        self,
        category: str,
        tri: AgentOutput,
        act: AgentOutput,
        ddl: AgentOutput,
        pri: AgentOutput,
        c: _Conflicts,
    ) -> bool:
        if c.review:
            return True
        # propagate an upstream review flag only when it is consequential
        flagged = [nm for out, nm in (
            (tri, "Triage Agent"), (act, "Action Agent"), (ddl, "Deadline Agent")
        ) if out.needs_human_review]
        if flagged:
            ar = bool(act.data.get("action_required"))
            dl = bool(ddl.data.get("deadline_detected"))
            level = pri.data.get("priority_level", "LOW")
            if ar or dl or ps.level_at_least(PriorityLevel(level), PriorityLevel.MEDIUM):
                c.reasons.append(
                    f"{', '.join(flagged)} flagged review on a "
                    f"{'actionable' if ar else 'notable'} email"
                )
                return True
        return False

    # -- agent execution helpers -------------------------------

    def _run(self, fn, name: str, email: NormalizedEmail, errors: list[AgentError]) -> _Step:
        try:
            out = fn()
            if not isinstance(out, AgentOutput) or not isinstance(out.data, dict):
                raise TypeError(f"{name} did not return a valid AgentOutput")
            return _Step(output=out, trace=self._trace_of(name, out))
        except Exception as exc:  # noqa: BLE001 — the orchestrator must not crash
            errors.append(AgentError(code=f"{_slug(name)}_failed", message=str(exc)[:200]))
            return _Step(
                output=self._error_output(name, email, str(exc)),
                trace=TraceEntry(agent=name, status="error", confidence=0.0,
                                 error_codes=["agent_exception"]),
                ran=True,
            )

    def _skipped(self, name: str, email: NormalizedEmail, data: dict) -> _Step:
        now = self._now()
        out = AgentOutput(
            agent=name, agent_version="0.0.0", email_id=email.email_id,
            run_id=self._run_id(), status=AgentStatus.OK, confidence=1.0,
            needs_human_review=False,
            reasoning_summary="Skipped — low-band category, deep analysis not required.",
            data=data, errors=[], started_at=now, finished_at=now,
        )
        return _Step(output=out, trace=TraceEntry(agent=name, status="skipped", confidence=1.0), ran=False)

    @staticmethod
    def _trace_of(name: str, out: AgentOutput) -> TraceEntry:
        data = out.data if isinstance(out.data, dict) else {}
        method = None
        sig = data.get("signals")
        if isinstance(sig, dict):
            method = sig.get("classification_method")
        method = method or data.get("detection_method") or data.get("scoring_method")
        if method is not None and not isinstance(method, str):
            method = getattr(method, "value", str(method))
        dur = None
        if out.started_at and out.finished_at:
            dur = max(0, int((out.finished_at - out.started_at).total_seconds() * 1000))
        return TraceEntry(
            agent=name,
            status=str(out.status),
            confidence=round(out.confidence, 4),
            method=str(method) if method else None,
            fallback_used=bool(method and "fallback" in str(method)),
            duration_ms=dur,
            error_codes=[e.code for e in out.errors],
        )

    def _intake_trace(self, intake_output: AgentOutput | None) -> TraceEntry:
        if intake_output is not None:
            return self._trace_of("Mail Intake Agent", intake_output)
        return TraceEntry(agent="Mail Intake Agent", status="ok", confidence=1.0,
                          method="deterministic")

    def _error_output(self, name: str, email: NormalizedEmail, msg: str) -> AgentOutput:
        now = self._now()
        data = {
            "Triage Agent": self._empty_triage(),
            "Action Agent": self._empty_action(),
            "Deadline Agent": self._empty_deadline(),
            "Priority Agent": self._empty_priority(),
        }.get(name, {})
        return AgentOutput(
            agent=name, agent_version="0.0.0", email_id=email.email_id,
            run_id=self._run_id(), status=AgentStatus.ERROR, confidence=0.0,
            needs_human_review=True, reasoning_summary=f"{name} failed: {msg}"[:300],
            data=data, errors=[AgentError(code="agent_exception", message=msg[:200])],
            started_at=now, finished_at=now,
        )

    def _fallback_priority(
        self, email: NormalizedEmail, category: str, act: AgentOutput, ddl: AgentOutput
    ) -> AgentOutput:
        now = self._now()
        actionable = bool(act.data.get("action_required")) or bool(ddl.data.get("deadline_detected"))
        high_value = category in ps.HIGH_VALUE_CATEGORIES
        level = PriorityLevel.HIGH if (actionable or high_value) else PriorityLevel.LOW
        score = 55 if level == PriorityLevel.HIGH else 0
        data = self._empty_priority()
        data.update(
            priority_level=level.value, priority_score=score,
            notify=level == PriorityLevel.HIGH, monitor=actionable,
            reasoning_summary="Priority Agent failed — conservative fallback.",
        )
        return AgentOutput(
            agent="Priority Agent", agent_version="0.0.0", email_id=email.email_id,
            run_id=self._run_id(), status=AgentStatus.PARTIAL, confidence=0.3,
            needs_human_review=True,
            reasoning_summary="Priority Agent failed — conservative fallback applied.",
            data=data, errors=[AgentError(code="priority_fallback", message="scored heuristically")],
            started_at=now, finished_at=now,
        )

    # -- empty payloads ---------------------------------------

    @staticmethod
    def _empty_triage() -> dict:
        return {"category": "OTHER", "confidence": 0.0, "further_analysis_required": True,
                "signals": {"classification_method": "deterministic"}}

    @staticmethod
    def _empty_action() -> dict:
        return {"action_required": False, "actions": [], "action_type": None,
                "action_description": None, "confidence": 1.0, "detection_method": "deterministic"}

    @staticmethod
    def _empty_deadline() -> dict:
        return {"deadline_detected": False, "normalized_deadline": None, "timezone": "UTC",
                "ambiguity_flag": False, "ambiguity_reason": None, "is_past": False,
                "monitoring_required": False, "confidence": 1.0,
                "reference_time_used": "", "deadlines": [], "event_dates": [],
                "detection_method": "deterministic"}

    @staticmethod
    def _empty_priority() -> dict:
        return {"priority_level": "LOW", "priority_score": 0, "proximity_bucket": "NONE",
                "time_remaining_seconds": None, "deadline_is_past": False,
                "score_breakdown": [], "notify": False, "monitor": False,
                "reasoning_summary": "", "confidence": 0.0, "factors": {},
                "overrides_applied": [], "scoring_method": "deterministic",
                "reference_time_used": ""}

    # -- misc -------------------------------------------------

    @staticmethod
    def _get(out: AgentOutput, key: str, default):
        if isinstance(out.data, dict):
            return out.data.get(key, default)
        return default

    @staticmethod
    def _confidence(*outs: AgentOutput) -> float:
        vals = [o.confidence for o in outs if o is not None]
        return max(0.0, min(1.0, min(vals))) if vals else 0.0

    @staticmethod
    def _summary(d: FinalDecision) -> str:
        r = d.routing
        return (
            f"{d.final_category} → {d.priority_level} (score {d.priority_score}); "
            f"route: store={r.store} notify={r.notify} monitor={r.monitor} "
            f"label={r.folder_label}; {len(d.conflicts_resolved)} conflict(s) resolved; "
            f"review={d.needs_human_review}."
        )

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


def _slug(name: str) -> str:
    return name.lower().replace(" ", "_")


def build_default_orchestrator(settings: Settings | None = None) -> AMAROrchestrator:
    """Construct a fully-wired orchestrator outside FastAPI DI.

    Mirrors ``app.api.deps.get_amar_orchestrator`` — same agents, same LLM
    client, same priority context — for background jobs (scheduler, Gmail sync).
    """
    settings = settings or get_settings()
    llm = build_llm_client(settings)
    context = get_priority_context()
    return AMAROrchestrator(
        TriageAgent(settings=settings, llm_client=llm),
        ActionAgent(settings=settings, llm_client=llm),
        DeadlineAgent(settings=settings, llm_client=llm),
        PriorityAgent(settings=settings, llm_client=llm, context=context),
        settings=settings,
    )


# ---------------------------------------------------------------------------
# Lightweight human-readable activity log (STEP 11) — dev/debug only.
# The structured agent_trace is the primary runtime artifact.
# ---------------------------------------------------------------------------

def to_activity_log(decision_envelope: AgentOutput) -> str:
    """Render the ``05-Logs/Agent Activity Log.md`` text-block format for one pass."""
    d = decision_envelope.data
    run_id = decision_envelope.run_id
    email_id = decision_envelope.email_id
    ts = decision_envelope.finished_at.isoformat()

    lines: list[str] = []

    def block(agent: str, event: str, details: dict, result: str, notes: str = "") -> None:
        lines.append("---")
        lines.append(f"Timestamp: {ts}")
        lines.append(f"Run ID: {run_id}")
        lines.append(f"Agent: {agent}")
        lines.append(f"Event: {event}")
        lines.append("Details:")
        for k, v in details.items():
            if v is not None and v != "":
                lines.append(f"  {k}: {v}")
        lines.append(f"Result: {result}")
        if notes:
            lines.append(f"Notes: {notes}")

    for t in d.get("agent_trace", []):
        block(
            t["agent"],
            {"Triage Agent": "Email Classified", "Action Agent": "Action Analysis",
             "Deadline Agent": "Deadline Extracted", "Priority Agent": "Priority Scored",
             "Mail Intake Agent": "Email Normalised"}.get(t["agent"], "Agent Ran"),
            {"Status": t["status"], "Confidence": t.get("confidence"),
             "Method": t.get("method"), "Fallback": t.get("fallback_used")},
            t["status"].upper(),
            notes="; ".join(t.get("error_codes", [])),
        )

    block(
        "AMAR Orchestrator",
        "Final Decision",
        {
            "final_category": d["final_category"],
            "priority_level": d["priority_level"],
            "priority_score": d["priority_score"],
            "routing": f"store={d['routing']['store']}, notify={d['routing']['notify']}, "
                       f"monitor={d['routing']['monitor']}, label={d['routing']['folder_label']}",
            "conflicts_resolved": len(d.get("conflicts_resolved", [])) or "none",
        },
        f"Routed → {d['routing']['folder_label']}"
        + (" (needs human review)" if d["needs_human_review"] else ""),
        notes="; ".join(d.get("review_reasons", [])),
    )
    lines.append("---")
    return "\n".join(lines)
