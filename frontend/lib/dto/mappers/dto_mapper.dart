import '../../models/agent_analysis.dart';
import '../../models/agent_trace.dart';
import '../../models/email.dart';
import '../../models/notification_event.dart';
import '../../models/reminder.dart';
import '../../models/user_state.dart';
import '../email_state_dto.dart';
import '../notification_dto.dart';
import '../pending_action_dto.dart';
import '../reminder_dto.dart';
import '../upcoming_deadline_dto.dart';

class DtoMapper {
  static PriorityLevel mapPriority(String priorityLevel) {
    switch (priorityLevel.toUpperCase()) {
      case 'CRITICAL':
        return PriorityLevel.critical;
      case 'URGENT':
      case 'HIGH':
        return PriorityLevel.high;
      case 'MEDIUM':
        return PriorityLevel.medium;
      case 'LOW':
      default:
        return PriorityLevel.low;
    }
  }

  static String formatCategory(String rawCategory) {
    final clean = rawCategory.replaceAll('_', ' ').toLowerCase();
    if (clean.isEmpty) return 'General';
    return clean.split(' ').map((word) {
      if (word.isEmpty) return '';
      return word[0].toUpperCase() + word.substring(1);
    }).join(' ');
  }

  static Email mapEmailState(EmailStateOutDto dto) {
    String? actionDescription;
    String? reasoning;
    List<AgentTrace> traces = [];

    if (dto is EmailStateDetailOutDto) {
      reasoning = dto.reasoningSummary ?? dto.latestProcessing?.summary;
      if (dto.actions.isNotEmpty) {
        final firstAction = dto.actions.first;
        actionDescription = firstAction.description ?? firstAction.actionType.replaceAll('_', ' ');
      }
      if (dto.latestProcessing != null && dto.latestProcessing!.agentTrace.isNotEmpty) {
        traces = dto.latestProcessing!.agentTrace.map((t) {
          final summaryText = t.method != null
              ? 'Processed via ${t.method}${t.durationMs != null ? ' (${t.durationMs}ms)' : ''}'
              : 'Status: ${t.status.toUpperCase()}${t.durationMs != null ? ' (${t.durationMs}ms)' : ''}';
          return AgentTrace(
            agentName: t.agent,
            status: t.status,
            confidence: t.confidence ?? 1.0,
            summary: summaryText,
            timestamp: dto.processedAt ?? DateTime.now(),
            method: t.method,
          );
        }).toList();
      }
    } else {
      if (dto.primaryActionType != null) {
        actionDescription = dto.primaryActionType!.replaceAll('_', ' ');
      }
    }

    final categoryDisplay = formatCategory(dto.finalCategory);
    final priority = mapPriority(dto.priorityLevel);

    return Email(
      id: dto.emailId,
      senderName: dto.senderName != null && dto.senderName!.isNotEmpty
          ? dto.senderName!
          : dto.senderEmail,
      senderEmail: dto.senderEmail,
      subject: dto.subject.isNotEmpty ? dto.subject : '(No Subject)',
      body: dto.snippet != null && dto.snippet!.isNotEmpty
          ? dto.snippet!
          : '(No preview snippet available from Gmail)',
      snippet: dto.snippet ?? '',
      receivedAt: dto.receivedAt ?? dto.createdAt ?? DateTime.now(),
      isUnread: dto.isUnread,
      labels: [dto.folderLabel, dto.finalCategory],
      analysis: AgentAnalysis(
        category: categoryDisplay,
        priority: priority,
        actionRequired: dto.actionRequired,
        actionType: dto.primaryActionType,
        actionDescription: actionDescription,
        deadline: dto.nextDeadlineAt,
        confidence: dto.categoryConfidence ?? 1.0,
        reasoningSummary: reasoning ?? '$categoryDisplay mail prioritized as ${dto.priorityLevel} (Score: ${dto.priorityScore}/100).',
        traces: traces,
      ),
      userState: UserState(
        isViewed: dto.isViewed,
        isCompleted: dto.isCompleted,
        snoozedUntil: dto.snoozedUntil,
        isMonitoringActive: dto.shouldMonitor,
      ),
    );
  }

