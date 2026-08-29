import 'package:flutter_test/flutter_test.dart';
import 'package:agent_amar/dto/email_state_dto.dart';
import 'package:agent_amar/dto/mappers/dto_mapper.dart';
import 'package:agent_amar/dto/notification_dto.dart';
import 'package:agent_amar/dto/pending_action_dto.dart';
import 'package:agent_amar/dto/reminder_dto.dart';
import 'package:agent_amar/dto/upcoming_deadline_dto.dart';
import 'package:agent_amar/models/agent_analysis.dart';
import 'package:agent_amar/models/notification_event.dart';

void main() {
  group('DtoMapper Tests', () {
    test('mapPriority correctly maps all backend priority levels', () {
      expect(DtoMapper.mapPriority('CRITICAL'), PriorityLevel.critical);
      expect(DtoMapper.mapPriority('URGENT'), PriorityLevel.high);
      expect(DtoMapper.mapPriority('HIGH'), PriorityLevel.high);
      expect(DtoMapper.mapPriority('MEDIUM'), PriorityLevel.medium);
      expect(DtoMapper.mapPriority('LOW'), PriorityLevel.low);
      expect(DtoMapper.mapPriority('UNKNOWN'), PriorityLevel.low);
    });

    test('formatCategory formats backend snake_case categories', () {
      expect(DtoMapper.formatCategory('JOB_OPPORTUNITY'), 'Job Opportunity');
      expect(DtoMapper.formatCategory('FACULTY_ANNOUNCEMENT'), 'Faculty Announcement');
      expect(DtoMapper.formatCategory('INTERNSHIP'), 'Internship');
      expect(DtoMapper.formatCategory(''), 'General');
    });

    test('mapEmailState maps flat EmailStateOutDto correctly', () {
      final dto = EmailStateOutDto(
        emailId: 'gmail_123',
        senderName: 'Placement Cell',
        senderEmail: 'placements@college.edu',
        subject: 'TCS Internship 2026',
        snippet: 'Submit application before deadline',
        finalCategory: 'PLACEMENT',
        priorityLevel: 'URGENT',
        priorityScore: 80,
        folderLabel: 'AMAR/Opportunities',
        actionRequired: true,
        primaryActionType: 'FORM_SUBMISSION',
        nextDeadlineAt: DateTime.parse('2026-09-05T12:30:00Z'),
        isUnread: true,
        isViewed: false,
        isCompleted: false,
      );

      final email = DtoMapper.mapEmailState(dto);
      expect(email.id, 'gmail_123');
      expect(email.senderName, 'Placement Cell');
      expect(email.senderEmail, 'placements@college.edu');
      expect(email.subject, 'TCS Internship 2026');
      expect(email.snippet, 'Submit application before deadline');
      expect(email.analysis.category, 'Placement');
      expect(email.analysis.priority, PriorityLevel.high);
      expect(email.analysis.actionRequired, true);
      expect(email.analysis.actionType, 'FORM_SUBMISSION');
      expect(email.analysis.deadline, DateTime.parse('2026-09-05T12:30:00Z'));
      expect(email.userState.isViewed, false);
      expect(email.userState.isCompleted, false);
    });

    test('mapEmailState maps full EmailStateDetailOutDto with children and trace', () {
      final detailDto = EmailStateDetailOutDto(
        emailId: 'gmail_detail_1',
        senderEmail: 'prof@univ.edu',
        senderName: 'Prof. Rao',
        subject: 'Assignment 3',
        snippet: 'Due tomorrow',
        finalCategory: 'ASSIGNMENT',
        priorityLevel: 'HIGH',
        priorityScore: 70,
        folderLabel: 'AMAR/Academics',
        reasoningSummary: 'Assignment due soon.',
        actions: const [
          ActionStateDto(
            actionRef: 'act_001',
            actionType: 'DOCUMENT_UPLOAD',
            description: 'Upload PDF report',
            blocking: true,
            targetLink: 'https://lms.univ.edu',
            confidence: 0.95,
          ),
        ],
        deadlines: [
          DeadlineStateDto(
            deadlineRef: 'dl_001',
            deadlineDatetime: DateTime.parse('2026-09-02T18:00:00Z'),
            sourceText: 'Tomorrow 6 PM',
          ),
        ],
        latestProcessing: ProcessingRunDto(
          runId: 'run_01',
          processedAt: DateTime.now(),
          status: 'ok',
          finalCategory: 'ASSIGNMENT',
          priorityLevel: 'HIGH',
          priorityScore: 70,
          summary: 'Processed successfully.',
          agentTrace: const [
            AgentTraceEntryDto(
              agent: 'Mail Intake Agent',
              status: 'ok',
              confidence: 1.0,
              durationMs: 2,
            ),
            AgentTraceEntryDto(
              agent: 'Triage Agent',
              status: 'ok',
              confidence: 0.95,
              method: 'deterministic',
            ),
          ],
        ),
      );

      final email = DtoMapper.mapEmailState(detailDto);
      expect(email.id, 'gmail_detail_1');
      expect(email.analysis.actionDescription, 'Upload PDF report');
      expect(email.analysis.traces.length, 2);
      expect(email.analysis.traces.first.agentName, 'Mail Intake Agent');
      expect(email.analysis.traces.last.agentName, 'Triage Agent');
    });

    test('mapPendingAction maps PendingActionDto to Email model', () {
      const dto = PendingActionDto(
        actionRef: 'act_001',
        actionType: 'FORM_SUBMISSION',
        description: 'Complete registration form',
        emailId: 'gmail_act_1',
        subject: 'Hackathon Registration',
        priorityLevel: 'CRITICAL',
      );

      final email = DtoMapper.mapPendingAction(dto);
      expect(email.id, 'gmail_act_1');
      expect(email.subject, 'Hackathon Registration');
      expect(email.analysis.priority, PriorityLevel.critical);
      expect(email.analysis.actionRequired, true);
      expect(email.analysis.actionDescription, 'Complete registration form');
    });

    test('mapUpcomingDeadline maps UpcomingDeadlineDto to Email model', () {
      final dto = UpcomingDeadlineDto(
        deadlineRef: 'dl_001',
        deadlineDatetime: DateTime.parse('2026-09-05T12:00:00Z'),
        sourceText: '5 September 2026',
        emailId: 'gmail_dl_1',
        subject: 'Scholarship Application',
        priorityLevel: 'HIGH',
      );

      final email = DtoMapper.mapUpcomingDeadline(dto);
      expect(email.id, 'gmail_dl_1');
      expect(email.subject, 'Scholarship Application');
      expect(email.analysis.deadline, DateTime.parse('2026-09-05T12:00:00Z'));
    });

    test('mapReminder maps ReminderOutDto correctly', () {
      final dto = ReminderOutDto(
        id: 42,
        emailId: 'gmail_rem_1',
        actionRef: 'act_001',
        reminderAt: DateTime.parse('2026-09-03T09:00:00Z'),
        note: 'Review before meeting',
        createdAt: DateTime.parse('2026-09-01T10:00:00Z'),
      );

      final reminder = DtoMapper.mapReminder(dto, 'Meeting Notes');
      expect(reminder.id, '42');
      expect(reminder.emailId, 'gmail_rem_1');
      expect(reminder.emailSubject, 'Meeting Notes');
      expect(reminder.actionDescription, 'Review before meeting');
    });

    test('mapNotification maps NotificationOutDto with alarm flag correctly', () {
      final alarmDto = NotificationOutDto(
        id: 101,
        emailId: 'gmail_crit_1',
        notificationType: 'deadline_escalation',
        severity: 'ALARM',
        requiresAlarm: true,
        status: 'PENDING',
        detail: '5 minutes left to submit internship form',
        createdAt: DateTime.now(),
      );

      final notif = DtoMapper.mapNotification(alarmDto, 'TCS Form Deadline');
      expect(notif.id, '101');
      expect(notif.requiresAlarm, true);
      expect(notif.severity, NotificationSeverity.alarm);
      expect(notif.title, 'ACTION REQUIRED: DEADLINE ALARM');
    });
  });
}
