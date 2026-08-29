"""Unit tests for app/utils/priority_scoring.py (the deterministic engine)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models.priority import PriorityLevel, ProximityBucket
from app.utils import priority_scoring as ps

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 8, 28, 16, 0, tzinfo=IST)


# --- proximity (STEP 3, 26, 27) ------------------------------------------

@pytest.mark.parametrize(
    "delta,expected",
    [
        (timedelta(minutes=-30), ProximityBucket.OVERDUE),
        (timedelta(minutes=30), ProximityBucket.WITHIN_1H),
        (timedelta(hours=5), ProximityBucket.WITHIN_24H),
        (timedelta(hours=40), ProximityBucket.WITHIN_72H),
        (timedelta(days=10), ProximityBucket.LATER),
    ],
)
def test_proximity_buckets(delta, expected):
    dl = (NOW + delta).isoformat()
    bucket, remaining, is_past = ps.compute_proximity(dl, NOW, "Asia/Kolkata")
    assert bucket == expected
    assert is_past is (expected == ProximityBucket.OVERDUE)
    assert remaining == int(delta.total_seconds())


def test_proximity_none_when_no_deadline():
    assert ps.compute_proximity(None, NOW)[0] == ProximityBucket.NONE


def test_proximity_handles_naive_now():
    naive = datetime(2026, 8, 28, 16, 0)  # no tzinfo
    dl = "2026-08-28T16:30:00+05:30"
    bucket, _, _ = ps.compute_proximity(dl, naive, "Asia/Kolkata")
    assert bucket == ProximityBucket.WITHIN_1H  # no naive/aware crash


def test_proximity_handles_naive_deadline_string():
    bucket, _, _ = ps.compute_proximity("2026-08-28T17:00:00", NOW, "Asia/Kolkata")
    assert bucket == ProximityBucket.WITHIN_1H


# --- scoring factors (STEP 2, Priority Rules §2) ----------------------

def _inp(**over):
    base = dict(
        category="OTHER", action_required=False, reply_requested=False,
        event_registration=False, has_form_attachment=False, urgency_language=False,
        proximity=ProximityBucket.NONE, deadline_present=False, deadline_ambiguous=False,
        deadline_unresolved=False, deadline_is_past=False, sender_importance=None,
    )
    base.update(over)
    return ps.ScoringInputs(**base)


def test_action_required_points():
    assert ps.score(_inp(action_required=True)).base_score == 30


def test_within_1h_points():
    r = ps.score(_inp(action_required=True, deadline_present=True, proximity=ProximityBucket.WITHIN_1H))
    assert r.base_score == 70  # 30 + 40


def test_placement_plus_critical_sender():
    r = ps.score(_inp(category="PLACEMENT", sender_importance="CRITICAL"))
    assert r.base_score == 40  # 20 + 20


def test_promotional_is_negative():
    assert ps.score(_inp(category="PROMOTIONAL")).base_score == 0  # -30 clamped


def test_ambiguous_unresolved_deadline_gives_flat_10():
    r = ps.score(_inp(deadline_present=True, deadline_ambiguous=True, deadline_unresolved=True,
                      proximity=ProximityBucket.NONE))
    assert any(f.factor == "possible_deadline_unresolved" and f.points == 10 for f in r.breakdown)


def test_ambiguous_resolved_deadline_reduces_proximity():
    full = ps.score(_inp(deadline_present=True, proximity=ProximityBucket.WITHIN_24H))
    reduced = ps.score(_inp(deadline_present=True, deadline_ambiguous=True,
                            proximity=ProximityBucket.WITHIN_24H))
    assert reduced.base_score < full.base_score


def test_overdue_with_action_is_35():
    r = ps.score(_inp(action_required=True, deadline_present=True, proximity=ProximityBucket.OVERDUE))
    assert r.base_score == 65  # 30 + 35


def test_clamp_0_100():
    r = ps.score(_inp(category="PLACEMENT", action_required=True, sender_importance="CRITICAL",
                      deadline_present=True, proximity=ProximityBucket.WITHIN_1H,
                      has_form_attachment=True))
    assert r.base_score == 100


# --- level mapping (Priority Rules §1) --------------------------------

@pytest.mark.parametrize(
    "value,level",
    [(95, PriorityLevel.CRITICAL), (80, PriorityLevel.URGENT), (60, PriorityLevel.HIGH),
     (40, PriorityLevel.MEDIUM), (10, PriorityLevel.LOW), (0, PriorityLevel.LOW)],
)
def test_score_to_level(value, level):
    assert ps.score_to_level(value) == level


def test_clamp_level_floor_and_ceiling():
    assert ps.clamp_level(PriorityLevel.LOW, floor=PriorityLevel.MEDIUM) == PriorityLevel.MEDIUM
    assert ps.clamp_level(PriorityLevel.CRITICAL, ceiling=PriorityLevel.URGENT) == PriorityLevel.URGENT
