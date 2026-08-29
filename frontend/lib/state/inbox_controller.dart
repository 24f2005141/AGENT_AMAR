import 'dart:async';
import 'package:flutter/foundation.dart';
import '../config/api_config.dart';
import '../dto/email_state_dto.dart';
import '../models/email.dart';
import '../models/notification_event.dart';
import '../models/reminder.dart';
import '../services/api_error.dart';
import '../services/email_repository.dart';
import '../services/notification_service.dart';

class InboxController extends ChangeNotifier {
  final EmailRepository _repository;
  final NotificationService _notificationService;

  List<Email> _emails = [];
  List<ReminderItem> _reminders = [];
  List<NotificationEvent> _notifications = [];
  String _currentFilter = 'all';
  bool _isLoading = false;
  bool _isRefreshing = false;
  String? _errorMessage;
  bool _isGmailConnected = true;
  String? _connectedAccountEmail;
  // Phase 12/13: Gmail incremental-sync state (backend owns the monitoring loop).
  bool _gmailMonitoringActive = true;
  DateTime? _lastGmailSyncAt;
  String? _lastSyncStatus;
  NotificationEvent? _activeAlarm;
  Timer? _countdownTimer;

  InboxController({
    EmailRepository? repository,
    NotificationService? notificationService,
    bool enableCountdownTimer = true,
  })  : _repository = repository ??
            (ApiConfig.useMockData
                ? MockEmailRepository()
                : ApiEmailRepository()),
        _notificationService = notificationService ?? NotificationService() {
    checkGmailStatus();
    loadData();
    if (enableCountdownTimer) {
      _countdownTimer = Timer.periodic(const Duration(seconds: 1), (_) {
        notifyListeners();
      });
    }
  }

  @override
  void dispose() {
    _countdownTimer?.cancel();
    super.dispose();
  }

  // Getters
  bool get isLoading => _isLoading;
  bool get isRefreshing => _isRefreshing;
  String? get errorMessage => _errorMessage;
  bool get isGmailConnected => _isGmailConnected;
  String? get connectedAccountEmail => _connectedAccountEmail;
  /// True once the backend has a Gmail monitoring baseline (see Phase 12).
  bool get gmailMonitoringActive => _isGmailConnected && _gmailMonitoringActive;
  DateTime? get lastGmailSyncAt => _lastGmailSyncAt;
  String? get lastSyncStatus => _lastSyncStatus;
  String get currentFilter => _currentFilter;
  List<Email> get emails => _filteredEmails();
  List<Email> get allEmails => _emails;
  List<ReminderItem> get reminders => _reminders;
  List<NotificationEvent> get notifications => _notifications;
  NotificationEvent? get activeAlarm => _activeAlarm;

  List<Email> get needsAttentionEmails {
    return _emails.where((e) => e.analysis.actionRequired && !e.userState.isCompleted).toList()
      ..sort((a, b) {
        if (a.isCritical && !b.isCritical) return -1;
        if (!a.isCritical && b.isCritical) return 1;
        if (a.analysis.deadline != null && b.analysis.deadline != null) {
          return a.analysis.deadline!.compareTo(b.analysis.deadline!);
        }
        return b.receivedAt.compareTo(a.receivedAt);
      });
  }

  List<Email> get deadlineEmails {
    return _emails.where((e) => e.analysis.deadline != null).toList()
      ..sort((a, b) => a.analysis.deadline!.compareTo(b.analysis.deadline!));
  }

  Email? get criticalEmail {
    try {
      return needsAttentionEmails.firstWhere((e) => e.isCritical);
    } catch (_) {
      return null;
    }
  }

  Future<void> checkGmailStatus() async {
    try {
      final status = await _repository.getAuthStatus();
      _isGmailConnected = status.connected;
      _connectedAccountEmail = status.accountEmail;
      if (status.connected) {
        await _refreshGmailSyncStatus();
      } else {
        _gmailMonitoringActive = false;
      }
      notifyListeners();
    } catch (e) {
      if (e is ApiException && e.isGmailNotConnected) {
        _isGmailConnected = false;
        _gmailMonitoringActive = false;
        notifyListeners();
      }
    }
  }

