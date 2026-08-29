# 🤖 AGENT AMAR

> An AI-powered multi-agent email intelligence system that monitors Gmail, identifies important emails, extracts actions and deadlines, prioritizes tasks, manages reminders, and delivers alerts through a Flutter application.

AGENT AMAR transforms an ordinary Gmail inbox into an intelligent productivity system.

Instead of manually digging through hundreds of emails, the system incrementally monitors new messages, processes them through specialized AI agents, extracts useful information, and presents only what requires attention.

---

# 📌 Features

## 📧 Intelligent Gmail Monitoring

- Connect a Gmail account using Google OAuth 2.0.
- Establishes a baseline when Gmail monitoring starts.
- Historical unread emails are not automatically processed during baseline creation.
- Incrementally detects new emails after the baseline.
- Avoids repeatedly processing the same emails.
- Supports Gmail History API based synchronization.
- Handles expired Gmail history by safely re-baselining.

---

## 🤖 Multi-Agent Email Processing

Incoming emails can pass through a structured multi-agent pipeline.

### 1. Triage Agent

Determines the category and relevance of an email.

Examples include:

- Academic
- Opportunity
- Placement
- Internship
- Announcement
- Informational
- Other

Low-confidence classifications can optionally be escalated to an LLM.

---

### 2. Action Agent

Extracts actionable tasks from an email.

Example:

> "Please submit the project report before Friday."

Possible extracted action:

```text
Submit project report