"""Triage Agent tests (Phase 3, STEP 9).

Covers the 15 required scenarios plus schema validation and the LLM layer.
Every LLM call is mocked; nothing touches the network.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.models.agent_output import AgentOutput
from app.models.email import AttachmentMetadata, BodyFormat
from app.models.triage import TriageCategory, TriageData
from app.agents.triage_agent import TriageAgent
from tests.triage_helpers import FakeLLM, make_email


@pytest.fixture
def agent() -> TriageAgent:
    """Deterministic-only agent (no LLM configured)."""
    return TriageAgent(settings=Settings())


def cat(agent: TriageAgent, email) -> str:
    return agent.classify(email).data["category"]


# --- 1-7: core categories -------------------------------------------------

def test_internship_opportunity(agent):
    out = agent.classify(
        make_email(
            sender="placement@college.edu",
            subject="Summer Internship 2026 – application form open",
            body="Applications for the software engineering internship are open. "
            "Fill the form https://forms.gle/abc and upload your resume.",
            links=["https://forms.gle/abc"],
        )
    )
    assert out.data["category"] == TriageCategory.INTERNSHIP.value
    assert out.data["subcategory"] == "application_form"
    assert out.data["importance_estimate"] == "HIGH"
    assert out.data["further_analysis_required"] is True
    assert out.confidence >= 0.7
    assert out.data["signals"]["sender_in_important_list"] is True


def test_placement_opportunity(agent):
    out = agent.classify(
        make_email(
            sender="placement@college.edu",
            subject="Campus placement drive – TCS",
            body="Recruitment drive on Monday. Pre-placement talk at 10am. "
            "Check the eligibility criteria.",
        )
    )
    assert out.data["category"] == TriageCategory.PLACEMENT.value


def test_faculty_announcement(agent):
    out = agent.classify(
        make_email(
            sender="hod.cse@college.edu",
            subject="Circular: department notice",
            body="All students are informed that classes resume Monday. "
            "Kind attention students.",
        )
    )
    assert out.data["category"] == TriageCategory.FACULTY_ANNOUNCEMENT.value
    assert out.data["importance_estimate"] == "HIGH"


def test_assignment_reminder(agent):
    out = agent.classify(
        make_email(
            sender="rahul.faculty@college.edu",
            subject="Assignment 3 submission",
            body="Submit assignment 3 and upload the lab record by tomorrow.",
        )
    )
    assert out.data["category"] == TriageCategory.ASSIGNMENT.value


def test_application_registration_email(agent):
    out = agent.classify(
        make_email(
            sender="events@college.edu",
            subject="Register for the coding hackathon",
            body="Register now via the registration form. Workshop and hackathon "
            "on Saturday.",
            links=["https://forms.gle/reg"],
        )
    )
    assert out.data["category"] in {TriageCategory.EVENT.value}
    assert out.data["subcategory"] in {"registration", "application_form", "hackathon"}


def test_promotional_email(agent):
    out = agent.classify(
        make_email(
            sender="offers@shopping.com",
            subject="FLAT 50% OFF – deal of the day",
            body="Limited time sale. Buy now, use this coupon for a discount.",
        )
    )
    assert out.data["category"] == TriageCategory.PROMOTIONAL.value
    assert out.data["importance_estimate"] == "LOW"
    assert out.data["further_analysis_required"] is False


def test_spam_phishing(agent):
    out = agent.classify(
        make_email(
            sender="security@paypa1-alerts.com",
            subject="Your account suspended – verify now",
            body="Your account will be closed. Verify your account immediately. "
            "Click here to verify. Act now.",
            links=["http://paypa1-alerts.com/verify"],
        )
    )
    assert out.data["category"] == TriageCategory.SPAM.value


# --- 8: ambiguous -------------------------------------------------------

def test_ambiguous_email_low_confidence_flagged(agent):
    out = agent.classify(
        make_email(
            sender="unknown@gmail.com",
            subject="Regarding your submission",
            body="Hi, just following up on the thing we talked about earlier.",
        )
    )
    assert out.confidence < 0.6
    assert out.needs_human_review is True


def test_completely_empty_email(agent):
    out = agent.classify(make_email(sender="x@y.com", subject="", body=""))
    assert out.data["category"] == TriageCategory.OTHER.value
    assert out.needs_human_review is True


# --- 9: important sender ------------------------------------------------

def test_important_sender_recorded_but_not_priority(agent):
    out = agent.classify(
        make_email(
            sender="exams@college.edu",
            subject="Hall ticket released",
            body="Download your admit card for the end sem examination.",
        )
    )
    assert out.data["category"] == TriageCategory.EXAM.value
    assert out.data["subcategory"] == "hall_ticket"
    assert out.data["signals"]["sender_importance"] == "CRITICAL"
    # Triage does not assign priority — only an importance *estimate*.
    assert set(out.data) == {
        "category", "subcategory", "importance_estimate",
        "further_analysis_required", "confidence", "reasoning_summary", "signals",
    }
    assert "priority" not in out.data
    assert "priority_score" not in out.data


def test_college_sender_never_spam_or_promotional(agent):
    out = agent.classify(
        make_email(
            sender="dept@college.edu",
            subject="Verify your account for the student portal",
            body="Please verify your account. Limited time offer discount sale buy now.",
        )
    )
    assert out.data["category"] not in {
        TriageCategory.SPAM.value,
        TriageCategory.PROMOTIONAL.value,
    }
    assert "college_domain_not_promotional_or_spam" in out.data["signals"]["precedence_applied"]


# --- 10: conflicting signals + precedence -----------------------------

def test_opportunity_beats_event_and_newsletter(agent):
    out = agent.classify(
        make_email(
            sender="tpo@college.edu",
            subject="Internship drive + orientation workshop",
            body="Apply for the internship opportunity. There is also a workshop "
            "and a webinar. Newsletter digest follows.",
        )
    )
    assert out.data["category"] == TriageCategory.INTERNSHIP.value


def test_subscribed_digest_stays_newsletter(agent):
    out = agent.classify(
        make_email(
            sender="notifications@internshala.com",
            subject="Your weekly internship digest: 25 new internships",
            body="In this issue: 25 internships this week. View in browser. "
            "You are receiving this because you subscribed. Unsubscribe.",
        )
    )
    assert out.data["category"] == TriageCategory.NEWSLETTER.value
    assert out.data["subcategory"] == "job_digest"


def test_reply_required_is_additive_not_primary(agent):
    out = agent.classify(
        make_email(
            sender="placement@college.edu",
            subject="Internship offer – please confirm",
            body="Please confirm your acceptance of the internship by replying to "
            "this email.",
        )
    )
    # INTERNSHIP is primary; the reply request is recorded as a signal, not the category.
    assert out.data["category"] == TriageCategory.INTERNSHIP.value
    assert "REPLY_REQUIRED" in out.data["signals"]["category_scores"]


def test_reply_required_primary_when_nothing_else(agent):
    out = agent.classify(
        make_email(
            sender="friend@example.com",
            subject="Quick question",
            body="Awaiting your reply — please confirm whether you can make it.",
        )
    )
    assert out.data["category"] == TriageCategory.REPLY_REQUIRED.value


def test_calendar_invite_is_event(agent):
    out = agent.classify(
        make_email(
            sender="club@college.edu",
            subject="Invitation",
            body="You are invited.",
            attachments=[
                AttachmentMetadata(
                    filename="event.ics", mime_type="text/calendar", size_bytes=512
                )
            ],
        )
    )
    assert out.data["category"] == TriageCategory.EVENT.value
    assert out.data["subcategory"] == "calendar_invite"


# --- 11-12: empty subject / body -----------------------------------

def test_empty_subject_uses_body(agent):
    out = agent.classify(
        make_email(sender="placement@college.edu", subject="", body="Internship opportunity: apply via the form.")
    )
    assert out.data["category"] == TriageCategory.INTERNSHIP.value


def test_empty_body_uses_subject(agent):
    out = agent.classify(
        make_email(sender="exams@college.edu", subject="End sem exam schedule and hall ticket", body="")
    )
    assert out.data["category"] == TriageCategory.EXAM.value


# --- schema validity --------------------------------------------------

def test_output_is_schema_valid_agent_output(agent):
    out = agent.classify(make_email(subject="Assignment due", body="Submit the assignment by Friday."))
    assert isinstance(out, AgentOutput)
    wire = out.to_wire()
    AgentOutput.model_validate(wire)
    TriageData.model_validate(wire["data"])
    assert wire["agent"] == "Triage Agent"
    assert wire["status"] in {"ok", "partial"}
    assert 0.0 <= wire["confidence"] <= 1.0
    assert wire["run_id"].startswith("run_")
    assert TriageCategory(wire["data"]["category"])  # valid enum member


def test_every_category_value_is_in_the_vault_enum():
    from app.agents import triage_rules

    for category in triage_rules.CATEGORY_KEYWORDS:
        assert isinstance(category, TriageCategory)


# --- 13-15: LLM layer -------------------------------------------------

def _low_confidence_email():
    # single weak keyword -> deterministic confidence below the LLM threshold
    return make_email(
        sender="unknown@gmail.com",
        subject="Regarding your submission",
        body="Following up as discussed.",
    )


def test_deterministic_only_when_no_api_key():
    agent = TriageAgent(settings=Settings())  # LLM_PROVIDER defaults to "none"
    out = agent.classify(_low_confidence_email())
    assert out.data["signals"]["classification_method"] == "deterministic"
    assert out.status == "ok"


def test_llm_used_when_deterministic_uncertain():
    llm = FakeLLM(
        response={
            "category": "REPLY_REQUIRED",
            "subcategory": "follow_up",
            "importance_estimate": "MEDIUM",
            "confidence": 0.82,
            "reasoning": "The sender is following up and expects a reply.",
        }
    )
    agent = TriageAgent(settings=Settings(), llm_client=llm)
    out = agent.classify(_low_confidence_email())
    assert llm.calls, "LLM should have been consulted"
    assert out.data["category"] == TriageCategory.REPLY_REQUIRED.value
    assert out.data["signals"]["classification_method"] == "llm"


def test_llm_not_called_when_deterministic_confident():
    llm = FakeLLM(response={"category": "SPAM", "confidence": 0.99})
    agent = TriageAgent(settings=Settings(), llm_client=llm)
    out = agent.classify(
        make_email(
            sender="placement@college.edu",
            subject="Summer internship 2026 application form",
            body="Apply for the internship. Fill the application form and upload your resume.",
            links=["https://forms.gle/x"],
        )
    )
    assert llm.calls == []
    assert out.data["category"] == TriageCategory.INTERNSHIP.value


def test_llm_unavailable_falls_back_to_deterministic():
    from app.services.llm_service import LLMUnavailableError

    llm = FakeLLM(raise_error=LLMUnavailableError("network down"))
    agent = TriageAgent(settings=Settings(), llm_client=llm)
    out = agent.classify(_low_confidence_email())
    assert out.data["signals"]["classification_method"] == "llm_fallback_deterministic"
    assert out.status == "partial"
    assert any(e["code"] == "llm_unavailable" for e in out.to_wire()["errors"])


def test_invalid_llm_response_falls_back():
    llm = FakeLLM(response={"category": "SUPER_URGENT_MAIL", "confidence": 0.9})
    agent = TriageAgent(settings=Settings(), llm_client=llm)
    out = agent.classify(_low_confidence_email())
    assert out.data["signals"]["classification_method"] == "llm_fallback_deterministic"
    assert out.status == "partial"
    assert any(e["code"] == "invalid_llm_response" for e in out.to_wire()["errors"])


def test_llm_cannot_override_precedence():
    # LLM says SPAM, but the sender is @college.edu -> precedence forbids it.
    llm = FakeLLM(response={"category": "SPAM", "confidence": 0.9, "reasoning": "looks spammy"})
    agent = TriageAgent(settings=Settings(), llm_client=llm)
    out = agent.classify(
        make_email(
            sender="office@college.edu",
            subject="Notice",
            body="Short notice about something unclear.",
        )
    )
    assert out.data["category"] != TriageCategory.SPAM.value


def test_llm_disagreement_lowers_confidence_and_flags_conflict():
    llm = FakeLLM(response={"category": "EVENT", "confidence": 0.6, "reasoning": "sounds like an event"})
    agent = TriageAgent(settings=Settings(), llm_client=llm)
    out = agent.classify(
        make_email(
            sender="unknown@gmail.com",
            subject="Regarding your submission",
            body="Following up as discussed.",
        )
    )
    assert out.data["signals"]["conflicting_signals"] is True
    assert out.confidence <= 0.8
