"""Load + predict for the local email classifier.

Kept deliberately small:

* :func:`build_feature_text` — turn (subject, body, sender) into the normalized
  string the model was trained on. Training and inference MUST use this same
  function.
* :class:`EmailMLClassifier` — loads a ``joblib`` bundle produced by
  :mod:`app.ml.training`, exposes :meth:`is_available` / :meth:`predict`.
* :func:`get_email_ml_classifier` — cached accessor used by the DI layer.

No model file → :meth:`is_available` is ``False`` and the Triage Agent falls back
to its original deterministic + LLM behaviour. A corrupt / unreadable / schema-
mismatched file is treated the same way (logged once, never raised).
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.core.logging_setup import secure_logger
from app.models.triage import TriageCategory

logger = secure_logger(__name__)

#: Bump when :func:`build_feature_text` changes in a way that invalidates an
#: existing trained model. :class:`EmailMLClassifier` refuses to load a bundle
#: whose ``feature_version`` does not match.
FEATURE_VERSION = "1"

#: Model artifact format version (the joblib bundle layout, not the sklearn one).
MODEL_FORMAT_VERSION = "1"

_WS_RE = re.compile(r"\s+")
_VALID_LABELS = frozenset(c.value for c in TriageCategory)


class MLPredictionError(RuntimeError):
    """Raised by :meth:`EmailMLClassifier.predict` when no model is loaded."""


def _domain_of(sender: str | None) -> str:
    addr = (sender or "").strip().lower()
    return addr.split("@", 1)[1] if "@" in addr else ""


def build_feature_text(subject: str | None, body: str | None, sender: str | None = None) -> str:
    """Normalized combined text fed to the vectorizer.

    Lightweight on purpose — lower-casing, whitespace collapse and a few field
    markers so the model can learn e.g. that a token appeared in the subject or
    that the sender domain matters. Training and prediction share this exactly.
    """
    sender = (sender or "").strip().lower()
    domain = _domain_of(sender)
    parts = [
        f"subject: {subject or ''}",
        f"sender: {sender}",
        f"domain: {domain}",
        f"body: {body or ''}",
    ]
    text = " \n ".join(parts).lower()
    return _WS_RE.sub(" ", text).strip()


@dataclass(frozen=True)
class Prediction:
    """Result of :meth:`EmailMLClassifier.predict`."""

    label: str
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)


class EmailMLClassifier:
    """Thin wrapper around a persisted scikit-learn pipeline."""

    def __init__(
        self,
        model_path: str | Path | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._path = Path(model_path) if model_path is not None else (
            self._settings.ml_classifier_model_path_resolved
        )
        self._pipeline: Any | None = None
        self._metadata: dict[str, Any] = {}
        self._load_error: str | None = None
        self._load()

    # -- loading ------------------------------------------------------

    def _load(self) -> None:
        if not self._path.is_file():
            self._load_error = "model_file_missing"
            return
        try:
            import joblib

            bundle = joblib.load(self._path)
            pipeline = bundle["pipeline"] if isinstance(bundle, dict) else bundle
            metadata = bundle.get("metadata", {}) if isinstance(bundle, dict) else {}

            if not hasattr(pipeline, "predict_proba"):
                raise ValueError("pipeline has no predict_proba")

            feature_version = str(metadata.get("feature_version", ""))
            if feature_version and feature_version != FEATURE_VERSION:
                raise ValueError(
                    f"feature_version mismatch: model={feature_version!r} "
                    f"expected={FEATURE_VERSION!r}"
                )

            classes = [str(c) for c in getattr(pipeline, "classes_", [])]
            unknown = sorted(set(classes) - _VALID_LABELS)
            if unknown:
                raise ValueError(f"model predicts unknown labels: {unknown}")

            self._pipeline = pipeline
            self._metadata = dict(metadata)
        except Exception as exc:  # noqa: BLE001 — a bad model must never crash the app
            self._pipeline = None
            self._load_error = type(exc).__name__
            logger.warning(
                "Local ML classifier disabled — could not load %s (%s: %s)",
                self._path,
                type(exc).__name__,
                exc,
            )
            return

        logger.info(
            "Local ML classifier loaded: %s (labels=%d, trained_at=%s)",
            self._path.name,
            len(self._metadata.get("labels", [])),
            self._metadata.get("trained_at", "unknown"),
        )

    # -- public API -------------------------------------------------

    def is_available(self) -> bool:
        return self._pipeline is not None

    @property
    def model_path(self) -> Path:
        return self._path

    @property
    def load_error(self) -> str | None:
        return self._load_error

    @property
    def metadata(self) -> dict[str, Any]:
        return dict(self._metadata)

    def predict(
        self, subject: str | None, body: str | None, sender: str | None = None
    ) -> Prediction:
        if self._pipeline is None:
            raise MLPredictionError(
                f"No local ML model loaded ({self._load_error or 'unavailable'})."
            )
        text = build_feature_text(subject, body, sender)
        proba = self._pipeline.predict_proba([text])[0]
        classes = [str(c) for c in self._pipeline.classes_]
        pairs = sorted(zip(classes, (float(p) for p in proba)), key=lambda kv: kv[1], reverse=True)
        top_label, top_conf = pairs[0]
        return Prediction(
            label=top_label,
            confidence=round(top_conf, 4),
            probabilities={label: round(p, 4) for label, p in pairs},
        )


# ---------------------------------------------------------------------------
# Cached accessor — one classifier instance per (resolved) model path.
# ---------------------------------------------------------------------------

_LOCK = threading.Lock()


@lru_cache(maxsize=8)
def _cached_classifier(path_str: str) -> EmailMLClassifier:
    return EmailMLClassifier(path_str)


def get_email_ml_classifier(settings: Settings | None = None) -> EmailMLClassifier | None:
    """Return a shared classifier, or ``None`` when the feature is disabled.

    ``None`` (disabled) means the Triage Agent skips the ML step entirely — no
    file access, no import cost.
    """
    settings = settings or get_settings()
    if not settings.ml_classifier_enabled:
        return None
    with _LOCK:
        return _cached_classifier(str(settings.ml_classifier_model_path_resolved))


def reset_cache() -> None:
    """Drop cached classifiers (used by tests / after retraining in-process)."""
    with _LOCK:
        _cached_classifier.cache_clear()
