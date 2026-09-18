import 'package:flutter_test/flutter_test.dart';
import 'package:agent_amar/models/scheduled_event.dart';
import 'package:agent_amar/services/local_schedule_service.dart';
import 'package:agent_amar/services/schedule_notifier.dart';
import 'package:agent_amar/services/scheduled_event_store.dart';

/// Records every OS scheduling / cancellation call instead of touching a real
/// platform channel (Part 14: "mock all platform notification APIs"). No
/// network, Gmail, Firebase or backend call happens anywhere in this file —
/// `LocalScheduleService` has no repository/API collaborator at all.
class _FakeNotifier implements ScheduleNotifier {
  final List<int> scheduled = [];
  final List<DateTime> scheduledAt = [];
  final List<String> scheduledTitles = [];
  final List<String> scheduledBodies = [];
  final List<bool> scheduledAlarmFlags = [];
  final List<int> cancelled = [];

  @override
  Future<void> scheduleEvent({
    required int id,
    required String title,
    required String body,
    required DateTime scheduledAt,
    bool isAlarm = false,
    String? emailId,
  }) async {
    scheduled.add(id);
    this.scheduledAt.add(scheduledAt);
    scheduledTitles.add(title);
    scheduledBodies.add(body);
    scheduledAlarmFlags.add(isAlarm);
  }

  @override
  Future<void> cancelEvent(int id) async => cancelled.add(id);
}

DeadlineTarget _target(
  String emailId, {
  required DateTime deadlineAt,
  String subject = 'Submit the report',
  bool isCompleted = false,
}) =>
    DeadlineTarget(
      emailId: emailId,
      subject: subject,
      deadlineAt: deadlineAt,
      isCompleted: isCompleted,
    );

