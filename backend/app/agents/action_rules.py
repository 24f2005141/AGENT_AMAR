"""Deterministic action-detection rules — data only.

Source of truth: ``04-Schemas/Action Schema.md`` (action types) and
``01-Agents/Action Agent.md`` (example triggers). Scoring / negation / merge
logic lives in ``action_agent.py``.

All phrases are lowercase and matched as substrings on individual clauses of
the cleaned body + subject.
"""

from __future__ import annotations

import re

from app.models.action import ActionType as A

# ---------------------------------------------------------------------------
# Explicit action phrases  (phrase, weight, is_explicit)
# ---------------------------------------------------------------------------
ACTION_PHRASES: dict[A, list[tuple[str, float, bool]]] = {
    A.FORM_SUBMISSION: [
        ("fill the form", 3.0, True), ("fill out the form", 3.0, True),
        ("fill up the form", 3.0, True), ("complete the form", 3.0, True),
        ("submit the form", 3.0, True), ("fill the google form", 3.2, True),
        ("fill this form", 3.0, True), ("fill in the form", 3.0, True),
        ("application form", 2.0, True), ("apply through the link", 2.5, True),
        ("apply using the form", 3.0, True), ("apply here", 2.2, True),
        ("apply via", 2.2, True), ("submit your application", 2.8, True),
        ("submit the application", 2.8, True), ("fill the below form", 3.0, True),
        # implied
        ("applications are open", 1.6, False), ("applications are now open", 1.8, False),
        ("interested students may apply", 1.8, False),
        ("interested candidates can apply", 1.8, False),
        ("interested students are requested to apply", 2.0, False),
        ("register your interest", 1.6, False), ("last date to apply", 1.6, False),
    ],
    A.REPLY: [
        ("please reply", 3.0, True), ("kindly reply", 3.0, True),
        ("reply to this email", 3.0, True), ("respond to this email", 3.0, True),
        ("please respond", 2.8, True), ("kindly respond", 2.8, True),
        ("please confirm", 2.8, True), ("kindly confirm", 2.8, True),
        ("please acknowledge", 2.5, True), ("revert with", 2.5, True),
        ("let us know", 2.0, True), ("let me know", 2.0, True),
        ("awaiting your response", 2.8, True), ("awaiting your reply", 2.8, True),
        ("confirm your attendance", 2.8, True), ("confirm your availability", 2.8, True),
        ("confirm your participation", 2.8, True), ("rsvp", 2.2, True),
        ("reply with your", 2.8, True), ("get back to us", 2.2, True),
    ],
    A.REGISTRATION: [
        ("register for", 3.0, True), ("registration is required", 3.0, True),
        ("registration is mandatory", 3.2, True), ("please register", 3.0, True),
        ("kindly register", 3.0, True), ("register now", 2.6, True),
        ("register using", 2.8, True), ("sign up for", 2.6, True),
        ("sign-up for", 2.6, True), ("enrol for", 2.6, True), ("enroll for", 2.6, True),
        ("registration link", 2.2, True), ("register before", 2.8, True),
        ("registration deadline", 2.0, True), ("register yourself", 2.8, True),
    ],
    A.DOCUMENT_UPLOAD: [
        ("upload your", 3.0, True), ("upload the", 2.6, True),
        ("attach your", 2.8, True), ("attach the", 2.4, True),
        ("share your resume", 3.0, True), ("submit your resume", 3.0, True),
        ("send your resume", 2.8, True), ("send your cv", 2.8, True),
        ("upload your resume", 3.2, True), ("attach your resume", 3.2, True),
        ("upload the following documents", 3.0, True),
        ("submit the required documents", 2.8, True), ("upload your marksheet", 3.0, True),
        ("provide the following documents", 2.6, True),
    ],
    A.PAYMENT: [
        ("pay the fee", 3.2, True), ("make the payment", 3.2, True),
        ("complete the payment", 3.2, True), ("registration fee of", 2.6, True),
        ("pay a fee", 2.8, True), ("fee payment", 2.4, True),
        ("pay rs", 2.6, True), ("pay inr", 2.6, True), ("pay the amount", 3.0, True),
        ("payment is required", 3.0, True), ("pay online", 2.4, True),
        ("submit the fee", 2.8, True),
    ],
    A.ATTEND_EVENT: [
        ("attend the", 2.8, True), ("you are required to attend", 3.2, True),
        ("mandatory to attend", 3.2, True), ("please attend", 3.0, True),
        ("join the meeting", 3.0, True), ("join the session", 3.0, True),
        ("join us on", 2.4, True), ("be present", 2.8, True),
        ("report to", 2.4, True), ("attendance is mandatory", 3.2, True),
        ("attendance is compulsory", 3.2, True), ("all students must attend", 3.2, True),
        ("participate in the", 2.2, True), ("present yourself", 2.6, True),
    ],
    A.COMPLETE_ASSIGNMENT: [
        ("submit the assignment", 3.2, True), ("submit assignment", 3.2, True),
        ("submit your assignment", 3.2, True), ("complete the assignment", 3.0, True),
        ("assignment submission", 2.4, True), ("submit the lab record", 3.0, True),
        ("submit the lab report", 3.0, True), ("turn in your", 2.6, True),
        ("submit your coursework", 3.0, True), ("submit the project report", 2.8, True),
        ("hand in the assignment", 3.0, True), ("complete and submit", 2.6, True),
    ],
    A.READ_AND_ACKNOWLEDGE: [
        ("please read", 2.6, True), ("kindly read", 2.6, True),
        ("read carefully", 2.8, True), ("go through the", 2.2, True),
        ("please go through", 2.6, True), ("review the attached", 2.6, True),
        ("please review", 2.4, True), ("for your review", 1.8, True),
        ("please acknowledge receipt", 3.0, True), ("acknowledge this", 2.6, True),
        ("please note the following", 2.0, True), ("take note of", 1.8, True),
        ("please find attached", 1.4, False), ("please find the attached", 1.4, False),
    ],
}

