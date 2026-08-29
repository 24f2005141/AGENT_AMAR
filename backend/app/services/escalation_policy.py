"""Centralised escalation policy — one place for every threshold.

Phase 10. The [[Reminder Escalation]] vault doc + [[Priority Rules]] §5 are the
source of truth for the ladders; this module is their machine copy. Nothing
here touches the database or sends anything — it is pure, deterministic
"given a priority level and time-remaining, what rung are we on?" logic.

    NORMAL  ── initial "important email" alert (created at processing time)
      ▼
    REMINDER
      ▼
    URGENT
      ▼
    ALARM   ── requires_alarm = true; the defining AGENT AMAR behaviour
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from zoneinfo import ZoneInfo


class EscalationLevel(str, Enum):
    NONE = "NONE"
    NORMAL = "NORMAL"
    REMINDER = "REMINDER"
    URGENT = "URGENT"
    ALARM = "ALARM"


_RANK: dict[EscalationLevel, int] = {
    EscalationLevel.NONE: 0,
    EscalationLevel.NORMAL: 1,
    EscalationLevel.REMINDER: 2,
    EscalationLevel.URGENT: 3,
    EscalationLevel.ALARM: 4,
}

_DEMOTE: dict[EscalationLevel, EscalationLevel] = {
    EscalationLevel.ALARM: EscalationLevel.URGENT,
    EscalationLevel.URGENT: EscalationLevel.REMINDER,
    EscalationLevel.REMINDER: EscalationLevel.NORMAL,
    EscalationLevel.NORMAL: EscalationLevel.NORMAL,
    EscalationLevel.NONE: EscalationLevel.NONE,
}


def rank(level: EscalationLevel | str) -> int:
    return _RANK[EscalationLevel(level)]


def demote(level: EscalationLevel | str) -> EscalationLevel:
    return _DEMOTE[EscalationLevel(level)]


def highest(*levels: EscalationLevel | str) -> EscalationLevel:
    out = EscalationLevel.NONE
    for lv in levels:
        lv = EscalationLevel(lv)
        if _RANK[lv] > _RANK[out]:
            out = lv
    return out


# --- the ladders (mirror of Reminder Escalation.md) --------------------
# Each entry: time-remaining <= threshold  ⇒  at least this level.
# Only CRITICAL (≤5m) and URGENT (≤15m) ever reach ALARM — matching the vault,
# where the "final + repeat + sound" behaviour is CRITICAL/URGENT only.
LADDERS: dict[str, tuple[tuple[timedelta, EscalationLevel], ...]] = {
    "CRITICAL": (
        (timedelta(minutes=30), EscalationLevel.REMINDER),
        (timedelta(minutes=15), EscalationLevel.URGENT),
        (timedelta(minutes=5), EscalationLevel.ALARM),
    ),
    "URGENT": (
        (timedelta(hours=12), EscalationLevel.REMINDER),
        (timedelta(hours=3), EscalationLevel.URGENT),
        (timedelta(hours=1), EscalationLevel.URGENT),
        (timedelta(minutes=15), EscalationLevel.ALARM),
    ),
    "HIGH": (
        (timedelta(hours=24), EscalationLevel.REMINDER),
        (timedelta(hours=6), EscalationLevel.REMINDER),
        (timedelta(hours=1), EscalationLevel.URGENT),
    ),
    "MEDIUM": (
        (timedelta(hours=24), EscalationLevel.REMINDER),
    ),
    "LOW": (),
}

#: Priority levels that are ever allowed to reach an alarm (Reminder
#: Escalation.md — alarm-level alerts are CRITICAL/URGENT territory).
ALARM_ELIGIBLE_PRIORITIES = frozenset({"HIGH", "URGENT", "CRITICAL"})


def ladder_level(priority_level: str, remaining: timedelta) -> EscalationLevel:
    """The escalation rung for ``priority_level`` at ``remaining`` time left.

    ``remaining`` <= 0 (overdue) is handled by the caller, not here.
    """
    hit = EscalationLevel.NONE
    for threshold, level in LADDERS.get((priority_level or "").upper(), ()):
        if remaining <= threshold and _RANK[level] > _RANK[hit]:
            hit = level
    return hit


# --- quiet hours ------------------------------------------------------
@dataclass(frozen=True)
class QuietHours:
    """A local-time window during which non-critical alerts are held back."""

    start_hour: int
    end_hour: int
    tz: str

    def contains(self, instant: datetime) -> bool:
        try:
            local = instant.astimezone(ZoneInfo(self.tz))
        except Exception:  # noqa: BLE001 — bad tz name ⇒ treat as no quiet hours
            return False
        if self.start_hour == self.end_hour:
            return False
        h = local.hour + local.minute / 60.0
        if self.start_hour < self.end_hour:
            return self.start_hour <= h < self.end_hour
        return h >= self.start_hour or h < self.end_hour  # wraps midnight


def quiet_hours_suppress(
    level: EscalationLevel,
    priority_level: str,
    in_quiet_hours: bool,
    *,
    alarm_breaks_quiet_hours_for_critical: bool = True,
) -> bool:
    """Documented policy (Reminder Escalation.md · User Preferences §3):

    * NORMAL / REMINDER  — always respect quiet hours.
    * URGENT             — respect quiet hours (no per-user override is persisted yet).
    * ALARM              — breaks quiet hours **only** for a CRITICAL deadline.
    """
    if not in_quiet_hours:
        return False
    if level in (EscalationLevel.NORMAL, EscalationLevel.REMINDER, EscalationLevel.URGENT):
        return True
    if level == EscalationLevel.ALARM:
        return not (
            alarm_breaks_quiet_hours_for_critical
            and (priority_level or "").upper() == "CRITICAL"
        )
    return False
