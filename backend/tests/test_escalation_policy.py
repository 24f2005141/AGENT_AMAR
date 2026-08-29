"""Phase 10 — centralised escalation policy (pure, no DB)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.escalation_policy import (
    EscalationLevel,
    QuietHours,
    demote,
    highest,
    ladder_level,
    quiet_hours_suppress,
    rank,
)

H = timedelta(hours=1)
M = timedelta(minutes=1)


# --- ladders --------------------------------------------------------------

@pytest.mark.parametrize(
    ("priority", "remaining", "expected"),
    [
        ("CRITICAL", 40 * M, EscalationLevel.NONE),
        ("CRITICAL", 25 * M, EscalationLevel.REMINDER),
        ("CRITICAL", 12 * M, EscalationLevel.URGENT),
        ("CRITICAL", 4 * M, EscalationLevel.ALARM),
        ("URGENT", 13 * H, EscalationLevel.NONE),
        ("URGENT", 10 * H, EscalationLevel.REMINDER),
        ("URGENT", 2 * H, EscalationLevel.URGENT),
        ("URGENT", 10 * M, EscalationLevel.ALARM),
        ("HIGH", 30 * H, EscalationLevel.NONE),
        ("HIGH", 20 * H, EscalationLevel.REMINDER),
        ("HIGH", 30 * M, EscalationLevel.URGENT),  # HIGH never reaches ALARM
        ("MEDIUM", 20 * H, EscalationLevel.REMINDER),
        ("MEDIUM", 10 * M, EscalationLevel.REMINDER),
        ("LOW", 1 * M, EscalationLevel.NONE),
    ],
)
def test_ladder_level(priority, remaining, expected):
    assert ladder_level(priority, remaining) == expected


def test_high_priority_never_alarms():
    for r in (60 * M, 5 * M, 1 * M):
        assert ladder_level("HIGH", r) != EscalationLevel.ALARM


def test_rank_and_demote_and_highest():
    assert rank(EscalationLevel.ALARM) > rank(EscalationLevel.URGENT) > rank(EscalationLevel.NORMAL)
    assert demote(EscalationLevel.ALARM) == EscalationLevel.URGENT
    assert demote(EscalationLevel.NORMAL) == EscalationLevel.NORMAL
    assert highest("NORMAL", "URGENT", "REMINDER") == EscalationLevel.URGENT
    assert highest() == EscalationLevel.NONE


# --- quiet hours ---------------------------------------------------------

def test_quiet_hours_wraps_midnight():
    qh = QuietHours(23, 7, "Asia/Kolkata")
    # 01:30 IST -> inside
    assert qh.contains(datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc)) is True
    # 17:30 IST -> outside
    assert qh.contains(datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)) is False


def test_quiet_hours_bad_tz_is_no_quiet_hours():
    assert QuietHours(23, 7, "Not/AZone").contains(datetime.now(timezone.utc)) is False


def test_quiet_hours_policy_is_deterministic():
    # NORMAL / REMINDER / URGENT held back; ALARM only breaks through for CRITICAL
    assert quiet_hours_suppress(EscalationLevel.NORMAL, "HIGH", True) is True
    assert quiet_hours_suppress(EscalationLevel.REMINDER, "URGENT", True) is True
    assert quiet_hours_suppress(EscalationLevel.URGENT, "URGENT", True) is True
    assert quiet_hours_suppress(EscalationLevel.ALARM, "URGENT", True) is True
    assert quiet_hours_suppress(EscalationLevel.ALARM, "CRITICAL", True) is False
    # nothing is suppressed outside quiet hours
    assert quiet_hours_suppress(EscalationLevel.REMINDER, "HIGH", False) is False
