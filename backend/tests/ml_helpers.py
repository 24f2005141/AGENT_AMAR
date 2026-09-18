"""Helpers for the local ML pre-classifier tests.

Everything trains tiny, strongly-separable models into a tmp directory — no
network, no bundled model file, fast.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.ml.email_classifier import EmailMLClassifier, Prediction
from app.ml.training import train_and_save

# A few strongly-separable classes so a fitted LogisticRegression is confident.
TINY_RECORDS: list[dict[str, str]] = [
    *[
        {
            "subject": f"Internship opening {i}",
            "body": "Apply for the software engineering internship. Fill the application form and upload your resume.",
            "sender": "careers@example.com",
            "label": "INTERNSHIP",
        }
        for i in range(6)
    ],
    *[
        {
            "subject": f"Your weekly digest {i}",
            "body": "This week's newsletter digest. You are receiving this because you subscribed. Unsubscribe anytime.",
            "sender": "newsletter@example.com",
            "label": "NEWSLETTER",
        }
        for i in range(6)
    ],
    *[
        {
            "subject": f"Please reply {i}",
            "body": "Following up on my previous email. Please reply and confirm. Awaiting your response.",
            "sender": "person@example.com",
            "label": "REPLY_REQUIRED",
        }
        for i in range(6)
    ],
]


def write_dataset(path: str | Path, records: list[dict[str, str]]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", "utf-8")
    return path


def train_model(
    model_path: str | Path,
    records: list[dict[str, str]] | None = None,
    *,
    data_path: str | Path | None = None,
) -> dict[str, Any]:
    """Train + persist a tiny model, return its metadata."""
    model_path = Path(model_path)
    data_path = Path(data_path) if data_path else model_path.with_name("_train.jsonl")
    write_dataset(data_path, records or TINY_RECORDS)
    return train_and_save(data_path, model_path)


class SpyClassifier:
    """Duck-typed stand-in for :class:`EmailMLClassifier` used in routing tests."""

    def __init__(
        self,
        *,
        available: bool = True,
        prediction: Prediction | None = None,
        raise_error: Exception | None = None,
    ) -> None:
        self._available = available
        self._prediction = prediction or Prediction("INTERNSHIP", 0.95, {"INTERNSHIP": 0.95})
        self._raise = raise_error
        self.calls: list[tuple[str | None, str | None, str | None]] = []

    def is_available(self) -> bool:
        return self._available

    def predict(self, subject: str | None, body: str | None, sender: str | None = None) -> Prediction:
        self.calls.append((subject, body, sender))
        if self._raise is not None:
            raise self._raise
        return self._prediction


__all__ = ["EmailMLClassifier", "Prediction", "SpyClassifier", "TINY_RECORDS", "train_model", "write_dataset"]
