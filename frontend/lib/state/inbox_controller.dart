import 'dart:async';
import 'package:flutter/foundation.dart';
import '../config/api_config.dart';
import '../dto/email_state_dto.dart';
import '../dto/ai_mode_dto.dart';
import '../dto/full_email_dto.dart';
import '../dto/reply_dto.dart';
import '../models/agent_analysis.dart';
import '../models/email.dart';
import '../models/notification_event.dart';
import '../models/scheduled_event.dart';
import '../services/api_error.dart';
import '../services/email_repository.dart';
import '../services/home_widget_service.dart';
import '../services/local_schedule_service.dart';
import '../services/notification_service.dart';

class InboxController extends ChangeNotifier {
  final EmailRepository _repository;
  final NotificationService _notificationService;

  /// Device-local scheduling (reminders + deadline alarms). This controller
  /// never fires a notification itself — it only tells the schedule service
  /// what the backend now says, and the OS does the firing (see
  /// `LocalScheduleService`).
  final LocalScheduleService _schedule;

  /// The ONE way home-screen widgets get refreshed. Nothing else in the app
  /// touches the widget platform: this controller owns the app's view of
  /// active items, so it is the single place that can say "what the widgets
  /// show has changed". See `HomeWidgetService`.
  final HomeWidgetService _widgets;

  // The homepage is an attention dashboard, not an archive: `_emails` holds only
  // the emails that still need the user's attention (backend `?active=true`).
  // Resolved / acknowledged mail is loaded on demand into `_resolvedEmails` for
  // the history / "completed" views — it is never deleted, just off the feed.
  List<Email> _emails = [];
  List<Email> _resolvedEmails = [];
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

  // System connection status (backend reachable? + configured LLM status).
  // FastAPI checks the LLM provider — the app never contacts an LLM directly.
  bool _backendOnline = true;
  String _llmStatus = 'unknown'; // online | offline | unconfigured | unknown
  String? _llmProvider;
  String? _llmModel;
  AiModeDto? _aiMode;
  bool _isAiModeUpdating = false;
  String? _aiModeError;

  /// How often to silently reload persisted backend state while foregrounded.
  /// `null` disables the poll (resume-only refresh still runs). Never triggers a
  /// Gmail sync / LLM — the backend scheduler owns Gmail monitoring.
  final Duration? _autoRefreshInterval;
  Timer? _autoRefreshTimer;

  InboxController({
    EmailRepository? repository,
    NotificationService? notificationService,
    LocalScheduleService? scheduleService,
    HomeWidgetService? widgetService,
    bool enableCountdownTimer = true,
    Duration? autoRefreshInterval,
    bool autoRefreshOverride = false,
  }) : _repository =
           repository ??
           (ApiConfig.useMockData
               ? MockEmailRepository()
               : ApiEmailRepository()),
       _notificationService = notificationService ?? NotificationService(),
       _schedule = scheduleService ?? LocalScheduleService(),
       _widgets = widgetService ?? HomeWidgetService(),
       _autoRefreshInterval = autoRefreshOverride
           ? autoRefreshInterval
           : (autoRefreshInterval ?? ApiConfig.foregroundRefreshInterval) {
    // ONE hook for home-screen widgets: every state change this controller
    // publishes flows through here, so no UI widget or repository ever calls
    // the widget platform itself. Cheap by construction — see
    // [_publishWidgets].
    addListener(_publishWidgets);
    refreshSystemStatus();
    refreshAiMode();
    checkGmailStatus();
    loadData();
    if (enableCountdownTimer) {
      _countdownTimer = Timer.periodic(const Duration(seconds: 1), (_) {
        notifyListeners();
      });
      startAutoRefresh();
    }
  }

  bool _disposed = false;

  @override
  void dispose() {
    _disposed = true;
    removeListener(_publishWidgets);
    _countdownTimer?.cancel();
    _autoRefreshTimer?.cancel();
    super.dispose();
  }

  /// Start the lightweight foreground poll of persisted backend state.
  /// Idempotent; no-op when the interval is disabled. Call on app resume.
  void startAutoRefresh() {
    if (_disposed ||
        _autoRefreshInterval == null ||
        _autoRefreshTimer != null) {
      return;
    }
    _autoRefreshTimer = Timer.periodic(
      _autoRefreshInterval,
      (_) => autoRefresh(),
    );
  }

