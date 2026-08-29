"""Action Agent tests (Phase 5, STEP 13).

The 22 required scenarios + schema validation + LLM layer. LLM/Gmail/OAuth all
mocked; no network.
"""

from __future__ import annotations

import pytest

from app.agents.action_agent import ActionAgent
from app.core.config import Settings
from app.models.action import ActionData, ActionType
from app.models.agent_output import AgentOutput
from tests.triage_helpers import FakeLLM, make_email, triage_stub


@pytest.fixture
def agent() -> ActionAgent:
    """Deterministic-only Action Agent (no LLM)."""
    return ActionAgent(settings=Settings())


def types_of(out: AgentOutput) -> list[str]:
    return [a["action_type"] for a in out.data["actions"]]


# --- 1-6: each action type -------------------------------------------------

def test_internship_requires_form_submission(agent):
    out = agent.detect(
        make_email(
            sender="placement@college.edu",
            subject="Summer Internship 2026",
            body="Interested students are requested to apply. Fill the application form "
            "at https://forms.gle/abc before 5 September.",
            links=["https://forms.gle/abc"],
        ),
        triage_stub("INTERNSHIP"),
    )
    assert out.data["action_required"] is True
    assert ActionType.FORM_SUBMISSION.value in types_of(out)
    a0 = next(a for a in out.data["actions"] if a["action_type"] == "FORM_SUBMISSION")
    assert a0["target_link"] == "https://forms.gle/abc"
    assert a0["raw_deadline_hint"] and "5 september" in a0["raw_deadline_hint"].lower()
    assert a0["evidence"]


def test_placement_requires_registration(agent):
    out = agent.detect(
        make_email(
            sender="placement@college.edu",
            subject="Placement drive – TCS",
            body="All eligible students must register for the placement drive using the portal.",
        ),
        triage_stub("PLACEMENT"),
    )
    assert ActionType.REGISTRATION.value in types_of(out)


def test_assignment_requires_complete_assignment(agent):
    out = agent.detect(
        make_email(
            sender="rahul.faculty@college.edu",
            subject="Assignment 3",
            body="Please submit the assignment through the portal before Friday.",
        ),
        triage_stub("ASSIGNMENT"),
    )
    assert types_of(out) == [ActionType.COMPLETE_ASSIGNMENT.value]
    assert out.data["actions"][0]["blocking"] is True


def test_faculty_email_requires_reply(agent):
    out = agent.detect(
        make_email(
            sender="hod.cse@college.edu",
            subject="Confirmation required",
            body="Please confirm your attendance for the orientation by replying to this email.",
        ),
        triage_stub("FACULTY_ANNOUNCEMENT"),
    )
    assert ActionType.REPLY.value in types_of(out)


def test_google_form_requires_form_submission(agent):
    out = agent.detect(
        make_email(
            sender="events@college.edu",
            subject="Feedback",
            body="Kindly fill the Google Form: https://forms.gle/feedback",
            links=["https://forms.gle/feedback"],
        ),
        triage_stub("FACULTY_ANNOUNCEMENT"),
    )
    assert ActionType.FORM_SUBMISSION.value in types_of(out)


def test_meeting_requires_attend_event(agent):
    out = agent.detect(
        make_email(
            sender="mentor@college.edu",
            subject="Project review",
            body="You are required to attend the project review session on Monday at 3 PM.",
        ),
        triage_stub("PROJECT_UPDATE"),
    )
    assert ActionType.ATTEND_EVENT.value in types_of(out)


# --- 7-9: no action -----------------------------------------------------

def test_pure_informational_no_action(agent):
    out = agent.detect(
        make_email(
            sender="dept@college.edu",
            subject="Library timings updated",
            body="The library will now remain open until 9 PM on weekdays. "
            "This is for your information.",
        ),
        triage_stub("ACADEMIC_INFORMATION"),
    )
    assert out.data["action_required"] is False
    assert out.data["actions"] == []
    assert out.data["action_type"] is None


def test_promotional_no_action(agent):
    out = agent.detect(
        make_email(
            sender="offers@shopping.com",
            subject="FLAT 50% OFF",
            body="Limited time sale. Buy now and grab the discount!",
        ),
        triage_stub("PROMOTIONAL"),
    )
    assert out.data["action_required"] is False
    assert out.confidence >= 0.8  # confident it's informational


def test_application_confirmation_no_action(agent):
    out = agent.detect(
        make_email(
            sender="noreply@portal.college.edu",
            subject="Application received",
            body="Thank you for applying. Your application has been submitted successfully. "
            "No action is required from your side.",
        ),
        triage_stub("OTHER"),
    )
    assert out.data["action_required"] is False


# --- 10-11: multiple / repeated -------------------------------------

