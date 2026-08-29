"""Unit tests for app/utils/deadline_parsing.py."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.utils import deadline_parsing as dp

IST = ZoneInfo("Asia/Kolkata")
REF = datetime(2026, 8, 28, 16, 30, tzinfo=IST)  # Friday


def norm(phrase: str, sentence: str | None = None, tz: str = "Asia/Kolkata", locale: str = "DMY"):
    return dp.normalize_phrase(phrase, sentence or phrase, REF, tz, locale)


def test_explicit_named_date():
    r = norm("30 August 2026", "Deadline: 30 August 2026.")
    assert r.dt == datetime(2026, 8, 30, 23, 59, 59, tzinfo=IST)
    assert r.date_only is True


def test_explicit_date_and_time():
    r = norm("30 August 2026", "Submit by 30 August 2026, 6:30 PM.")
    assert r.dt == datetime(2026, 8, 30, 18, 30, tzinfo=IST)
    assert r.date_only is False


def test_today_with_time():
    r = norm("today", "Please submit before 5 PM today.")
    assert r.dt == datetime(2026, 8, 28, 17, 0, tzinfo=IST)
    assert r.ambiguous is False


def test_tomorrow_no_time_is_eod_and_flagged():
    r = norm("tomorrow", "Applications close tomorrow.")
    assert r.dt == datetime(2026, 8, 29, 23, 59, 59, tzinfo=IST)
    assert r.ambiguous is True


def test_this_friday():
    r = norm("this Friday", "Submit this Friday.")
    assert r.dt.date() == datetime(2026, 9, 4).date()


def test_next_monday_is_ambiguous():
    r = norm("next Monday", "Register by next Monday.")
    assert r.dt.date() == datetime(2026, 9, 7).date()
    assert r.ambiguous is True


def test_within_2_hours():
    r = norm("within 2 hours", "Confirm within 2 hours.")
    assert r.dt == datetime(2026, 8, 28, 18, 30, tzinfo=IST)


def test_within_24_hours():
    r = norm("within 24 hours", "Complete within 24 hours.")
    assert r.dt == datetime(2026, 8, 29, 16, 30, tzinfo=IST)


def test_eod():
    r = norm("EOD", "Submit by EOD.")
    assert r.dt == datetime(2026, 8, 28, 23, 59, 59, tzinfo=IST)
    assert r.ambiguous is False


def test_midnight():
    r = norm("midnight", "Form closes at midnight.")
    assert r.dt == datetime(2026, 8, 28, 23, 59, 59, tzinfo=IST)


def test_noon():
    r = norm("noon", "Submit by noon.")
    assert r.dt == datetime(2026, 8, 28, 12, 0, tzinfo=IST)


def test_ambiguous_numeric_uses_locale_and_flags():
    dmy = norm("05/09/2026", "Last date: 05/09/2026.", locale="DMY")
    assert dmy.dt.date() == datetime(2026, 9, 5).date()
    assert dmy.ambiguous is True
    mdy = norm("05/09/2026", "Last date: 05/09/2026.", locale="MDY")
    assert mdy.dt.date() == datetime(2026, 5, 9).date()


def test_unambiguous_numeric_when_day_gt_12():
    r = norm("30/08/2026", "Submit by 30/08/2026.")
    assert r.dt.date() == datetime(2026, 8, 30).date()
    # day 30 forces DD/MM — not format-ambiguous (may still be "no time" flagged)
    assert "DD/MM vs MM/DD" not in (r.reason or "")


def test_iso_date():
    r = norm("2026-09-05", "By 2026-09-05.")
    assert r.dt.date() == datetime(2026, 9, 5).date()


def test_explicit_ist():
    r = norm("before 5 PM IST", "Register before 5 PM IST.")
    assert r.dt == datetime(2026, 8, 28, 17, 0, tzinfo=IST)
    assert r.tz_name == "Asia/Kolkata"


def test_explicit_utc():
    r = norm("before 5 PM UTC", "Submit before 5 PM UTC.", tz="Asia/Kolkata")
    assert r.tz_name == "UTC"
    assert r.dt.utcoffset().total_seconds() == 0


def test_vague_returns_none():
    for p in ("asap", "next week"):
        r = norm(p, f"Please respond {p}.")
        assert r.dt is None
        assert r.ambiguous is True


def test_missing_year_assumes_future_and_flags():
    r = norm("September 1", "Last date to register is September 1.")
    assert r.dt.year == 2026
    assert r.ambiguous is True


# --- classification ------------------------------------------------

@pytest.mark.parametrize(
    "sentence,phrase,expected",
    [
        ("Please submit the form by Friday.", "by Friday", "DEADLINE"),
        ("The interview will be held on Friday.", "on Friday", "EVENT_DATE"),
        ("Your interview is scheduled for Monday.", "for Monday", "EVENT_DATE"),
        ("Our mega sale ends this Sunday.", "this Sunday", "IGNORE"),
        ("Applications close tomorrow.", "tomorrow", "DEADLINE"),
        ("Complete the assessment within 24 hours.", "within 24 hours", "DEADLINE"),
    ],
)
def test_classify_kind(sentence, phrase, expected):
    assert dp.classify_kind(sentence, phrase, low_priority_context=False) == expected


def test_promotional_context_ignores_dates():
    assert dp.classify_kind(
        "Book your seats before September 1 for early-bird pricing.",
        "before September 1",
        low_priority_context=True,
    ) == "IGNORE"


def test_extract_candidates_multiple():
    cands = dp.extract_candidates(
        "Register by September 1 and submit your resume by September 3."
    )
    texts = {c.text.lower() for c in cands}
    assert "september 1" in texts and "september 3" in texts
    assert all(c.kind == "DEADLINE" for c in cands)