# Generic imperative verbs (weak, implied) mapped to a type when nothing
# stronger matched in the same clause.
GENERIC_IMPERATIVES: list[tuple[str, A, float]] = [
    ("apply", A.FORM_SUBMISSION, 1.4),
    ("register", A.REGISTRATION, 1.4),
    ("submit", A.COMPLETE_ASSIGNMENT, 1.2),
    ("upload", A.DOCUMENT_UPLOAD, 1.4),
    ("attend", A.ATTEND_EVENT, 1.4),
    ("reply", A.REPLY, 1.2),
    ("respond", A.REPLY, 1.2),
    ("confirm", A.REPLY, 1.2),
    ("pay", A.PAYMENT, 1.4),
]

# ---------------------------------------------------------------------------
# Context patterns
# ---------------------------------------------------------------------------

# Whole-email "nothing to do" markers -> action_required = false outright.
NO_ACTION_MARKERS: list[re.Pattern[str]] = [
    re.compile(r"\bno action (is )?(required|needed|necessary)\b", re.I),
    re.compile(r"\bno further action\b", re.I),
    re.compile(r"\bthis is (an|a) (automated|auto-generated|system-generated)\b", re.I),
    re.compile(r"\bfor your information only\b", re.I),
    re.compile(r"\bthis email is for information\b", re.I),
    re.compile(r"\bplease do not reply\b.*\bno action\b", re.I),
]

# Per-clause negation of an action ("do not reply", "need not register").
NEGATION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(do not|don't|dont|please do not|kindly do not|must not|should not|no need to|need not|not required to|do not have to)\b", re.I),
    re.compile(r"\bthis is a no-?reply\b", re.I),
    re.compile(r"\bunmonitored (mailbox|inbox)\b", re.I),
]

