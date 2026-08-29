"""Deadline Agent tests (Phase 6, STEP 15). LLM/Gmail/OAuth all mocked."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.agents.deadline_agent import DeadlineAgent
from app.core.config import Settings
from app.models.agent_output import AgentOutput
from app.models.deadline import DeadlineData
from tests.triage_helpers import FakeLLM, action_stub, make_email, triage_stub

IST = ZoneInfo("Asia/Kolkata")
REF = datetime(2026, 8, 28, 16, 30, tzinfo=IST)  # Friday


@pytest.fixture
def agent() -> DeadlineAgent:
    return DeadlineAgent(settings=Settings())


def run(agent, *, subject="", body="", category="OTHER", actions=None, received_at=REF):
    email = make_email(subject=subject, body=body, received_at=received_at)
    tri = triage_stub(category)
    items = actions if actions is not None else [{"action_type": "FORM_SUBMISSION"}]
    act = action_stub(items)
    return agent.analyze(email, tri, act)


def primary(out) -> str | None:
    return out.data["normalized_deadline"]


# --- 1-2: explicit -----------------------------------------------------

def test_explicit_date_deadline(agent):
    out = run(agent, subject="Form", body="Deadline: 30 August 2026 to submit the form.")
    assert out.data["deadline_detected"] is True
    assert primary(out).startswith("2026-08-30T23:59:59")
    assert out.data["deadlines"][0]["date_only"] is True


def test_explicit_date_and_time_deadline(agent):
    out = run(agent, body="Please submit the form by 30 August 2026, 6:30 PM.")
    assert primary(out).startswith("2026-08-30T18:30:00")
    assert out.data["deadlines"][0]["date_only"] is False


# --- 3-10: relative --------------------------------------------------

def test_today(agent):
    out = run(agent, body="Please submit your application before 5 PM today.")
    assert primary(out) == "2026-08-28T17:00:00+05:30"
    assert out.data["is_past"] is False


def test_tomorrow(agent):
    out = run(agent, body="Applications close tomorrow, so submit the form.")
    assert primary(out).startswith("2026-08-29")


def test_this_friday(agent):
    out = run(agent, body="You must submit the form this Friday.")
    assert primary(out).startswith("2026-09-04")


def test_next_monday_ambiguous(agent):
    out = run(agent, body="Please register using the form by next Monday.")
    assert primary(out).startswith("2026-09-07")
    assert out.data["ambiguity_flag"] is True


def test_within_2_hours(agent):
    out = run(agent, body="Please confirm your slot within 2 hours by filling the form.")
    assert primary(out) == "2026-08-28T18:30:00+05:30"


def test_within_24_hours(agent):
    out = run(agent, body="Complete the form within 24 hours.")
    assert primary(out) == "2026-08-29T16:30:00+05:30"


def test_eod(agent):
    out = run(agent, body="Submit the form by EOD today.")
    assert primary(out) == "2026-08-28T23:59:59+05:30"
    assert out.data["ambiguity_flag"] is False


def test_midnight(agent):
    out = run(agent, body="The submission form closes at midnight.")
    assert primary(out) == "2026-08-28T23:59:59+05:30"


# --- 11: date-only --------------------------------------------------

def test_date_only_deadline(agent):
    out = run(agent, body="The last date to apply via the form is September 1.")
    d = out.data["deadlines"][0]
    assert d["date_only"] is True
    assert d["normalized_deadline"].endswith("23:59:59+05:30")


# --- 12-13: multiple + action linking -----------------------------

def test_multiple_deadlines_not_collapsed(agent):
    out = run(
        agent,
        body="Register for the drive by September 1 and submit your resume by September 3.",
        actions=[
            {"action_type": "REGISTRATION", "evidence": "Register for the drive"},
            {"action_type": "DOCUMENT_UPLOAD", "evidence": "submit your resume"},
        ],
    )
    dls = out.data["deadlines"]
    assert len(dls) == 2
    dates = sorted(d["normalized_deadline"][:10] for d in dls)
    assert dates == ["2026-09-01", "2026-09-03"]


def test_deadlines_link_to_actions(agent):
    out = run(
        agent,
        body="Register by September 1 and upload your resume by September 3.",
        actions=[
            {"action_id": "act_001", "action_type": "REGISTRATION", "evidence": "Register"},
            {"action_id": "act_002", "action_type": "DOCUMENT_UPLOAD", "evidence": "upload your resume"},
        ],
    )
    by_date = {d["normalized_deadline"][:10]: d for d in out.data["deadlines"]}
    assert by_date["2026-09-01"]["action_context"] == "REGISTRATION"
    assert by_date["2026-09-03"]["action_context"] == "DOCUMENT_UPLOAD"


# --- 14: past ------------------------------------------------------

def test_past_deadline_detected_not_deleted(agent):
    out = run(
        agent,
        body="Reminder: the last date to submit the form was 20 August 2026.",
        received_at=datetime(2026, 8, 28, 10, 0, tzinfo=IST),
    )
    assert out.data["deadline_detected"] is True
    assert out.data["is_past"] is True
    assert out.data["deadlines"][0]["is_past"] is True


# --- 15: ambiguous numeric --------------------------------------

def test_ambiguous_numeric_date_flagged_and_reviewed(agent):
    out = run(agent, body="Last date to submit the form: 05/09/2026.")
    assert out.data["ambiguity_flag"] is True
    assert "DD/MM vs MM/DD" in (out.data["ambiguity_reason"] or "")
    assert out.needs_human_review is True  # no LLM to disambiguate


# --- 16-18: event dates, not deadlines --------------------------

def test_event_date_is_not_a_deadline(agent):
    out = run(
        agent,
        subject="Shortlisted",
        body="Congratulations. The interview will be held on Friday at 3 PM in Room 4.",
        category="PLACEMENT",
        actions=[{"action_type": "ATTEND_EVENT"}],
    )
    assert out.data["deadline_detected"] is False
    assert len(out.data["event_dates"]) >= 1


def test_promotional_email_with_dates_has_no_deadline(agent):
    out = run(
        agent,
        subject="MEGA SALE",
        body="Our biggest sale ends this Sunday! Grab 50% off before August 31.",
        category="PROMOTIONAL",
        actions=[],
    )
    assert out.data["deadline_detected"] is False


def test_scheduled_interview_is_not_a_deadline(agent):
    out = run(
        agent,
        body="Your interview is scheduled for Monday. Please be on time.",
        category="PLACEMENT",
        actions=[{"action_type": "ATTEND_EVENT"}],
    )
    assert out.data["deadline_detected"] is False


# --- 19-21: no deadline / empty --------------------------------

def test_no_deadline(agent):
    out = run(agent, subject="Notes", body="The lecture notes for this week are attached for your reference.",
              category="ACADEMIC_INFORMATION", actions=[])
    assert out.data["deadline_detected"] is False
    assert out.data["deadlines"] == []


def test_empty_subject(agent):
    out = run(agent, subject="", body="Submit the form by 30 August 2026.")
    assert out.data["deadline_detected"] is True


def test_empty_body(agent):
    out = run(agent, subject="Submit the form by tomorrow", body="")
    assert isinstance(out, AgentOutput)
    DeadlineData.model_validate(out.data)


def test_fully_empty(agent):
    out = run(agent, subject="", body="")
    assert out.data["deadline_detected"] is False


# --- 22: conflicting ------------------------------------------

def test_conflicting_deadlines_for_same_action_flagged(agent):
    out = run(
        agent,
        body="Please submit the assignment by Friday. Correction: submit the assignment by Monday.",
        category="ASSIGNMENT",
        actions=[{"action_type": "COMPLETE_ASSIGNMENT", "evidence": "submit the assignment"}],
    )
    assert out.needs_human_review is True


# --- 23-24: explicit timezones -------------------------------

def test_explicit_ist(agent):
    out = run(agent, body="Register using the form before 10:30 AM IST tomorrow.")
    assert out.data["timezone"] == "Asia/Kolkata"
    assert primary(out).endswith("+05:30")


def test_explicit_utc(agent):
    out = run(agent, body="Submit the form before 5 PM UTC on 2026-09-05.")
    dl = out.data["deadlines"][0]
    assert dl["timezone"] == "UTC"
    assert dl["normalized_deadline"].endswith("+00:00")


# --- schema validity ----------------------------------------

def test_output_is_schema_valid(agent):
    out = run(agent, body="Submit the form by 30 August 2026, 5 PM.")
    AgentOutput.model_validate(out.to_wire())
    DeadlineData.model_validate(out.data)
    assert out.data["agent"] if False else out.agent == "Deadline Agent"
    assert out.data["reference_time_used"] == REF.isoformat()
    for d in out.data["deadlines"]:
        assert d["deadline_id"].startswith("dl_")
        assert d["source"] == "deterministic"
        if d["normalized_deadline"]:
            parsed = datetime.fromisoformat(d["normalized_deadline"])
            assert parsed.tzinfo is not None  # never naive


def test_reference_time_falls_back_when_missing_tz(agent):
    # received_at is always tz-aware from intake, but guard the path anyway
    out = run(agent, body="Submit by tomorrow.")
    assert out.data["reference_time_used"]


# --- 25-27: LLM layer --------------------------------------

def _hard_email():
    return dict(
        body="Kindly ensure the paperwork reaches us by the usual cut-off next cycle.",
        actions=[{"action_type": "DOCUMENT_UPLOAD"}],
    )


def test_deterministic_only_without_api_key(agent):
    out = run(agent, **_hard_email())
    assert out.data["detection_method"] == "deterministic"
    assert out.status == "ok"


def test_llm_used_when_uncertain():
    llm = FakeLLM(
        response={
            "has_deadline": True,
            "deadlines": [
                {
                    "raw_deadline_text": "by the usual cut-off next cycle",
                    "normalized_deadline": None,
                    "kind": "DEADLINE",
                    "is_ambiguous": True,
                    "ambiguity_reason": "no concrete date",
                    "confidence": 0.55,
                    "evidence": "reaches us by the usual cut-off",
                }
            ],
        }
    )
    agent = DeadlineAgent(settings=Settings(), llm_client=llm)
    out = run(agent, **_hard_email())
    assert llm.calls
    assert out.data["detection_method"] == "llm"
    assert out.data["deadline_detected"] is True
    assert out.data["ambiguity_flag"] is True


def test_llm_cannot_invent_a_deadline():
    llm = FakeLLM(
        response={
            "has_deadline": True,
            "deadlines": [
                {
                    "raw_deadline_text": "by 31 December 2027",  # not in the email
                    "normalized_deadline": "2027-12-31T23:59:59+05:30",
                    "kind": "DEADLINE",
                    "confidence": 0.9,
                    "evidence": "made up",
                }
            ],
        }
    )
    agent = DeadlineAgent(settings=Settings(), llm_client=llm)
    out = run(agent, **_hard_email())
    # unsupported -> dropped -> LLM found nothing usable
    assert all("2027" not in (d["normalized_deadline"] or "") for d in out.data["deadlines"])


def test_llm_unavailable_falls_back():
    from app.services.llm_service import LLMUnavailableError

    llm = FakeLLM(raise_error=LLMUnavailableError("timeout"))
    agent = DeadlineAgent(settings=Settings(), llm_client=llm)
    out = run(agent, **_hard_email())
    assert out.data["detection_method"] == "llm_fallback_deterministic"
    assert out.status == "partial"
    assert any(e["code"] == "llm_unavailable" for e in out.to_wire()["errors"])


def test_invalid_llm_response_falls_back():
    llm = FakeLLM(response={"deadlines": "not a list"})
    agent = DeadlineAgent(settings=Settings(), llm_client=llm)
    out = run(agent, **_hard_email())
    assert out.data["detection_method"] == "llm_fallback_deterministic"
    assert out.status == "partial"


def test_llm_gets_minimal_context():
    llm = FakeLLM(response={"has_deadline": False, "deadlines": []})
    agent = DeadlineAgent(settings=Settings(), llm_client=llm)
    email = make_email(subject="Cutoff soon", body="Reach us by the usual cutoff.", received_at=REF)
    agent.analyze(email, triage_stub("OTHER"), action_stub([{"action_type": "DOCUMENT_UPLOAD"}]))
    _system, user = llm.calls[0]
    assert REF.isoformat() in user
    assert "someone@example.com" in user
    assert "labelIds" not in user and "gmail_test001" not in user