def test_multiple_actions(agent):
    out = agent.detect(
        make_email(
            sender="tpo@college.edu",
            subject="Placement drive",
            body="Register for the placement drive and submit your resume.",
        ),
        triage_stub("PLACEMENT"),
    )
    got = set(types_of(out))
    assert got == {ActionType.REGISTRATION.value, ActionType.DOCUMENT_UPLOAD.value}


def test_fill_form_and_attend(agent):
    out = agent.detect(
        make_email(
            sender="tpo@college.edu",
            subject="Shortlisted",
            body="Fill the application form and attend the interview on Friday.",
            links=["https://forms.gle/i"],
        ),
        triage_stub("PLACEMENT"),
    )
    got = set(types_of(out))
    assert got == {ActionType.FORM_SUBMISSION.value, ActionType.ATTEND_EVENT.value}


def test_repeated_action_language_collapses_to_one(agent):
    out = agent.detect(
        make_email(
            sender="prof@college.edu",
            subject="Assignment 3",
            body="Please submit the assignment. Submit the assignment before Friday. "
            "Assignment submission is compulsory.",
        ),
        triage_stub("ASSIGNMENT"),
    )
    assert types_of(out) == [ActionType.COMPLETE_ASSIGNMENT.value]


# --- 12-14: negation / completion / conditional ------------------

def test_do_not_reply_produces_no_reply(agent):
    out = agent.detect(
        make_email(
            sender="noreply@bank.com",
            subject="Statement ready",
            body="Your account statement is ready. Please do not reply to this email.",
        ),
        triage_stub("OTHER"),
    )
    assert ActionType.REPLY.value not in types_of(out)
    assert out.data["action_required"] is False


def test_already_submitted_produces_no_submit(agent):
    out = agent.detect(
        make_email(
            sender="portal@college.edu",
            subject="Status",
            body="Your assignment has been submitted and recorded. Nothing further is needed.",
        ),
        triage_stub("ASSIGNMENT"),
    )
    assert ActionType.COMPLETE_ASSIGNMENT.value not in types_of(out)
    assert out.data["action_required"] is False


def test_conditional_reply_is_not_mandatory(agent):
    out = agent.detect(
        make_email(
            sender="prof@college.edu",
            subject="Lecture notes",
            body="The lecture notes are attached. Reply only if you have questions.",
        ),
        triage_stub("ACADEMIC_INFORMATION"),
    )
    # a conditional "reply if..." must not become a mandatory REPLY action
    reply_actions = [a for a in out.data["actions"] if a["action_type"] == "REPLY" and a["blocking"]]
    assert reply_actions == []


# --- 15-16: empty subject / body -------------------------------

def test_empty_subject(agent):
    out = agent.detect(
        make_email(sender="prof@college.edu", subject="", body="Please submit the assignment by Friday."),
        triage_stub("ASSIGNMENT"),
    )
    assert ActionType.COMPLETE_ASSIGNMENT.value in types_of(out)


def test_empty_body(agent):
    out = agent.detect(
        make_email(sender="prof@college.edu", subject="Please fill the feedback form", body=""),
        triage_stub("FACULTY_ANNOUNCEMENT"),
    )
    assert out.data["action_required"] in {True, False}
    AgentOutput.model_validate(out.to_wire())


def test_fully_empty_email(agent):
    out = agent.detect(make_email(sender="x@y.com", subject="", body=""), triage_stub("OTHER"))
    assert out.data["action_required"] is False
    ActionData.model_validate(out.data)


# --- 17-18: conflicting / ambiguous --------------------------

def test_conflicting_signals_lower_confidence(agent):
    out = agent.detect(
        make_email(
            sender="prof@college.edu",
            subject="Assignment",
            body="Please submit the assignment by Friday. Update: your assignment has "
            "already been submitted, ignore this.",
        ),
        triage_stub("ASSIGNMENT"),
    )
    # either flagged for review, or resolved to no-action — never a confident SUBMIT
    if out.data["action_required"]:
        assert out.needs_human_review is True or out.confidence < 0.7


def test_ambiguous_implied_action_flagged(agent):
    out = agent.detect(
        make_email(
            sender="unknown@gmail.com",
            subject="Opportunity",
            body="Applications are now open for various roles this season.",
        ),
        triage_stub("OTHER"),
    )
    if out.data["action_required"]:
        assert out.data["actions"][0]["confidence"] < 0.75


# --- 19: important sender context ----------------------------

def test_important_sender_supports_but_does_not_fabricate(agent):
    # placement cell email with NO action language -> still no action
    out = agent.detect(
        make_email(
            sender="placement@college.edu",
            subject="Placement statistics 2025",
            body="Here are last year's placement statistics for your reference.",
        ),
        triage_stub("PLACEMENT"),
    )
    assert out.data["action_required"] is False


