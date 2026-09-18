import 'dart:async';

import 'package:flutter/foundation.dart';
import '../models/scheduled_event.dart';
import 'notification_service.dart';
import 'schedule_notifier.dart';
import 'scheduled_event_store.dart';

/// THE single local scheduling system for everything time-based the device
/// already knows about (Category A): user reminders and AI-extracted deadline
/// warnings/alarms.
///
/// Rules this type enforces:
///  * Every scheduled event is persisted locally and handed to the OS
///    scheduler. Firing never depends on an API call, a poll, a Gmail sync,
///    a pull-to-refresh, or the app being open.
///  * Reconciliation, not recreation: repeated backend data (loadData /
///    autoRefresh / Gmail sync / app resume) never duplicates an alarm — an
///    unchanged event is left completely untouched.
///  * Cancellation is idempotent and cancels the real OS notification.
///
/// Category B (an email the device has not learned about yet) is explicitly
/// NOT this type's job — that is backend Gmail monitoring + push. Once a push
/// or a refresh teaches the app about a new deadline, it lands here.
class LocalScheduleService extends ChangeNotifier {
  static LocalScheduleService? _appInstance;

  /// How far ahead of a deadline to warn. `Duration.zero` is the deadline
  /// moment itself (the alarm). Ordered soonest-warning-last for readability.
  static const List<Duration> deadlineWarningOffsets = [
    Duration(hours: 24),
    Duration(hours: 1),
    Duration.zero,
  ];

  /// Safety ceiling on pending deadline events. iOS only keeps 64 pending
  /// local notifications, and a user's reminders must never be crowded out by
  /// deadline warnings — the soonest deadlines win.
  static const int maxPendingDeadlineEvents = 48;

  /// The single app-wide instance: real device storage, real OS scheduling.
  factory LocalScheduleService() => _appInstance ??= LocalScheduleService._(
        store: SharedPreferencesScheduleStore(),
        notifier: NotificationService(),
      );

  /// A throwaway instance for tests, backed by injected fakes. Never reads or
  /// mutates the real app-wide singleton.
  @visibleForTesting
  factory LocalScheduleService.forTest({
    required ScheduledEventStore store,
    required ScheduleNotifier notifier,
  }) =>
      LocalScheduleService._(store: store, notifier: notifier);

  LocalScheduleService._({
    required ScheduledEventStore store,
    required ScheduleNotifier notifier,
  })  : _store = store,
        _notifier = notifier;

  final ScheduledEventStore _store;
  final ScheduleNotifier _notifier;

  /// Exposes the backing store so a test can simulate "app restart" by
  /// constructing a fresh service over the same store.
  @visibleForTesting
  ScheduledEventStore get debugStore => _store;

  List<ScheduledEvent> _events = [];
  bool _loaded = false;

  /// Serialises every mutation. Two reconciliations can genuinely overlap —
  /// `InboxController`'s constructor kicks off a `loadData()` while a
  /// pull-to-refresh or the resume poll starts another — and without this
  /// they would both read an empty schedule and each create the same alarms,
  /// producing exact duplicates.
  Future<void> _lock = Future<void>.value();

  Future<T> _serialize<T>(Future<T> Function() action) {
    final completer = Completer<T>();
    _lock = _lock.then((_) async {
      try {
        completer.complete(await action());
      } catch (e, s) {
        completer.completeError(e, s);
      }
    });
    return completer.future;
  }

  // --- reads ---------------------------------------------------------

  /// All events (any status), soonest-scheduled first.
  List<ScheduledEvent> get all {
    final copy = List<ScheduledEvent>.of(_events);
    copy.sort((a, b) => a.scheduledAt.compareTo(b.scheduledAt));
    return List.unmodifiable(copy);
  }

  List<ScheduledEvent> get pending => all.where((e) => e.isPending).toList();

  /// User-created reminders only — what the Reminders tab shows.
  List<ScheduledEvent> get reminders =>
      all.where((e) => e.type == ScheduledEventType.userReminder).toList();

  List<ScheduledEvent> get pendingReminders =>
      reminders.where((e) => e.isPending).toList();

  /// Deadline warnings/alarms only.
  List<ScheduledEvent> get pendingDeadlineEvents =>
      pending.where((e) => e.type.isDeadline).toList();

  List<ScheduledEvent> forEmail(String emailId) =>
      all.where((e) => e.emailId == emailId).toList();

  /// The current pending user reminder for [emailId], if any.
  ScheduledEvent? pendingReminderForEmail(String emailId) {
    for (final e in pendingReminders) {
      if (e.emailId == emailId) return e;
    }
    return null;
  }

  /// Pending deadline warnings/alarms for one email (for "alarms set" UI).
  List<ScheduledEvent> pendingDeadlineEventsForEmail(String emailId) =>
      pendingDeadlineEvents.where((e) => e.emailId == emailId).toList();

  // --- lifecycle -----------------------------------------------------

