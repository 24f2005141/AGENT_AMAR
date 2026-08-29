# 🧠 Memory: Classification Rules

**Related:** [[Triage Agent]] · [[User Preferences]] · [[Important Senders]] · [[Priority Rules]]

The source of truth for how the [[Triage Agent]] assigns a category. Edit here to change classification behaviour.

---

## Category definitions & signals

| Category | Assign when… | Strong keywords / signals |
|---|---|---|
| `INTERNSHIP` | Offers or asks to apply for an internship | "internship", "intern role", "summer training", "SIP", application form + "intern" |
| `PLACEMENT` | Campus placement drive, company visit, eligibility notice | "placement drive", "campus recruitment", "pre-placement talk", "PPT", "shortlist" |
| `JOB_OPPORTUNITY` | Full/part-time job opening, off-campus role | "job opening", "we're hiring", "apply now", "CTC", "full-time" |
| `ASSIGNMENT` | Coursework to submit | "assignment", "submission", "lab record", "due", "submit by", faculty sender |
| `EXAM` | Exam logistics or results | "exam", "hall ticket", "time table", "result", "revaluation", "internal marks" |
| `FACULTY_ANNOUNCEMENT` | Official notice from faculty/dept | faculty/HOD sender, "circular", "notice", "all students are informed" |
| `REPLY_REQUIRED` | Sender explicitly waits on the user's response | "please confirm", "let me know", "awaiting your reply", "RSVP", direct question to user |
| `ACADEMIC_INFORMATION` | Academic content, no urgent action | "syllabus", "notes", "reference", "class rescheduled" without a deadline |
| `PROJECT_UPDATE` | Team/group/project status | "project", "sprint", "PR", "meeting notes", teammate sender |
| `EVENT` | Event/workshop/club, optional attendance | "webinar", "workshop", "fest", "register", "join us", date + venue |
| `PROMOTIONAL` | Selling something | "sale", "% off", "coupon", "limited time", "buy now" |
| `NEWSLETTER` | Recurring digest the user subscribed to | "newsletter", "weekly digest", "unsubscribe" prominent, no personalisation |
| `SPAM` | Unsolicited / suspicious / phishing | mismatched sender domain, urgency + link + credential ask, lottery/prize |
| `SOCIAL` | Social platform notification | "tagged you", "new follower", "liked your", instagram/facebook/x domains |
| `OTHER` | Genuine content that fits nothing above | — (set low confidence, orchestrator flags) |

---

## Precedence (when multiple categories seem to fit)

1. **User overrides** in [[User Preferences]] §6 (highest).
2. **Opportunity categories** `INTERNSHIP` / `PLACEMENT` / `JOB_OPPORTUNITY` beat `EVENT`, `NEWSLETTER`, `ACADEMIC_INFORMATION`.
3. `EXAM` / `ASSIGNMENT` beat `FACULTY_ANNOUNCEMENT` when a specific task + date is present.
4. `REPLY_REQUIRED` is **additive** — if a reply is needed AND it's an internship email, category = `INTERNSHIP`, and [[Action Agent]] records the reply action. Use `REPLY_REQUIRED` as the primary category only when no other category dominates.
5. `SPAM` beats everything **only** with strong phishing signals; never mark a `@college.edu` sender as `SPAM` (see [[User Preferences]] §6 rule 2).

---

## Worked examples

### Example A — clear internship
> From: placement@college.edu — "Summer Internship 2026: fill the form by Sep 2, 6:30 PM"

- Category: `INTERNSHIP`, subcategory `application_form`
- `importance_estimate`: HIGH
- `further_analysis_required`: true
- Confidence: 0.96

### Example B — newsletter that mentions jobs
> From: notifications@internshala.com — "Your weekly internship digest: 25 new internships"

- Category: `NEWSLETTER` (digest, not a specific opportunity), subcategory `job_digest`
- `importance_estimate`: LOW–MEDIUM
- `further_analysis_required`: false
- Note: if a later email links a **specific** application → `INTERNSHIP`.

### Example C — faculty circular with a deadline
> From: hod.cse@college.edu — "All students must submit the anti-ragging undertaking by Friday"

- Category: `FACULTY_ANNOUNCEMENT` (task + date) → but `ASSIGNMENT`-like; keep `FACULTY_ANNOUNCEMENT`, subcategory `compliance_form`
- `further_analysis_required`: true (action + deadline indicators)

### Example D — ambiguous
> From: unknown@gmail.com — "Regarding your submission"

- Category: `OTHER`, confidence 0.35
- Orchestrator sets `needs_human_review = true`

---

## Edge cases

| Situation | Rule |
|---|---|
| Opportunity email from an unknown external sender | Classify by content, but cap confidence at 0.7 and let [[Priority Agent]] weigh sender trust |
| Same thread, new reply | Re-classify only if the new message changes the task; otherwise inherit thread category |
| Forwarded email | Classify by the **original** content, not the forwarder's note |
| Multi-topic email | Choose the highest-priority topic as primary; note others in `reasoning_summary` |
| Non-English body | Translate internally, classify normally, keep `language` from [[Email Schema]] |
| Calendar invite (.ics) | `EVENT`, subcategory `calendar_invite`; [[Deadline Agent]] extracts the event time |

---

## Override logic

- A [[User Preferences]] §6 override can **force** a category or **forbid** one.
- [[Important Senders]] can raise `importance_estimate` but does **not** change the category.
- If an override and the model disagree, the override wins and the [[Agent Activity Log]] records `override_applied = true`.

---

## Maintenance

Add new keywords and examples here as misclassifications are found. Keep the examples section growing — it doubles as few-shot material for the [[Triage Agent]] prompt.

### Backend deviations from this doc (Phase 3)

The machine copy lives in `backend/app/agents/triage_rules.py`. Two deliberate
deviations, kept here for the record:

- **`ppt`** (from the `PLACEMENT` signals) is **not** used as a keyword — it
  collides with "PowerPoint". `pre-placement talk` / `pre placement` cover the
  same case.
- The `noreply@*` / marketing-address patterns in [[Important Senders]] are
  matched as **substring/suffix** patterns (`*noreply@*`, `*-digest@*`, …) so
  addresses like `updates-noreply@linkedin.com` are also treated as `LOW_TRUST`.
- A `LOW_TRUST` sender claiming a HIGH-band category (opportunity/exam/assignment/
  faculty) is forced below the review threshold — real placement/faculty mail
  does not come from bulk `noreply@` addresses.
