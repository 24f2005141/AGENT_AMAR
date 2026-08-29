"""Deterministic deadline / date parsing utilities.

Used by ``app/agents/deadline_agent.py``. Kept separate so the temporal logic
is testable in isolation and reusable.

What it does:
  * extract candidate temporal phrases from text
  * decide whether a phrase reads as a DEADLINE, an EVENT date, or neither
  * normalise explicit dates (locale-aware for ambiguous numeric formats)
  * resolve relative expressions against a **reference instant**
  * preserve the source phrase and report ambiguity

What it does NOT do: compute "time remaining", trigger anything, or call an LLM.

Documented conventions
----------------------
* Reference instant  = the email's ``received_at`` (the agent passes it in).
* Date with no time  -> 23:59:59 in the target timezone, flagged
  ``ambiguity_reason = "no time specified"`` (matches ``Deadline Agent.md``).
* EOD / "end of day" -> 23:59:59 (not flagged — EOD is a defined time).
* "midnight"         -> 23:59:59 of the stated day (end-of-day reading).
* noon               -> 12:00:00 ;  COB / "close of business" -> 17:00:00.
* "next <weekday>"   -> the <weekday> a week after the coming one, flagged
  ambiguous (``Deadline Agent.md`` lists "next Friday" as an ambiguity case).
* "this <weekday>" / "by <weekday>" -> the coming <weekday>.
* Ambiguous numeric date (DD/MM vs MM/DD, both <= 12) -> resolved with
  ``Settings.deadline_date_locale`` ("DMY" default) and flagged.
* "soon" / "asap" / "next week" -> not resolvable -> ``dt = None`` + flag.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


def _du_parse(text: str, default: datetime) -> datetime:
    """Lazy wrapper around dateutil.parser.parse (its tz import is slow on Windows)."""
    from dateutil import parser as du_parser

    return du_parser.parse(text, default=default)


EOD_TIME = time(23, 59, 59)
COB_HOUR = 17

_WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2,
    "wed": 2, "thursday": 3, "thu": 3, "thurs": 3, "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}
_MONTHS = (
    "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|"
    "april|june|july|august|september|october|november|december"
)

DEADLINE_CUES = (
    "deadline", "due ", "due.", "due,", "by ", "before ", "within ", "no later than",
    "not later than", "last date", "last day", "cut-off", "cutoff", "closes",
    "close on", "close tomorrow", "close today", "closing date", " close ",
    "submit by", "apply by", "register by", "register before", "respond by",
    "reply by", "expires", "expiry", "ends on", "ends at", "on or before",
    "latest by", "till ", "until ", "complete by", "finish by", "valid till",
    "valid until", "should reach", "must reach", "last chance",
)

_RELATIVE_WINDOW_RE = re.compile(
    r"\b(?:within\s+(?:the\s+next\s+)?\d+|in\s+\d+\s*(?:hours?|hrs?|days?|weeks?)|"
    r"\d+\s*(?:hours?|hrs?|days?)\s+from\s+now|next\s+\d+\s*(?:hours?|days?))\b",
    re.I,
)
# relative day-words that read as a deadline when a directive is present
_RELATIVE_DAY_RE = re.compile(
    r"\b(?:today|tonight|tomorrow|day after tomorrow|eod|cob|end of (?:the )?day|"
    r"end of week|(?:this|next|coming|by|on|before|for)\s+"
    r"(?:mon|tue|tues|wed|thu|thurs|fri|sat|sun)(?:day)?)\b",
    re.I,
)
EVENT_CUES = (
    "will be held", "will take place", "is scheduled", "scheduled for",
    "scheduled on", "will be conducted", "happening on", "held on", "takes place",
    "join us on", "join us at", "meeting on", "meeting is on", "session on",
    "the interview will", "interview is scheduled", "interview will be",
    "interview is on", "venue", "starts on", "starts at", "begins on",
    "will begin", "will start", "orientation on", "webinar on", "workshop on",
)


@dataclass
class PhraseMatch:
    text: str          # the temporal phrase, verbatim
    sentence: str      # the sentence it appeared in
    kind: str          # "DEADLINE" | "EVENT_DATE" | "IGNORE"


@dataclass
class NormalizedResult:
    dt: datetime | None
    date_only: bool
    ambiguous: bool
    reason: str | None
    tz_name: str


# Split on sentence enders and ';' — but NOT ':' (it breaks "Deadline: ...").
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")

_PHRASE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bwithin\s+(?:the\s+next\s+)?\d+\s*(?:hours?|hrs?|days?|weeks?|business\s+days?)\b", re.I),
    re.compile(r"\bin\s+\d+\s*(?:hours?|hrs?|days?|weeks?)\b", re.I),
    re.compile(r"\b\d+\s*(?:hours?|hrs?|days?)\s+from\s+now\b", re.I),
    re.compile(r"\bnext\s+\d+\s*(?:hours?|days?)\b", re.I),
    re.compile(r"\bday\s+after\s+tomorrow\b", re.I),
    re.compile(r"\bend\s+of\s+(?:the\s+)?day\b|\bend\s+of\s+business(?:\s+day)?\b|\bclose\s+of\s+business\b", re.I),
    re.compile(r"\beod\b|\bcob\b|\beow\b|\bend\s+of\s+week\b", re.I),
    re.compile(r"\b(?:by\s+)?(?:tonight|midnight|noon)\b", re.I),
    re.compile(r"\b(?:by\s+|before\s+)?(?:today|tomorrow)\b", re.I),
    re.compile(r"\b(?:this|next|coming|by|on|before|for)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thurs|fri|sat|sun)\b", re.I),
    re.compile(r"\bnext\s+week\b|\bas\s+soon\s+as\s+possible\b|\ba\.?s\.?a\.?p\.?\b", re.I),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\b"),
    re.compile(rf"\b(?:{_MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b", re.I),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{_MONTHS})\.?(?:,?\s+\d{{4}})?\b", re.I),
    re.compile(r"\b(?:by|before|at)\s+\d{1,2}(?::\d{2})?\s*(?:[ap]\.?\s?m\.?|hours?)\b(?:\s*(?:ist|utc|gmt|est|pst|bst|cet))?", re.I),
    re.compile(r"\b(?:by|before)\s+\d{1,2}:\d{2}\b(?:\s*(?:ist|utc|gmt|est|pst|bst|cet))?", re.I),
]

_USER_DIRECTIVE_RE = re.compile(
    r"\b(submit|register|apply|reply|respond|confirm|complete|upload|fill|pay|send|"
    r"enrol|enroll|acknowledge|rsvp|return|provide|share|you must|you need to|"
    r"you should|required to|please|kindly|make sure)\b",
    re.I,
)
_EXPLICIT_TIME_RE = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s?m\.?\b|\b(\d{1,2}):(\d{2})\b", re.I
)


def split_sentences(text: str) -> list[str]:
    if not text:
        return []
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s and s.strip()]


def has_user_directive(sentence: str) -> bool:
    return bool(_USER_DIRECTIVE_RE.search(sentence))


def classify_kind(sentence: str, phrase: str = "", *, low_priority_context: bool) -> str:
    """DEADLINE / EVENT_DATE / IGNORE for a temporal phrase in ``sentence``."""
    s = sentence.lower()
    has_deadline_cue = any(c in s for c in DEADLINE_CUES)
    has_event_cue = any(c in s for c in EVENT_CUES)
    directive = has_user_directive(sentence)
    # "within N" is inherently deadline-shaped; a relative day-word ("this Friday",
    # "tomorrow", "EOD") is a deadline when the user is being told to do something.
    is_relative_window = bool(_RELATIVE_WINDOW_RE.search(phrase))
    is_relative_day = bool(_RELATIVE_DAY_RE.search(phrase))
    has_deadline_cue = has_deadline_cue or is_relative_window
    if is_relative_day and directive:
        has_deadline_cue = True

    if low_priority_context and not (has_deadline_cue and directive):
        return "IGNORE"
    if has_deadline_cue and directive:
        return "DEADLINE"
    if has_deadline_cue and not has_event_cue:
        return "DEADLINE"
    if has_event_cue and not has_deadline_cue:
        return "EVENT_DATE"
    if has_deadline_cue and has_event_cue:
        return "DEADLINE" if directive else "EVENT_DATE"
    return "IGNORE"


def extract_candidates(text: str, *, low_priority_context: bool = False) -> list[PhraseMatch]:
    out: list[PhraseMatch] = []
    seen: set[tuple[str, str]] = set()
    for sentence in split_sentences(text):
        for pat in _PHRASE_PATTERNS:
            for m in pat.finditer(sentence):
                phrase = m.group(0).strip()
                key = (phrase.lower(), sentence.lower())
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    PhraseMatch(
                        text=phrase,
                        sentence=sentence.strip(),
                        kind=classify_kind(
                            sentence, phrase, low_priority_context=low_priority_context
                        ),
                    )
                )
    return out


# --- normalisation ---------------------------------------------------

def _tz(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


def _tz_from_text(text: str, default_tz: str) -> str:
    p = text.lower()
    if re.search(r"\bist\b", p):
        return "Asia/Kolkata"
    if re.search(r"\b(utc|gmt)\b", p):
        return "UTC"
    if re.search(r"\b(bst)\b", p):
        return "Europe/London"
    if re.search(r"\b(est|edt)\b", p):
        return "America/New_York"
    if re.search(r"\b(pst|pdt)\b", p):
        return "America/Los_Angeles"
    if re.search(r"\b(cet|cest)\b", p):
        return "Europe/Paris"
    return default_tz


def _end_of(dt: datetime) -> datetime:
    return dt.replace(hour=23, minute=59, second=59, microsecond=0)


def _explicit_time(sentence: str) -> tuple[int, int] | None:
    m = _EXPLICIT_TIME_RE.search(sentence)
    if not m:
        return None
    if m.group(1) is not None:
        hour = int(m.group(1)) % 12
        if m.group(3).lower() == "p":
            hour += 12
        return hour, int(m.group(2) or 0)
    return int(m.group(4)), int(m.group(5))


def _with_time_or_eod(day: datetime, sentence: str) -> NormalizedResult:
    hm = _explicit_time(sentence)
    if hm:
        return NormalizedResult(
            day.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0),
            date_only=False, ambiguous=False, reason=None, tz_name=str(day.tzinfo),
        )
    return NormalizedResult(_end_of(day), True, True, "no time specified", str(day.tzinfo))


def _nearest_future_year(month: int, day: int, ref: datetime) -> int:
    try:
        cand = ref.replace(year=ref.year, month=month, day=day)
    except ValueError:
        return ref.year
    return ref.year if cand.date() >= ref.date() else ref.year + 1


def _normalize_numeric(phrase: str, tz_name: str, locale: str) -> NormalizedResult:
    tzinfo = _tz(tz_name)
    iso = re.match(r"^\s*(\d{4})-(\d{2})-(\d{2})\s*$", phrase)
    if iso:
        y, mo, d = map(int, iso.groups())
        try:
            return NormalizedResult(datetime(y, mo, d, tzinfo=tzinfo), True, False, None, tz_name)
        except ValueError:
            return NormalizedResult(None, False, True, f"invalid date '{phrase}'", tz_name)
    m = re.match(r"^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\s*$", phrase)
    if not m:
        return NormalizedResult(None, False, True, "unrecognised numeric date", tz_name)
    a, b, c = int(m.group(1)), int(m.group(2)), int(m.group(3))
    year = c + 2000 if c < 100 else c
    ambiguous, reason = False, None
    if a > 12 >= b:
        day, month = a, b
    elif b > 12 >= a:
        month, day = a, b
    else:
        ambiguous = True
        reason = f"numeric date '{phrase}' is DD/MM vs MM/DD ambiguous (assumed {locale})"
        month, day = (a, b) if locale.upper() == "MDY" else (b, a)
    if c < 100:
        ambiguous = True
        reason = (reason + "; " if reason else "") + "2-digit year"
    try:
        return NormalizedResult(datetime(year, month, day, tzinfo=tzinfo), True, ambiguous, reason, tz_name)
    except ValueError:
        return NormalizedResult(None, False, True, f"invalid date '{phrase}'", tz_name)


def _normalize_named(phrase: str, ref: datetime, tz_name: str) -> NormalizedResult:
    tzinfo = _tz(tz_name)
    has_year = bool(re.search(r"\d{4}", phrase))
    cleaned = re.sub(r"(\d{1,2})(st|nd|rd|th)", r"\1", phrase, flags=re.I)
    try:
        parsed = _du_parse(cleaned, ref.replace(hour=0, minute=0, second=0, microsecond=0))
    except (ValueError, OverflowError):
        return NormalizedResult(None, False, True, f"could not parse '{phrase}'", tz_name)
    dt = parsed if parsed.tzinfo else parsed.replace(tzinfo=tzinfo)
    if not has_year:
        dt = dt.replace(year=_nearest_future_year(dt.month, dt.day, ref))
        return NormalizedResult(dt, True, True, "year not specified (assumed nearest future)", tz_name)
    return NormalizedResult(dt, True, False, None, tz_name)


def normalize_phrase(
    phrase: str,
    sentence: str,
    reference_dt: datetime,
    default_tz: str,
    locale: str = "DMY",
) -> NormalizedResult:
    """Normalise one temporal phrase against ``reference_dt``. Deterministic."""
    p = phrase.lower().strip()
    tz_name = _tz_from_text(f"{phrase} {sentence}", default_tz)
    tzinfo = _tz(tz_name)
    ref = (
        reference_dt.astimezone(tzinfo)
        if reference_dt.tzinfo
        else reference_dt.replace(tzinfo=tzinfo)
    )

    if re.search(r"as\s+soon\s+as\s+possible|a\.?s\.?a\.?p", p) or p in {"soon", "shortly"}:
        return NormalizedResult(None, False, True, "vague urgency ('asap'/'soon')", tz_name)
    if "next week" in p:
        return NormalizedResult(None, False, True, "'next week' — no specific date", tz_name)

    m = re.search(r"(\d+)\s*(hour|hr|day|week|business\s+day)s?", p)
    if m and ("within" in p or "in " in p or "from now" in p or p.startswith("next ")):
        n, unit = int(m.group(1)), m.group(2)
        if unit in ("hour", "hr"):
            return NormalizedResult(ref + timedelta(hours=n), False, False, None, tz_name)
        if unit == "week":
            return NormalizedResult(_end_of(ref + timedelta(weeks=n)), True, False, None, tz_name)
        return NormalizedResult(ref + timedelta(days=n), False, False, None, tz_name)

    if "day after tomorrow" in p:
        return _with_time_or_eod(ref + timedelta(days=2), sentence)
    if re.fullmatch(r"(?:by\s+|before\s+)?tomorrow", p):
        return _with_time_or_eod(ref + timedelta(days=1), sentence)
    if re.fullmatch(r"(?:by\s+|before\s+)?(?:today|tonight)", p):
        res = _with_time_or_eod(ref, sentence)
        if "tonight" in p and res.date_only:
            return NormalizedResult(res.dt, False, False, "'tonight' read as end of day", tz_name)
        return res
    if "end of week" in p or p == "eow":
        days = (6 - ref.weekday()) % 7 or 7
        return NormalizedResult(_end_of(ref + timedelta(days=days)), True, True,
                                "'end of week' interpreted as Sunday", tz_name)
    if re.search(r"end\s+of\s+(?:the\s+)?day|end\s+of\s+business|close\s+of\s+business|\beod\b", p):
        base = ref + timedelta(days=1) if "tomorrow" in sentence.lower() else ref
        return NormalizedResult(_end_of(base), False, False, None, tz_name)
    if "cob" in p:
        return NormalizedResult(ref.replace(hour=COB_HOUR, minute=0, second=0, microsecond=0),
                                False, False, None, tz_name)
    if "midnight" in p:
        return NormalizedResult(_end_of(ref), False, False,
                                "'midnight' read as end of the stated day", tz_name)
    if "noon" in p:
        return NormalizedResult(ref.replace(hour=12, minute=0, second=0, microsecond=0),
                                False, False, None, tz_name)

    wd = re.search(r"(this|next|coming|by|on|before)\s+(mon|tue|tues|wed|thu|thurs|fri|sat|sun)", p)
    if wd:
        qualifier, wname = wd.group(1), wd.group(2)
        delta = (_WEEKDAYS[wname] - ref.weekday()) % 7 or 7
        base = ref + timedelta(days=delta)
        ambiguous, reason = False, None
        if qualifier == "next":
            base = base + timedelta(days=7)
            ambiguous = True
            reason = f"'next {wname}' is ambiguous (may mean the coming {wname})"
        res = _with_time_or_eod(base, sentence)
        return NormalizedResult(res.dt, res.date_only, ambiguous or res.ambiguous,
                                reason or res.reason, tz_name)

    if re.match(r"^\s*\d{4}-\d{2}-\d{2}\s*$", p) or re.match(r"^\s*\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\s*$", p):
        return _finish_date(_normalize_numeric(phrase, tz_name, locale), sentence)
    if re.search(_MONTHS, p):
        return _finish_date(_normalize_named(phrase, ref, tz_name), sentence)

    hm = _explicit_time(p) or _explicit_time(sentence)
    if hm:
        dt = ref.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        if dt < ref:
            dt += timedelta(days=1)
        return NormalizedResult(dt, False, False, None, tz_name)

    return NormalizedResult(None, False, True, f"could not resolve '{phrase}'", tz_name)


def _finish_date(res: NormalizedResult, sentence: str) -> NormalizedResult:
    if res.dt is None:
        return res
    hm = _explicit_time(sentence)
    if hm:
        return NormalizedResult(
            res.dt.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0),
            False, res.ambiguous, res.reason, res.tz_name,
        )
    return NormalizedResult(
        _end_of(res.dt), True, True if not res.ambiguous else res.ambiguous,
        res.reason or "no time specified", res.tz_name,
    )