void main() {
  late _FakeNotifier notifier;
  late LocalScheduleService service;

  setUp(() {
    notifier = _FakeNotifier();
    service = LocalScheduleService.forTest(
      store: InMemoryScheduleStore(),
      notifier: notifier,
    );
  });

  // ------------------------------------------------------------------
  group('reminders', () {
    test('a reminder is optional — nothing creates one implicitly', () async {
      expect(service.all, isEmpty);
      await service.syncDeadlines([
        _target('e1', deadlineAt: DateTime.now().add(const Duration(days: 3))),
      ]);
      // deadline alarms exist, but not a single user reminder
      expect(service.reminders, isEmpty);
    });

    test('createReminder persists locally and schedules with the OS', () async {
      final when = DateTime.now().add(const Duration(hours: 1));
      final r = await service.createReminder(emailId: 'e1', label: 'Reply to John', scheduledAt: when);

      expect(r.type, ScheduledEventType.userReminder);
      expect(r.status, ScheduledEventStatus.pending);
      expect(notifier.scheduled, [r.id]);
      expect(notifier.scheduledAt.single, when);
      expect(notifier.scheduledBodies.single, 'Reply to John');
      expect(notifier.scheduledAlarmFlags.single, isFalse);
    });

    test('emailId is optional at the data-model level', () async {
      final r = await service.createReminder(
        label: 'Stand-alone reminder',
        scheduledAt: DateTime.now().add(const Duration(minutes: 10)),
      );
      expect(r.emailId, isNull);
    });

    test('each reminder gets a stable unique id used as its notification id', () async {
      final r1 = await service.createReminder(label: 'one', scheduledAt: DateTime.now().add(const Duration(minutes: 5)));
      final r2 = await service.createReminder(label: 'two', scheduledAt: DateTime.now().add(const Duration(minutes: 6)));
      expect(r1.id, isNot(r2.id));
      expect(notifier.scheduled, [r1.id, r2.id]);
    });

    test('cancel cancels the OS notification and marks it cancelled', () async {
      final r = await service.createReminder(label: 'x', scheduledAt: DateTime.now().add(const Duration(minutes: 30)));
      await service.cancel(r.id);

      expect(notifier.cancelled, [r.id]);
      expect(service.pendingReminders, isEmpty);
      expect(service.all.single.status, ScheduledEventStatus.cancelled);
    });

    test('cancelling twice, or an unknown id, is safe (idempotent)', () async {
      final r = await service.createReminder(label: 'x', scheduledAt: DateTime.now().add(const Duration(minutes: 30)));
      await service.cancel(r.id);
      await service.cancel(r.id);
      await service.cancel(4242);

      expect(service.all, hasLength(1));
      expect(service.all.single.status, ScheduledEventStatus.cancelled);
    });

    test('a cancelled reminder does not come back after an app restart', () async {
      final r = await service.createReminder(label: 'x', scheduledAt: DateTime.now().add(const Duration(minutes: 30)));
      await service.cancel(r.id);

      final restarted = LocalScheduleService.forTest(store: service.debugStore, notifier: _FakeNotifier());
      await restarted.load();
      expect(restarted.pendingReminders, isEmpty);
      expect(restarted.all.single.status, ScheduledEventStatus.cancelled);
    });

    test('reschedule replaces the alarm under the SAME id — no duplicate', () async {
      final r = await service.createReminder(label: 'x', scheduledAt: DateTime.now().add(const Duration(hours: 1)));
      final newTime = DateTime.now().add(const Duration(hours: 3));
      final updated = await service.reschedule(r.id, newTime);

      expect(updated.id, r.id);
      expect(service.all, hasLength(1));
      expect(notifier.scheduled, [r.id, r.id]);
      expect(notifier.scheduledAt.last, newTime);
    });
  });

  // ------------------------------------------------------------------
  group('deadlines', () {
    test('an extracted deadline schedules 24h / 1h / at-deadline events locally', () async {
      final deadline = DateTime.now().add(const Duration(days: 3));
      final result = await service.syncDeadlines([_target('e1', deadlineAt: deadline)]);

      expect(result.created, 3);
      final events = service.pendingDeadlineEvents;
      expect(events, hasLength(3));
      expect(
        events.map((e) => e.scheduledAt.toLocal()),
        containsAll([
          deadline.subtract(const Duration(hours: 24)),
          deadline.subtract(const Duration(hours: 1)),
          deadline,
        ]),
      );
      // only the deadline moment itself rings as a full alarm
      expect(events.where((e) => e.isAlarm), hasLength(1));
      expect(events.singleWhere((e) => e.isAlarm).type, ScheduledEventType.deadlineDue);
    });

    test('warning points already in the past are never scheduled', () async {
      // 30 minutes away: the 24h and 1h warnings are both in the past.
      final deadline = DateTime.now().add(const Duration(minutes: 30));
      final result = await service.syncDeadlines([_target('e1', deadlineAt: deadline)]);

      expect(result.created, 1);
      expect(service.pendingDeadlineEvents.single.type, ScheduledEventType.deadlineDue);
      for (final at in notifier.scheduledAt) {
        expect(at.isAfter(DateTime.now()), isTrue);
      }
    });

    test('a deadline entirely in the past schedules nothing at all', () async {
      final result = await service.syncDeadlines([
        _target('e1', deadlineAt: DateTime.now().subtract(const Duration(days: 1))),
      ]);
      expect(result.created, 0);
      expect(notifier.scheduled, isEmpty);
    });

    test('repeated identical syncs do not duplicate or re-schedule anything', () async {
      final deadline = DateTime.now().add(const Duration(days: 2));
      final targets = [_target('e1', deadlineAt: deadline)];

      final first = await service.syncDeadlines(targets);
      expect(first.created, 3);
      final scheduledAfterFirst = notifier.scheduled.length;

      // simulate loadData() / autoRefresh() / Gmail sync / app resume, twice
      final second = await service.syncDeadlines(targets);
      final third = await service.syncDeadlines(targets);

      expect(second.changedNothing, isTrue);
      expect(second.unchanged, 3);
      expect(third.changedNothing, isTrue);
      expect(service.pendingDeadlineEvents, hasLength(3));
      // NOT ONE extra OS call, and nothing cancelled
      expect(notifier.scheduled, hasLength(scheduledAfterFirst));
      expect(notifier.cancelled, isEmpty);
    });

    test('a moved deadline reschedules the same ids instead of stacking new ones', () async {
      final deadline = DateTime.now().add(const Duration(days: 2));
      await service.syncDeadlines([_target('e1', deadlineAt: deadline)]);
      final idsBefore = service.pendingDeadlineEvents.map((e) => e.id).toSet();

      final moved = deadline.add(const Duration(days: 1));
      final result = await service.syncDeadlines([_target('e1', deadlineAt: moved)]);

      expect(result.updated, 3);
      expect(result.created, 0);
      expect(service.pendingDeadlineEvents, hasLength(3));
      expect(service.pendingDeadlineEvents.map((e) => e.id).toSet(), idsBefore);
      expect(
        service.pendingDeadlineEvents.map((e) => e.scheduledAt.toLocal()),
        containsAll([moved, moved.subtract(const Duration(hours: 1))]),
      );
    });

    test('completing the task cancels every future deadline alarm', () async {
      final deadline = DateTime.now().add(const Duration(days: 2));
      await service.syncDeadlines([_target('e1', deadlineAt: deadline)]);
      final ids = service.pendingDeadlineEvents.map((e) => e.id).toList();

      final result = await service.syncDeadlines([
        _target('e1', deadlineAt: deadline, isCompleted: true),
      ]);

      expect(result.cancelled, 3);
      expect(service.pendingDeadlineEvents, isEmpty);
      expect(notifier.cancelled, containsAll(ids)); // real OS cancels issued
    });

    test('an email that leaves the active feed (resolved) has its alarms cancelled', () async {
      final deadline = DateTime.now().add(const Duration(days: 2));
      await service.syncDeadlines([_target('e1', deadlineAt: deadline)]);

      final result = await service.syncDeadlines(const <DeadlineTarget>[]);
      expect(result.cancelled, 3);
      expect(service.pendingDeadlineEvents, isEmpty);
    });

    test('cancelForEmail kills deadline alarms but keeps the user\'s own reminder', () async {
      final deadline = DateTime.now().add(const Duration(days: 2));
      await service.syncDeadlines([_target('e1', deadlineAt: deadline)]);
      final reminder = await service.createReminder(
        emailId: 'e1',
        label: 'my own nudge',
        scheduledAt: DateTime.now().add(const Duration(hours: 2)),
      );

      final n = await service.cancelForEmail('e1');
      expect(n, 3);
      expect(service.pendingDeadlineEventsForEmail('e1'), isEmpty);
      expect(service.pendingReminderForEmail('e1')!.id, reminder.id);
    });

    test('deadlines for several emails stay independent of each other', () async {
      final d1 = DateTime.now().add(const Duration(days: 2));
      final d2 = DateTime.now().add(const Duration(days: 4));
      await service.syncDeadlines([
        _target('e1', deadlineAt: d1),
        _target('e2', deadlineAt: d2),
      ]);
      expect(service.pendingDeadlineEvents, hasLength(6));

      await service.syncDeadlines([
        _target('e1', deadlineAt: d1, isCompleted: true),
        _target('e2', deadlineAt: d2),
      ]);
      expect(service.pendingDeadlineEventsForEmail('e1'), isEmpty);
      expect(service.pendingDeadlineEventsForEmail('e2'), hasLength(3));
    });

    test('the pending-deadline cap keeps the soonest events only', () async {
      final targets = List.generate(
        30, // 30 × 3 offsets = 90 desired, above the 48 cap
        (i) => _target('e$i', deadlineAt: DateTime.now().add(Duration(days: 2 + i))),
      );
      await service.syncDeadlines(targets);
      expect(
        service.pendingDeadlineEvents.length,
        LocalScheduleService.maxPendingDeadlineEvents,
      );
    });
  });

  // ------------------------------------------------------------------
  group('app lifecycle', () {
    test('load() reschedules everything still pending and in the future', () async {
      final store = InMemoryScheduleStore();
      final seed = LocalScheduleService.forTest(store: store, notifier: _FakeNotifier());
      await seed.createReminder(label: 'upcoming', scheduledAt: DateTime.now().add(const Duration(hours: 5)));
      await seed.syncDeadlines([_target('e1', deadlineAt: DateTime.now().add(const Duration(days: 2)))]);

      final restarted = LocalScheduleService.forTest(store: store, notifier: notifier);
      await restarted.load();

      expect(restarted.pending, hasLength(4)); // 1 reminder + 3 deadline events
      expect(notifier.scheduled, hasLength(4));
    });

    test('load() marks an already-passed pending event fired, without rescheduling it', () async {
      final store = InMemoryScheduleStore();
      final seed = LocalScheduleService.forTest(store: store, notifier: _FakeNotifier());
      await seed.createReminder(label: 'already due', scheduledAt: DateTime.now().subtract(const Duration(hours: 1)));

      final restarted = LocalScheduleService.forTest(store: store, notifier: notifier);
      await restarted.load();

      expect(restarted.pending, isEmpty);
      expect(restarted.all.single.status, ScheduledEventStatus.fired);
      expect(notifier.scheduled, isEmpty);
    });

    test('the schedule survives the process dying — nothing lives only in memory', () async {
      final store = InMemoryScheduleStore();
      final seed = LocalScheduleService.forTest(store: store, notifier: _FakeNotifier());
      await seed.createReminder(emailId: 'e1', label: 'persisted', scheduledAt: DateTime.now().add(const Duration(hours: 3)));

      // A brand-new service object, as if the app process had been killed.
      final revived = LocalScheduleService.forTest(store: store, notifier: notifier);
      await revived.load();
      expect(revived.pendingReminders.single.body, 'persisted');
      expect(revived.pendingReminders.single.emailId, 'e1');
    });

    test('repeated load() calls (resume, restart) never duplicate the schedule', () async {
      final store = InMemoryScheduleStore();
      final seed = LocalScheduleService.forTest(store: store, notifier: _FakeNotifier());
      await seed.createReminder(label: 'x', scheduledAt: DateTime.now().add(const Duration(hours: 1)));
      await seed.syncDeadlines([_target('e1', deadlineAt: DateTime.now().add(const Duration(days: 2)))]);

      final a = LocalScheduleService.forTest(store: store, notifier: _FakeNotifier());
      await a.load();
      await a.load();
      await a.load();
      expect(a.all, hasLength(4));
    });

    test('a reconcile after a restart leaves an unchanged schedule untouched', () async {
      final store = InMemoryScheduleStore();
      final deadline = DateTime.now().add(const Duration(days: 2));
      final seed = LocalScheduleService.forTest(store: store, notifier: _FakeNotifier());
      await seed.syncDeadlines([_target('e1', deadlineAt: deadline)]);

      final restarted = LocalScheduleService.forTest(store: store, notifier: notifier);
      await restarted.load();
      final scheduledOnLoad = notifier.scheduled.length; // the restore pass

      final result = await restarted.syncDeadlines([_target('e1', deadlineAt: deadline)]);
      expect(result.changedNothing, isTrue);
      expect(notifier.scheduled, hasLength(scheduledOnLoad)); // no extra OS calls
      expect(notifier.cancelled, isEmpty);
    });
  });
}
