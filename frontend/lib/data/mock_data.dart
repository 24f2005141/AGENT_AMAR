import '../models/agent_analysis.dart';
import '../models/agent_trace.dart';
import '../models/email.dart';
import '../models/notification_event.dart';
import '../models/reminder.dart';
import '../models/user_state.dart';

class MockData {
  static final DateTime now = DateTime.now();

  static List<Email> getInitialEmails() {
    // Relative times for realistic live demonstration
    final DateTime today5pm = DateTime(now.year, now.month, now.day, 17, 0);
    final DateTime tomorrowMidnight = DateTime(now.year, now.month, now.day + 1, 23, 59);
    final DateTime thisFriday5pm = DateTime(now.year, now.month, now.day + (5 - now.weekday + 7) % 7, 17, 0);

    return [
      // Scenario 1 & 5: Critical Placement Application (Dominant item)
      Email(
        id: 'email_tcs_01',
        senderName: 'Placement Cell',
        senderEmail: 'placements@university.edu.in',
        subject: 'TCS Internship Application - Action Required',
        snippet: 'Applications for the TCS Digital & Ninja 2026 Internship drive close today at 5:00 PM. Mandatory form for CSE, ECE & IT students.',
        body: '''Dear Students,

Tata Consultancy Services (TCS) has officially opened the application window for the 2026 Digital Internship program.

ELIGIBILITY:
- B.Tech CSE / IT / ECE / Data Science (2026 & 2027 graduating batches)
- Minimum CGPA: 7.0 with no standing arrears

MANDATORY INSTRUCTIONS:
1. Complete the university placement verification form: https://forms.university.edu.in/tcs-internship-2026
2. Upload your verified ATS resume (PDF format, max 2MB).
3. Ensure your TCS NextStep Portal CT/DT Reference Number is accurately entered.

DEADLINE:
Today at 5:00 PM IST sharp. Late submissions or incorrect form entries will strictly not be forwarded to the campus recruiting panel.

Best regards,
Head of Placements & Training
University Career Development Cell''',
        receivedAt: now.subtract(const Duration(minutes: 25)),
        isUnread: true,
        labels: ['INBOX', 'IMPORTANT', 'PLACEMENT'],
        analysis: AgentAnalysis(
          category: 'Placement',
          priority: PriorityLevel.critical,
          actionRequired: true,
          actionType: 'FORM_SUBMISSION',
          actionDescription: 'Complete TCS application form & upload resume',
          deadline: today5pm,
          confidence: 0.98,
          reasoningSummary: 'High-stakes placement internship application closing today at 5:00 PM. Requires immediate resume and form submission.',
          traces: [
            AgentTrace(
              agentName: 'Mail Intake Agent',
              status: 'ok',
              confidence: 1.0,
              summary: 'Ingested raw email from Placement Cell <placements@university.edu.in>. Normalized headers & content.',
              timestamp: now.subtract(const Duration(minutes: 24, seconds: 48)),
              method: 'deterministic',
            ),
            AgentTrace(
              agentName: 'Triage Agent',
              status: 'ok',
              confidence: 0.97,
              summary: 'Category identified as Placement/Internship. High importance sender matched against whitelist.',
              timestamp: now.subtract(const Duration(minutes: 24, seconds: 46)),
              method: 'nlp_classifier',
            ),
            AgentTrace(
              agentName: 'Action Agent',
              status: 'ok',
              confidence: 0.95,
              summary: 'Detected mandatory FORM_SUBMISSION with external Google Form and ATS resume requirement.',
              timestamp: now.subtract(const Duration(minutes: 24, seconds: 44)),
              method: 'rule_parser',
            ),
            AgentTrace(
              agentName: 'Deadline Agent',
              status: 'ok',
              confidence: 0.99,
              summary: 'Extracted explicit timestamp "Today at 5:00 PM IST" (normalized: ${today5pm.toIso8601String()}).',
              timestamp: now.subtract(const Duration(minutes: 24, seconds: 43)),
              method: 'temporal_extractor',
            ),
            AgentTrace(
              agentName: 'Priority Agent',
              status: 'ok',
              confidence: 0.96,
              summary: 'Assigned CRITICAL priority score (95/100) due to imminent deadline threshold (< 1 hour remaining).',
              timestamp: now.subtract(const Duration(minutes: 24, seconds: 42)),
              method: 'scoring_engine',
            ),
            AgentTrace(
              agentName: 'AMAR Orchestrator',
              status: 'ok',
              confidence: 0.98,
              summary: 'Synthesized multi-agent analysis. Enabled proactive deadline monitoring & escalation triggers.',
              timestamp: now.subtract(const Duration(minutes: 24, seconds: 40)),
              method: 'orchestrator_decision',
            ),
          ],
        ),
        userState: const UserState(
          isViewed: false,
          isCompleted: false,
          isMonitoringActive: true,
        ),
      ),

      // Scenario 2: Robotics Assignment Submission (High Priority)
      Email(
        id: 'email_robotics_02',
        senderName: 'Prof. Sharma',
        senderEmail: 'sharma.robotics@university.edu.in',
        subject: 'Robotics Assignment 3 - Kinematics Code Submission',
        snippet: 'Assignment 3 is due tomorrow at 11:59 PM. Please upload your Jupyter Notebook and PDF derivation to Canvas.',
        body: '''Hello Class,

This is a reminder that Assignment 3 (Forward and Inverse Kinematics for 6-DOF Manipulator) is due tomorrow at 11:59 PM.

Submission Guidelines:
1. Include all Jupyter Notebook (.ipynb) code files with simulation plots.
2. Include a clean PDF report explaining your DH parameter calculations.
3. Submit as a single zip archive on Canvas under Assignment 3.

No extensions will be provided beyond the grace period.

Regards,
Prof. R. Sharma
Department of Mechanical & Robotics Engineering''',
        receivedAt: now.subtract(const Duration(hours: 3)),
        isUnread: false,
        labels: ['INBOX', 'ACADEMICS'],
        analysis: AgentAnalysis(
          category: 'Academics',
          priority: PriorityLevel.high,
          actionRequired: true,
          actionType: 'CODE_SUBMISSION',
          actionDescription: 'Submit Robotics Kinematics code on Canvas',
          deadline: tomorrowMidnight,
          confidence: 0.94,
          reasoningSummary: 'Course assignment with mandatory code and report submission due tomorrow night.',
          traces: [
            AgentTrace(
              agentName: 'Mail Intake Agent',
              status: 'ok',
              confidence: 1.0,
              summary: 'Ingested email from Prof. Sharma.',
              timestamp: now.subtract(const Duration(hours: 3)),
            ),
            AgentTrace(
              agentName: 'Triage Agent',
              status: 'ok',
              confidence: 0.95,
              summary: 'Classified as Academics / Assignment reminder.',
              timestamp: now.subtract(const Duration(hours: 2, minutes: 59)),
            ),
            AgentTrace(
              agentName: 'Action Agent',
              status: 'ok',
              confidence: 0.92,
              summary: 'Action detected: Code and PDF report submission.',
              timestamp: now.subtract(const Duration(hours: 2, minutes: 58)),
            ),
            AgentTrace(
              agentName: 'Deadline Agent',
              status: 'ok',
              confidence: 0.98,
              summary: 'Deadline normalized to tomorrow 23:59 IST.',
              timestamp: now.subtract(const Duration(hours: 2, minutes: 57)),
            ),
            AgentTrace(
              agentName: 'Priority Agent',
              status: 'ok',
              confidence: 0.91,
              summary: 'Assigned HIGH priority score (78/100).',
              timestamp: now.subtract(const Duration(hours: 2, minutes: 56)),
            ),
          ],
        ),
        userState: const UserState(
          isViewed: true,
          isCompleted: false,
          isMonitoringActive: true,
        ),
      ),

      // Scenario 3: Faculty Announcement (Medium Priority, No Action)
      Email(
        id: 'email_faculty_03',
        senderName: 'Prof. K. Rao',
        senderEmail: 'dean.academics@university.edu.in',
        subject: 'Revised Mid-Semester Examination Timetable - Autumn 2026',
        snippet: 'The academic council has shifted mid-semester examinations by one week. The updated slot schedule is attached.',
        body: '''Dear Students and Faculty,

Please take note of the revised mid-semester examination schedule for the Autumn 2026 session. 

Key changes:
- Examinations will now commence from September 14, 2026.
- Practical lab assessments will precede theory exams during the first week of September.
- The detailed slot matrix is available on the student portal.

Please plan your preparation accordingly.

Office of Dean Academics
University Academic Council''',
        receivedAt: now.subtract(const Duration(hours: 5)),
        isUnread: false,
        labels: ['INBOX', 'ANNOUNCEMENT'],
        analysis: AgentAnalysis(
          category: 'Faculty Announcement',
          priority: PriorityLevel.medium,
          actionRequired: false,
          reasoningSummary: 'Official informational announcement regarding updated midterm examination dates. No reply or submission required.',
          traces: [
            AgentTrace(
              agentName: 'Triage Agent',
              status: 'ok',
              confidence: 0.99,
              summary: 'Identified informational faculty broadcast. No action needed.',
              timestamp: now.subtract(const Duration(hours: 5)),
            ),
          ],
        ),
        userState: const UserState(
          isViewed: true,
          isCompleted: false,
        ),
      ),

      // Scenario 6: Hackathon Registration (Medium Priority, Action Required)
      Email(
        id: 'email_acm_04',
        senderName: 'ACM Student Chapter',
        senderEmail: 'acm.core@university.edu.in',
        subject: 'ACM National Hackathon 2026 - Team Roster Confirmation',
        snippet: 'Please confirm your 4-member team roster before Friday 5:00 PM to lock your team slot.',
        body: '''Hey Hackers!

Congratulations on clearing the preliminary round of ACM National Hackathon 2026!

Action Required:
All shortlisted teams must verify and finalize their 4-member team roster on the Devfolio portal before Friday 5:00 PM.

Failure to confirm by the deadline will release your slot to the waitlisted teams.

Happy Coding!
ACM Organizing Committee''',
        receivedAt: now.subtract(const Duration(hours: 8)),
        isUnread: true,
        labels: ['INBOX', 'HACKATHON'],
        analysis: AgentAnalysis(
          category: 'Hackathon',
          priority: PriorityLevel.medium,
          actionRequired: true,
          actionType: 'CONFIRM_ROSTER',
          actionDescription: 'Confirm 4-member team roster on Devfolio',
          deadline: thisFriday5pm,
          confidence: 0.90,
          reasoningSummary: 'Hackathon team roster confirmation due this Friday.',
        ),
        userState: const UserState(
          isViewed: false,
          isCompleted: false,
        ),
      ),

      // Scenario 4: Promotional Newsletter (Low Priority, Visually Muted)
      Email(
        id: 'email_promo_05',
        senderName: 'Campus Perks & Coursera',
        senderEmail: 'newsletter@campusperks.io',
        subject: 'Flash 50% Off: Master Generative AI & Cloud Architecture',
        snippet: 'Upgrade your tech stack this semester with verified certifications at half price.',
        body: '''Hi Learner,

Accelerate your career with special student discounts on top-rated specialization tracks in LLMs, Deep Learning, and Cloud Architecture.

Click here to claim your 50% discount voucher valid until Sunday midnight.

Unsubscribe anytime.''',
        receivedAt: now.subtract(const Duration(hours: 12)),
        isUnread: true,
        labels: ['PROMOTIONS', 'LOW_PRIORITY'],
        analysis: const AgentAnalysis(
          category: 'Promotional',
          priority: PriorityLevel.low,
          actionRequired: false,
          reasoningSummary: 'Automated promotional marketing email filtered to low priority to minimize inbox distraction.',
        ),
        userState: const UserState(
          isViewed: false,
          isCompleted: false,
        ),
      ),
    ];
  }