  /// Load persisted events (call once at startup) and reconcile:
  ///  * an event still pending whose time already passed while the app was
  ///    closed is marked fired — the OS already delivered it independently of
  ///    this call; this only stops the UI calling it "upcoming".
  ///  * every event still pending and in the future is (re)scheduled with the
  ///    OS. Deliberately redundant with Android's native boot receiver — a
  ///    harmless belt-and-braces pass for the rare case that didn't run.
  Future<void> load() => _serialize(_loadLocked);

  Future<void> _loadLocked() async {
    _events = await _store.loadAll();
    _loaded = true;
    final now = DateTime.now().toUtc();
    var changed = false;
    for (var i = 0; i < _events.length; i++) {
      final e = _events[i];
      if (e.isPending && e.scheduledAt.isBefore(now)) {
        _events[i] = e.copyWith(status: ScheduledEventStatus.fired);
        changed = true;
      }
    }
    if (changed) await _persist();
    for (final e in _events.where((e) => e.isPending)) {
      await _schedule(e);
    }
    notifyListeners();
  }

  Future<void> _persist() => _store.saveAll(_events);

  Future<void> _schedule(ScheduledEvent e) => _notifier.scheduleEvent(
        id: e.id,
        title: e.title,
        body: e.body,
        scheduledAt: e.scheduledAt.toLocal(),
        isAlarm: e.isAlarm,
        emailId: e.emailId,
      );

  // --- user reminders -------------------------------------------------

  /// Create and immediately schedule a user reminder. [scheduledAt] is
  /// interpreted in the device's local timezone. Reminders are always
  /// optional — nothing in the app creates one implicitly.
  Future<ScheduledEvent> createReminder({
    String? emailId,
    required String label,
    required DateTime scheduledAt,
  }) =>
      _serialize(() => _createReminderLocked(
            emailId: emailId,
            label: label,
            scheduledAt: scheduledAt,
          ));

  Future<ScheduledEvent> _createReminderLocked({
    String? emailId,
    required String label,
    required DateTime scheduledAt,
  }) async {
    if (!_loaded) await _loadLocked();
    final id = await _store.nextId();
    final now = DateTime.now().toUtc();
    final event = ScheduledEvent(
      id: id,
      type: ScheduledEventType.userReminder,
      emailId: emailId,
      sourceKey: 'reminder:$id',
      title: 'Sorted Reminder',
      body: label,
      scheduledAt: scheduledAt.toUtc(),
      createdAt: now,
      updatedAt: now,
    );
    _events = [..._events, event];
    await _persist();
    await _schedule(event);
    notifyListeners();
    return event;
  }

  /// Reschedule an event to [newTime], re-using its SAME stable id so the OS
  /// replaces the previous alarm outright — never a second, duplicate one.
  Future<ScheduledEvent> reschedule(int id, DateTime newTime) =>
      _serialize(() => _rescheduleLocked(id, newTime));

  Future<ScheduledEvent> _rescheduleLocked(int id, DateTime newTime) async {
    final idx = _events.indexWhere((e) => e.id == id);
    if (idx == -1) {
      throw StateError('Scheduled event $id does not exist.');
    }
    final updated = _events[idx].copyWith(
      scheduledAt: newTime.toUtc(),
      status: ScheduledEventStatus.pending,
    );
    _events = List.of(_events)..[idx] = updated;
    await _persist();
    await _schedule(updated);
    notifyListeners();
    return updated;
  }

  // --- cancellation ---------------------------------------------------

  /// Cancel one event. Idempotent — the OS cancel is always issued first
  /// (harmless if nothing is scheduled for that id), so an event can never
  /// "come back"; calling it twice, or on an unknown id, is a safe no-op.
  Future<void> cancel(int id) => _serialize(() => _cancelLocked(id));

  Future<void> _cancelLocked(int id) async {
    await _notifier.cancelEvent(id);
    final idx = _events.indexWhere((e) => e.id == id);
    if (idx == -1) return;
    final existing = _events[idx];
    if (existing.status == ScheduledEventStatus.cancelled) return;
    _events = List.of(_events)
      ..[idx] = existing.copyWith(status: ScheduledEventStatus.cancelled);
    await _persist();
    notifyListeners();
  }

  /// Cancel every pending event for one email — used the moment its task is
  /// completed / resolved / dismissed, so no ghost alarm survives the work
  /// being done. [includeReminders] keeps a user's own reminder alive by
  /// default (they set it deliberately); deadline alarms always go.
  Future<int> cancelForEmail(String emailId, {bool includeReminders = false}) =>
      _serialize(() async {
        final targets = _events
            .where((e) =>
                e.isPending &&
                e.emailId == emailId &&
                (includeReminders || e.type.isDeadline))
            .map((e) => e.id)
            .toList();
        for (final id in targets) {
          await _cancelLocked(id);
        }
        return targets.length;
      });

  // --- deadline reconciliation ----------------------------------------

