"""Train + persist the local email classifier.

Explicit, offline, CPU-only. Nothing here runs at application startup — call it
from the command line:

    python -m app.ml.train                    # uses data/training/email_training_data.jsonl
    python -m app.ml.train --sample           # uses the bundled starter dataset
    python -m app.ml.train --data path.jsonl --model out.joblib

Pipeline: ``TfidfVectorizer`` (word 1-2 grams) + ``LogisticRegression``. Both
give calibrated-enough ``predict_proba`` for the confidence gate in
``ml_classifier_threshold`` — no separate calibration step needed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import _BACKEND_DIR
from app.ml.email_classifier import (
    FEATURE_VERSION,
    MODEL_FORMAT_VERSION,
    build_feature_text,
)
from app.models.triage import TriageCategory

DEFAULT_DATA_PATH = _BACKEND_DIR / "data" / "training" / "email_training_data.jsonl"
SAMPLE_DATA_PATH = _BACKEND_DIR / "data" / "training" / "email_training_data.sample.jsonl"
DEFAULT_MODEL_PATH = _BACKEND_DIR / "data" / "models" / "email_classifier.joblib"

_VALID_LABELS = frozenset(c.value for c in TriageCategory)
#: Below this a model is almost certainly under-trained; training still succeeds
#: but prints a loud warning.
MIN_RECOMMENDED_SAMPLES = 40
MIN_RECOMMENDED_PER_LABEL = 3


class TrainingDataError(ValueError):
    """The training dataset is missing, malformed, or has invalid labels."""


@dataclass(frozen=True)
class TrainingRecord:
    subject: str
    body: str
    sender: str
    label: str

    @property
    def text(self) -> str:
        return build_feature_text(self.subject, self.body, self.sender)


def load_training_data(path: str | Path) -> list[TrainingRecord]:
    """Read a JSONL file of ``{subject, body, sender, label}`` records."""
    path = Path(path)
    if not path.is_file():
        raise TrainingDataError(f"Training data file not found: {path}")

    records: list[TrainingRecord] = []
    for lineno, raw in enumerate(path.read_text("utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TrainingDataError(f"{path}:{lineno}: invalid JSON ({exc})") from exc
        if not isinstance(obj, dict):
            raise TrainingDataError(f"{path}:{lineno}: expected a JSON object")
        label = str(obj.get("label", "")).strip()
        if not label:
            raise TrainingDataError(f"{path}:{lineno}: missing 'label'")
        records.append(
            TrainingRecord(
                subject=str(obj.get("subject", "") or ""),
                body=str(obj.get("body", "") or ""),
                sender=str(obj.get("sender", "") or ""),
                label=label,
            )
        )

    if not records:
        raise TrainingDataError(f"{path}: no usable records")
    return records


def validate_labels(records: list[TrainingRecord]) -> None:
    """Every label must be an existing :class:`TriageCategory` value."""
    unknown = sorted({r.label for r in records if r.label not in _VALID_LABELS})
    if unknown:
        raise TrainingDataError(
            "Training data contains labels that are not existing TriageCategory "
            f"values: {unknown}. Valid labels: {sorted(_VALID_LABELS)}"
        )


def build_pipeline() -> Any:
    """TF-IDF + LogisticRegression. Imported lazily so the app never needs sklearn."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    return Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    lowercase=True,
                    ngram_range=(1, 2),
                    min_df=1,
                    max_df=0.95,
                    sublinear_tf=True,
                    strip_accents="unicode",
                ),
            ),
            (
                "clf",
                # C is deliberately high: with short email text and a 15-class
                # softmax, the default (C=1) produces very flat probabilities and
                # nothing ever clears ML_CLASSIFIER_THRESHOLD. C≈30 sharpens the
                # distribution without hurting cross-validation accuracy (see
                # `python -m app.ml.evaluate`). class_weight balances small /
                # skewed real datasets.
                LogisticRegression(
                    max_iter=2000,
                    class_weight="balanced",
                    C=30.0,
                ),
            ),
        ]
    )


def train(records: list[TrainingRecord]) -> Any:
    """Fit and return a pipeline. Does not touch disk."""
    validate_labels(records)
    pipeline = build_pipeline()
    x = [r.text for r in records]
    y = [r.label for r in records]
    pipeline.fit(x, y)
    return pipeline


def _evaluate(pipeline: Any, records: list[TrainingRecord]) -> dict[str, float]:
    """Cheap self-consistency metrics (training-set accuracy + optional CV)."""
    x = [r.text for r in records]
    y = [r.label for r in records]
    metrics: dict[str, float] = {}
    try:
        metrics["train_accuracy"] = round(float(pipeline.score(x, y)), 4)
    except Exception:  # noqa: BLE001
        pass

    label_counts = {label: y.count(label) for label in set(y)}
    if len(records) >= 2 * len(label_counts) and min(label_counts.values()) >= 2:
        try:
            from sklearn.model_selection import cross_val_score

            folds = max(2, min(5, min(label_counts.values())))
            scores = cross_val_score(build_pipeline(), x, y, cv=folds)
            metrics["cv_accuracy_mean"] = round(float(scores.mean()), 4)
            metrics["cv_folds"] = float(folds)
        except Exception:  # noqa: BLE001
            pass
    return metrics


def build_metadata(records: list[TrainingRecord], metrics: dict[str, float]) -> dict[str, Any]:
    labels = sorted({r.label for r in records})
    per_label = {label: sum(1 for r in records if r.label == label) for label in labels}
    try:
        import sklearn

        sklearn_version = sklearn.__version__
    except Exception:  # noqa: BLE001
        sklearn_version = "unknown"
    return {
        "model_version": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
        "model_format_version": MODEL_FORMAT_VERSION,
        "feature_version": FEATURE_VERSION,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "num_samples": len(records),
        "labels": labels,
        "samples_per_label": per_label,
        "sklearn_version": sklearn_version,
        "algorithm": "tfidf(1,2)+logreg",
        "metrics": metrics,
    }


def train_and_save(
    data_path: str | Path,
    model_path: str | Path = DEFAULT_MODEL_PATH,
) -> dict[str, Any]:
    """Full training run: load → validate → fit → evaluate → persist.

    Returns the metadata dict (also written next to the model as ``*_metadata.json``).
    """
    import joblib

    records = load_training_data(data_path)
    validate_labels(records)
    pipeline = train(records)
    metrics = _evaluate(pipeline, records)
    metadata = build_metadata(records, metrics)

    model_path = Path(model_path)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"pipeline": pipeline, "metadata": metadata}, model_path)
    meta_path = model_path.with_name(model_path.stem + "_metadata.json")
    meta_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), "utf-8")

    warnings: list[str] = []
    if metadata["num_samples"] < MIN_RECOMMENDED_SAMPLES:
        warnings.append(
            f"only {metadata['num_samples']} samples "
            f"(recommended >= {MIN_RECOMMENDED_SAMPLES})"
        )
    thin = {
        label: n
        for label, n in metadata["samples_per_label"].items()
        if n < MIN_RECOMMENDED_PER_LABEL
    }
    if thin:
        warnings.append(f"thin labels (< {MIN_RECOMMENDED_PER_LABEL} samples): {thin}")
    metadata["warnings"] = warnings
    return metadata