  /// Reads the persistent Gmail monitoring baseline. Non-fatal — the backend
  /// establishes the baseline lazily on the first sync anyway.
  Future<void> _refreshGmailSyncStatus() async {
    try {
      final s = await _repository.getGmailSyncStatus();
      _gmailMonitoringActive = s.monitoring;
      _lastGmailSyncAt = s.lastSyncAt ?? _lastGmailSyncAt;
      if (s.accountEmail != null && s.accountEmail!.isNotEmpty) {
        _connectedAccountEmail = s.accountEmail;
      }
    } catch (_) {
      // Ignore — status is advisory only.
    }
  }

  Future<void> disconnectGmail() async {
    try {
      await _repository.disconnectGmail();
      _isGmailConnected = false;
      _gmailMonitoringActive = false;
      _connectedAccountEmail = null;
      notifyListeners();
    } catch (e) {
      _errorMessage = e.toString();
      notifyListeners();
    }
  }

  Future<void> loadData() async {
    _isLoading = true;
    _errorMessage = null;
    notifyListeners();

    try {
      _emails = await _repository.getEmails();
      _reminders = await _repository.getReminders();
      _notifications = await _repository.getNotifications();

      // Sync backend notifications with device local notifications
      await _notificationService.syncBackendNotifications(_notifications);

      // Check if there are any active alarms
      final alarmEvent = _notifications.where((n) => n.requiresAlarm && !n.isDismissed).firstOrNull;
      if (alarmEvent != null && _activeAlarm == null) {
        _activeAlarm = alarmEvent;
      }
    } on ApiException catch (e) {
      if (e.isGmailNotConnected) {
        _isGmailConnected = false;
      } else {
        _errorMessage = e.message;
      }
    } catch (e) {
      _errorMessage = 'Failed to load inbox data: $e';
    } finally {
      _isLoading = false;
      notifyListeners();
    }
  }

  Future<Email?> getEmailForNavigation(String emailId) async {
    final existing = _emails.where((e) => e.id == emailId).firstOrNull;
    if (existing != null) return existing;
    return await _repository.getEmailById(emailId);
  }

  /// Pull-to-refresh: ask the backend for ONE incremental Gmail sync, then
  /// refresh the data the app already shows.
  ///
  /// The backend scheduler owns continuous Gmail monitoring — the app never
  /// polls Gmail and never triggers the legacy bulk-ingest endpoint. Every
  /// sync status (`baselined` / `synced` / `history_expired_rebaselined` /
  /// `skipped_locked`) is a normal outcome, not an error.
  Future<void> refreshInbox() async {
    _isRefreshing = true;
    _errorMessage = null;
    notifyListeners();

    try {
      final result = await _repository.syncGmail();
      _lastSyncStatus = result.status;
      // Any successful response means a monitoring baseline now exists.
      _gmailMonitoringActive = true;
      _lastGmailSyncAt = result.lastSyncAt ?? DateTime.now();

      // Refresh only what the current app state already needs. loadData()
      // re-fetches emails + reminders + notifications and re-syncs device
      // notifications through the existing NotificationService.
      await loadData();
    } on ApiException catch (e) {
      if (e.isGmailNotConnected) {
        _isGmailConnected = false;
        _gmailMonitoringActive = false;
      } else {
        _errorMessage = e.message;
      }
    } catch (e) {
      _errorMessage = 'Failed to sync Gmail: $e';
    } finally {
      _isRefreshing = false;
      notifyListeners();
    }
  }

  void setFilter(String filter) {
    _currentFilter = filter;
    notifyListeners();
  }

  List<Email> _filteredEmails() {
    switch (_currentFilter) {
      case 'action_required':
        return _emails.where((e) => e.analysis.actionRequired && !e.userState.isCompleted).toList();
      case 'reply_needed':
        return _emails.where((e) => e.analysis.actionType == 'REPLY_REQUIRED' || e.analysis.actionType == 'REPLY').toList();
      case 'important':
        return _emails.where((e) => e.analysis.priority.displayName == 'HIGH' || e.analysis.priority.displayName == 'CRITICAL' || e.analysis.priority.displayName == 'URGENT').toList();
      case 'low_priority':
        return _emails.where((e) => e.analysis.priority.displayName == 'LOW').toList();
      case 'all':
      default:
        return _emails;
    }
  }

