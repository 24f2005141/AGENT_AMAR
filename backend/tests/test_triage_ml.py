"""Triage Agent — local ML routing between the deterministic layer and the LLM.

Confirms the hybrid decision flow:
  deterministic (confident)  -> used, ML + LLM skipped
  deterministic (uncertain)  -> ML (confident, non-conflicting) -> used, LLM skipped
                             -> ML (unsure / unavailable / conflicting) -> LLM fallback
"""

from __future__ import annotations

import json

import pytest

from app.core.config import Settings
from app.models.agent_output import AgentOutput
from app.models.triage import ClassificationMethod, TriageCategory, TriageData
from app.agents.triage_agent import TriageAgent
from tests.ml_helpers import EmailMLClassifier, Prediction, SpyClassifier, train_model
from tests.triage_helpers import FakeLLM, make_email

_ML_ON = dict(ml_classifier_enabled=True, ml_classifier_threshold=0.85)


def _weak_email():
    """Deterministic confidence ~0.30, OTHER, no precedence, not phishing."""
    return make_email(
        sender="hr@unknownstartup.io",
        subject="Opportunity",
        body="We wanted to reach out regarding a possible opportunity. Let us know if interested.",
    )


def _confident_internship_email():
    return make_email(
        sender="placement@college.edu",
        subject="Summer internship 2026 application form",
        body="Apply for the internship. Fill the application form and upload your resume.",
        links=["https://forms.gle/x"],
    )


# --- ML disabled -------------------------------------------------------

def test_ml_disabled_is_never_consulted():
    spy = SpyClassifier(prediction=Prediction("SPAM", 0.99))
    # settings say disabled, but a classifier is still injected — the agent must
    # honour the injected object; the *disable* path is covered where the DI
    # accessor returns None (test_ml_classifier.py). Here we assert the plain
    # deterministic path when neither ML nor LLM is available.
    agent = TriageAgent(settings=Settings(ml_classifier_enabled=False))
    out = agent.classify(_weak_email())
    assert out.data["signals"]["classification_method"] == "deterministic"


# --- deterministic confident ----------------------------------------

def test_ml_not_called_when_deterministic_is_confident():
    spy = SpyClassifier(prediction=Prediction("SPAM", 0.99))
    llm = FakeLLM(response={"category": "SPAM", "confidence": 0.99})
    agent = TriageAgent(settings=Settings(**_ML_ON), llm_client=llm, ml_classifier=spy)
    out = agent.classify(_confident_internship_email())
    assert spy.calls == []
    assert llm.calls == []
    assert out.data["category"] == TriageCategory.INTERNSHIP.value
    assert out.data["signals"]["classification_method"] == "deterministic"


# --- high-confidence ML --------------------------------------------

def test_high_confidence_ml_is_used_and_llm_is_skipped():
    spy = SpyClassifier(prediction=Prediction("INTERNSHIP", 0.93, {"INTERNSHIP": 0.93}))
    llm = FakeLLM(response={"category": "REPLY_REQUIRED", "confidence": 0.9})
    agent = TriageAgent(settings=Settings(**_ML_ON), llm_client=llm, ml_classifier=spy)

    out = agent.classify(_weak_email())

    assert spy.calls, "ML should have been consulted"
    assert llm.calls == [], "LLM must be skipped when ML is confident"
    assert out.data["category"] == TriageCategory.INTERNSHIP.value
    assert out.data["signals"]["classification_method"] == "ml"
    assert out.confidence == pytest.approx(0.93)
    assert "LLM escalation avoided" in out.reasoning_summary


# --- low-confidence ML -> LLM fallback ------------------------------

def test_low_confidence_ml_falls_through_to_llm():
    spy = SpyClassifier(prediction=Prediction("INTERNSHIP", 0.60))
    llm = FakeLLM(response={"category": "REPLY_REQUIRED", "confidence": 0.82,
                            "reasoning": "sender is following up"})
    agent = TriageAgent(settings=Settings(**_ML_ON), llm_client=llm, ml_classifier=spy)

    out = agent.classify(_weak_email())

    assert spy.calls and llm.calls, "both ML and LLM consulted"
    assert out.data["category"] == TriageCategory.REPLY_REQUIRED.value
    assert out.data["signals"]["classification_method"] == "llm"


def test_ml_unavailable_falls_through_to_llm():
    spy = SpyClassifier(available=False)
    llm = FakeLLM(response={"category": "REPLY_REQUIRED", "confidence": 0.82})
    agent = TriageAgent(settings=Settings(**_ML_ON), llm_client=llm, ml_classifier=spy)

    out = agent.classify(_weak_email())

    assert spy.calls == []
    assert llm.calls, "LLM consulted when ML is unavailable"
    assert out.data["signals"]["classification_method"] == "llm"


# --- no LLM configured --------------------------------------------

def test_low_confidence_ml_with_no_llm_keeps_deterministic():
    spy = SpyClassifier(prediction=Prediction("INTERNSHIP", 0.55))
    agent = TriageAgent(settings=Settings(**_ML_ON), ml_classifier=spy)  # NullLLMClient

    out = agent.classify(_weak_email())

    assert spy.calls
    assert out.data["signals"]["classification_method"] == "deterministic"
    assert out.status == "ok"


# --- safety: ML cannot override strong deterministic signals --------

