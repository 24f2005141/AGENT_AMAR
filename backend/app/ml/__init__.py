"""Local machine-learning pre-classification layer.

A lightweight, CPU-only scikit-learn pipeline (TF-IDF + LogisticRegression) that
sits *between* the deterministic Triage rules and the configured LLM. It predicts
the **existing** :class:`~app.models.triage.TriageCategory` labels — it does not
introduce new categories or change any API contract.

The model is optional: with no trained artifact on disk the classifier reports
itself unavailable and the Triage Agent keeps its original behaviour
(deterministic result, LLM fallback when uncertain).
"""

from app.ml.email_classifier import (
    EmailMLClassifier,
    MLPredictionError,
    Prediction,
    build_feature_text,
    get_email_ml_classifier,
)

__all__ = [
    "EmailMLClassifier",
    "MLPredictionError",
    "Prediction",
    "build_feature_text",
    "get_email_ml_classifier",
]
