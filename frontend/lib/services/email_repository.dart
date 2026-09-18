import '../data/mock_data.dart';
import '../dto/app_user_dto.dart';
import '../dto/auth_flow_dto.dart';
import '../dto/auth_status_dto.dart';
import '../dto/email_state_dto.dart';
import '../dto/full_email_dto.dart';
import '../dto/gmail_sync_dto.dart';
import '../dto/mappers/dto_mapper.dart';
import '../dto/monitor_check_dto.dart';
import '../dto/notification_dto.dart';
import '../dto/pending_action_dto.dart';
import '../dto/reminder_dto.dart';
import '../dto/reply_dto.dart';
import '../dto/snooze_request_dto.dart';
import '../dto/system_status_dto.dart';
import '../dto/upcoming_deadline_dto.dart';
import '../models/agent_analysis.dart';
import '../models/email.dart';
import '../models/notification_event.dart';
import '../models/reminder.dart';
import '../config/api_config.dart';
import 'api_client.dart';

abstract class EmailRepository {
  // Application login / session (Phase 15)
  Future<GoogleAuthStartDto> startGoogleAuth();

  /// Swap the one-time browser→app handoff code (from the
  /// `agentamar://auth/callback?code=…` deep link) for the application session
  /// token. Throws [ApiException] (400) if the code is invalid / expired / used.
  Future<GoogleSessionDto> exchangeSession(String code, {String? state});

  /// Manual-browser fallback: poll the backend for the session token after the
  /// user consents. Returns `null` while pending; throws [ApiException] (410)
  /// once the flow has expired. The mobile flow uses [exchangeSession] instead.
  Future<GoogleSessionDto?> pollGoogleSession(String flowId);
  Future<AuthMeDto> getCurrentUser();
  Future<void> logout({String? fcmToken});

  // Push device registration (Phase 16)
  Future<void> registerDevice({
    required String fcmToken,
    String platform = 'android',
    String? deviceLabel,
    String? appVersion,
    String? previousToken,
  });
  Future<void> unregisterDevice(String fcmToken);

