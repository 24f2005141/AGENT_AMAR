"""Priority Agent tests (Phase 7, STEP 15). LLM/Gmail/OAuth all mocked."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.agents.priority_agent import PriorityAgent
from app.core.config import Settings
from app.models.agent_output import AgentOutput
from app.models.priority import PriorityData, PriorityLevel
from tests.triage_helpers import (
    FakeLLM,
    action_stub,
    deadline_stub,
    make_email,
    triage_stub,
)

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 8, 28, 16, 0, tzinfo=IST)


@pytest.fixture
def agent() -> PriorityAgent:
    return PriorityAgent(settings=Settings())


def run(
    agent,
    *,
    category="OTHER",
    actions=None,
    deadline=None,
    sender="someone@example.com",
    subject="Subject",
    body="Body text.",
    triage_conf=0.9,
    action_review=False,
    now=NOW,
):
    email = make_email(sender=sender, subject=subject, body=body)
    tri = triage_stub(category, confidence=triage_conf)
    act = action_stub(actions if actions is not None else [])
    if action_review:
        act.needs_human_review = True
    dl = deadline if deadline is not None else deadline_stub(None)
    return agent.score(email, tri, act, dl, now=now)


def level(out) -> str:
    return out.data["priority_level"]


# --- 1-4: category + deadline combinations -------------------------------

def test_placement_apply_deadline_30min_is_critical(agent):
    out = run(
        agent, category="PLACEMENT", actions=[{"action_type": "FORM_SUBMISSION"}],
        deadline=deadline_stub((NOW + timedelta(minutes=30)).isoformat()),
        sender="placement@college.edu",
    )
    assert level(out) == PriorityLevel.CRITICAL.value
    assert out.data["priority_score"] >= 90
    assert out.data["proximity_bucket"] == "WITHIN_1H"
    assert out.data["notify"] is True and out.data["monitor"] is True


def test_internship_apply_deadline_tomorrow(agent):
    out = run(
        agent, category="INTERNSHIP", actions=[{"action_type": "FORM_SUBMISSION"}],
        deadline=deadline_stub((NOW + timedelta(hours=20)).isoformat()),
        sender="tpo@college.edu",
    )
    assert level(out) in {PriorityLevel.URGENT.value, PriorityLevel.CRITICAL.value}
    assert out.data["proximity_bucket"] == "WITHIN_24H"


def test_assignment_due_within_24h(agent):
    out = run(
        agent, category="ASSIGNMENT", actions=[{"action_type": "COMPLETE_ASSIGNMENT"}],
        deadline=deadline_stub((NOW + timedelta(hours=18)).isoformat()),
        sender="rahul.faculty@college.edu",
    )
    assert level(out) in {PriorityLevel.HIGH.value, PriorityLevel.URGENT.value}


def test_exam_announcement_no_action_not_critical(agent):
    out = run(agent, category="EXAM", actions=[], deadline=deadline_stub(None),
              sender="exams@college.edu")
    assert level(out) != PriorityLevel.CRITICAL.value
    # User Preferences §6.4: EXAM always notify + monitor
    assert out.data["notify"] is True and out.data["monitor"] is True
    assert "pref_exam_always_notify_monitor" in out.data["overrides_applied"]


# --- 5-8: senders and low-value categories -----------------------

def test_important_faculty_sender_raises_but_not_to_critical(agent):
    out = run(agent, category="FACULTY_ANNOUNCEMENT", actions=[],
              sender="hod.cse@college.edu", body="For your information.")
    assert level(out) in {PriorityLevel.MEDIUM.value, PriorityLevel.HIGH.value}
    assert level(out) != PriorityLevel.CRITICAL.value


def test_promotional_with_marketing_urgency_is_low(agent):
    out = run(
        agent, category="PROMOTIONAL", actions=[{"action_type": "REPLY"}],
        sender="offers@shopdeals.com",
        subject="URGENT: last chance!", body="Act now, this offer expires soon!!!",
    )
    assert level(out) == PriorityLevel.LOW.value
    assert out.data["notify"] is False
    assert out.data["monitor"] is False


def test_newsletter_no_action_is_low(agent):
    out = run(agent, category="NEWSLETTER", actions=[], sender="news@digest.com")
    assert level(out) == PriorityLevel.LOW.value
    assert out.data["notify"] is False


def test_social_notification_is_low(agent):
    out = run(agent, category="SOCIAL", actions=[], sender="notify@social.com")
    assert level(out) == PriorityLevel.LOW.value


# --- 9-10: action / deadline present alone -----------------------

def test_action_required_no_deadline(agent):
    out = run(agent, category="ACADEMIC_INFORMATION",
              actions=[{"action_type": "READ_AND_ACKNOWLEDGE"}], deadline=deadline_stub(None))
    assert out.data["proximity_bucket"] == "NONE"
    assert level(out) in {PriorityLevel.MEDIUM.value, PriorityLevel.HIGH.value}
    assert out.data["monitor"] is True


def test_deadline_no_action(agent):
    out = run(agent, category="ACADEMIC_INFORMATION", actions=[],
              deadline=deadline_stub((NOW + timedelta(hours=10)).isoformat()))
    assert out.data["proximity_bucket"] == "WITHIN_24H"
    assert level(out) in {PriorityLevel.MEDIUM.value, PriorityLevel.HIGH.value}


# --- 11-15: proximity buckets ------------------------------------

def test_overdue_deadline_capped_at_urgent(agent):
    out = run(
        agent, category="ASSIGNMENT", actions=[{"action_type": "COMPLETE_ASSIGNMENT"}],
        deadline=deadline_stub((NOW - timedelta(hours=5)).isoformat(), is_past=True),
        sender="rahul.faculty@college.edu",
    )
    assert out.data["proximity_bucket"] == "OVERDUE"
    assert out.data["deadline_is_past"] is True
    assert level(out) != PriorityLevel.CRITICAL.value  # capped
    assert "overdue_deadline_ceiling_urgent" in out.data["overrides_applied"]


def test_within_1h(agent):
    out = run(agent, category="ASSIGNMENT", actions=[{"action_type": "COMPLETE_ASSIGNMENT"}],
              deadline=deadline_stub((NOW + timedelta(minutes=45)).isoformat()))
    assert out.data["proximity_bucket"] == "WITHIN_1H"
    assert level(out) in {PriorityLevel.URGENT.value, PriorityLevel.CRITICAL.value}


def test_within_24h(agent):
    out = run(agent, category="ASSIGNMENT", actions=[{"action_type": "COMPLETE_ASSIGNMENT"}],
              deadline=deadline_stub((NOW + timedelta(hours=10)).isoformat()))
    assert out.data["proximity_bucket"] == "WITHIN_24H"


def test_within_72h(agent):
    out = run(agent, category="ASSIGNMENT", actions=[{"action_type": "COMPLETE_ASSIGNMENT"}],
              deadline=deadline_stub((NOW + timedelta(hours=50)).isoformat()))
    assert out.data["proximity_bucket"] == "WITHIN_72H"


def test_later_than_72h(agent):
    out = run(agent, category="JOB_OPPORTUNITY", actions=[{"action_type": "FORM_SUBMISSION"}],
              deadline=deadline_stub((NOW + timedelta(days=10)).isoformat()))
    assert out.data["proximity_bucket"] == "LATER"


def test_no_deadline_bucket_none(agent):
    out = run(agent, category="OTHER", actions=[], deadline=deadline_stub(None))
    assert out.data["proximity_bucket"] == "NONE"
    assert out.data["time_remaining_seconds"] is None


# --- 17-19: overrides -------------------------------------------

def test_explicit_user_pref_internship_min_urgent(agent):
    # User Preferences §6.1 — any internship/placement mention -> min URGENT
    out = run(agent, category="INTERNSHIP", actions=[{"action_type": "FORM_SUBMISSION"}],
              deadline=deadline_stub(None), sender="unknown@gmail.com")
    assert PriorityLevel(level(out)) in {PriorityLevel.URGENT, PriorityLevel.CRITICAL}
    assert "pref_internship_placement_min_urgent" in out.data["overrides_applied"]


def test_important_sender_critical_floor(agent):
    out = run(agent, category="OTHER", actions=[], sender="placement@college.edu",
              body="Just an FYI note.")
    assert PriorityLevel(level(out)) in {PriorityLevel.HIGH, PriorityLevel.URGENT, PriorityLevel.CRITICAL}
    assert any(o.startswith("important_sender_critical") or o.startswith("pref_")
               for o in out.data["overrides_applied"])


def test_explicit_urgency_language_bumps_high_value(agent):
    plain = run(agent, category="ASSIGNMENT", actions=[{"action_type": "COMPLETE_ASSIGNMENT"}],
                deadline=deadline_stub((NOW + timedelta(hours=50)).isoformat()),
                subject="Assignment 3", body="Please submit assignment 3.")
    urgent = run(agent, category="ASSIGNMENT", actions=[{"action_type": "COMPLETE_ASSIGNMENT"}],
                 deadline=deadline_stub((NOW + timedelta(hours=50)).isoformat()),
                 subject="URGENT: Assignment 3", body="Time-sensitive: submit assignment 3 now.")
    assert urgent.data["priority_score"] >= plain.data["priority_score"]
    assert any(f["factor"] == "explicit_urgency_language" for f in urgent.data["score_breakdown"])


def test_marketing_noreply_forced_low(agent):
    out = run(agent, category="ACADEMIC_INFORMATION", actions=[{"action_type": "READ_AND_ACKNOWLEDGE"}],
              sender="noreply@marketing.example.com", body="Newsletter content.")
    assert level(out) == PriorityLevel.LOW.value
    assert out.data["notify"] is False


# --- 20-22: conflicting / uncertain ---------------------------

def test_conflicting_signals_important_sender_on_social(agent):
    # important sender but SOCIAL category -> conflict; without LLM it stays deterministic
    out = run(agent, category="SOCIAL", actions=[], sender="placement@college.edu")
    assert level(out) in {PriorityLevel.LOW.value, PriorityLevel.MEDIUM.value}
    PriorityData.model_validate(out.data)


def test_low_confidence_upstream_flags_review_for_high_value(agent):
    out = run(
        agent, category="PLACEMENT", actions=[{"action_type": "FORM_SUBMISSION"}],
        deadline=deadline_stub((NOW + timedelta(hours=10)).isoformat()),
        triage_conf=0.35, action_review=True, sender="unknown@gmail.com",
    )
    assert out.needs_human_review is True


def test_ambiguous_deadline_not_silently_low(agent):
    # STEP 8 — PLACEMENT + action + ambiguous deadline must not be LOW/MEDIUM
    out = run(
        agent, category="PLACEMENT", actions=[{"action_type": "FORM_SUBMISSION"}],
        deadline=deadline_stub(None, ambiguity_flag=True), sender="unknown@gmail.com",
    )
    assert PriorityLevel(level(out)) not in {PriorityLevel.LOW, PriorityLevel.MEDIUM}
    assert out.needs_human_review is True
    assert out.data["monitor"] is True


# --- 26-27: timezone handling -------------------------------

def test_timezone_aware_calculation(agent):
    out = run(agent, category="ASSIGNMENT", actions=[{"action_type": "COMPLETE_ASSIGNMENT"}],
              deadline=deadline_stub("2026-08-28T18:00:00+05:30"), now=NOW)
    assert out.data["proximity_bucket"] == "WITHIN_24H"
    assert datetime.fromisoformat(out.data["reference_time_used"]).tzinfo is not None


def test_naive_now_is_made_aware(agent):
    naive_now = datetime(2026, 8, 28, 16, 0)
    email = make_email()
    out = agent.score(email, triage_stub("OTHER"), action_stub([]), deadline_stub(None),
                      now=naive_now)
    assert datetime.fromisoformat(out.data["reference_time_used"]).tzinfo is not None


# --- schema / explainability ---------------------------

def test_output_is_schema_valid_and_explainable(agent):
    out = run(agent, category="PLACEMENT", actions=[{"action_type": "FORM_SUBMISSION"}],
              deadline=deadline_stub((NOW + timedelta(minutes=30)).isoformat()),
              sender="placement@college.edu")
    AgentOutput.model_validate(out.to_wire())
    PriorityData.model_validate(out.data)
    assert out.agent == "Priority Agent"
    assert out.data["score_breakdown"], "must explain the score"
    assert out.data["reasoning_summary"]
    assert set(out.data["factors"]) >= {"category", "action_required", "deadline_proximity"}
    assert 0 <= out.data["priority_score"] <= 100


def test_upstream_outputs_not_mutated(agent):
    tri, act, dl = triage_stub("INTERNSHIP"), action_stub([{"action_type": "FORM_SUBMISSION"}]), deadline_stub(None)
    tri_before, act_before, dl_before = tri.model_dump(), act.model_dump(), dl.model_dump()
    agent.score(make_email(), tri, act, dl, now=NOW)
    assert tri.model_dump() == tri_before
    assert act.model_dump() == act_before
    assert dl.model_dump() == dl_before


# --- 23-25: LLM layer --------------------------------

def _conflict_email():
    # important sender + SOCIAL category -> _signals_conflict -> LLM consulted
    return dict(category="SOCIAL", actions=[], sender="placement@college.edu",
                subject="Congrats on your work anniversary", body="See who reacted.")


def test_deterministic_only_without_api_key(agent):
    out = run(agent, **_conflict_email())
    assert out.data["scoring_method"] == "deterministic"
    assert out.status == "ok"


def test_llm_adjustment_is_bounded_and_applied():
    llm = FakeLLM(response={"score_adjustment": 25, "reasoning": "feels important"})  # will be clamped
    agent = PriorityAgent(settings=Settings(), llm_client=llm)
    out = run(agent, **_conflict_email())
    assert llm.calls
    adj = next((f for f in out.data["score_breakdown"] if f["factor"] == "llm_context_adjustment"), None)
    assert adj is not None and -10 <= adj["points"] <= 10
    assert out.data["scoring_method"] == "deterministic+llm_adjustment"


def test_llm_zero_adjustment_keeps_deterministic_method():
    llm = FakeLLM(response={"score_adjustment": 0, "reasoning": "score looks right"})
    agent = PriorityAgent(settings=Settings(), llm_client=llm)
    out = run(agent, **_conflict_email())
    assert out.data["scoring_method"] == "deterministic"


def test_llm_unavailable_does_not_break_pipeline():
    from app.services.llm_service import LLMUnavailableError

    llm = FakeLLM(raise_error=LLMUnavailableError("timeout"))
    agent = PriorityAgent(settings=Settings(), llm_client=llm)
    out = run(agent, **_conflict_email())
    assert out.data["scoring_method"] == "deterministic+llm_unavailable"
    assert out.status == "partial"
    assert isinstance(out.data["priority_score"], int)


def test_invalid_llm_response_ignored():
    llm = FakeLLM(response={"score_adjustment": "a lot"})
    agent = PriorityAgent(settings=Settings(), llm_client=llm)
    out = run(agent, **_conflict_email())
    assert out.data["scoring_method"] in {"deterministic+llm_unavailable", "deterministic"}
    assert isinstance(out.data["priority_score"], int)


def test_llm_not_consulted_when_no_conflict():
    llm = FakeLLM(response={"score_adjustment": 10, "reasoning": "x"})
    agent = PriorityAgent(settings=Settings(), llm_client=llm)
    out = run(agent, category="PLACEMENT", actions=[{"action_type": "FORM_SUBMISSION"}],
              deadline=deadline_stub((NOW + timedelta(minutes=30)).isoformat()),
              sender="placement@college.edu")
    assert llm.calls == []
