"""Deterministic priority scoring — the engine behind the Priority Agent.

All numbers here mirror ``03-Memory/Priority Rules.md`` (§2 factors, §4 buckets,
§1 level bands). The vault is the source of truth; change it first, then here.

Pure functions only: no LLM, no I/O, no Gmail. Time arithmetic (proximity) is
done here deterministically — never by the LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from app.models.priority import PriorityLevel, ProximityBucket, ScoreFactor
from app.models.triage import LOW_BAND_CATEGORIES  # PROMOTIONAL/NEWSLETTER/SPAM/SOCIAL

# --- category bands (User Preferences §2 / Priority Rules §6) ---------------
HIGH_VALUE_CATEGORIES = frozenset(
    {"INTERNSHIP", "PLACEMENT", "JOB_OPPORTUNITY", "ASSIGNMENT", "EXAM",
     "FACULTY_ANNOUNCEMENT", "REPLY_REQUIRED"}
)
_OPPORTUNITY = frozenset({"INTERNSHIP", "PLACEMENT", "JOB_OPPORTUNITY"})
_LOW_BAND = {c.value if hasattr(c, "value") else c for c in LOW_BAND_CATEGORIES}

# --- level bands (Priority Rules §1) --------------------------------------
_LEVEL_BANDS: list[tuple[int, PriorityLevel]] = [
    (90, PriorityLevel.CRITICAL),
    (75, PriorityLevel.URGENT),
    (55, PriorityLevel.HIGH),
    (30, PriorityLevel.MEDIUM),
    (0, PriorityLevel.LOW),
]
_LEVEL_RANK = {
    PriorityLevel.LOW: 0, PriorityLevel.MEDIUM: 1, PriorityLevel.HIGH: 2,
    PriorityLevel.URGENT: 3, PriorityLevel.CRITICAL: 4,
}
_RANK_LEVEL = {v: k for k, v in _LEVEL_RANK.items()}

# --- proximity thresholds (Priority Rules §4) --------------------------
_ONE_HOUR = 3600
_ONE_DAY = 86400
_THREE_DAYS = 259200


@dataclass
class ScoringInputs:
    category: str
    action_required: bool
    reply_requested: bool
    event_registration: bool
    has_form_attachment: bool
    urgency_language: bool
    proximity: ProximityBucket
    deadline_present: bool
    deadline_ambiguous: bool          # Deadline Agent ambiguity_flag on the primary
    deadline_unresolved: bool         # ambiguous AND no normalized datetime
    deadline_is_past: bool
    sender_importance: str | None     # CRITICAL | HIGH | NORMAL | LOW_TRUST | None


@dataclass
class ScoreResult:
    base_score: int
    breakdown: list[ScoreFactor] = field(default_factory=list)


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def compute_proximity(
    deadline_iso: str | None,
    now: datetime,
    default_tz_name: str = "UTC",
) -> tuple[ProximityBucket, int | None, bool]:
    """Map ``deadline - now`` onto a proximity bucket. Deterministic.

    Returns ``(bucket, time_remaining_seconds, is_past)``. Both datetimes are
    forced timezone-aware first — naive/aware are never compared.
    """
    if not deadline_iso:
        return ProximityBucket.NONE, None, False
    try:
        deadline = datetime.fromisoformat(deadline_iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return ProximityBucket.NONE, None, False

    tzinfo = _tz(default_tz_name)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=tzinfo)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tzinfo)

    remaining = int((deadline - now).total_seconds())
    if remaining < 0:
        return ProximityBucket.OVERDUE, remaining, True
    if remaining <= _ONE_HOUR:
        return ProximityBucket.WITHIN_1H, remaining, False
    if remaining <= _ONE_DAY:
        return ProximityBucket.WITHIN_24H, remaining, False
    if remaining <= _THREE_DAYS:
        return ProximityBucket.WITHIN_72H, remaining, False
    return ProximityBucket.LATER, remaining, False


def score(inp: ScoringInputs, *, ambiguous_deadline_factor: float = 0.7) -> ScoreResult:
    """Sum the deterministic factors from Priority Rules §2. Clamp to 0-100."""
    bd: list[ScoreFactor] = []

    def add(factor: str, points: float, detail: str | None = None) -> None:
        if points:
            bd.append(ScoreFactor(factor=factor, points=points, detail=detail))

    # --- action ---
    if inp.action_required:
        add("action_required", 30)
    if inp.reply_requested:
        add("reply_explicitly_required", 15)
    if inp.event_registration:
        add("event_with_registration", 8)
    if inp.has_form_attachment:
        add("form_or_official_attachment", 5)

    # --- deadline proximity (Priority Rules §2 + the ambiguity rule) ---
    if inp.deadline_present:
        if inp.deadline_unresolved:
            add("possible_deadline_unresolved", 10,
                "ambiguity_flag set and no concrete datetime")
        else:
            prox_points = {
                ProximityBucket.WITHIN_1H: 40,
                ProximityBucket.WITHIN_24H: 25,
                ProximityBucket.WITHIN_72H: 12,
                ProximityBucket.LATER: 5,
            }.get(inp.proximity, 0)
            if inp.proximity == ProximityBucket.OVERDUE:
                prox_points = 35 if inp.action_required else 5
            if inp.deadline_ambiguous and prox_points:
                reduced = round(prox_points * ambiguous_deadline_factor)
                add(f"deadline_{inp.proximity.value.lower()}_ambiguous", reduced,
                    f"proximity points reduced x{ambiguous_deadline_factor} (ambiguity_flag)")
            elif prox_points:
                add(f"deadline_{inp.proximity.value.lower()}", prox_points)

    # --- category (Priority Rules §2) ---
    cat = inp.category
    if cat in _OPPORTUNITY:
        add("internship_placement_or_job", 20)
    elif cat in ("ASSIGNMENT", "EXAM"):
        add("assignment_or_exam", 18)
    elif cat in ("FACULTY_ANNOUNCEMENT", "ACADEMIC_INFORMATION"):
        add("faculty_or_academic", 15)
    elif cat == "PROMOTIONAL":
        add("promotional_email", -30)
    elif cat == "NEWSLETTER":
        add("newsletter", -20)
    elif cat == "SOCIAL":
        add("social_notification", -20)
    elif cat == "SPAM":
        add("spam", -40)

    # --- sender importance (Important Senders.md) ---
    if inp.sender_importance == "CRITICAL":
        add("important_sender_critical", 20)
    elif inp.sender_importance == "HIGH":
        add("important_sender_high", 10)
    elif inp.sender_importance == "LOW_TRUST":
        add("low_trust_sender", -10)

    # --- explicit urgency language (not for low-band categories) ---
    if inp.urgency_language and cat not in _LOW_BAND:
        add("explicit_urgency_language", 5)

    raw = sum(f.points for f in bd)
    return ScoreResult(base_score=int(max(0, min(100, round(raw)))), breakdown=bd)


def score_to_level(score_value: int) -> PriorityLevel:
    for threshold, level in _LEVEL_BANDS:
        if score_value >= threshold:
            return level
    return PriorityLevel.LOW


def clamp_level(level: PriorityLevel, *, floor: PriorityLevel | None = None,
                ceiling: PriorityLevel | None = None) -> PriorityLevel:
    r = _LEVEL_RANK[level]
    if floor is not None:
        r = max(r, _LEVEL_RANK[floor])
    if ceiling is not None:
        r = min(r, _LEVEL_RANK[ceiling])
    return _RANK_LEVEL[r]


def level_at_least(a: PriorityLevel, b: PriorityLevel) -> bool:
    return _LEVEL_RANK[a] >= _LEVEL_RANK[b]
