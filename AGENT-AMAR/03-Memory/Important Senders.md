# 🧠 Memory: Important Senders

**Related:** [[Triage Agent]] · [[Priority Agent]] · [[User Preferences]] · [[Classification Rules]]

A structured list of senders that matter. The [[Triage Agent]] and [[Priority Agent]] check every email's sender against this list.

> The backend keeps the queryable copy. This file is the human-readable master list and the seed.

---

## Importance levels

| Level | Effect on priority |
|---|---|
| `CRITICAL` | Floor priority at `HIGH`; +20 score; always notify + monitor |
| `HIGH` | +10 score; strong notify bias |
| `NORMAL` | No adjustment (kept here for context / future learning) |
| `LOW_TRUST` | Treat cautiously; do not auto-raise; may still be promotional |

---

## Sender record format

```
### <Sender name>
- **Email:** <address or pattern, e.g. *@college.edu>
- **Organisation:** <org>
- **Importance:** CRITICAL | HIGH | NORMAL | LOW_TRUST
- **Reason:** <why this sender matters>
- **Categories usually sent:** <e.g. PLACEMENT, EXAM>
- **Last updated:** YYYY-MM-DD
```

---

## Senders

### Placement Cell
- **Email:** placement@college.edu
- **Organisation:** College Training & Placement Office
- **Importance:** CRITICAL
- **Reason:** Sends internship, placement, and job opportunities with hard deadlines.
- **Categories usually sent:** PLACEMENT, INTERNSHIP, JOB_OPPORTUNITY
- **Last updated:** 2026-08-28

### Examination Department
- **Email:** exams@college.edu
- **Organisation:** College Controller of Examinations
- **Importance:** CRITICAL
- **Reason:** Exam schedules, hall tickets, results, revaluation deadlines.
- **Categories usually sent:** EXAM
- **Last updated:** 2026-08-28

### Head of Department
- **Email:** hod.cse@college.edu
- **Organisation:** Department of Computer Science
- **Importance:** HIGH
- **Reason:** Official departmental announcements and academic instructions.
- **Categories usually sent:** FACULTY_ANNOUNCEMENT, ACADEMIC_INFORMATION
- **Last updated:** 2026-08-28

### Course Faculty (pattern)
- **Email:** *.faculty@college.edu
- **Organisation:** College teaching staff
- **Importance:** HIGH
- **Reason:** Assignment instructions, class updates, submission deadlines.
- **Categories usually sent:** ASSIGNMENT, ACADEMIC_INFORMATION, FACULTY_ANNOUNCEMENT
- **Last updated:** 2026-08-28

### College Domain (catch-all pattern)
- **Email:** *@college.edu
- **Organisation:** College / university
- **Importance:** HIGH
- **Reason:** Any official college address is trusted and academically relevant.
- **Categories usually sent:** varies
- **Last updated:** 2026-08-28

### Internshala / LinkedIn Jobs (example external)
- **Email:** jobs@linkedin.com, notifications@internshala.com
- **Organisation:** External job platforms
- **Importance:** HIGH
- **Reason:** Genuine internship/job leads, but also send digests — pair with [[Classification Rules]] to separate real opportunities from marketing.
- **Categories usually sent:** JOB_OPPORTUNITY, INTERNSHIP, NEWSLETTER
- **Last updated:** 2026-08-28

---

## Muted / low-trust senders

### Generic marketing
- **Email:** noreply@*, promo@*, offers@*, deals@*
- **Organisation:** Various
- **Importance:** LOW_TRUST
- **Reason:** Almost always promotional; do not auto-raise priority.
- **Last updated:** 2026-08-28

---

## Maintenance

- When the user consistently acts fast on a new sender, promote them (learned signal from [[User Preferences]] §5).
- Review quarterly. Record changes with a new `Last updated` date.