  // System connection status (backend + configured LLM, checked server-side)
  Future<SystemStatusDto> getSystemStatus();

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
    /// Attention-dashboard filter (backend-derived). `true` = only emails that
    /// still need attention (the homepage feed); `false` = the resolved /
    /// acknowledged complement (history); `null` = the full list.
    bool? active,
    int limit = 100,
  });
  Future<Email?> getEmailById(String id);
  Future<EmailStateDetailOutDto?> getEmailDetailDto(String id);
  Future<List<ProcessingRunDto>> getEmailProcessingRuns(String id);

  /// The complete email body (fetched live from Gmail by the backend, always
  /// returned as sanitised plain text). Throws [ApiException] (404 owner/missing).
  Future<FullEmailDto> getFullEmail(String emailId);

  /// Manually correct an email's canonical primary category (Phase 18). Returns
  /// the updated [Email] so the caller can move it between sections immediately.
  /// Throws [ApiException] (404 owner/missing, 422 invalid category).
  Future<Email> submitClassificationFeedback(String emailId, PrimaryCategory category);

  /// AI reply drafting (backend runs the LLM; exactly 3 options).
  /// Throws [ApiException] on failure (503 = AI unavailable, 502 = bad response).
  Future<ReplySuggestionsDto> getReplySuggestions(String emailId);

  /// Send [body] verbatim as a threaded reply from the user's connected Gmail.
  /// The AI never sends — this is only ever called from an explicit user tap.
  Future<ReplySendResultDto> sendReply(String emailId, String body);

  // Cross-cutting views
  Future<List<Email>> getNeedsAttentionEmails();
  Future<List<PendingActionDto>> getPendingActions({int limit = 100});
  Future<List<Email>> getDeadlineEmails({int? withinHours});
  Future<List<UpcomingDeadlineDto>> getUpcomingDeadlinesDto({int? withinHours, int limit = 100});

  // Mutations
  Future<Email> markEmailViewed(String emailId);

  /// Explicitly resolve the whole email (the "mark done" / tick action) —
  /// completes every pending action, persists `is_completed` +
  /// `completion_source=user` so a Gmail sync can never revert it.
  Future<Email> markEmailComplete(String emailId);

  /// Undo a completion.
  Future<Email> reopenEmail(String emailId);

  Future<Email> snoozeEmail(String emailId, DateTime until);
  Future<Email> clearSnooze(String emailId);
  Future<Email> completeAction(String emailId, String actionRef);
  Future<Email> dismissAction(String emailId, String actionRef);

  /// "Clear Resolved" — acknowledge every active non-actionable email
  /// (`IMPORTANT` / `LOW_PRIORITY`). Never completes a reply/action task, never
  /// deletes anything, makes no Gmail call. Returns the number acknowledged.
  Future<int> clearAcknowledged();

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
  Future<GoogleAuthStartDto> startGoogleAuth() async {
    final res = await _client.post('/api/v1/auth/google/start');
    return GoogleAuthStartDto.fromJson(res as Map<String, dynamic>);
  }

  @override
  Future<GoogleSessionDto> exchangeSession(String code, {String? state}) async {
    final res = await _client.post(
      '/api/v1/auth/session/exchange',
      body: {'code': code, if (state != null && state.isNotEmpty) 'state': state},
    );
    return GoogleSessionDto.fromJson(res as Map<String, dynamic>);
  }

  @override
  Future<GoogleSessionDto?> pollGoogleSession(String flowId) async {
    final res = await _client.get(
      '/api/v1/auth/google/session',
      queryParameters: {'flow_id': flowId},
    );
    final map = res as Map<String, dynamic>;
    if (map['status'] == 'ready' || map['session_token'] != null) {
      return GoogleSessionDto.fromJson(map);
    }
    return null; // pending (HTTP 202)
  }

  @override
  Future<AuthMeDto> getCurrentUser() async {
    final res = await _client.get('/api/v1/auth/me');
    return AuthMeDto.fromJson(res as Map<String, dynamic>);
  }

  @override
  Future<void> logout({String? fcmToken}) async {
    await _client.post('/api/v1/auth/logout',
        body: fcmToken != null ? {'fcm_token': fcmToken} : null);
  }

  @override
  Future<void> registerDevice({
    required String fcmToken,
    String platform = 'android',
    String? deviceLabel,
    String? appVersion,
    String? previousToken,
  }) async {
    await _client.post('/api/v1/devices/register', body: {
      'fcm_token': fcmToken,
      'platform': platform,
      if (deviceLabel != null) 'device_label': deviceLabel,
      if (appVersion != null) 'app_version': appVersion,
      if (previousToken != null) 'previous_token': previousToken,
    });
  }

  @override
  Future<void> unregisterDevice(String fcmToken) async {
    await _client.post('/api/v1/devices/unregister', body: {'fcm_token': fcmToken});
  }

  @override
  Future<SystemStatusDto> getSystemStatus() async {
    final res = await _client.get('/api/v1/system/status');
    return SystemStatusDto.fromJson(res as Map<String, dynamic>);
  }

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
    bool? active,
    int limit = 100,
  }) async {
    final query = <String, dynamic>{
      if (priority != null) 'priority': priority,
      if (category != null) 'category': category,
      if (actionRequired != null) 'action_required': actionRequired,
      if (viewed != null) 'viewed': viewed,
      if (completed != null) 'completed': completed,
      if (active != null) 'active': active,
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
  Future<FullEmailDto> getFullEmail(String emailId) async {
    final res = await _client.get('/api/v1/emails/$emailId/full');
    return FullEmailDto.fromJson(res as Map<String, dynamic>);
  }

  @override
  Future<Email> submitClassificationFeedback(
      String emailId, PrimaryCategory category) async {
    final res = await _client.post(
      '/api/v1/emails/$emailId/classification-feedback',
      body: {'category': category.wire},
    );
    return DtoMapper.mapEmailState(
        EmailStateDetailOutDto.fromJson(res as Map<String, dynamic>));
  }

  @override
  Future<ReplySuggestionsDto> getReplySuggestions(String emailId) async {
    // The backend waits on the LLM here — allow a long ceiling (a slow local
    // model can take > 1 min to draft 3 options).
    final res = await _client.post(
      '/api/v1/emails/$emailId/reply-suggestions',
      timeout: ApiConfig.aiReceiveTimeout,
    );
    return ReplySuggestionsDto.fromJson(res as Map<String, dynamic>);
  }

  @override
  Future<ReplySendResultDto> sendReply(String emailId, String body) async {
    final res = await _client.post(
      '/api/v1/emails/$emailId/reply',
      body: {'body': body},
      timeout: ApiConfig.aiReceiveTimeout,
    );
    return ReplySendResultDto.fromJson(res as Map<String, dynamic>);
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
  Future<Email> markEmailComplete(String emailId) async {
    final res = await _client.patch('/api/v1/emails/$emailId/complete');
    return DtoMapper.mapEmailState(
        EmailStateDetailOutDto.fromJson(res as Map<String, dynamic>));
  }

  @override
  Future<Email> reopenEmail(String emailId) async {
    final res = await _client.patch('/api/v1/emails/$emailId/reopen');
    return DtoMapper.mapEmailState(
        EmailStateDetailOutDto.fromJson(res as Map<String, dynamic>));
  }

  @override
  Future<int> clearAcknowledged() async {
    final res = await _client.post('/api/v1/emails/clear-acknowledged');
    return (res as Map<String, dynamic>)['acknowledged'] as int? ?? 0;
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
  Future<GoogleAuthStartDto> startGoogleAuth() async => const GoogleAuthStartDto(
        authorizationUrl: 'https://accounts.google.com/o/oauth2/auth?mock=1',
        flowId: 'mock-flow',
      );

  @override
  Future<GoogleSessionDto> exchangeSession(String code, {String? state}) async =>
      const GoogleSessionDto(
        sessionToken: 'mock-session-token',
        user: AppUserDto(id: 1, googleEmail: 'demo.student@gmail.com', displayName: 'Demo Student'),
      );

  @override
  Future<GoogleSessionDto?> pollGoogleSession(String flowId) async => const GoogleSessionDto(
        sessionToken: 'mock-session-token',
        user: AppUserDto(id: 1, googleEmail: 'demo.student@gmail.com', displayName: 'Demo Student'),
      );

  @override
  Future<AuthMeDto> getCurrentUser() async => const AuthMeDto(
        user: AppUserDto(id: 1, googleEmail: 'demo.student@gmail.com', displayName: 'Demo Student'),
        gmailConnected: true,
      );

  @override
  Future<void> logout({String? fcmToken}) async {}

  @override
  Future<void> registerDevice({
    required String fcmToken,
    String platform = 'android',
    String? deviceLabel,
    String? appVersion,
    String? previousToken,
  }) async {}

  @override
  Future<void> unregisterDevice(String fcmToken) async {}

  @override
  Future<SystemStatusDto> getSystemStatus() async {
    return const SystemStatusDto(
      backendStatus: 'online',
      llmStatus: 'online',
      llmProvider: 'ollama',
      llmModel: 'qwen2.5:3b',
      llmDetail: '2 model(s) available',
    );
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
    bool? active,
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
    if (active != null) {
      list = list.where((e) => e.isActive == active).toList();
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
      primaryCategory: email.primaryCategory.wire,
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
  Future<FullEmailDto> getFullEmail(String emailId) async {
    _ensureInitialized();
    final email = await getEmailById(emailId);
    if (email == null) throw Exception('Email not found');
    return FullEmailDto(
      emailId: email.id,
      subject: email.subject,
      senderName: email.senderName,
      senderEmail: email.senderEmail,
      receivedAt: email.receivedAt,
      body: '${email.snippet}\n\n'
          'This is the full mock email body. It spans multiple lines so the '
          'reader can scroll.\n\nRegards,\n${email.senderName}',
      bodyFormat: 'text',
      primaryCategory: email.primaryCategory,
      finalCategory: email.analysis.category,
      priorityLevel: email.analysis.priority.displayName,
      actionRequired: email.analysis.actionRequired,
    );
  }

  @override
  Future<Email> submitClassificationFeedback(
      String emailId, PrimaryCategory category) async {
    _ensureInitialized();
    final index = _emails.indexWhere((e) => e.id == emailId);
    if (index == -1) throw Exception('Email not found');
    _emails[index] = _emails[index].copyWith(
      primaryCategory: category,
      primaryCategoryUserCorrected: true,
    );
    return _emails[index];
  }

  @override
  Future<ReplySuggestionsDto> getReplySuggestions(String emailId) async {
    return ReplySuggestionsDto(emailId: emailId, suggestions: const [
      ReplySuggestionDto(
        id: 'option_1',
        label: 'Direct',
        body: 'Yes, I will attend the project meeting tomorrow.',
      ),
      ReplySuggestionDto(
        id: 'option_2',
        label: 'Professional',
        body:
            'Thank you for the note. I expect to be able to join the meeting tomorrow; '
            'please let me know if there is anything I should review beforehand.',
      ),
      ReplySuggestionDto(
        id: 'option_3',
        label: 'Alternative',
        body:
            'Unfortunately I do not think I can make the meeting tomorrow. '
            'Could you share the key updates afterwards?',
      ),
    ]);
  }

  @override
  Future<ReplySendResultDto> sendReply(String emailId, String body) async {
    return ReplySendResultDto(
      emailId: emailId,
      threadId: 'mock-thread',
      gmailMessageId: 'mock-sent-1',
      replyActionCompleted: true,
      emailMarkedCompleted: true,
    );
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
  Future<Email> markEmailComplete(String emailId) async {
    _ensureInitialized();
    final index = _emails.indexWhere((e) => e.id == emailId);
    if (index == -1) throw Exception('Email not found');
    _emails[index] = _emails[index].copyWith(
      userState: _emails[index].userState.copyWith(isViewed: true, isCompleted: true),
    );
    return _emails[index];
  }

  @override
  Future<Email> reopenEmail(String emailId) async {
    _ensureInitialized();
    final index = _emails.indexWhere((e) => e.id == emailId);
    if (index == -1) throw Exception('Email not found');
    _emails[index] = _emails[index].copyWith(
      userState: _emails[index].userState.copyWith(isCompleted: false),
    );
    return _emails[index];
  }

  @override
  Future<int> clearAcknowledged() async {
    _ensureInitialized();
    var n = 0;
    for (var i = 0; i < _emails.length; i++) {
      final e = _emails[i];
      final nonActionable = e.primaryCategory == PrimaryCategory.important ||
          e.primaryCategory == PrimaryCategory.lowPriority;
      if (nonActionable && e.isActive && !e.userState.isViewed) {
        _emails[i] = e.copyWith(userState: e.userState.copyWith(isViewed: true));
        n++;
      }
    }
    return n;
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
      senderName: email?.senderName ?? 'Sorted',
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
