"""Request / response models for user classification feedback (Phase 18)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.decision import PrimaryCategory


class ClassificationFeedbackRequest(BaseModel):
    """``POST /api/v1/emails/{email_id}/classification-feedback`` body.

    ``category`` is the corrected canonical inbox bucket. Any value outside the
    four :class:`PrimaryCategory` members is a ``422``.
    """

    model_config = ConfigDict(extra="forbid")

    category: PrimaryCategory


class ClassificationFeedbackOut(BaseModel):
    """One recorded correction (history / audit view)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email_id: str
    original_primary_category: str
    corrected_primary_category: str
    original_final_category: str | None = None
    classifier_source: str | None = None
    ml_confidence: float | None = None
    llm_used: bool = False
    email_classified_at: datetime | None = None
    created_at: datetime