  /// Stop the foreground poll (call when the app is backgrounded).
  void stopAutoRefresh() {
    _autoRefreshTimer?.cancel();
    _autoRefreshTimer = null;
  }

  /// Silent reload of persisted backend state (emails / reminders /
  /// notifications). A cheap GET — NO Gmail sync, NO LLM. User-set reminders
  /// are device-local now (see LocalScheduleService) and are never fetched
  /// here. Updates the UI only when something actually changed, and never
  /// surfaces a transient error.
  Future<void> autoRefresh() async {
    if (_disposed || _isLoading || _isRefreshing) return;
    try {
      final emails = await _repository.getEmails(active: true);
      final notifications = await _repository.getNotifications();

      final changed =
          !_sameEmails(emails, _emails) ||
          notifications.length != _notifications.length;

      _emails = emails;
      _notifications = notifications;
      await _notificationService.syncBackendNotifications(_notifications);
      _reconcileDeadlineSchedule();

      final alarmEvent = _notifications
          .where((n) => n.requiresAlarm && !n.isDismissed)
          .firstOrNull;
      if (alarmEvent != null && _activeAlarm == null) {
        _activeAlarm = alarmEvent;
      }
      if (changed || alarmEvent != null) _notify();
    } on ApiException catch (e) {
      // background poll — stay quiet; centralised 401 handling still applies.
      if (e.isGmailNotConnected) {
        _isGmailConnected = false;
        _notify();
      }
    } catch (_) {
      // ignore — the next tick tries again
    }
  }

  List<Email>? _lastWidgetEmails;
  Future<void>? _widgetWork;

  /// The in-flight widget publish, if any — awaited by tests.
  @visibleForTesting
  Future<void> get pendingWidgetWork => _widgetWork ?? Future<void>.value();

  /// Push the current active-item state to the Android home-screen widgets.
  ///
  /// Wired once, as a listener on this controller, so *every* state change —
  /// a sync, a push-driven reload, marking something done, dismissing,
  /// sending a reply, clearing the inbox — refreshes the widgets without a
  /// single `updateWidget` call anywhere else in the codebase.
  ///
  /// Two cheap guards keep that free: the once-a-second countdown rebuild
  /// doesn't replace the `_emails` list, so it is skipped by identity here,
  /// and `HomeWidgetService` additionally skips the platform write when the
  /// rendered content is unchanged. No API call is involved at any point.
  void _publishWidgets() {
    if (_disposed) return;
    if (identical(_lastWidgetEmails, _emails)) return;
    _lastWidgetEmails = _emails;
    _widgetWork = _widgets.publish(_emails).catchError((Object e) {
      // A home-screen widget must never be able to break the inbox.
      debugPrint('[InboxController] widget publish failed: $e');
      return false;
    });
  }

  /// Apply "Done" taps made on the Focus Now widget while the app was closed.
  ///
  /// The widget itself never calls the backend; it records the intent locally.
  /// The backend stays authoritative, so the app completes the item properly
  /// here on the next load.
  Future<void> flushPendingWidgetCompletions() async {
    final pending = await _widgets.takePendingCompletions();
    for (final emailId in pending) {
      await markComplete(emailId);
    }
  }

  /// The in-flight schedule reconciliation, if any — awaited by tests that
  /// need to observe the resulting schedule deterministically.
  @visibleForTesting
  Future<void> get pendingScheduleWork => _scheduleWork ?? Future<void>.value();
  Future<void>? _scheduleWork;

  /// Hand the freshly-fetched backend truth to the device scheduler so any
  /// deadline it now knows about is scheduled with the OS (Category A).
  ///
  /// This is reconciliation, NOT recreation: an unchanged deadline is left
  /// completely untouched, so loadData / autoRefresh / a Gmail sync / an app
  /// resume can run as often as they like without ever duplicating an alarm.
  /// Only ever called after a SUCCESSFUL fetch — reconciling against a
  /// partial list from a failed request would cancel live alarms.
  ///
  /// Deliberately NOT awaited by its callers: OS scheduling must never block,
  /// slow, or break loading the inbox. Ordering is still safe because
  /// `LocalScheduleService` serialises its own mutations, so two overlapping
  /// reconciliations can't double-schedule.
  void _reconcileDeadlineSchedule() {
    _scheduleWork = _runReconcile();
  }

