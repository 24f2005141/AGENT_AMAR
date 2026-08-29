"""Deterministic classification rules — the machine copy of the vault.

Source of truth (edit those first, then mirror here):
  * ``03-Memory/Classification Rules.md``  — categories, signals, precedence, edge cases
  * ``03-Memory/Important Senders.md``      — sender importance patterns
  * ``03-Memory/User Preferences.md`` §6    — the two category-affecting overrides

This module holds **data only** (keyword tables, patterns). The scoring and
precedence logic lives in ``triage_agent.py``.
"""

from __future__ import annotations

import fnmatch

from app.models.triage import TriageCategory as C

# ---------------------------------------------------------------------------
# Important senders  (Important Senders.md)
# ---------------------------------------------------------------------------
# (glob pattern, importance level). Ordered most-specific first; the first
# match wins. Importance levels: CRITICAL | HIGH | NORMAL | LOW_TRUST.
IMPORTANT_SENDER_PATTERNS: list[tuple[str, str]] = [
    ("placement@college.edu", "CRITICAL"),
    ("exams@college.edu", "CRITICAL"),
    ("hod.*@college.edu", "HIGH"),
    ("*.faculty@college.edu", "HIGH"),
    ("jobs@linkedin.com", "HIGH"),
    ("notifications@internshala.com", "HIGH"),
    ("*@college.edu", "HIGH"),  # catch-all: any official college address
    ("*noreply@*", "LOW_TRUST"),   # noreply@, updates-noreply@, ...
    ("*no-reply@*", "LOW_TRUST"),
    ("*donotreply@*", "LOW_TRUST"),
    ("promo@*", "LOW_TRUST"),
    ("promo*@*", "LOW_TRUST"),
    ("offers@*", "LOW_TRUST"),
    ("deals@*", "LOW_TRUST"),
    ("marketing@*", "LOW_TRUST"),
    ("newsletter@*", "LOW_TRUST"),
    ("*-digest@*", "LOW_TRUST"),
]

#: Categories a sender "usually sends" — a small confidence nudge when the
#: deterministic pick matches (Important Senders.md "Categories usually sent").
SENDER_EXPECTED_CATEGORIES: list[tuple[str, frozenset[TriageCategory]]] = [
    ("placement@college.edu", frozenset({C.PLACEMENT, C.INTERNSHIP, C.JOB_OPPORTUNITY})),
    ("exams@college.edu", frozenset({C.EXAM})),
    ("hod.*@college.edu", frozenset({C.FACULTY_ANNOUNCEMENT, C.ACADEMIC_INFORMATION})),
    ("*.faculty@college.edu", frozenset({C.ASSIGNMENT, C.ACADEMIC_INFORMATION, C.FACULTY_ANNOUNCEMENT})),
    ("jobs@linkedin.com", frozenset({C.JOB_OPPORTUNITY, C.INTERNSHIP, C.NEWSLETTER})),
    ("notifications@internshala.com", frozenset({C.JOB_OPPORTUNITY, C.INTERNSHIP, C.NEWSLETTER})),
]

TriageCategory = C  # re-export for callers

