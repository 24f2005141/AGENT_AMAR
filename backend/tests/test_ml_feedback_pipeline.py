"""Phase 18 — feedback → training-data export → controlled candidate retrain.

Nothing here retrains or promotes the production model automatically.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from app.db import session as db_session
from app.ml import retrain
from app.ml.feedback_dataset import collect_feedback, to_training_records
from app.services.classification_feedback_service import ClassificationFeedbackService
from app.services.persistence_service import PersistenceService
from tests.auth_helpers import seed_user
from tests.ml_helpers import TINY_RECORDS, write_dataset
from tests.persistence_helpers import decision_for, default_user_pk
from tests.triage_helpers import make_email


def _seed_email(email_id: str, user_pk: int, *, subject="Meeting request",
                body="Hi, please reply to confirm whether you can attend the review."):
    e = make_email(sender="prof@x.edu", subject=subject, body=body).model_copy(
        update={"email_id": email_id, "thread_id": f"{email_id}_t"}
    )
    with db_session.db_session() as db:
        return PersistenceService(db, user_pk=user_pk).persist_decision(
            e, decision_for(e)
        ).id


def _feedback(email_id: str, user_pk: int, category: str):
    with db_session.db_session() as db:
        ClassificationFeedbackService(db, user_pk=user_pk).submit(email_id, category)


# --- export -------------------------------------------------------

def test_collect_feedback_returns_latest_valid_per_email():
    with db_session.db_session() as db:
        upk = default_user_pk(db)
    _seed_email("gmail_fx_1", upk)
    _feedback("gmail_fx_1", upk, "IMPORTANT")
    _feedback("gmail_fx_1", upk, "REPLY_REQUIRED")  # latest wins

    with db_session.db_session() as db:
        examples = collect_feedback(db)
    mine = [e for e in examples if e.email_id == "gmail_fx_1"]
    assert len(mine) == 1
    assert mine[0].corrected_primary_category == "REPLY_REQUIRED"
    assert mine[0].original_final_category  # snapshot present


def test_deleted_email_feedback_is_excluded():
    with db_session.db_session() as db:
        upk = default_user_pk(db)
    pk = _seed_email("gmail_fx_del", upk)
    _feedback("gmail_fx_del", upk, "IMPORTANT")
    with db_session.db_session() as db:
        from app.db.models import EmailRecord
        db.delete(db.get(EmailRecord, pk))
        db.commit()

    with db_session.db_session() as db:
        examples = collect_feedback(db)
    assert all(e.email_id != "gmail_fx_del" for e in examples)


def test_to_training_records_only_emits_clean_triage_labels():
    with db_session.db_session() as db:
        upk = default_user_pk(db)
    _seed_email("gmail_fx_reply", upk)
    _seed_email("gmail_fx_imp", upk, subject="FYI campus notice",
                body="Informational only, no action needed.")
    _feedback("gmail_fx_reply", upk, "REPLY_REQUIRED")
    _feedback("gmail_fx_imp", upk, "IMPORTANT")

    with db_session.db_session() as db:
        records = to_training_records(collect_feedback(db))
    labels = {r["label"] for r in records}
    assert labels == {"REPLY_REQUIRED"}          # IMPORTANT correction is not a triage-kind change
    assert all(r["source"] == "user_feedback" for r in records)


# --- candidate retrain ------------------------------------------

@pytest.fixture
def tiny_train(tmp_path):
    return write_dataset(tmp_path / "base.jsonl", TINY_RECORDS)


@pytest.fixture
def tiny_eval(tmp_path):
    rows = [
        {"subject": "Internship application", "body": "apply internship form resume upload",
         "sender": "careers@x.com", "expected_label": "INTERNSHIP", "kind": "clear"},
        {"subject": "weekly digest", "body": "newsletter unsubscribe subscribed digest",
         "sender": "news@x.com", "expected_label": "NEWSLETTER", "kind": "clear"},
        {"subject": "please reply", "body": "please reply and confirm awaiting your response",
         "sender": "p@x.com", "expected_label": "REPLY_REQUIRED", "kind": "clear"},
    ]
    p = tmp_path / "eval.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", "utf-8")
    return p


def _digest(path) -> str | None:
    from pathlib import Path
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def test_run_builds_candidate_without_touching_production(tmp_path, tiny_train, tiny_eval):
    prod = retrain.DEFAULT_MODEL_PATH
    prod_before = _digest(prod)

    with db_session.db_session() as db:
        upk = default_user_pk(db)
    _seed_email("gmail_fx_run", upk)
    _feedback("gmail_fx_run", upk, "REPLY_REQUIRED")

    cand = tmp_path / "candidate" / "email_classifier.joblib"
    report = retrain.run(
        data_path=tiny_train, eval_path=tiny_eval,
        candidate_model_path=cand, threshold=0.5,
    )

    assert cand.is_file()                                   # candidate written
    assert (cand.parent / "retrain_report.json").is_file()
    assert (cand.parent / "feedback_export.jsonl").is_file()
    assert report["feedback_training_records_used"] >= 1
    assert report["production_model_untouched"] is True
    assert _digest(prod) == prod_before                     # production UNCHANGED


def test_run_with_no_feedback_still_works(tmp_path, tiny_train, tiny_eval):
    cand = tmp_path / "cand2" / "email_classifier.joblib"
    report = retrain.run(
        data_path=tiny_train, eval_path=tiny_eval,
        candidate_model_path=cand, include_feedback=False, threshold=0.5,
    )
    assert report["feedback_training_records_used"] == 0
    assert cand.is_file()


def test_promote_requires_explicit_confirmation(tmp_path, tiny_train, tiny_eval):
    cand = tmp_path / "cand3" / "email_classifier.joblib"
    retrain.run(data_path=tiny_train, eval_path=tiny_eval,
                candidate_model_path=cand, threshold=0.5)
    fake_prod = tmp_path / "prod" / "email_classifier.joblib"

    with pytest.raises(retrain.PromotionRefused):
        retrain.promote(candidate_model_path=cand, production_model_path=fake_prod)
    assert not fake_prod.is_file()

    result = retrain.promote(yes=True, candidate_model_path=cand,
                             production_model_path=fake_prod)
    assert fake_prod.is_file()
    assert result["promoted"] == str(fake_prod)