  Future<void> _runReconcile() async {
    try {
      await _schedule.syncDeadlines([
        for (final e in _emails)
          if (e.analysis.deadline != null)
            DeadlineTarget(
              emailId: e.id,
              subject: e.subject,
              deadlineAt: e.analysis.deadline!,
              isCompleted: e.userState.isCompleted,
            ),
      ]);
    } catch (e) {
      // Scheduling is best-effort and must never break the inbox.
      debugPrint(
        '[InboxController] Deadline schedule reconciliation failed: $e',
      );
    }
  }

  bool _sameEmails(List<Email> a, List<Email> b) {
    if (a.length != b.length) return false;
    for (var i = 0; i < a.length; i++) {
      final x = a[i], y = b[i];
      if (x.id != y.id ||
          x.primaryCategory != y.primaryCategory ||
          x.userState.isViewed != y.userState.isViewed ||
          x.userState.isCompleted != y.userState.isCompleted ||
          x.isUnread != y.isUnread) {
        return false;
      }
    }
    return true;
  }

  /// Notify only while alive. Centralised 401 handling can tear this controller
  /// down (Login transition) while one of its async loads is still in-flight.
  void _notify() {
    if (!_disposed) notifyListeners();
  }

  // Getters
  bool get isLoading => _isLoading;
  bool get isRefreshing => _isRefreshing;
  String? get errorMessage => _errorMessage;
  bool get isGmailConnected => _isGmailConnected;
  String? get connectedAccountEmail => _connectedAccountEmail;

  /// The FastAPI backend responded to the last status check.
  bool get backendOnline => _backendOnline;

  /// Configured LLM status as reported by FastAPI:
  /// `online` | `offline` | `unconfigured` | `unknown`.
  /// `unknown` while the backend is unreachable — the app never guesses.
  String get llmStatus => _backendOnline ? _llmStatus : 'unknown';
  String? get llmProvider => _llmProvider;
  String? get llmModel => _llmModel;
  AiModeDto? get aiMode => _aiMode;
  bool get isAiModeUpdating => _isAiModeUpdating;
  String? get aiModeError => _aiModeError;

  /// True once the backend has a Gmail monitoring baseline (see Phase 12).
  bool get gmailMonitoringActive => _isGmailConnected && _gmailMonitoringActive;
  DateTime? get lastGmailSyncAt => _lastGmailSyncAt;
  String? get lastSyncStatus => _lastSyncStatus;
  String get currentFilter => _currentFilter;
  List<Email> get emails => _filteredEmails();

  /// The active-attention feed (what the homepage shows). Never includes
  /// resolved / acknowledged mail — that lives in [resolvedEmails].
  List<Email> get allEmails => _emails;

  /// Resolved / acknowledged / historical emails, loaded on demand by
  /// [loadResolvedEmails] for the "completed" / history views. Empty until then.
  List<Email> get resolvedEmails => _resolvedEmails;

  List<NotificationEvent> get notifications => _notifications;
  NotificationEvent? get activeAlarm => _activeAlarm;

