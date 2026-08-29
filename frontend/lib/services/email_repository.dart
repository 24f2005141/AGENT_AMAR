import '../data/mock_data.dart';
import '../dto/auth_status_dto.dart';
import '../dto/email_state_dto.dart';
import '../dto/gmail_sync_dto.dart';
import '../dto/mappers/dto_mapper.dart';
import '../dto/monitor_check_dto.dart';
import '../dto/notification_dto.dart';
import '../dto/pending_action_dto.dart';
import '../dto/reminder_dto.dart';
import '../dto/snooze_request_dto.dart';
import '../dto/upcoming_deadline_dto.dart';
import '../models/agent_analysis.dart';
import '../models/email.dart';
import '../models/notification_event.dart';
import '../models/reminder.dart';
import 'api_client.dart';

abstract class EmailRepository {
  // Auth & incremental Gmail sync (Phase 12)
  Future<AuthStatusDto> getAuthStatus();
  Future<void> disconnectGmail();

  /// Request ONE incremental Gmail sync from the backend
  /// (`POST /api/v1/gmail/sync`). The backend scheduler owns continuous
  /// monitoring — the app never polls Gmail and never triggers the legacy
  /// bulk-ingest endpoint (which can re-process historical mail).
  Future<GmailSyncResultDto> syncGmail();

  /// Persistent Gmail monitoring baseline / progress
  /// (`GET /api/v1/gmail/sync/status`).
  Future<GmailSyncStatusDto> getGmailSyncStatus();

  // Emails
  Future<List<Email>> getEmails({
    String? priority,
    String? category,
    bool? actionRequired,
    bool? viewed,
    bool? completed,
    int limit = 100,
  });
  Future<Email?> getEmailById(String id);
  Future<EmailStateDetailOutDto?> getEmailDetailDto(String id);
  Future<List<ProcessingRunDto>> getEmailProcessingRuns(String id);

  // Cross-cutting views
  Future<List<Email>> getNeedsAttentionEmails();
  Future<List<PendingActionDto>> getPendingActions({int limit = 100});
  Future<List<Email>> getDeadlineEmails({int? withinHours});
  Future<List<UpcomingDeadlineDto>> getUpcomingDeadlinesDto({int? withinHours, int limit = 100});

  // Mutations
  Future<Email> markEmailViewed(String emailId);
  Future<Email> snoozeEmail(String emailId, DateTime until);
  Future<Email> clearSnooze(String emailId);
  Future<Email> completeAction(String emailId, String actionRef);
  Future<Email> dismissAction(String emailId, String actionRef);

  // Reminders
  Future<List<ReminderItem>> getReminders({String? status});
  Future<List<ReminderOutDto>> getEmailReminders(String emailId);
  Future<ReminderItem> createReminder({
    required String emailId,
    required DateTime reminderAt,
    String? actionRef,
    String? note,
    ReminderType type = ReminderType.userScheduled,
  });
  Future<void> cancelReminder(String emailId, int reminderId);

  // Notifications & Monitor
  Future<List<NotificationEvent>> getNotifications({
    bool? requiresAlarm,
    String? severity,
    String? type,
  });
  Future<MonitorCheckResultDto> runDeadlineCheck({DateTime? now});
}

class ApiEmailRepository implements EmailRepository {
  final ApiClient _client;

  ApiEmailRepository({ApiClient? client}) : _client = client ?? ApiClient();

  @override
  Future<AuthStatusDto> getAuthStatus() async {
    final res = await _client.get('/api/v1/auth/google/status');
    return AuthStatusDto.fromJson(res as Map<String, dynamic>);
  }

  @override
  Future<void> disconnectGmail() async {
    await _client.post('/api/v1/auth/google/disconnect');
  }

  @override
  Future<GmailSyncResultDto> syncGmail() async {
    final res = await _client.post('/api/v1/gmail/sync');
    return GmailSyncResultDto.fromJson(res as Map<String, dynamic>);
  }

  @override
  Future<GmailSyncStatusDto> getGmailSyncStatus() async {
    final res = await _client.get('/api/v1/gmail/sync/status');
    return GmailSyncStatusDto.fromJson(res as Map<String, dynamic>);
  }