# Per-clause "already done" markers. Deliberately permissive about the words
# between the auxiliary and the past participle ("has already been submitted").
COMPLETION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(has|have|had|is|was|been|already|successfully)\b[\w\s,'-]{0,30}?\b"
        r"(submitted|registered|applied|paid|completed|recorded|processed|accepted|received|confirmed)\b",
        re.I,
    ),
    re.compile(r"\bwe have received your\b", re.I),
    re.compile(r"\bthank you for (applying|registering|your submission|your payment|your response|submitting)\b", re.I),
    re.compile(r"\bthis is (a|an) (confirmation|acknowledgement|receipt)\b", re.I),
    re.compile(r"\b(registration|application|payment|submission) (is |was )?(now )?(confirmed|complete|successful)\b", re.I),
    re.compile(r"\bno (further )?action (is )?(required|needed)\b", re.I),
]

# When a clause is negated or marks completion but contains no exact action
# phrase, these keyword -> action-type hints tell us which action is being
# cancelled / reported as already done.
CONTEXT_TYPE_HINTS: list[tuple[str, tuple[A, ...]]] = [
    ("assignment", (A.COMPLETE_ASSIGNMENT,)),
    ("coursework", (A.COMPLETE_ASSIGNMENT,)),
    ("lab record", (A.COMPLETE_ASSIGNMENT,)),
    ("application", (A.FORM_SUBMISSION,)),
    ("applied", (A.FORM_SUBMISSION,)),
    ("registration", (A.REGISTRATION,)),
    ("registered", (A.REGISTRATION,)),
    ("payment", (A.PAYMENT,)),
    ("paid", (A.PAYMENT,)),
    ("fee", (A.PAYMENT,)),
    ("resume", (A.DOCUMENT_UPLOAD,)),
    ("cv", (A.DOCUMENT_UPLOAD,)),
    ("document", (A.DOCUMENT_UPLOAD,)),
    ("upload", (A.DOCUMENT_UPLOAD,)),
    ("form", (A.FORM_SUBMISSION,)),
    ("submission", (A.FORM_SUBMISSION, A.COMPLETE_ASSIGNMENT)),
    ("submitted", (A.FORM_SUBMISSION, A.COMPLETE_ASSIGNMENT, A.DOCUMENT_UPLOAD)),
    ("reply", (A.REPLY,)),
    ("respond", (A.REPLY,)),
    ("response", (A.REPLY,)),
    ("replied", (A.REPLY,)),
    ("attend", (A.ATTEND_EVENT,)),
    ("meeting", (A.ATTEND_EVENT,)),
]

# Per-clause conditional markers ("reply if…", "only if you…").
CONDITIONAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(only )?if you (have|need|face|require|wish|want)\b", re.I),
    re.compile(r"\bin case (you|of)\b", re.I),
    re.compile(r"\bshould you (have|need|wish|require)\b", re.I),
    re.compile(r"\bif (required|needed|interested|applicable)\b", re.I),
    re.compile(r"\bfeel free to\b", re.I),
]

# Verbatim deadline-hint grab (NOT parsed — copied for the Deadline Agent).
DEADLINE_HINT_RE = re.compile(
    r"\b(?:by|before|on or before|no later than|due (?:on|by)?|deadline[:\s]|last date[:\s]|"
    r"within|not later than)\s+[^.\n;]{2,60}",
    re.IGNORECASE,
)

# Link relevance hints per action type (for target_link selection).
LINK_HINTS: dict[A, tuple[str, ...]] = {
    A.FORM_SUBMISSION: ("form", "forms.gle", "docs.google.com/forms", "apply", "application", "typeform"),
    A.REGISTRATION: ("register", "registration", "signup", "sign-up", "rsvp", "event", "eventbrite", "unstop"),
    A.DOCUMENT_UPLOAD: ("upload", "drive.google.com", "form"),
    A.PAYMENT: ("pay", "payment", "razorpay", "paytm", "stripe", "checkout"),
    A.ATTEND_EVENT: ("meet.google.com", "zoom.us", "teams.microsoft.com", "webex"),
}


def split_clauses(text: str) -> list[str]:
    """Split text into rough clauses for per-clause context checks."""
    if not text:
        return []
    parts = re.split(r"(?<=[.!?;:\n])\s+|\n+", text)
    return [p.strip() for p in parts if p and p.strip()]