  List<Email> get needsAttentionEmails {
    // "Action Required" = the canonical backend bucket, NOT `analysis.actionRequired`
    // (a Reply Required email also has an action, but its primary bucket is
    // REPLY_REQUIRED — it must not show here too).
    return _emails
        .where(
          (e) =>
              e.primaryCategory == PrimaryCategory.actionRequired &&
              !e.userState.isCompleted,
        )
        .toList()
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

  /// Refresh the backend + LLM connection status shown in the top status bar.
  ///
  /// Lightweight: `GET /api/v1/system/status` never triggers Gmail sync, agent
  /// workflows or LLM inference. Called on start, on app resume and after
  /// pull-to-refresh — never in a polling loop.
  Future<void> refreshSystemStatus() async {
    try {
      final s = await _repository.getSystemStatus();
      _backendOnline = true;
      _llmStatus = s.llmStatus;
      _llmProvider = s.llmProvider;
      _llmModel = s.llmModel;
    } on ApiException catch (e) {
      // Session expiry is handled centrally by ApiClient.onUnauthorized (clears
      // the token + drops to Login); nothing to do here. Any other error =
      // backend unreachable — do NOT pretend to know the LLM state.
      if (!e.isAuthExpired) {
        _backendOnline = false;
        _llmStatus = 'unknown';
      }
    } catch (_) {
      _backendOnline = false;
      _llmStatus = 'unknown';
    } finally {
      _notify();
    }
  }

  Future<void> refreshAiMode() async {
    try {
      _aiMode = await _repository.getAiMode();
      _aiModeError = null;
    } catch (e) {
      _aiModeError = e is ApiException ? e.message : 'Could not load AI mode';
    } finally {
      _notify();
    }
  }

  Future<bool> selectAiMode(String mode) async {
    if (_isAiModeUpdating || mode == _aiMode?.selected) return true;
    _isAiModeUpdating = true;
    _aiModeError = null;
    _notify();
    try {
      _aiMode = await _repository.setAiMode(mode);
      await refreshSystemStatus();
      return true;
    } catch (e) {
      _aiModeError = e is ApiException ? e.message : 'Could not change AI mode';
      return false;
    } finally {
      _isAiModeUpdating = false;
      _notify();
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
      _notify();
    } catch (e) {
      // Session expiry: handled centrally (ApiClient.onUnauthorized).
      if (e is ApiException && e.isGmailNotConnected) {
        _isGmailConnected = false;
        _gmailMonitoringActive = false;
        _notify();
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
      _emails = await _repository.getEmails(active: true);
      _notifications = await _repository.getNotifications();

      // Sync backend notifications with device local notifications
      await _notificationService.syncBackendNotifications(_notifications);

      // Hand any deadline the backend now reports to the OS scheduler, so it
      // fires later without the app, a fetch, or the backend. Reconciled, so
      // this is a no-op when nothing has actually changed.
      _reconcileDeadlineSchedule();

      // Check if there are any active alarms
      final alarmEvent = _notifications
          .where((n) => n.requiresAlarm && !n.isDismissed)
          .firstOrNull;
      if (alarmEvent != null && _activeAlarm == null) {
        _activeAlarm = alarmEvent;
      }
    } on ApiException catch (e) {
      // Session expiry: handled centrally (ApiClient.onUnauthorized).
      if (e.isGmailNotConnected) {
        _isGmailConnected = false;
      } else if (!e.isAuthExpired) {
        _errorMessage = e.message;
      }
    } catch (e) {
      _errorMessage = 'Failed to load inbox data: $e';
    } finally {
      _isLoading = false;
      _notify();
    }
  }

  /// Load the resolved / acknowledged / historical emails for the "completed" /
  /// history views. Kept separate from the attention feed — a cheap GET, no
  /// Gmail sync. Safe to call repeatedly.
  Future<void> loadResolvedEmails() async {
    try {
      _resolvedEmails = await _repository.getEmails(active: false);
      _notify();
    } on ApiException catch (e) {
      if (!e.isAuthExpired) _errorMessage = e.message;
    } catch (_) {
      // history is non-critical — ignore transient failures
    }
  }

  /// Drop any email that no longer needs attention from the live feed, so the
  /// card disappears the instant its state changes (no refetch, no app
  /// restart). The email is not deleted — it moves to [resolvedEmails].
  void _pruneInactive() {
    final removed = _emails.where((e) => !e.isActive).toList();
    if (removed.isEmpty) return;
    _emails = _emails.where((e) => e.isActive).toList();
    // keep the history list coherent for a view that is already open
    final ids = removed.map((e) => e.id).toSet();
    _resolvedEmails = [
      ..._resolvedEmails.where((e) => !ids.contains(e.id)),
      ...removed,
    ];
  }

  Future<Email?> getEmailForNavigation(String emailId) async {
    final existing = _emails.where((e) => e.id == emailId).firstOrNull;
    if (existing != null) return existing;
    final resolved = _resolvedEmails.where((e) => e.id == emailId).firstOrNull;
    if (resolved != null) return resolved;
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

    // Refresh the connection status bar alongside the pull-to-refresh.
    unawaited(refreshSystemStatus());

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
      // Session expiry: handled centrally (ApiClient.onUnauthorized).
      if (e.isGmailNotConnected) {
        _isGmailConnected = false;
        _gmailMonitoringActive = false;
      } else if (!e.isAuthExpired) {
        _errorMessage = e.message;
      }
    } catch (e) {
      _errorMessage = 'Failed to sync Gmail: $e';
    } finally {
      _isRefreshing = false;
      _notify();
    }
  }

  void setFilter(String filter) {
    _currentFilter = filter;
    notifyListeners();
  }

  List<Email> _filteredEmails() {
    // Every chip maps to exactly ONE canonical backend bucket (primary_category)
    // — mutually exclusive, so an email is never in two filtered sections.
    switch (_currentFilter) {
      case 'action_required':
        return _emails
            .where(
              (e) =>
                  e.primaryCategory == PrimaryCategory.actionRequired &&
                  !e.userState.isCompleted,
            )
            .toList();
      case 'reply_needed':
        return _emails
            .where((e) => e.primaryCategory == PrimaryCategory.replyRequired)
            .toList();
      case 'important':
        return _emails
            .where((e) => e.primaryCategory == PrimaryCategory.important)
            .toList();
      case 'low_priority':
        return _emails
            .where((e) => e.primaryCategory == PrimaryCategory.lowPriority)
            .toList();
      case 'all':
      default:
        return _emails;
    }
  }

  Future<EmailStateDetailOutDto?> getEmailDetail(String emailId) async {
    return await _repository.getEmailDetailDto(emailId);
  }

  /// The complete email body (backend fetches it live from Gmail; always
  /// sanitised plain text). Errors propagate so the full-email screen renders
  /// its own error state.
  Future<FullEmailDto> getFullEmail(String emailId) {
    return _repository.getFullEmail(emailId);
  }

  /// Manually correct an email's primary classification (Phase 18). Updates the
  /// local list immediately so the email moves out of its old section — every
  /// section filters on `primaryCategory`. Errors propagate to the caller.
  Future<Email> submitClassificationFeedback(
    String emailId,
    PrimaryCategory category,
  ) async {
    final updated = await _repository.submitClassificationFeedback(
      emailId,
      category,
    );
    final index = _emails.indexWhere((e) => e.id == emailId);
    if (index != -1) {
      _emails[index] = updated;
    } else if (updated.isActive) {
      _emails = [..._emails, updated];
    }
    _pruneInactive();
    notifyListeners();
    return updated;
  }

  /// Ask the backend for exactly 3 AI reply drafts. The backend owns the LLM
  /// call and the "does this need a reply" decision. Errors ([ApiException])
  /// propagate so the detail screen can render its own error/retry state.
  Future<ReplySuggestionsDto> generateReplySuggestions(String emailId) {
    return _repository.getReplySuggestions(emailId);
  }

  /// Send a user-approved reply. Only ever called from an explicit Send tap.
  /// On success the inbox is reloaded so a now-answered email reflects its
  /// completed state. Errors propagate (the screen preserves the draft).
  Future<ReplySendResultDto> sendReply(String emailId, String body) async {
    final result = await _repository.sendReply(emailId, body);
    // A sent reply completes the email backend-side — drop its deadline alarms.
    await _schedule.cancelForEmail(emailId);
    await loadData();
    return result;
  }

  /// Persist "the user opened this email". The backend is authoritative — the
  /// card only leaves the feed once the PATCH has succeeded (an informational
  /// Important / Low Priority email leaves the moment it is acknowledged; a
  /// Reply / Action email stays). A transient failure is retried once; if it
  /// still fails the card is kept (never silently hidden) so the next refresh
  /// re-reconciles from the backend.
  Future<void> markViewed(String emailId) async {
    for (var attempt = 0; attempt < 2; attempt++) {
      try {
        final updated = await _repository.markEmailViewed(emailId);
        final index = _emails.indexWhere((e) => e.id == emailId);
        if (index != -1) {
          _emails[index] = updated;
          _pruneInactive();
          notifyListeners();
        }
        return;
      } on ApiException catch (e) {
        if (e.isAuthExpired || e.statusCode == 404) return; // nothing to retry
        if (attempt == 1) return; // give up quietly; refresh will reconcile
      } catch (_) {
        if (attempt == 1) return;
      }
    }
  }

  Future<void> snoozeEmail(String emailId, DateTime until) async {
    try {
      final updated = await _repository.snoozeEmail(emailId, until);
      final index = _emails.indexWhere((e) => e.id == emailId);
      if (index != -1) {
        _emails[index] = updated;
        _pruneInactive();
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
      } else if (updated.isActive) {
        // un-snoozing brings it back onto the feed
        _emails = [..._emails, updated];
        _resolvedEmails = _resolvedEmails
            .where((e) => e.id != emailId)
            .toList();
        notifyListeners();
      }
    } catch (e) {
      _errorMessage = 'Failed to clear snooze: $e';
      notifyListeners();
    }
  }

  /// Explicitly resolve the whole email (the "mark done" / tick affordance).
  /// Completes every pending action on the backend and drops the card from the
  /// active feed immediately. The backend response is authoritative — a failure
  /// keeps the card and surfaces an error.
  Future<Email?> markComplete(String emailId) async {
    try {
      final updated = await _repository.markEmailComplete(emailId);
      // The task is done — kill its future deadline alarms immediately rather
      // than waiting for the next reconcile, so no ghost alarm can fire.
      await _schedule.cancelForEmail(emailId);
      final index = _emails.indexWhere((e) => e.id == emailId);
      if (index != -1) {
        _emails[index] = updated;
        _pruneInactive();
      } else {
        _resolvedEmails = [
          ..._resolvedEmails.where((e) => e.id != emailId),
          updated,
        ];
      }
      notifyListeners();
      return updated;
    } on ApiException catch (e) {
      if (!e.isAuthExpired) {
        _errorMessage = 'Could not mark this as done: ${e.message}';
      }
      notifyListeners();
      return null;
    } catch (e) {
      _errorMessage = 'Could not mark this as done: $e';
      notifyListeners();
      return null;
    }
  }

  /// Undo a completion.
  Future<void> reopenEmail(String emailId) async {
    try {
      final updated = await _repository.reopenEmail(emailId);
      _resolvedEmails = _resolvedEmails.where((e) => e.id != emailId).toList();
      if (updated.isActive && !_emails.any((e) => e.id == emailId)) {
        _emails = [..._emails, updated];
      }
      notifyListeners();
    } catch (_) {
      /* non-critical */
    }
  }

  /// "Clear Resolved" — acknowledge every active non-actionable email on the
  /// backend (Important / Low Priority). Never completes a reply/action task,
  /// never deletes a Gmail message. Reloads the feed from the backend
  /// afterwards so the UI is authoritative. Returns the number acknowledged.
  Future<int> clearAcknowledged() async {
    try {
      final n = await _repository.clearAcknowledged();
      await loadData();
      await loadResolvedEmails();
      return n;
    } on ApiException catch (e) {
      if (!e.isAuthExpired) {
        _errorMessage = 'Could not clear resolved items: ${e.message}';
        _notify();
      }
      return 0;
    }
  }

  Future<void> completeAction(
    String emailId, [
    String actionRef = 'act_001',
  ]) async {
    try {
      final updated = await _repository.completeAction(emailId, actionRef);
      // Action done → no future deadline alarm for it (Part 7: no ghost alarms).
      await _schedule.cancelForEmail(emailId);
      final index = _emails.indexWhere((e) => e.id == emailId);
      if (index != -1) {
        _emails[index] = updated;
        _pruneInactive();
        notifyListeners();
      }
      await loadData();
    } catch (e) {
      _errorMessage = 'Failed to complete action: $e';
      notifyListeners();
    }
  }

  Future<void> dismissAction(
    String emailId, [
    String actionRef = 'act_001',
  ]) async {
    try {
      final updated = await _repository.dismissAction(emailId, actionRef);
      await _schedule.cancelForEmail(emailId);
      final index = _emails.indexWhere((e) => e.id == emailId);
      if (index != -1) {
        _emails[index] = updated;
        _pruneInactive();
        notifyListeners();
      }
      await loadData();
    } catch (e) {
      _errorMessage = 'Failed to dismiss action: $e';
      notifyListeners();
    }
  }

  // Reminders are device-local now (see LocalScheduleService) — this
  // controller no longer creates, lists, or cancels them. The backend
  // reminder API (EmailRepository.createReminder/getReminders/cancelReminder)
  // is kept but unused by the app; see the reminder-redesign audit notes.

  Future<void> triggerMonitorCheck() async {
    try {
      final result = await _repository.runDeadlineCheck();
      final alarmDecision = result.results
          .where((r) => r.requiresAlarm)
          .firstOrNull;

      await loadData();

      if (alarmDecision != null) {
        final matchingNotification = _notifications
            .where(
              (n) =>
                  n.id == alarmDecision.notificationId.toString() ||
                  n.requiresAlarm,
            )
            .firstOrNull;

        if (matchingNotification != null) {
          _activeAlarm = matchingNotification;
          notifyListeners();
        } else {
          _activeAlarm = NotificationEvent(
            id:
                alarmDecision.notificationId?.toString() ??
                'alarm_${DateTime.now().millisecondsSinceEpoch}',
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