  @override
  Future<List<Email>> getEmails({
    String? priority,
    String? category,
    bool? actionRequired,
    bool? viewed,
    bool? completed,
    int limit = 100,
  }) async {
    final query = <String, dynamic>{
      if (priority != null) 'priority': priority,
      if (category != null) 'category': category,
      if (actionRequired != null) 'action_required': actionRequired,
      if (viewed != null) 'viewed': viewed,
      if (completed != null) 'completed': completed,
      'limit': limit,
    };

    final res = await _client.get('/api/v1/emails', queryParameters: query);
    final list = res as List<dynamic>;
    final dtos = list.map((e) => EmailStateOutDto.fromJson(e as Map<String, dynamic>)).toList();
    return dtos.map(DtoMapper.mapEmailState).toList();
  }

  @override
  Future<Email?> getEmailById(String id) async {
    final detail = await getEmailDetailDto(id);
    if (detail == null) return null;
    return DtoMapper.mapEmailState(detail);
  }

  @override
  Future<EmailStateDetailOutDto?> getEmailDetailDto(String id) async {
    try {
      final res = await _client.get('/api/v1/emails/$id');
      return EmailStateDetailOutDto.fromJson(res as Map<String, dynamic>);
    } catch (_) {
      return null;
    }
  }

  @override
  Future<List<ProcessingRunDto>> getEmailProcessingRuns(String id) async {
    final res = await _client.get('/api/v1/emails/$id/processing');
    final list = res as List<dynamic>;
    return list.map((e) => ProcessingRunDto.fromJson(e as Map<String, dynamic>)).toList();
  }

  @override
  Future<List<Email>> getNeedsAttentionEmails() async {
    return getEmails(actionRequired: true, completed: false);
  }

  @override
  Future<List<PendingActionDto>> getPendingActions({int limit = 100}) async {
    final res = await _client.get(
      '/api/v1/actions/pending',
      queryParameters: {'limit': limit},
    );
    final list = res as List<dynamic>;
    return list.map((e) => PendingActionDto.fromJson(e as Map<String, dynamic>)).toList();
  }

  @override
  Future<List<Email>> getDeadlineEmails({int? withinHours}) async {
    final upcoming = await getUpcomingDeadlinesDto(withinHours: withinHours);
    return upcoming.map(DtoMapper.mapUpcomingDeadline).toList();
  }

  @override
  Future<List<UpcomingDeadlineDto>> getUpcomingDeadlinesDto({
    int? withinHours,
    int limit = 100,
  }) async {
    final query = <String, dynamic>{
      if (withinHours != null) 'within_hours': withinHours,
      'limit': limit,
    };
    final res = await _client.get('/api/v1/deadlines/upcoming', queryParameters: query);
    final list = res as List<dynamic>;
    return list.map((e) => UpcomingDeadlineDto.fromJson(e as Map<String, dynamic>)).toList();
  }

  @override
  Future<Email> markEmailViewed(String emailId) async {
    final res = await _client.patch('/api/v1/emails/$emailId/viewed');
    final detail = EmailStateDetailOutDto.fromJson(res as Map<String, dynamic>);
    return DtoMapper.mapEmailState(detail);
  }

  @override
  Future<Email> snoozeEmail(String emailId, DateTime until) async {
    final body = SnoozeRequestDto(snoozedUntil: until).toJson();
    final res = await _client.patch('/api/v1/emails/$emailId/snooze', body: body);
    final detail = EmailStateDetailOutDto.fromJson(res as Map<String, dynamic>);
    return DtoMapper.mapEmailState(detail);
  }

  @override
  Future<Email> clearSnooze(String emailId) async {
    final res = await _client.delete('/api/v1/emails/$emailId/snooze');
    final detail = EmailStateDetailOutDto.fromJson(res as Map<String, dynamic>);
    return DtoMapper.mapEmailState(detail);
  }

  @override
  Future<Email> completeAction(String emailId, String actionRef) async {
    final res = await _client.patch(
      '/api/v1/emails/$emailId/actions/$actionRef/complete',
    );
    final detail = EmailStateDetailOutDto.fromJson(res as Map<String, dynamic>);
    return DtoMapper.mapEmailState(detail);
  }

