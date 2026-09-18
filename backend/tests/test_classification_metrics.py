"""In-process triage routing metrics (app/ml/metrics.py)."""

from __future__ import annotations

from app.agents.triage_agent import TriageAgent
from app.core.config import Settings
from app.ml.metrics import ClassificationMetrics, get_classification_metrics
from tests.ml_helpers import Prediction, SpyClassifier
from tests.triage_helpers import FakeLLM, make_email

_ML_ON = dict(ml_classifier_enabled=True, ml_classifier_threshold=0.85)


def _weak_email():
    return make_email(
        sender="hr@unknownstartup.io",
        subject="Opportunity",
        body="We wanted to reach out regarding a possible opportunity. Let us know if interested.",
    )


def _confident_email():
    return make_email(
        sender="placement@college.edu",
        subject="Summer internship 2026 application form",
        body="Apply for the internship. Fill the application form and upload your resume.",
        links=["https://forms.gle/x"],
    )


def test_record_counts_methods_and_rates():
    m = ClassificationMetrics()
    m.record({"method": "deterministic", "ml_attempted": False, "llm_invoked": False})
    m.record({"method": "ml", "ml_attempted": True, "ml_confidence": 0.9, "llm_invoked": False})
    m.record({"method": "ml", "ml_attempted": True, "ml_confidence": 0.95, "llm_invoked": False})
    m.record({"method": "llm_fallback_deterministic", "ml_attempted": True,
              "ml_confidence": 0.4, "ml_reject_reason": "low_confidence", "llm_invoked": True})
    m.record({"method": "llm", "ml_attempted": False, "ml_reject_reason": "disabled",
              "llm_invoked": True})

    snap = m.snapshot()
    assert snap["total"] == 5
    assert snap["deterministic"] == 1
    assert snap["ml"] == 2
    assert snap["llm"] == 1
    assert snap["llm_fallback_deterministic"] == 1
    assert snap["ml_attempted"] == 3
    assert snap["ml_accepted"] == 2
    assert snap["ml_rejected_low_confidence"] == 1
    assert snap["ml_rejected_unavailable"] == 1
    assert snap["ml_average_confidence"] == round((0.9 + 0.95 + 0.4) / 3, 4)
    assert snap["deterministic_to_llm"] == 1
    # avoided = deterministic + ml = 3 / 5
    assert snap["llm_avoidance_rate"] == 60.0


def test_reject_reason_buckets():
    m = ClassificationMetrics()
    for reason in ("precedence_conflict", "signal_conflict", "invalid_label"):
        m.record({"method": "llm", "ml_attempted": True, "ml_confidence": 0.9,
                  "ml_reject_reason": reason, "llm_invoked": True})
    m.record({"method": "llm", "ml_attempted": True, "ml_reject_reason": "predict_error",
              "llm_invoked": True})
    snap = m.snapshot()
    assert snap["ml_rejected_conflict"] == 3
    assert snap["ml_rejected_error"] == 1


def test_reset_zeroes_everything():
    m = ClassificationMetrics()
    m.record({"method": "ml", "ml_attempted": True, "ml_confidence": 0.9})
    m.reset()
    snap = m.snapshot()
    assert snap["total"] == 0
    assert snap["ml"] == 0
    assert snap["ml_average_confidence"] is None
    assert snap["llm_avoidance_rate"] == 0.0


def test_triage_agent_updates_the_singleton():
    metrics = get_classification_metrics()
    metrics.reset()

    # 1 confident -> deterministic
    TriageAgent(settings=Settings(**_ML_ON)).classify(_confident_email())
    # 1 uncertain -> ML accepted
    spy = SpyClassifier(prediction=Prediction("JOB_OPPORTUNITY", 0.93))
    TriageAgent(settings=Settings(**_ML_ON), ml_classifier=spy).classify(_weak_email())
    # 1 uncertain -> ML low conf -> LLM
    spy_low = SpyClassifier(prediction=Prediction("JOB_OPPORTUNITY", 0.5))
    llm = FakeLLM(response={"category": "OTHER", "confidence": 0.6})
    TriageAgent(settings=Settings(**_ML_ON), llm_client=llm, ml_classifier=spy_low).classify(_weak_email())

    snap = metrics.snapshot()
    assert snap["total"] == 3
    assert snap["deterministic"] == 1
    assert snap["ml"] == 1
    assert snap["ml_accepted"] == 1
    assert snap["ml_rejected_low_confidence"] == 1
    assert snap["llm"] == 1
    assert 0.0 <= snap["llm_avoidance_rate"] <= 100.0


def test_monitor_status_exposes_classification_block():
    from fastapi.testclient import TestClient
    from app.main import app

    body = TestClient(app).get("/api/v1/monitor/status").json()
    assert "classification" in body
    assert set(body["classification"]) >= {
        "total", "deterministic", "ml", "llm", "llm_avoidance_rate", "ml_average_confidence"
    }