def test_ml_cannot_override_college_domain_spam_guard():
    spy = SpyClassifier(prediction=Prediction("SPAM", 0.99))
    agent = TriageAgent(settings=Settings(**_ML_ON), ml_classifier=spy)
    out = agent.classify(
        make_email(sender="office@college.edu", subject="Notice",
                   body="Short notice about something unclear.")
    )
    assert out.data["category"] != TriageCategory.SPAM.value
    assert out.data["signals"]["classification_method"] != "ml"


def test_ml_cannot_downgrade_actionable_dated_email_to_low_band():
    spy = SpyClassifier(prediction=Prediction("PROMOTIONAL", 0.99))
    agent = TriageAgent(settings=Settings(**_ML_ON), ml_classifier=spy)
    out = agent.classify(
        make_email(sender="team@somelist.com", subject="Reminder",
                   body="Kindly submit your response. The due date is tomorrow.")
    )
    assert out.data["category"] != TriageCategory.PROMOTIONAL.value
    assert out.data["signals"]["classification_method"] != "ml"


def test_ml_prediction_exception_is_swallowed():
    spy = SpyClassifier(raise_error=RuntimeError("model blew up"))
    llm = FakeLLM(response={"category": "REPLY_REQUIRED", "confidence": 0.8})
    agent = TriageAgent(settings=Settings(**_ML_ON), llm_client=llm, ml_classifier=spy)

    out = agent.classify(_weak_email())  # must not raise

    assert out.data["signals"]["classification_method"] == "llm"


# --- schema + real model ----------------------------------------

def test_ml_backed_output_is_schema_valid():
    spy = SpyClassifier(prediction=Prediction("JOB_OPPORTUNITY", 0.91))
    agent = TriageAgent(settings=Settings(**_ML_ON), ml_classifier=spy)
    out = agent.classify(_weak_email())
    assert isinstance(out, AgentOutput)
    wire = out.to_wire()
    AgentOutput.model_validate(wire)
    TriageData.model_validate(wire["data"])
    assert wire["data"]["signals"]["classification_method"] == ClassificationMethod.ML.value
    assert TriageCategory(wire["data"]["category"])


def test_routing_trace_populated_on_deterministic_path():
    out = TriageAgent(settings=Settings(**_ML_ON)).classify(_confident_internship_email())
    r = out.data["signals"]["classification_routing"]
    assert r["method"] == "deterministic"
    assert r["ml_attempted"] is False
    assert r["ml_confidence"] is None
    assert r["llm_invoked"] is False
    assert r["deterministic_confidence"] >= 0.7


def test_routing_trace_populated_on_ml_path():
    spy = SpyClassifier(prediction=Prediction("INTERNSHIP", 0.92))
    out = TriageAgent(settings=Settings(**_ML_ON), ml_classifier=spy).classify(_weak_email())
    r = out.data["signals"]["classification_routing"]
    assert r["method"] == "ml"
    assert r["ml_attempted"] is True
    assert r["ml_confidence"] == pytest.approx(0.92)
    assert r["ml_reject_reason"] is None
    assert r["llm_invoked"] is False


def test_routing_trace_populated_on_llm_path():
    spy = SpyClassifier(prediction=Prediction("INTERNSHIP", 0.4))
    llm = FakeLLM(response={"category": "REPLY_REQUIRED", "confidence": 0.82})
    out = TriageAgent(settings=Settings(**_ML_ON), llm_client=llm, ml_classifier=spy).classify(_weak_email())
    r = out.data["signals"]["classification_routing"]
    assert r["ml_attempted"] is True
    assert r["ml_reject_reason"] == "low_confidence"
    assert r["llm_invoked"] is True


def test_routing_trace_contains_no_email_content():
    secret_body = "Your OTP is 483921 and the wire password is hunter2-secret-token"
    spy = SpyClassifier(prediction=Prediction("OTHER", 0.99))
    out = TriageAgent(settings=Settings(**_ML_ON), ml_classifier=spy).classify(
        make_email(sender="unknown@x.io", subject="verify", body=secret_body)
    )
    blob = json.dumps(out.data["signals"]["classification_routing"])
    assert "483921" not in blob
    assert "hunter2" not in blob
    assert "password" not in blob


def test_trusted_domain_newsletter_is_not_forced_important():
    # A subscribed digest from a college address stays a low-band NEWSLETTER —
    # a trusted domain alone must not push it up.
    out = TriageAgent(settings=Settings(**_ML_ON)).classify(
        make_email(
            sender="newsletter@college.edu",
            subject="Weekly campus digest",
            body="Your weekly digest of campus news. You are receiving this because you "
            "subscribed. Unsubscribe at the bottom. View in browser.",
        )
    )
    assert out.data["category"] == TriageCategory.NEWSLETTER.value
    assert out.data["further_analysis_required"] is False


def test_real_trained_model_end_to_end(tmp_path):
    train_model(tmp_path / "m.joblib")
    clf = EmailMLClassifier(tmp_path / "m.joblib")
    llm = FakeLLM(raise_error=AssertionError("LLM must not be called"))
    agent = TriageAgent(
        settings=Settings(
            ml_classifier_enabled=True,
            ml_classifier_threshold=0.6,
            triage_llm_threshold=0.9,  # widen the "uncertain" window for this test
        ),
        llm_client=llm,
        ml_classifier=clf,
    )
    out = agent.classify(
        make_email(
            sender="talent@somecompany.com",
            subject="internship",
            body="Apply for the internship, fill the application form and upload your resume.",
        )
    )
    assert out.data["category"] == TriageCategory.INTERNSHIP.value
    assert out.data["signals"]["classification_method"] == "ml"
    assert llm.calls == []