  @override
  Future<Email> dismissAction(String emailId, String actionRef) async {
    final res = await _client.patch(
      '/api/v1/emails/$emailId/actions/$actionRef/dismiss',
    );
    final detail = EmailStateDetailOutDto.fromJson(res as Map<String, dynamic>);
    return DtoMapper.mapEmailState(detail);
  }

  @override
  Future<List<ReminderItem>> getReminders({String? status}) async {
    final query = <String, dynamic>{
      if (status != null) 'status': status,
    };
    final res = await _client.get('/api/v1/reminders', queryParameters: query);
    final list = res as List<dynamic>;
    final dtos = list.map((e) => ReminderOutDto.fromJson(e as Map<String, dynamic>)).toList();
    return dtos.map((r) => DtoMapper.mapReminder(r)).toList();
  }

  @override
  Future<List<ReminderOutDto>> getEmailReminders(String emailId) async {
    final res = await _client.get('/api/v1/emails/$emailId/reminders');
    final list = res as List<dynamic>;
    return list.map((e) => ReminderOutDto.fromJson(e as Map<String, dynamic>)).toList();
  }

  @override
  Future<ReminderItem> createReminder({
    required String emailId,
    required DateTime reminderAt,
    String? actionRef,
    String? note,
    ReminderType type = ReminderType.userScheduled,
  }) async {
    final body = ReminderCreateDto(
      reminderAt: reminderAt,
      actionRef: actionRef,
      note: note,
    ).toJson();

    final res = await _client.post(
      '/api/v1/emails/$emailId/reminders',
      body: body,
    );
    final dto = ReminderOutDto.fromJson(res as Map<String, dynamic>);
    return DtoMapper.mapReminder(dto);
  }

  @override
  Future<void> cancelReminder(String emailId, int reminderId) async {
    await _client.delete('/api/v1/emails/$emailId/reminders/$reminderId');
  }

  @override
  Future<List<NotificationEvent>> getNotifications({
    bool? requiresAlarm,
    String? severity,
    String? type,
  }) async {
    final query = <String, dynamic>{
      if (requiresAlarm != null) 'requires_alarm': requiresAlarm,
      if (severity != null) 'severity': severity,
      if (type != null) 'type': type,
    };
    final res = await _client.get('/api/v1/notifications', queryParameters: query);
    final list = res as List<dynamic>;
    final dtos = list.map((e) => NotificationOutDto.fromJson(e as Map<String, dynamic>)).toList();
    return dtos.map((n) => DtoMapper.mapNotification(n)).toList();
  }

  @override
  Future<MonitorCheckResultDto> runDeadlineCheck({DateTime? now}) async {
    final body = now != null ? {'now': now.toUtc().toIso8601String()} : null;
    final res = await _client.post('/api/v1/monitor/deadlines/check', body: body);
    return MonitorCheckResultDto.fromJson(res as Map<String, dynamic>);
  }
}

class MockEmailRepository implements EmailRepository {
  List<Email> _emails = [];
  List<ReminderItem> _reminders = [];
  List<NotificationEvent> _notifications = [];
  bool _initialized = false;

  void _ensureInitialized() {
    if (!_initialized) {
      _emails = MockData.getInitialEmails();
      _reminders = MockData.getInitialReminders();
      _notifications = MockData.getInitialNotifications();
      _initialized = true;
    }
  }

  @override
  Future<AuthStatusDto> getAuthStatus() async {
    return const AuthStatusDto(
      connected: true,
      provider: 'gmail',
      accountEmail: 'demo.student@gmail.com',
      scopes: ['https://www.googleapis.com/auth/gmail.readonly'],
    );
  }

  @override
  Future<void> disconnectGmail() async {}

  @override
  Future<GmailSyncResultDto> syncGmail() async {
    _ensureInitialized();
    return GmailSyncResultDto(
      status: 'synced',
      processed: 0,
      lastHistoryId: '1000',
      fromHistoryId: '1000',
      lastSyncAt: DateTime.now(),
    );
  }