  static Email mapPendingAction(PendingActionDto dto) {
    final priority = mapPriority(dto.priorityLevel);
    return Email(
      id: dto.emailId,
      senderName: 'Sender',
      senderEmail: '',
      subject: dto.subject.isNotEmpty ? dto.subject : 'Pending Action',
      body: dto.description ?? 'Action required: ${dto.actionType}',
      snippet: dto.description ?? 'Action required: ${dto.actionType}',
      receivedAt: dto.createdAt ?? DateTime.now(),
      isUnread: false,
      labels: ['ACTION_REQUIRED'],
      analysis: AgentAnalysis(
        category: 'Action Required',
        priority: priority,
        actionRequired: true,
        actionType: dto.actionType,
        actionDescription: dto.description ?? dto.actionType.replaceAll('_', ' '),
        confidence: dto.confidence,
        reasoningSummary: 'Pending action: ${dto.description ?? dto.actionType}',
      ),
      userState: UserState(
        isViewed: false,
        isCompleted: dto.status == 'COMPLETED' || dto.status == 'DISMISSED',
      ),
    );
  }

  static Email mapUpcomingDeadline(UpcomingDeadlineDto dto) {
    final priority = mapPriority(dto.priorityLevel);
    return Email(
      id: dto.emailId,
      senderName: 'Sender',
      senderEmail: '',
      subject: dto.subject.isNotEmpty ? dto.subject : 'Upcoming Deadline',
      body: dto.sourceText ?? 'Upcoming commitment',
      snippet: dto.sourceText ?? 'Upcoming commitment',
      receivedAt: DateTime.now(),
      isUnread: false,
      labels: ['DEADLINE'],
      analysis: AgentAnalysis(
        category: 'Deadline',
        priority: priority,
        actionRequired: true,
        actionType: dto.actionContext,
        actionDescription: dto.sourceText ?? 'Deadline approaching',
        deadline: dto.deadlineDatetime,
        confidence: dto.confidence,
        reasoningSummary: 'Deadline: ${dto.sourceText ?? dto.deadlineDatetime?.toIso8601String()}',
      ),
      userState: UserState(
        isViewed: true,
        isCompleted: dto.isPast,
        isMonitoringActive: dto.isMonitoring,
      ),
    );
  }

  static ReminderItem mapReminder(ReminderOutDto dto, [String? emailSubject]) {
    ReminderStatus status;
    switch (dto.status.toUpperCase()) {
      case 'TRIGGERED':
        status = ReminderStatus.triggered;
        break;
      case 'CANCELLED':
      case 'SKIPPED':
        status = ReminderStatus.dismissed;
        break;
      case 'PENDING':
      default:
        status = ReminderStatus.pending;
    }

    return ReminderItem(
      id: dto.id.toString(),
      emailId: dto.emailId ?? '',
      emailSubject: emailSubject ?? dto.note ?? 'Email Reminder #${dto.id}',
      senderName: 'AGENT AMAR',
      reminderAt: dto.reminderAt.toLocal(),
      reminderType: ReminderType.userScheduled,
      status: status,
      actionDescription: dto.note ?? (dto.actionRef != null ? 'Ref: ${dto.actionRef}' : null),
    );
  }

  static NotificationEvent mapNotification(NotificationOutDto dto, [String? emailSubject]) {
    NotificationSeverity severity;
    if (dto.requiresAlarm || dto.severity.toUpperCase() == 'ALARM') {
      severity = NotificationSeverity.alarm;
    } else if (dto.severity.toUpperCase() == 'URGENT') {
      severity = NotificationSeverity.urgent;
    } else if (dto.severity.toUpperCase() == 'REMINDER') {
      severity = NotificationSeverity.reminder;
    } else {
      severity = NotificationSeverity.normal;
    }

    String title;
    switch (dto.notificationType) {
      case 'deadline_escalation':
        title = dto.requiresAlarm ? 'ACTION REQUIRED: DEADLINE ALARM' : 'Deadline Escalation Notice';
        break;
      case 'deadline_passed':
        title = 'Deadline Has Elapsed';
        break;
      case 'ambiguous_deadline':
        title = 'Ambiguous Deadline Detected';
        break;
      case 'user_reminder':
        title = 'Scheduled Reminder';
        break;
      case 'new_priority_email':
      default:
        title = 'New Important Email Ingested';
    }

    return NotificationEvent(
      id: dto.id.toString(),
      emailId: dto.emailId ?? '',
      emailSubject: emailSubject ?? 'Email Notification #${dto.id}',
      notificationType: dto.notificationType,
      severity: severity,
      title: title,
      message: dto.detail ?? 'Notification detail for email ${dto.emailId}',
      createdAt: dto.createdAt.toLocal(),
      requiresAlarm: dto.requiresAlarm,
      isDismissed: dto.status == 'SKIPPED' || dto.status == 'SENT',
    );
  }
}
