"""Tests for the deterministic Mail Intake Agent.

Covers the checklist from Phase 2 STEP 7:
 1. Gmail payload parses
 2. sender extracted
 3. subject extracted
 4. plain-text body extracted
 5. HTML body handled (used only when no plain-text part)
 6. labels preserved
 7. attachment metadata extracted
 8. Gmail message id + thread id preserved
 9. result validates against the Pydantic model
10. missing optional fields do not crash intake
11. the agent performs no AI / classification logic
"""

from __future__ import annotations

import copy
import inspect
from datetime import datetime

import pytest

from app.agents import intake_agent as intake_module
from app.agents.intake_agent import MailIntakeAgent
from app.models.email import BodyFormat, NormalizedEmail
from app.services import gmail_service


@pytest.fixture
def agent() -> MailIntakeAgent:
    return MailIntakeAgent()


# --- 1. parse -----------------------------------------------------------------

def test_gmail_payload_parses(agent, sample_gmail_message):
    email = agent.normalize(sample_gmail_message)
    assert isinstance(email, NormalizedEmail)
    assert email.body_parse_error is False
    assert email.needs_human_review is False


# --- 2. sender --------------------------------------------------------------

def test_sender_extracted(agent, sample_gmail_message):
    email = agent.normalize(sample_gmail_message)
    assert email.sender.email == "placement@college.edu"
    assert email.sender.name == "Placement Cell"


def test_recipients_extracted(agent, sample_gmail_message):
    email = agent.normalize(sample_gmail_message)
    assert email.to == ["students-2026@college.edu"]
    assert "training.head@college.edu" in email.cc
    assert "dean.office@college.edu" in email.cc
    assert email.reply_to == "placement@college.edu"


# --- 3. subject ----------------------------------------------------------

def test_subject_extracted(agent, sample_gmail_message):
    email = agent.normalize(sample_gmail_message)
    assert email.subject == "Software Engineering Internship Application - Action Required"


# --- 4. plain-text body -------------------------------------------------

def test_plain_text_body_extracted(agent, sample_gmail_message):
    email = agent.normalize(sample_gmail_message)
    assert email.body_format == BodyFormat.TEXT.value
    assert "Software Engineering Internship" in email.body
    assert "5 September 2026, 6:00 PM IST" in email.body  # deadline text preserved verbatim


def test_body_is_cleaned(agent, sample_gmail_message):
    """Quoted reply + signature are stripped; body is never raw HTML."""
    email = agent.normalize(sample_gmail_message)
    assert "Please circulate the internship notice" not in email.body  # quoted reply
    assert "This is an automated message" not in email.body            # signature block
    assert "<p>" not in email.body and "<b>" not in email.body


def test_links_extracted_from_body(agent, sample_gmail_message):
    email = agent.normalize(sample_gmail_message)
    assert email.has_links is True
    assert "https://forms.college.edu/sde-internship-2026" in email.links


# --- 5. HTML handling -------------------------------------------------------

def test_html_body_used_when_no_plain_text(agent, sample_gmail_message):
    msg = copy.deepcopy(sample_gmail_message)
    alt = msg["payload"]["parts"][0]["parts"]
    # drop the text/plain part, keep only text/html
    msg["payload"]["parts"][0]["parts"] = [p for p in alt if p["mimeType"] == "text/html"]

    email = agent.normalize(msg)
    assert email.body_format == BodyFormat.HTML_CONVERTED.value
    assert "<" not in email.body  # markup removed
    assert "Software Engineering Internship" in email.body
    # href target is preserved through the conversion
    assert "https://forms.college.edu/sde-internship-2026" in email.body


def test_html_converter_is_deterministic(sample_gmail_message):
    from app.utils.text_cleaning import html_to_text

    html = "<p>Hello</p><div>World <a href='https://x.test/a'>link</a></div>"
    assert html_to_text(html) == html_to_text(html)


# --- 6. labels ---------------------------------------------------------------

def test_labels_preserved(agent, sample_gmail_message):
    email = agent.normalize(sample_gmail_message)
    assert email.labels == ["INBOX", "UNREAD", "IMPORTANT", "CATEGORY_UPDATES"]
    assert email.is_unread is True


# --- 7. attachments -----------------------------------------------------

def test_attachment_metadata_extracted(agent, sample_gmail_message):
    email = agent.normalize(sample_gmail_message)
    assert len(email.attachments) == 1
    att = email.attachments[0]
    assert att.filename == "Internship_JD_SDE_Nimbus_2026.pdf"
    assert att.mime_type == "application/pdf"
    assert att.size_bytes == 183456
    assert att.attachment_id == "ANGjdJ8k2l3m4n5o6p7q8r9s0t-EXAMPLE"