  @override
  Future<GmailSyncStatusDto> getGmailSyncStatus() async {
    return GmailSyncStatusDto(
      monitoring: true,
      accountEmail: 'demo.student@gmail.com',
      monitoringStartedAt: DateTime.now().subtract(const Duration(days: 1)),
      lastSyncAt: DateTime.now(),
      lastHistoryId: '1000',
    );
  }

  @override
  Future<List<Email>> getEmails({
    String? priority,
    String? category,
    bool? actionRequired,
    bool? viewed,
    bool? completed,
    int limit = 100,
  }) async {
    _ensureInitialized();
    var list = _emails;
    if (priority != null) {
      list = list.where((e) => e.analysis.priority.displayName == priority.toUpperCase()).toList();
    }
    if (actionRequired != null) {
      list = list.where((e) => e.analysis.actionRequired == actionRequired).toList();
    }
    if (completed != null) {
      list = list.where((e) => e.userState.isCompleted == completed).toList();
    }
    if (viewed != null) {
      list = list.where((e) => e.userState.isViewed == viewed).toList();
    }
    return List.unmodifiable(list);
  }

  @override
  Future<Email?> getEmailById(String id) async {
    _ensureInitialized();
    try {
      return _emails.firstWhere((e) => e.id == id);
    } catch (_) {
      return null;
    }
  }

  @override
  Future<EmailStateDetailOutDto?> getEmailDetailDto(String id) async {
    final email = await getEmailById(id);
    if (email == null) return null;
    return EmailStateDetailOutDto(
      emailId: email.id,
      senderEmail: email.senderEmail,
      senderName: email.senderName,
      subject: email.subject,
      snippet: email.snippet,
      finalCategory: email.analysis.category,
      priorityLevel: email.analysis.priority.displayName,
      priorityScore: email.isCritical ? 95 : 50,
      folderLabel: 'AMAR/Inbox',
      isUnread: email.isUnread,
      isViewed: email.userState.isViewed,
      actionRequired: email.analysis.actionRequired,
      isCompleted: email.userState.isCompleted,
      reasoningSummary: email.analysis.reasoningSummary,
    );
  }

  @override
  Future<List<ProcessingRunDto>> getEmailProcessingRuns(String id) async {
    return [];
  }

  @override
  Future<List<Email>> getNeedsAttentionEmails() async {
    _ensureInitialized();
    return _emails.where((e) => e.analysis.actionRequired && !e.userState.isCompleted).toList()
      ..sort((a, b) {
        final pCompare = _priorityWeight(b.analysis.priority).compareTo(_priorityWeight(a.analysis.priority));
        if (pCompare != 0) return pCompare;
        if (a.analysis.deadline != null && b.analysis.deadline != null) {
          return a.analysis.deadline!.compareTo(b.analysis.deadline!);
        }
        return b.receivedAt.compareTo(a.receivedAt);
      });
  }

  @override
  Future<List<PendingActionDto>> getPendingActions({int limit = 100}) async {
    final emails = await getNeedsAttentionEmails();
    return emails.map((e) => PendingActionDto(
      actionRef: 'act_001',
      actionType: e.analysis.actionType ?? 'OTHER',
      description: e.analysis.actionDescription,
      emailId: e.id,
      subject: e.subject,
      priorityLevel: e.analysis.priority.displayName,
    )).toList();
  }

  @override
  Future<List<Email>> getDeadlineEmails({int? withinHours}) async {
    _ensureInitialized();
    return _emails.where((e) => e.analysis.deadline != null).toList()
      ..sort((a, b) => a.analysis.deadline!.compareTo(b.analysis.deadline!));
  }

  @override
  Future<List<UpcomingDeadlineDto>> getUpcomingDeadlinesDto({int? withinHours, int limit = 100}) async {
    final emails = await getDeadlineEmails(withinHours: withinHours);
    return emails.map((e) => UpcomingDeadlineDto(
      deadlineRef: 'dl_001',
      deadlineDatetime: e.analysis.deadline,
      emailId: e.id,
      subject: e.subject,
      priorityLevel: e.analysis.priority.displayName,
    )).toList();
  }

