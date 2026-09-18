"""Turn user classification feedback into training data (Phase 18).

Part of a **controlled** retraining loop — nothing here retrains or promotes a
model. It only reads the ``classification_feedback`` table (latest valid
correction per email) and shapes it for the existing training format.

Label-space note
----------------
The local ML model predicts the 15-value :class:`~app.models.triage.TriageCategory`
(``INTERNSHIP`` / ``EXAM`` / …). User feedback corrects the **4-value derived
primary bucket** (``IMPORTANT`` vs ``ACTION_REQUIRED`` …). The only correction
that maps cleanly onto a triage label is *"this email needs a reply"* →
``REPLY_REQUIRED``. Those are emitted as training records; the rest are exported
verbatim for human review (they are priority / action signals, not a change of
the email's *kind*).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models import ClassificationFeedback, EmailRecord
from app.models.triage import TriageCategory
from app.repositories import ClassificationFeedbackRepository

_VALID_PRIMARY = {"REPLY_REQUIRED", "ACTION_REQUIRED", "IMPORTANT", "LOW_PRIORITY"}
_MIN_TEXT_CHARS = 12


@dataclass(frozen=True)
class FeedbackExample:
    email_id: str
    subject: str
    body: str  # the stored snippet — the full body is never persisted
    sender: str
    original_primary_category: str
    corrected_primary_category: str
    original_final_category: str | None
    classifier_source: str | None
    ml_confidence: float | None
    llm_used: bool
    feedback_at: str


def _text_ok(subject: str, body: str) -> bool:
    return len((subject + " " + body).strip()) >= _MIN_TEXT_CHARS


def collect_feedback(session: Session) -> list[FeedbackExample]:
    """Latest valid correction per still-existing email.

    Excludes: feedback for deleted emails (the FK join drops them), malformed
    categories, and rows with no usable text.
    """
    repo = ClassificationFeedbackRepository(session)
    out: list[FeedbackExample] = []
    for fb in repo.latest_valid_per_email():
        corrected = (fb.corrected_primary_category or "").upper()
        original = (fb.original_primary_category or "").upper()
        if corrected not in _VALID_PRIMARY or original not in _VALID_PRIMARY:
            continue
        email: EmailRecord | None = session.get(EmailRecord, fb.email_pk)
        if email is None:
            continue
        subject = email.subject or ""
        body = email.snippet or ""
        if not _text_ok(subject, body):
            continue
        out.append(
            FeedbackExample(
                email_id=fb.email_id,
                subject=subject,
                body=body,
                sender=email.sender_email or "",
                original_primary_category=original,
                corrected_primary_category=corrected,
                original_final_category=fb.original_final_category,
                classifier_source=fb.classifier_source,
                ml_confidence=fb.ml_confidence,
                llm_used=bool(fb.llm_used),
                feedback_at=_iso(fb.created_at),
            )
        )
    return out


def to_training_records(examples: list[FeedbackExample]) -> list[dict]:
    """The subset that maps cleanly onto a :class:`TriageCategory` label.

    Currently: ``corrected == REPLY_REQUIRED`` → a positive reply example. A
    stronger mapping (e.g. per-primary sub-models) can extend this without
    changing callers.
    """
    records: list[dict] = []
    for ex in examples:
        if ex.corrected_primary_category == "REPLY_REQUIRED":
            records.append(
                {
                    "subject": ex.subject,
                    "body": ex.body,
                    "sender": ex.sender,
                    "label": TriageCategory.REPLY_REQUIRED.value,
                    "source": "user_feedback",
                }
            )
    return records


def write_feedback_export(path: str | Path, examples: list[FeedbackExample]) -> int:
    """Full JSONL of every collected example (for human review / curation)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for ex in examples:
            fh.write(json.dumps(asdict(ex), ensure_ascii=False) + "\n")
    return len(examples)


def write_training_jsonl(path: str | Path, records: list[dict]) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(records)


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.isoformat() if value.tzinfo else value.isoformat() + "Z"