  Future<EmailStateDetailOutDto?> getEmailDetail(String emailId) async {
    return await _repository.getEmailDetailDto(emailId);
  }

  Future<void> markViewed(String emailId) async {
    try {
      final updated = await _repository.markEmailViewed(emailId);
      final index = _emails.indexWhere((e) => e.id == emailId);
      if (index != -1) {
        _emails[index] = updated;
        notifyListeners();
      }
    } catch (_) {}
  }

  Future<void> snoozeEmail(String emailId, DateTime until) async {
    try {
      final updated = await _repository.snoozeEmail(emailId, until);
      final index = _emails.indexWhere((e) => e.id == emailId);
      if (index != -1) {
        _emails[index] = updated;
        notifyListeners();
      }
    } catch (e) {
      _errorMessage = 'Failed to snooze: $e';
      notifyListeners();
    }
  }

  Future<void> clearSnooze(String emailId) async {
    try {
      final updated = await _repository.clearSnooze(emailId);
      final index = _emails.indexWhere((e) => e.id == emailId);
      if (index != -1) {
        _emails[index] = updated;
        notifyListeners();
      }
    } catch (e) {
      _errorMessage = 'Failed to clear snooze: $e';
      notifyListeners();
    }
  }

  Future<void> completeAction(String emailId, [String actionRef = 'act_001']) async {
    try {
      final updated = await _repository.completeAction(emailId, actionRef);
      final index = _emails.indexWhere((e) => e.id == emailId);
      if (index != -1) {
        _emails[index] = updated;
        notifyListeners();
      }
      await loadData();
    } catch (e) {
      _errorMessage = 'Failed to complete action: $e';
      notifyListeners();
    }
  }

  Future<void> dismissAction(String emailId, [String actionRef = 'act_001']) async {
    try {
      final updated = await _repository.dismissAction(emailId, actionRef);
      final index = _emails.indexWhere((e) => e.id == emailId);
      if (index != -1) {
        _emails[index] = updated;
        notifyListeners();
      }
      await loadData();
    } catch (e) {
      _errorMessage = 'Failed to dismiss action: $e';
      notifyListeners();
    }
  }

  Future<ReminderItem> createReminder({
    required String emailId,
    required DateTime reminderAt,
    String? actionRef,
    String? note,
  }) async {
    final reminder = await _repository.createReminder(
      emailId: emailId,
      reminderAt: reminderAt,
      actionRef: actionRef,
      note: note,
    );
    await loadData();
    return reminder;
  }

  Future<void> cancelReminder(String emailId, int reminderId) async {
    await _repository.cancelReminder(emailId, reminderId);
    await loadData();
  }

  Future<void> triggerMonitorCheck() async {
    try {
      final result = await _repository.runDeadlineCheck();
      final alarmDecision = result.results.where((r) => r.requiresAlarm).firstOrNull;

      await loadData();

      if (alarmDecision != null) {
        final matchingNotification = _notifications
            .where((n) => n.id == alarmDecision.notificationId.toString() || n.requiresAlarm)
            .firstOrNull;

        if (matchingNotification != null) {
          _activeAlarm = matchingNotification;
          notifyListeners();
        } else {
          _activeAlarm = NotificationEvent(
            id: alarmDecision.notificationId?.toString() ?? 'alarm_${DateTime.now().millisecondsSinceEpoch}',
            emailId: alarmDecision.emailId,
            emailSubject: 'Imminent Deadline Alarm',
            notificationType: 'deadline_escalation',
            severity: NotificationSeverity.alarm,
            title: 'ACTION REQUIRED: DEADLINE ALARM',
            message: alarmDecision.reason,
            createdAt: DateTime.now(),
            requiresAlarm: true,
          );
          notifyListeners();
        }
      }
    } catch (e) {
      _errorMessage = 'Deadline monitor check failed: $e';
      notifyListeners();
    }
  }

  void dismissActiveAlarm() {
    _activeAlarm = null;
    notifyListeners();
  }
}