  @override
  Future<Email> markEmailViewed(String emailId) async {
    _ensureInitialized();
    final index = _emails.indexWhere((e) => e.id == emailId);
    if (index != -1) {
      _emails[index] = _emails[index].copyWith(
        isUnread: false,
        userState: _emails[index].userState.copyWith(isViewed: true),
      );
      return _emails[index];
    }
    throw Exception('Email not found');
  }

  @override
  Future<Email> snoozeEmail(String emailId, DateTime until) async {
    _ensureInitialized();
    final index = _emails.indexWhere((e) => e.id == emailId);
    if (index != -1) {
      _emails[index] = _emails[index].copyWith(
        userState: _emails[index].userState.copyWith(snoozedUntil: until),
      );
      return _emails[index];
    }
    throw Exception('Email not found');
  }

  @override
  Future<Email> clearSnooze(String emailId) async {
    _ensureInitialized();
    final index = _emails.indexWhere((e) => e.id == emailId);
    if (index != -1) {
      _emails[index] = _emails[index].copyWith(
        userState: _emails[index].userState.copyWith(clearSnooze: true),
      );
      return _emails[index];
    }
    throw Exception('Email not found');
  }

  @override
  Future<Email> completeAction(String emailId, String actionRef) async {
    _ensureInitialized();
    final index = _emails.indexWhere((e) => e.id == emailId);
    if (index != -1) {
      _emails[index] = _emails[index].copyWith(
        userState: _emails[index].userState.copyWith(isCompleted: true),
      );
      return _emails[index];
    }
    throw Exception('Email not found');
  }

  @override
  Future<Email> dismissAction(String emailId, String actionRef) async {
    return completeAction(emailId, actionRef);
  }

  @override
  Future<List<ReminderItem>> getReminders({String? status}) async {
    _ensureInitialized();
    return List.unmodifiable(_reminders);
  }

  @override
  Future<List<ReminderOutDto>> getEmailReminders(String emailId) async {
    return [];
  }

  @override
  Future<ReminderItem> createReminder({
    required String emailId,
    required DateTime reminderAt,
    String? actionRef,
    String? note,
    ReminderType type = ReminderType.userScheduled,
  }) async {
    _ensureInitialized();
    final email = await getEmailById(emailId);
    final reminder = ReminderItem(
      id: '${DateTime.now().millisecondsSinceEpoch}',
      emailId: emailId,
      emailSubject: email?.subject ?? 'Follow up on email',
      senderName: email?.senderName ?? 'Agent Amar',
      reminderAt: reminderAt,
      reminderType: type,
      actionDescription: note ?? email?.analysis.actionDescription,
    );
    _reminders.add(reminder);
    return reminder;
  }

  @override
  Future<void> cancelReminder(String emailId, int reminderId) async {
    _ensureInitialized();
    _reminders.removeWhere((r) => r.id == reminderId.toString());
  }

  @override
  Future<List<NotificationEvent>> getNotifications({
    bool? requiresAlarm,
    String? severity,
    String? type,
  }) async {
    _ensureInitialized();
    var list = _notifications;
    if (requiresAlarm != null) {
      list = list.where((n) => n.requiresAlarm == requiresAlarm).toList();
    }
    return List.unmodifiable(list.where((n) => !n.isDismissed));
  }

  @override
  Future<MonitorCheckResultDto> runDeadlineCheck({DateTime? now}) async {
    return MonitorCheckResultDto(
      checkedAt: now ?? DateTime.now(),
      deadlinesEvaluated: 1,
      remindersEvaluated: 1,
      notificationsCreated: 1,
      results: [
        const MonitorDecisionOutDto(
          emailId: 'email_tcs_01',
          deadlineRef: 'dl_001',
          decision: 'ALARM',
          reason: 'Simulated deadline check',
          requiresAlarm: true,
        ),
      ],
    );
  }

  int _priorityWeight(PriorityLevel p) {
    switch (p) {
      case PriorityLevel.critical:
        return 4;
      case PriorityLevel.high:
        return 3;
      case PriorityLevel.medium:
        return 2;
      case PriorityLevel.low:
        return 1;
    }
  }
}