# ---------------------------------------------------------------------------
# Category keyword signals  (Classification Rules.md "Category definitions & signals")
# ---------------------------------------------------------------------------
# Each entry: (lowercase phrase, weight). Phrases are matched as substrings on
# the normalised "subject + body" text; subject hits are weighted extra by the
# agent. "ppt" from the vault is intentionally omitted (collides with
# "PowerPoint"); "pre-placement talk" / "pre placement" cover the same case.
CATEGORY_KEYWORDS: dict[TriageCategory, list[tuple[str, float]]] = {
    C.INTERNSHIP: [
        ("internship", 3.0), ("intern role", 3.0), ("summer intern", 3.0),
        ("winter intern", 2.5), ("summer training", 2.0), ("industrial training", 2.0),
        ("intern opportunity", 3.0), ("intern position", 2.5), (" sip ", 1.5),
        ("internship opportunity", 3.0),
    ],
    C.PLACEMENT: [
        ("placement drive", 3.0), ("campus placement", 3.0), ("campus recruitment", 3.0),
        ("pre-placement talk", 2.5), ("pre placement", 2.0), ("placement cell", 1.5),
        ("recruitment drive", 2.5), ("company visit", 2.0), ("shortlist", 1.5),
        ("eligibility criteria", 1.0), ("job drive", 2.5), ("hiring drive", 2.5),
    ],
    C.JOB_OPPORTUNITY: [
        ("job opening", 3.0), ("we're hiring", 3.0), ("we are hiring", 3.0),
        ("full-time role", 2.5), ("full time role", 2.5), ("apply now", 1.5),
        ("ctc", 1.5), ("job opportunity", 3.0), ("open position", 2.0),
        ("career opportunity", 2.0),
    ],
    C.ASSIGNMENT: [
        ("assignment", 3.0), ("submission", 2.0), ("submit by", 2.5),
        ("lab record", 2.5), ("coursework", 2.5), ("homework", 2.0),
        ("due date", 1.5), ("last date to submit", 2.5), ("turn in", 1.5),
        ("problem set", 2.0),
    ],
    C.EXAM: [
        ("hall ticket", 3.0), ("admit card", 3.0), ("exam schedule", 3.0),
        ("examination", 2.0), ("time table", 2.0), ("timetable", 2.0),
        ("revaluation", 2.5), ("internal marks", 2.5), ("result", 2.0),
        ("results declared", 3.0), ("semester exam", 2.5), ("mid-term", 2.0),
        ("end sem", 2.0),
    ],
    C.FACULTY_ANNOUNCEMENT: [
        ("circular", 2.5), ("notice", 1.5), ("all students are informed", 3.0),
        ("this is to inform", 2.0), ("department notice", 2.5),
        ("attendance shortage", 2.0), ("undertaking", 1.5), ("kind attention", 1.5),
        ("students are hereby", 2.5),
    ],
    C.REPLY_REQUIRED: [
        ("please confirm", 2.5), ("kindly confirm", 2.5), ("let me know", 2.0),
        ("awaiting your reply", 3.0), ("awaiting your response", 3.0),
        ("please respond", 2.5), ("rsvp", 2.0), ("please reply", 2.5),
        ("your confirmation is required", 3.0), ("can you send", 1.5),
    ],
    C.ACADEMIC_INFORMATION: [
        ("syllabus", 2.5), ("lecture notes", 2.0), ("reference material", 2.0),
        ("class rescheduled", 2.5), ("study material", 2.0), ("course plan", 2.0),
        ("reading list", 1.5), ("class timings", 1.5),
    ],
    C.PROJECT_UPDATE: [
        ("project update", 3.0), ("sprint", 2.0), ("pull request", 2.0),
        ("merge request", 2.0), ("standup", 2.0), ("meeting notes", 2.0),
        ("status update", 2.0), ("group project", 2.0), ("team update", 2.0),
    ],
    C.EVENT: [
        ("webinar", 2.5), ("workshop", 2.5), ("seminar", 2.0), ("hackathon", 2.5),
        ("fest", 1.5), ("guest lecture", 2.5), ("register now", 1.5),
        ("join us", 1.0), ("tech talk", 2.0), ("meetup", 2.0), ("bootcamp", 2.0),
        ("cultural event", 2.0),
    ],
    C.PROMOTIONAL: [
        ("% off", 3.0), ("discount", 2.5), ("sale", 2.0), ("coupon", 2.5),
        ("limited time", 2.5), ("buy now", 2.5), ("shop now", 2.5), ("offer ends", 2.5),
        ("lowest price", 2.5), ("deal of the day", 3.0), ("flat 50%", 3.0),
        ("exclusive offer", 2.5),
    ],
    C.NEWSLETTER: [
        ("newsletter", 3.0), ("weekly digest", 3.0), ("monthly digest", 3.0),
        ("this week's", 1.5), ("in this issue", 2.5), ("view in browser", 2.0),
        ("you are receiving this", 2.0), ("digest", 1.5), ("roundup", 2.0),
    ],
    C.SPAM: [
        ("you have won", 3.0), ("lottery", 3.0), ("claim your prize", 3.0),
        ("verify your account", 2.5), ("account suspended", 2.5),
        ("update your password", 2.0), ("wire transfer", 2.5), ("bitcoin", 1.5),
        ("nigerian prince", 3.0), ("gift card", 1.5), ("act now or", 2.0),
    ],
    C.SOCIAL: [
        ("tagged you", 3.0), ("new follower", 3.0), ("liked your", 3.0),
        ("commented on your", 3.0), ("friend request", 3.0), ("mentioned you", 3.0),
        ("sent you a message", 2.5), ("new connection", 2.5),
    ],
}

#: Domains that strongly imply SOCIAL regardless of body text.
SOCIAL_SENDER_DOMAINS: frozenset[str] = frozenset(
    {
        "facebookmail.com", "facebook.com", "linkedin.com", "instagram.com",
        "twitter.com", "x.com", "mail.instagram.com", "reddit.com",
        "discord.com", "quora.com", "pinterest.com", "snapchat.com",
    }
)
# LinkedIn *jobs* mail is a known exception handled by the agent (job leads).

#: Phrases that (together) signal phishing — SPAM only wins with strong signals.
PHISHING_PHRASES: frozenset[str] = frozenset(
    {
        "verify your account", "account suspended", "update your password",
        "confirm your identity", "unusual sign-in", "click here to verify",
        "your account will be closed", "validate your account",
    }
)

#: "form / apply" indicators -> subcategory application_form + has_form_link.
FORM_INDICATORS: frozenset[str] = frozenset(
    {"application form", "apply here", "fill the form", "fill out the form",
     "google form", "forms.gle", "docs.google.com/forms", "registration form",
     "apply through", "submit your application"}
)

# ---------------------------------------------------------------------------
# User Preferences.md §6 — the two overrides that affect *classification*
# ---------------------------------------------------------------------------
# Rule 2: sender domain @college.edu -> never PROMOTIONAL / SPAM; importance
#         floor MEDIUM.
# Rule 3: noreply@ marketing domains -> lean LOW / PROMOTIONAL.
# (Rules 1 and 4 are about *priority*, not category, and are the Priority
#  Agent's job.)
COLLEGE_DOMAIN = "college.edu"
FORBIDDEN_FOR_COLLEGE_DOMAIN: frozenset[TriageCategory] = frozenset(
    {C.PROMOTIONAL, C.SPAM}
)


def match_sender_importance(email_address: str) -> tuple[str | None, str | None]:
    """Return ``(matched_pattern, importance_level)`` for an address, or ``(None, None)``."""
    addr = (email_address or "").strip().lower()
    if not addr:
        return None, None
    for pattern, level in IMPORTANT_SENDER_PATTERNS:
        if addr == pattern or fnmatch.fnmatch(addr, pattern):
            return pattern, level
    return None, None


def sender_expected_categories(email_address: str) -> frozenset[TriageCategory]:
    addr = (email_address or "").strip().lower()
    for pattern, cats in SENDER_EXPECTED_CATEGORIES:
        if addr == pattern or fnmatch.fnmatch(addr, pattern):
            return cats
    return frozenset()
