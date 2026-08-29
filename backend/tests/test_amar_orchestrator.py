"""AMAR Orchestrator tests (Phase 8, STEP 14). Gmail / OAuth / LLM all mocked."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.agents.action_agent import ActionAgent
from app.agents.amar_orchestrator import AMAROrchestrator, to_activity_log
from app.agents.deadline_agent import DeadlineAgent
from app.agents.priority_agent import PriorityAgent
from app.agents.triage_agent import TriageAgent
from app.core.config import Settings
from app.models.agent_output import AgentOutput
from app.models.decision import FinalDecision
from tests.triage_helpers import make_email

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 8, 28, 16, 0, tzinfo=IST)  # Friday


def _orch(**over) -> AMAROrchestrator:
    s = Settings()
    agents = dict(
        triage=TriageAgent(settings=s),
        action=ActionAgent(settings=s),
        deadline=DeadlineAgent(settings=s),
        priority=PriorityAgent(settings=s),
    )
    agents.update(over)
    return AMAROrchestrator(**agents, settings=s)


def run(orch, *, sender="someone@example.com", subject="S", body="B", now=NOW):
    out = orch.process(make_email(sender=sender, subject=subject, body=body), now=now)
    AgentOutput.model_validate(out.to_wire())
    FinalDecision.model_validate(out.data)
    return out


# --- failure-injection stand-ins -------------------------------------------

class _Boom:
    def __init__(self, method: str):
        self._method = method

    def __getattr__(self, name):
        def _raise(*_a, **_k):
            raise RuntimeError(f"{self._method} exploded")
        return _raise


class _BadOutput:
    def detect(self, *_a, **_k):
        return {"not": "an AgentOutput"}


# --- 1-7: normal categories ---------------------------------------------

def test_normal_pipeline_succeeds():
    out = run(_orch(), sender="placement@college.edu", subject="Internship",
              body="Applications are open. Fill the form https://forms.gle/x by Friday.")
    d = out.data
    assert out.status == "ok"
    assert d["final_category"] == "INTERNSHIP"
    assert [t["agent"] for t in d["agent_trace"]] == [
        "Mail Intake Agent", "Triage Agent", "Action Agent", "Deadline Agent", "Priority Agent",
    ]
    assert all(t["status"] in {"ok", "skipped"} for t in d["agent_trace"])


def test_placement_apply_deadline_30_min_is_critical():
    out = run(_orch(), sender="placement@college.edu", subject="Placement drive - apply now",
              body="Register and apply: fill the form https://forms.gle/x before 4:30 PM today.")
    d = out.data
    assert d["final_category"] == "PLACEMENT"
    assert d["priority_level"] == "CRITICAL"
    assert d["proximity_bucket"] == "WITHIN_1H"
    assert d["routing"]["notify"] is True and d["routing"]["monitor"] is True
    assert d["routing"]["folder_label"] == "AMAR/Opportunities"


def test_internship_with_action_and_deadline():
    out = run(_orch(), sender="tpo@college.edu", subject="Summer Internship 2026",
              body="Apply via the form and upload your resume by 5 September 2026.")
    d = out.data
    assert d["final_category"] == "INTERNSHIP"
    assert d["action_required"] is True
    assert d["deadline"] and d["deadline"].startswith("2026-09-05")


def test_assignment_submission():
    out = run(_orch(), sender="rahul.faculty@college.edu", subject="Assignment 3",
              body="Please submit the assignment through the portal by tomorrow 5 PM.")
    d = out.data
    assert d["final_category"] == "ASSIGNMENT"
    assert d["primary_action_type"] == "COMPLETE_ASSIGNMENT"
    assert d["routing"]["folder_label"] == "AMAR/Academics"


def test_faculty_announcement_no_action_not_critical():
    out = run(_orch(), sender="hod.cse@college.edu", subject="Notice",
              body="All students are informed the library reopens Monday. For your information.")
    d = out.data
    assert d["final_category"] == "FACULTY_ANNOUNCEMENT"
    assert d["priority_level"] != "CRITICAL"
    assert d["action_required"] is False


def test_promotional_email_low_no_notify():
    out = run(_orch(), sender="offers@shopdeals.com", subject="FLAT 50% OFF",
              body="Limited time sale, buy now and grab the discount!")
    d = out.data
    assert d["final_category"] == "PROMOTIONAL"
    assert d["priority_level"] == "LOW"
    assert d["routing"]["notify"] is False and d["routing"]["monitor"] is False
    # Action + Deadline are gated off for a confident low-band category
    skipped = {t["agent"] for t in d["agent_trace"] if t["status"] == "skipped"}
    assert skipped == {"Action Agent", "Deadline Agent"}


def test_newsletter_low():
    out = run(_orch(), sender="news@digest.com", subject="Weekly digest",
              body="In this issue: 10 stories. You are receiving this because you subscribed.")
    assert out.data["priority_level"] == "LOW"
    assert out.data["routing"]["folder_label"] == "AMAR/Newsletters"


# --- 8-10: multiple / ambiguous ------------------------------------

def test_multiple_actions_preserved():
    out = run(_orch(), sender="tpo@college.edu", subject="Shortlisted",
              body="Register for the drive and upload your resume by Friday.")
    types = {a["action_type"] for a in out.data["actions"]}
    assert len(types) >= 2


def test_multiple_deadlines_primary_chosen():
    out = run(_orch(), sender="tpo@college.edu", subject="Placement",
              body="Register by 1 September 2026 and submit your resume by 3 September 2026.")
    d = out.data
    assert d["deadline"] is not None  # the primary one
    assert d["action_required"] is True


def test_ambiguous_deadline_flags_review_and_monitors():
    out = run(_orch(), sender="placement@college.edu", subject="Internship",
              body="Apply via the form soon — the deadline is next week sometime.")
    d = out.data
    assert d["deadline_ambiguous"] is True or d["deadline"] is None
    assert d["needs_human_review"] is True
    assert d["routing"]["monitor"] is True
    assert d["review_reasons"]


# --- 11-14: conflicts (STEP 5) ---------------------------------

def test_low_confidence_triage_high_confidence_action(monkeypatch):
    # a promo-looking email that actually asks the user to apply with a deadline
    out = run(
        _orch(), sender="opportunities@random-startup.io",
        subject="Exclusive offer for you",
        body="Congratulations! Fill this application form https://forms.gle/x and "
        "submit your resume before 5 PM today to claim your internship spot.",
    )
    d = out.data
    # must NOT be silently suppressed even if Triage leaned PROMOTIONAL
    assert d["needs_human_review"] is True
    assert d["action_required"] is True
    assert any("confidence" in r or "conflict" in r for r in d["review_reasons"])


def test_conflicting_category_and_action_signal():
    out = run(
        _orch(), sender="deals@shopping.com", subject="Your prize is waiting",
        body="URGENT: reply to this email and fill the form https://forms.gle/x within 1 hour "
        "to confirm and submit your details.",
    )
    d = out.data
    if d["final_category"] in {"PROMOTIONAL", "SPAM", "NEWSLETTER", "SOCIAL"} and d["action_required"]:
        assert d["needs_human_review"] is True
        assert any("category" in r.lower() or "confidence" in r.lower() for r in d["review_reasons"])


def test_action_required_but_no_deadline():
    out = run(_orch(), sender="prof@college.edu", subject="Feedback",
              body="Please fill the form at https://forms.gle/x to share your feedback.")
    d = out.data
    assert d["action_required"] is True
    assert d["deadline"] is None
    assert d["routing"]["monitor"] is True


def test_deadline_present_but_no_action_preserves_both():
    out = run(_orch(), sender="registrar@college.edu", subject="Info",
              body="The registration window closes on 5 September 2026. "
              "No action is required from you — this is for your information.")
    d = out.data
    assert d["action_required"] is False
    assert d["deadline"] is not None
    assert d["routing"]["monitor"] is True
    assert any(c["rule"] == "deadline_without_action_preserved" for c in d["conflicts_resolved"])


# --- 15-16: overrides ------------------------------------------

def test_important_sender_override():
    out = run(_orch(), sender="placement@college.edu", subject="FYI",
              body="Sharing last year's placement statistics for your reference.")
    d = out.data
    # placement@college.edu is a CRITICAL sender -> floored, but not fabricated CRITICAL
    assert d["priority_level"] in {"HIGH", "URGENT", "CRITICAL"}


def test_user_preference_override_internship_min_urgent():
    out = run(_orch(), sender="unknown@gmail.com", subject="Internship opening",
              body="We have an internship opportunity. Apply via the form https://forms.gle/x.")
    assert out.data["priority_level"] in {"URGENT", "CRITICAL"}


# --- 17-22: failures ---------------------------------------

def test_triage_failure_recovers():
    out = run(_orch(triage=_Boom("triage")))
    d = out.data
    assert out.status == "partial"
    assert d["final_category"] == "OTHER"
    assert d["needs_human_review"] is True
    tri = next(t for t in d["agent_trace"] if t["agent"] == "Triage Agent")
    assert tri["status"] == "error" and "agent_exception" in tri["error_codes"]


def test_action_failure_recovers():
    out = run(_orch(action=_Boom("action")),
              subject="Assignment", body="Submit the assignment by Friday.")
    d = out.data
    assert out.status == "partial"
    assert d["needs_human_review"] is True
    # deadline + priority still ran
    assert [t["status"] for t in d["agent_trace"] if t["agent"] == "Deadline Agent"][0] != "error"


def test_deadline_failure_recovers():
    out = run(_orch(deadline=_Boom("deadline")),
              sender="placement@college.edu", subject="Internship",
              body="Apply via the form by Friday.")
    d = out.data
    assert out.status == "partial"
    assert d["deadline"] is None
    ddl = next(t for t in d["agent_trace"] if t["agent"] == "Deadline Agent")
    assert ddl["status"] == "error"
    # priority still produced a level
    assert d["priority_level"] in {"CRITICAL", "URGENT", "HIGH", "MEDIUM", "LOW"}


def test_priority_failure_uses_conservative_fallback():
    out = run(_orch(priority=_Boom("priority")),
              sender="placement@college.edu", subject="Internship",
              body="Apply via the form and upload your resume by 5 September 2026.")
    d = out.data
    assert out.status == "partial"
    assert d["needs_human_review"] is True
    pri = next(t for t in d["agent_trace"] if t["agent"] == "Priority Agent")
    assert pri["fallback_used"] is True
    assert d["priority_level"] == "HIGH"  # actionable + high-value -> conservative HIGH


def test_invalid_agent_output_recovered():
    out = run(_orch(action=_BadOutput()),
              subject="Assignment", body="Submit the assignment by Friday.")
    assert out.status == "partial"
    assert out.data["needs_human_review"] is True


def test_pipeline_never_crashes_even_if_two_agents_fail():
    out = run(_orch(action=_Boom("a"), priority=_Boom("p")))
    assert isinstance(out, AgentOutput)
    FinalDecision.model_validate(out.data)


# --- 23-28: review / routing / trace ----------------------

def test_needs_human_review_has_reasons_when_true():
    out = run(_orch(triage=_Boom("t")))
    assert out.data["needs_human_review"] is True
    assert out.data["review_reasons"]


def test_clearly_low_email_not_flagged_despite_upstream_uncertainty():
    out = run(_orch(), sender="promo@newsletter.example", subject="Weekly deals",
              body="Big savings this week. Shop the sale.")
    d = out.data
    assert d["priority_level"] == "LOW"
    assert d["needs_human_review"] is False


def test_routing_object_shape():
    out = run(_orch(), subject="Hi", body="Hello.")
    r = out.data["routing"]
    assert set(r) == {"store", "notify", "monitor", "folder_label"}
    assert r["store"] is True  # everything is persisted


@pytest.mark.parametrize(
    "category_body,expected_label",
    [
        ("Apply for the internship via the form.", "AMAR/Opportunities"),
        ("Submit the assignment by Friday.", "AMAR/Academics"),
        ("Buy now, 50% off sale!", "AMAR/Promotions"),
    ],
)
def test_folder_label_from_category(category_body, expected_label):
    out = run(_orch(), sender="x@y.com", subject="S", body=category_body)
    assert out.data["routing"]["folder_label"] == expected_label


def test_agent_trace_order_and_fields():
    out = run(_orch(), sender="placement@college.edu", subject="Internship",
              body="Apply via the form https://forms.gle/x by Friday.")
    trace = out.data["agent_trace"]
    assert [t["agent"] for t in trace][0] == "Mail Intake Agent"
    for t in trace:
        assert set(t) >= {"agent", "status"}
        assert t["status"] in {"ok", "partial", "error", "skipped"}


def test_agent_trace_records_error():
    out = run(_orch(deadline=_Boom("d")), subject="Assignment",
              body="Submit the assignment by Friday.")
    ddl = next(t for t in out.data["agent_trace"] if t["agent"] == "Deadline Agent")
    assert ddl["status"] == "error"
    assert ddl["error_codes"] == ["agent_exception"]


def test_upstream_outputs_not_mutated():
    orch = _orch()
    email = make_email(sender="placement@college.edu", subject="Internship",
                       body="Apply via the form by Friday.")
    # run twice; second run must produce the same decision (no shared mutable state)
    d1 = orch.process(email, now=NOW).data
    d2 = orch.process(email, now=NOW).data
    d1.pop("agent_trace"); d2.pop("agent_trace")  # durations vary
    for x in (d1, d2):
        x["conflicts_resolved"] = sorted(c["rule"] for c in x["conflicts_resolved"])
    assert d1 == d2


# --- 29-30: schema + activity log --------------------------

def test_final_decision_schema_valid():
    out = run(_orch(), sender="placement@college.edu", subject="Internship",
              body="Apply via the form https://forms.gle/x by 5 September 2026, 6 PM.")
    wire = out.to_wire()
    AgentOutput.model_validate(wire)
    fd = FinalDecision.model_validate(wire["data"])
    assert fd.email_id == out.email_id
    assert fd.priority_score == out.data["priority_score"]
    assert wire["agent"] == "AMAR Orchestrator"


def test_activity_log_renders():
    out = run(_orch(), sender="placement@college.edu", subject="Internship",
              body="Apply via the form by Friday.")
    log = to_activity_log(out)
    assert "Agent: AMAR Orchestrator" in log
    assert "Event: Final Decision" in log
    assert "Agent: Triage Agent" in log
    # no email body leaked into the log
    assert "Apply via the form" not in log