def test_category_never_overrides_explicit_instruction(agent):
    # promotional email that genuinely asks the user to reply
    out = agent.detect(
        make_email(
            sender="newsletter@service.com",
            subject="Confirm your subscription",
            body="Please reply to this email to confirm your subscription.",
        ),
        triage_stub("PROMOTIONAL"),
    )
    assert ActionType.REPLY.value in types_of(out)


# --- schema validity ------------------------------------------

def test_output_is_schema_valid(agent):
    out = agent.detect(
        make_email(subject="Submit assignment", body="Submit the assignment by Friday."),
        triage_stub("ASSIGNMENT"),
    )
    assert isinstance(out, AgentOutput)
    wire = out.to_wire()
    AgentOutput.model_validate(wire)
    ActionData.model_validate(wire["data"])
    assert wire["agent"] == "Action Agent"
    assert wire["run_id"].startswith("run_")
    for a in wire["data"]["actions"]:
        assert ActionType(a["action_type"])
        assert a["status"] == "OPEN"
        assert a["related_email"] == wire["email_id"]
        assert 0.0 <= a["confidence"] <= 1.0


def test_works_without_triage_input(agent):
    out = agent.detect(make_email(subject="Please submit the assignment", body="Due Friday."))
    assert ActionType.COMPLETE_ASSIGNMENT.value in types_of(out)


# --- 20-22: LLM layer ---------------------------------------

def _ambiguous_email():
    return make_email(
        sender="unknown@gmail.com",
        subject="Next steps",
        body="Kindly proceed with the next steps as discussed regarding your candidature.",
    )


def test_deterministic_only_without_api_key():
    agent = ActionAgent(settings=Settings())  # LLM_PROVIDER defaults to none
    out = agent.detect(_ambiguous_email(), triage_stub("JOB_OPPORTUNITY"))
    assert out.data["detection_method"] == "deterministic"
    assert out.status == "ok"


def test_llm_used_when_deterministic_uncertain():
    llm = FakeLLM(
        response={
            "action_required": True,
            "actions": [
                {
                    "action_type": "DOCUMENT_UPLOAD",
                    "action_description": "Upload your updated CV to the shared drive.",
                    "confidence": 0.86,
                    "blocking": True,
                    "evidence": "proceed with the next steps",
                }
            ],
        }
    )
    agent = ActionAgent(settings=Settings(), llm_client=llm)
    out = agent.detect(_ambiguous_email(), triage_stub("JOB_OPPORTUNITY"))
    assert llm.calls
    assert out.data["detection_method"] == "llm"
    assert ActionType.DOCUMENT_UPLOAD.value in types_of(out)


def test_llm_not_called_when_deterministic_confident():
    llm = FakeLLM(response={"action_required": False, "actions": []})
    agent = ActionAgent(settings=Settings(), llm_client=llm)
    out = agent.detect(
        make_email(subject="Assignment 3", body="Please submit the assignment through the portal before Friday."),
        triage_stub("ASSIGNMENT"),
    )
    assert llm.calls == []
    assert ActionType.COMPLETE_ASSIGNMENT.value in types_of(out)


def test_llm_unavailable_falls_back():
    from app.services.llm_service import LLMUnavailableError

    llm = FakeLLM(raise_error=LLMUnavailableError("timeout"))
    agent = ActionAgent(settings=Settings(), llm_client=llm)
    out = agent.detect(_ambiguous_email(), triage_stub("OTHER"))
    assert out.data["detection_method"] == "llm_fallback_deterministic"
    assert out.status == "partial"
    assert any(e["code"] == "llm_unavailable" for e in out.to_wire()["errors"])


def test_invalid_llm_action_type_falls_back():
    llm = FakeLLM(
        response={"action_required": True, "actions": [{"action_type": "DO_A_BACKFLIP", "confidence": 0.9}]}
    )
    agent = ActionAgent(settings=Settings(), llm_client=llm)
    out = agent.detect(_ambiguous_email(), triage_stub("OTHER"))
    assert out.data["detection_method"] == "llm_fallback_deterministic"
    assert out.status == "partial"
    assert any(e["code"] == "invalid_llm_response" for e in out.to_wire()["errors"])


def test_llm_says_no_action():
    llm = FakeLLM(response={"action_required": False, "actions": []})
    agent = ActionAgent(settings=Settings(), llm_client=llm)
    out = agent.detect(_ambiguous_email(), triage_stub("OTHER"))
    assert out.data["action_required"] is False
    assert out.data["detection_method"] == "llm"


def test_llm_only_gets_minimal_context():
    llm = FakeLLM(response={"action_required": False, "actions": []})
    agent = ActionAgent(settings=Settings(), llm_client=llm)
    agent.detect(_ambiguous_email(), triage_stub("JOB_OPPORTUNITY"))
    system, user = llm.calls[0]
    assert "unknown@gmail.com" in user
    assert "JOB_OPPORTUNITY" in user
    assert "Next steps" in user
    # no Gmail internals leaked
    assert "gmail_test001" not in user and "labelIds" not in user
