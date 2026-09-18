"""Local ML pre-classifier — model load / train / predict (no network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.ml.email_classifier import (
    FEATURE_VERSION,
    EmailMLClassifier,
    MLPredictionError,
    build_feature_text,
    get_email_ml_classifier,
    reset_cache,
)
from app.ml.training import TrainingDataError, load_training_data, train_and_save
from app.models.triage import TriageCategory
from tests.ml_helpers import TINY_RECORDS, train_model, write_dataset


def test_build_feature_text_is_stable_and_marks_fields():
    text = build_feature_text("Internship", "Apply now", "careers@ACME.com")
    assert text == build_feature_text("internship", "apply now", "careers@acme.com")
    assert "subject: internship" in text
    assert "domain: acme.com" in text
    assert "\n" not in text  # whitespace collapsed


def test_classifier_unavailable_when_no_model(tmp_path):
    clf = EmailMLClassifier(tmp_path / "missing.joblib")
    assert clf.is_available() is False
    assert clf.load_error == "model_file_missing"
    with pytest.raises(MLPredictionError):
        clf.predict("hi", "there", "x@y.com")


def test_train_then_predict_roundtrip(tmp_path):
    train_model(tmp_path / "m.joblib")
    clf = EmailMLClassifier(tmp_path / "m.joblib")
    assert clf.is_available() is True

    pred = clf.predict(
        "Internship opening",
        "Apply for the internship, fill the application form and upload your resume.",
        "careers@newco.com",
    )
    assert pred.label == "INTERNSHIP"
    assert 0.0 <= pred.confidence <= 1.0
    assert TriageCategory(pred.label)
    assert abs(sum(pred.probabilities.values()) - 1.0) < 0.05
    # confidence is the top probability
    assert pred.confidence == max(pred.probabilities.values())


def test_metadata_file_written_with_expected_fields(tmp_path):
    meta = train_model(tmp_path / "m.joblib")
    meta_file = tmp_path / "m_metadata.json"
    assert meta_file.is_file()
    on_disk = json.loads(meta_file.read_text("utf-8"))
    assert on_disk["num_samples"] == len(TINY_RECORDS)
    assert set(on_disk["labels"]) == {"INTERNSHIP", "NEWSLETTER", "REPLY_REQUIRED"}
    assert on_disk["feature_version"] == FEATURE_VERSION
    assert "trained_at" in on_disk
    assert meta["labels"] == on_disk["labels"]


def test_training_rejects_labels_outside_triage_enum(tmp_path):
    bad = write_dataset(
        tmp_path / "bad.jsonl",
        [{"subject": "x", "body": "y", "sender": "a@b.com", "label": "NOT_A_CATEGORY"}] * 3,
    )
    with pytest.raises(TrainingDataError):
        train_and_save(bad, tmp_path / "out.joblib")
    assert not (tmp_path / "out.joblib").exists()


def test_training_data_missing_file_raises(tmp_path):
    with pytest.raises(TrainingDataError):
        load_training_data(tmp_path / "nope.jsonl")


def test_corrupt_model_file_is_disabled_not_raised(tmp_path):
    p = tmp_path / "corrupt.joblib"
    p.write_bytes(b"this is not a joblib pickle at all")
    clf = EmailMLClassifier(p)
    assert clf.is_available() is False
    assert clf.load_error is not None
    with pytest.raises(MLPredictionError):
        clf.predict("a", "b", "c@d.com")


def test_feature_version_mismatch_rejected(tmp_path):
    import joblib

    train_model(tmp_path / "good.joblib")
    bundle = joblib.load(tmp_path / "good.joblib")
    bundle["metadata"]["feature_version"] = "999"
    joblib.dump(bundle, tmp_path / "stale.joblib")

    clf = EmailMLClassifier(tmp_path / "stale.joblib")
    assert clf.is_available() is False


def test_get_email_ml_classifier_none_when_disabled(tmp_path):
    reset_cache()
    s = Settings(ml_classifier_enabled=False, ml_classifier_model_path=str(tmp_path / "m.joblib"))
    assert get_email_ml_classifier(s) is None


def test_get_email_ml_classifier_returns_instance_when_enabled(tmp_path):
    reset_cache()
    train_model(tmp_path / "m.joblib")
    s = Settings(ml_classifier_enabled=True, ml_classifier_model_path=str(tmp_path / "m.joblib"))
    clf = get_email_ml_classifier(s)
    assert isinstance(clf, EmailMLClassifier)
    assert clf.is_available() is True
    reset_cache()


def test_model_path_resolves_relative_to_backend():
    s = Settings(ml_classifier_model_path="data/models/email_classifier.joblib")
    resolved = s.ml_classifier_model_path_resolved
    assert resolved.is_absolute()
    assert resolved.parts[-3:] == ("data", "models", "email_classifier.joblib")


def test_absolute_model_path_is_left_untouched(tmp_path):
    s = Settings(ml_classifier_model_path=str(tmp_path / "x.joblib"))
    assert s.ml_classifier_model_path_resolved == tmp_path / "x.joblib"