  /// Reconcile locally scheduled deadline warnings/alarms against what the
  /// backend currently says (Part 8: reconcile, never blindly recreate).
  ///
  /// [targets] is treated as the authoritative set of emails-with-deadlines
  /// the user currently has open work on, so ONLY call this after a
  /// successful fetch — never with a partial list from a failed request, or
  /// live alarms would be cancelled by a network blip.
  ///
  /// For each target and each warning offset:
  ///   * warning point already in the past → not scheduled (never a
  ///     notification for a moment that has passed);
  ///   * no local event yet                → create + schedule;
  ///   * event exists, same time           → left completely untouched
  ///     (this is what makes repeated syncs free of duplicates);
  ///   * event exists, deadline moved      → rescheduled under the same id;
  ///   * event exists but the deadline is gone / completed / the email is no
  ///     longer in [targets] → cancelled.
  Future<ScheduleReconciliation> syncDeadlines(Iterable<DeadlineTarget> targets) =>
      _serialize(() => _syncDeadlinesLocked(targets));

  Future<ScheduleReconciliation> _syncDeadlinesLocked(
      Iterable<DeadlineTarget> targets) async {
    if (!_loaded) await _loadLocked();
    final now = DateTime.now();

    // 1. Build the desired schedule.
    final desired = <String, _DesiredEvent>{};
    for (final t in targets) {
      if (t.isCompleted) continue; // a done task schedules nothing
      for (final offset in deadlineWarningOffsets) {
        final fireAt = t.deadlineAt.subtract(offset);
        if (!fireAt.isAfter(now)) continue; // never schedule the past
        desired['deadline:${t.emailId}:${offset.inMinutes}'] = _DesiredEvent(
          type: offset == Duration.zero
              ? ScheduledEventType.deadlineDue
              : ScheduledEventType.deadlineWarning,
          emailId: t.emailId,
          title: _deadlineTitle(offset),
          body: t.subject,
          scheduledAt: fireAt,
        );
      }
    }
    _applyPendingCap(desired);

    // 2. Diff against what is already scheduled locally.
    final existing = {
      for (final e in _events.where((e) => e.type.isDeadline && e.isPending)) e.sourceKey: e,
    };

    var created = 0, updated = 0, cancelled = 0, unchanged = 0;

    for (final entry in desired.entries) {
      final current = existing[entry.key];
      final want = entry.value;
      if (current == null) {
        await _createDeadlineEvent(entry.key, want);
        created++;
      } else if (current.scheduledAt.isAtSameMomentAs(want.scheduledAt.toUtc())) {
        unchanged++; // untouched — no cancel, no re-schedule, no duplicate
      } else {
        await _rescheduleLocked(current.id, want.scheduledAt);
        updated++;
      }
    }

    for (final entry in existing.entries) {
      if (!desired.containsKey(entry.key)) {
        await _cancelLocked(entry.value.id);
        cancelled++;
      }
    }

    if (created > 0 || updated > 0 || cancelled > 0) notifyListeners();
    return ScheduleReconciliation(
      created: created,
      updated: updated,
      cancelled: cancelled,
      unchanged: unchanged,
    );
  }

  /// Keep only the soonest [maxPendingDeadlineEvents] desired deadline events.
  void _applyPendingCap(Map<String, _DesiredEvent> desired) {
    if (desired.length <= maxPendingDeadlineEvents) return;
    final sorted = desired.entries.toList()
      ..sort((a, b) => a.value.scheduledAt.compareTo(b.value.scheduledAt));
    for (final entry in sorted.skip(maxPendingDeadlineEvents)) {
      desired.remove(entry.key);
    }
  }

  Future<void> _createDeadlineEvent(String sourceKey, _DesiredEvent want) async {
    final id = await _store.nextId();
    final now = DateTime.now().toUtc();
    final event = ScheduledEvent(
      id: id,
      type: want.type,
      emailId: want.emailId,
      sourceKey: sourceKey,
      title: want.title,
      body: want.body,
      scheduledAt: want.scheduledAt.toUtc(),
      createdAt: now,
      updatedAt: now,
    );
    _events = [..._events, event];
    await _persist();
    await _schedule(event);
  }

  static String _deadlineTitle(Duration offset) {
    if (offset == Duration.zero) return 'Deadline now';
    if (offset.inHours >= 24) return 'Deadline in ${offset.inHours ~/ 24}d';
    return 'Deadline in ${offset.inHours}h';
  }

  @visibleForTesting
  static void resetAppInstanceForTest() => _appInstance = null;
}

/// What one reconciliation pass actually changed — surfaced so tests (and
/// debug logs) can assert that a repeated sync did nothing.
class ScheduleReconciliation {
  final int created;
  final int updated;
  final int cancelled;
  final int unchanged;

  const ScheduleReconciliation({
    this.created = 0,
    this.updated = 0,
    this.cancelled = 0,
    this.unchanged = 0,
  });

  bool get changedNothing => created == 0 && updated == 0 && cancelled == 0;

  @override
  String toString() =>
      'ScheduleReconciliation(created: $created, updated: $updated, '
      'cancelled: $cancelled, unchanged: $unchanged)';
}

class _DesiredEvent {
  final ScheduledEventType type;
  final String emailId;
  final String title;
  final String body;
  final DateTime scheduledAt;

  const _DesiredEvent({
    required this.type,
    required this.emailId,
    required this.title,
    required this.body,
    required this.scheduledAt,
  });
}