  static List<ReminderItem> getInitialReminders() {
    final DateTime tomorrow9am = DateTime(now.year, now.month, now.day + 1, 9, 0);

    return [
      ReminderItem(
        id: 'rem_01',
        emailId: 'email_acm_04',
        emailSubject: 'ACM National Hackathon 2026 - Team Roster Confirmation',
        senderName: 'ACM Student Chapter',
        reminderAt: tomorrow9am,
        reminderType: ReminderType.userScheduled,
        actionDescription: 'Confirm 4th member on Devfolio before Friday deadline',
      ),
      ReminderItem(
        id: 'rem_02',
        emailId: 'email_robotics_02',
        emailSubject: 'Robotics Assignment 3 - Kinematics Code Submission',
        senderName: 'Prof. Sharma',
        reminderAt: now.add(const Duration(hours: 18)),
        reminderType: ReminderType.system,
        actionDescription: 'Deadline approaching in 18 hours',
      ),
    ];
  }

  static List<NotificationEvent> getInitialNotifications() {
    return [
      NotificationEvent(
        id: 'notif_alarm_01',
        emailId: 'email_tcs_01',
        emailSubject: 'TCS Internship Application - Action Required',
        notificationType: 'DEADLINE_ALARM',
        severity: NotificationSeverity.alarm,
        title: 'ACTION REQUIRED: 5 MINUTES LEFT',
        message: 'TCS Internship Application deadline is closing immediately. You have not completed the required submission form.',
        createdAt: now.subtract(const Duration(minutes: 1)),
        requiresAlarm: true,
      ),
      NotificationEvent(
        id: 'notif_urgent_02',
        emailId: 'email_tcs_01',
        emailSubject: 'TCS Internship Application - Action Required',
        notificationType: 'URGENT',
        severity: NotificationSeverity.urgent,
        title: 'Deadline Approaching: TCS Internship',
        message: 'Less than 30 minutes remaining to complete your placement application form.',
        createdAt: now.subtract(const Duration(minutes: 15)),
        requiresAlarm: false,
      ),
      NotificationEvent(
        id: 'notif_normal_03',
        emailId: 'email_robotics_02',
        emailSubject: 'Robotics Assignment 3 - Kinematics Code Submission',
        notificationType: 'NORMAL',
        severity: NotificationSeverity.normal,
        title: 'New Important Email Parsed',
        message: 'Prof. Sharma posted Assignment 3 with submission deadline tomorrow.',
        createdAt: now.subtract(const Duration(hours: 3)),
        requiresAlarm: false,
      ),
    ];
  }
}