def test_attachment_contents_never_included(agent, sample_gmail_message):
    email = agent.normalize(sample_gmail_message)
    dumped = email.to_wire()["attachments"][0]
    assert set(dumped) == {"filename", "mime_type", "size_bytes", "attachment_id"}
    assert "data" not in dumped


# --- 8. ids preserved -------------------------------------------------

def test_message_and_thread_ids_preserved(agent, sample_gmail_message):
    email = agent.normalize(sample_gmail_message)
    assert email.email_id == "gmail_18f0a1b2c3d4e5f6"
    assert email.thread_id == "gmail_thread_18f0a1b2c3d4e5f6"
    assert email.message_id_header == "<CAF=abc123def456ghi789@mail.college.edu>"


def test_source_and_timestamps(agent, sample_gmail_message):
    email = agent.normalize(sample_gmail_message)
    assert email.source == "gmail"
    # internalDate 1787888662000 -> 2026-08-28T09:14:22+05:30
    assert email.received_at == datetime.fromisoformat("2026-08-28T09:14:22+05:30")
    assert email.received_at.utcoffset() is not None
    assert email.ingested_at.utcoffset() is not None


# --- 9. validates against the model -------------------------------

def test_result_validates_against_model(agent, sample_gmail_message):
    email = agent.normalize(sample_gmail_message)
    # round-trip through JSON and back must still validate
    wire = email.to_wire()
    reparsed = NormalizedEmail.model_validate(wire)
    assert reparsed.to_wire() == wire


def test_run_returns_agent_output_envelope(agent, sample_gmail_message):
    output = agent.run(sample_gmail_message)
    wire = output.to_wire()
    assert wire["agent"] == "Mail Intake Agent"
    assert wire["status"] == "ok"
    assert wire["email_id"] == "gmail_18f0a1b2c3d4e5f6"
    assert wire["confidence"] == 1.0
    # data payload IS the full normalized email
    NormalizedEmail.model_validate(wire["data"])
    assert wire["run_id"].startswith("run_")


# --- 10. missing / degraded input does not crash --------------

def test_missing_optional_headers_do_not_crash(agent, sample_gmail_message):
    msg = copy.deepcopy(sample_gmail_message)
    keep = {"From", "To", "Subject", "Date"}
    msg["payload"]["headers"] = [
        h for h in msg["payload"]["headers"] if h["name"] in keep
    ]
    email = agent.normalize(msg)
    assert isinstance(email, NormalizedEmail)
    assert email.message_id_header is None
    assert email.reply_to is None
    assert email.cc == []


def test_missing_from_header_flags_human_review(agent, sample_gmail_message):
    msg = copy.deepcopy(sample_gmail_message)
    msg["payload"]["headers"] = [
        h for h in msg["payload"]["headers"] if h["name"] != "From"
    ]
    email = agent.normalize(msg)
    assert email.needs_human_review is True
    assert email.sender.email == "unknown@unknown.invalid"


def test_no_body_parts_sets_parse_error(agent, sample_gmail_message):
    msg = copy.deepcopy(sample_gmail_message)
    msg["payload"]["parts"] = [msg["payload"]["parts"][1]]  # attachment only
    email = agent.normalize(msg)
    assert email.body == ""
    assert email.body_parse_error is True
    assert email.needs_human_review is True


def test_empty_message_does_not_crash(agent):
    email = agent.normalize({})
    assert isinstance(email, NormalizedEmail)
    assert email.needs_human_review is True
    assert email.email_id == "gmail_UNKNOWN"


def test_non_dict_input_raises_typeerror(agent):
    with pytest.raises(TypeError):
        agent.normalize("not a message")  # type: ignore[arg-type]


def test_run_on_garbage_returns_error_envelope(agent):
    out = agent.run("garbage")  # type: ignore[arg-type]
    assert out.status == "error"
    assert out.needs_human_review is True
    assert out.data == {}


# --- 11. no AI / classification logic ------------------------

def test_agent_does_not_classify_or_prioritise(agent, sample_gmail_message):
    email = agent.normalize(sample_gmail_message)
    forbidden = {
        "category", "subcategory", "priority", "priority_score", "priority_level",
        "importance", "importance_estimate", "action_required", "actions",
        "deadline", "normalized_deadline", "confidence",
    }
    assert forbidden.isdisjoint(email.to_wire().keys())


def test_intake_module_imports_no_ai_libraries():
    src = inspect.getsource(intake_module) + inspect.getsource(gmail_service)
    for banned in ("import openai", "import anthropic", "from openai", "from anthropic",
                   "langchain", "transformers"):
        assert banned not in src


def test_gmail_fetch_is_not_wired_yet():
    from app.services.gmail_service import GmailFetchNotConfigured, GmailService

    with pytest.raises(GmailFetchNotConfigured):
        GmailService().get_message("x")
