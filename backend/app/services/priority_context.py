"""Priority context provider — the memory adapter for the Priority Agent.

STEP 11 of the phase brief: the Priority Agent must not be coupled to how the
memory is stored. It asks a :class:`PriorityContext` for:

  * sender importance   (``03-Memory/Important Senders.md``)
  * user overrides       (``03-Memory/User Preferences.md`` §6)
  * category priority band (``User Preferences.md`` §2)
  * notification levels  (``User Preferences.md`` §3)

The current implementation (:class:`StaticPriorityContext`) mirrors those
markdown files as Python constants. A future ``DbPriorityContext`` can replace
it without touching ``priority_agent.py``.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.agents.triage_rules import COLLEGE_DOMAIN, match_sender_importance
from app.models.priority import PriorityLevel

# --- User Preferences.md §6 — explicit overrides ------------------------
# Only the parts that affect *priority*. Each returns a forced floor/ceiling
# level (or None) and whether it also forces notify/monitor.


@dataclass(frozen=True)
class UserOverride:
    name: str
    floor_level: PriorityLevel | None = None
    ceiling_level: PriorityLevel | None = None
    force_notify: bool | None = None
    force_monitor: bool | None = None


@dataclass(frozen=True)
class OverrideMatch:
    override: UserOverride
    reason: str


class PriorityContext(ABC):
    @abstractmethod
    def sender_importance(self, email_address: str) -> str | None:
        """CRITICAL | HIGH | NORMAL | LOW_TRUST, or None if not listed."""

    @abstractmethod
    def user_overrides(self, *, category: str, subject: str, body: str,
                       sender_email: str) -> list[OverrideMatch]:
        """Explicit User Preferences §6 rules that apply to this email."""

    @abstractmethod
    def category_band(self, category: str) -> str:
        """'HIGH' | 'MEDIUM' | 'LOW' — the user's priority band for a category."""

    @abstractmethod
    def notify_levels(self) -> set[PriorityLevel]:
        """Levels at which a notification is allowed (User Preferences §3)."""


# --- User Preferences.md §2 — priority categories ----------------------
_HIGH_BAND = {
    "INTERNSHIP", "PLACEMENT", "JOB_OPPORTUNITY", "ASSIGNMENT", "EXAM",
    "FACULTY_ANNOUNCEMENT", "REPLY_REQUIRED",
}
_MEDIUM_BAND = {"ACADEMIC_INFORMATION", "PROJECT_UPDATE", "EVENT", "OTHER"}
_LOW_BAND = {"PROMOTIONAL", "NEWSLETTER", "SPAM", "SOCIAL"}

_INTERNSHIP_PLACEMENT_RE = re.compile(r"\b(internship|placement)\b", re.I)
_MARKETING_NOREPLY_RE = re.compile(
    r"^(?:no-?reply|donotreply|newsletter|promo\w*|offers?|deals?|marketing)@", re.I
)


class StaticPriorityContext(PriorityContext):
    """Vault-backed (constants) implementation — development default."""

    def sender_importance(self, email_address: str) -> str | None:
        _pattern, level = match_sender_importance(email_address or "")
        return level

    def category_band(self, category: str) -> str:
        if category in _HIGH_BAND:
            return "HIGH"
        if category in _LOW_BAND:
            return "LOW"
        return "MEDIUM"

    def notify_levels(self) -> set[PriorityLevel]:
        return {PriorityLevel.HIGH, PriorityLevel.URGENT, PriorityLevel.CRITICAL}

    def user_overrides(self, *, category: str, subject: str, body: str,
                       sender_email: str) -> list[OverrideMatch]:
        matches: list[OverrideMatch] = []
        text = f"{subject}\n{body}"
        addr = (sender_email or "").strip().lower()
        domain = addr.split("@")[-1] if "@" in addr else ""
        is_college = domain == COLLEGE_DOMAIN or domain.endswith("." + COLLEGE_DOMAIN)

        # §6 rule 1 — any email mentioning "internship"/"placement" -> min URGENT.
        # Not applied to low-band categories: a social/marketing/spam email that
        # merely contains the word is not an opportunity (clarification recorded
        # in User Preferences §6.1).
        if category not in _LOW_BAND and (
            _INTERNSHIP_PLACEMENT_RE.search(text) or category in {"INTERNSHIP", "PLACEMENT"}
        ):
            matches.append(OverrideMatch(
                UserOverride("pref_internship_placement_min_urgent",
                             floor_level=PriorityLevel.URGENT),
                "email mentions internship/placement (User Preferences §6.1)",
            ))

        # §6 rule 2 — @college.edu -> never PROMOTIONAL/SPAM, minimum MEDIUM
        if is_college:
            matches.append(OverrideMatch(
                UserOverride("pref_college_domain_min_medium",
                             floor_level=PriorityLevel.MEDIUM),
                "sender is a @college.edu address (User Preferences §6.2)",
            ))

        # §6 rule 3 — marketing no-reply address -> force LOW, no notification
        if _MARKETING_NOREPLY_RE.match(addr) and not is_college:
            matches.append(OverrideMatch(
                UserOverride("pref_marketing_noreply_force_low",
                             ceiling_level=PriorityLevel.LOW, force_notify=False),
                "sender is a marketing no-reply address (User Preferences §6.3)",
            ))

        # §6 rule 4 — EXAM category -> always notify, always monitor
        if category == "EXAM":
            matches.append(OverrideMatch(
                UserOverride("pref_exam_always_notify_monitor",
                             force_notify=True, force_monitor=True),
                "EXAM category (User Preferences §6.4)",
            ))
        return matches


def get_priority_context() -> PriorityContext:
    """Factory — swap for a DB-backed context later."""
    return StaticPriorityContext()
