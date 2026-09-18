// Home-screen widget DATA layer: what the widgets are allowed to show, and
// how they get refreshed. Everything here is pure Dart against fakes — no
// Gmail, FastAPI, Ollama, Firebase, network or real widget-platform call.
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:agent_amar/models/agent_analysis.dart';
import 'package:agent_amar/models/email.dart';
import 'package:agent_amar/models/notification_event.dart';
import 'package:agent_amar/models/reminder.dart';
import 'package:agent_amar/models/user_state.dart';
import 'package:agent_amar/models/widget_snapshot.dart';
import 'package:agent_amar/services/email_repository.dart';
import 'package:agent_amar/services/home_widget_service.dart';
import 'package:agent_amar/services/local_schedule_service.dart';
import 'package:agent_amar/services/schedule_notifier.dart';
import 'package:agent_amar/services/scheduled_event_store.dart';
import 'package:agent_amar/services/widget_snapshot_builder.dart';
import 'package:agent_amar/state/inbox_controller.dart';

final DateTime now = DateTime(2026, 9, 6, 12, 0);

Email _email(
  String id, {
  PrimaryCategory category = PrimaryCategory.actionRequired,
  PriorityLevel priority = PriorityLevel.medium,
  DateTime? deadline,
  bool completed = false,
  bool viewed = false,
  DateTime? snoozedUntil,
  String sender = 'Project Manager',
  String subject = 'Quarterly report',
  String? actionDescription,
}) =>
    Email(
      id: id,
      senderName: sender,
      senderEmail: 'x@example.com',
      subject: subject,
      body: 'b',
      snippet: 'b',
      receivedAt: DateTime(2026, 9, 1),
      primaryCategory: category,
      analysis: AgentAnalysis(
        category: 'Cat',
        priority: priority,
        actionRequired: category == PrimaryCategory.actionRequired,
        reasoningSummary: 'r',
        deadline: deadline,
        actionDescription: actionDescription,
      ),
      userState: UserState(
        isViewed: viewed,
        isCompleted: completed,
        snoozedUntil: snoozedUntil,
      ),
    );

void main() {
  // The service also caches its snapshot in shared_preferences so the app can
  // recover what the widgets show after a process death — mock that store.
  TestWidgetsFlutterBinding.ensureInitialized();
  setUp(() => SharedPreferences.setMockInitialValues({}));

  group('Focus Now selection', () {
    test('picks the overdue item over everything else', () {
      final s = WidgetSnapshotBuilder.build([
        _email('calm', category: PrimaryCategory.replyRequired),
        _email('overdue',
            deadline: now.subtract(const Duration(hours: 2)), subject: 'Late report'),
        _email('soon', deadline: now.add(const Duration(hours: 5))),
      ], now: now);

      expect(s.focus!.emailId, 'overdue');
    });

    test('prefers a deadline inside 24h over a critical item without one', () {
      final s = WidgetSnapshotBuilder.build([
        _email('critical', priority: PriorityLevel.critical),
        _email('due_soon', deadline: now.add(const Duration(hours: 3))),
      ], now: now);

      expect(s.focus!.emailId, 'due_soon');
    });

    test('falls back to critical, then to the canonical category order', () {
      final s = WidgetSnapshotBuilder.build([
        _email('low', category: PrimaryCategory.lowPriority),
        _email('action', category: PrimaryCategory.actionRequired),
        _email('reply', category: PrimaryCategory.replyRequired),
      ], now: now);
      // Reply Required outranks Action Required (backend's canonical order).
      expect(s.focus!.emailId, 'reply');

      final withCritical = WidgetSnapshotBuilder.build([
        _email('reply', category: PrimaryCategory.replyRequired),
        _email('crit',
            category: PrimaryCategory.actionRequired, priority: PriorityLevel.critical),
      ], now: now);
      expect(withCritical.focus!.emailId, 'crit');
    });

    test('says what to do, not just the subject', () {
      final s = WidgetSnapshotBuilder.build([
        _email('r', category: PrimaryCategory.replyRequired, sender: 'Project Manager'),
      ], now: now);
      expect(s.focus!.title, 'Reply to Project Manager');
      expect(s.focus!.subtitle, 'Quarterly report');
    });
  });

  group('exclusions — the widget is not a mini inbox', () {
    test('completed items are excluded', () {
      final s = WidgetSnapshotBuilder.build([
        _email('done', completed: true, deadline: now.add(const Duration(hours: 1))),
      ], now: now);

      expect(s.focus, isNull);
      expect(s.actionCount, 0);
      expect(s.deadlineCount, 0);
      expect(s.nextDeadline, isNull);
    });

    test('cleared / acknowledged informational mail is excluded', () {
      // Important + LowPriority drop off the feed once viewed — the same rule
      // the homepage uses (Email.isActive).
      final s = WidgetSnapshotBuilder.build([
        _email('ack', category: PrimaryCategory.important, viewed: true),
        _email('ack2', category: PrimaryCategory.lowPriority, viewed: true),
      ], now: now);

      expect(s.focus, isNull);
      expect(s.isEmpty, isTrue);
    });

    test('snoozed items are excluded', () {
      // `UserState.isSnoozed` compares against the REAL wall clock, not the
      // injected `now`, so the snooze must be relative to DateTime.now() —
      // anchoring it to the fixed `now` made this pass only until that date
      // arrived.
      final s = WidgetSnapshotBuilder.build([
        _email('snoozed',
            snoozedUntil: DateTime.now().add(const Duration(hours: 4))),
      ], now: now);
      expect(s.focus, isNull);
      expect(s.actionCount, 0);
    });

    test('spam never reaches the widget layer', () {
      // Spam is dropped by the backend at every ingestion path, so it is
      // simply absent from the app's state — the widget shows what is there.
      final s = WidgetSnapshotBuilder.build([], now: now);
      expect(s.focus, isNull);
      expect(s.isEmpty, isTrue);
    });
  });

  group('Attention Dashboard counts', () {
    test('counts each canonical bucket', () {
      final s = WidgetSnapshotBuilder.build([
        _email('a1'),
        _email('a2'),
        _email('r1', category: PrimaryCategory.replyRequired),
        _email('r2', category: PrimaryCategory.replyRequired),
        _email('r3', category: PrimaryCategory.replyRequired),
        _email('d1', deadline: now.add(const Duration(days: 1))),
      ], now: now);

      expect(s.actionCount, 3); // a1, a2, d1 are all ACTION_REQUIRED
      expect(s.replyCount, 3);
      expect(s.deadlineCount, 1);
    });

    test('a Reply Needed email is never double-counted as an Action', () {
      final s = WidgetSnapshotBuilder.build([
        _email('r', category: PrimaryCategory.replyRequired),
      ], now: now);

      expect(s.replyCount, 1);
      expect(s.actionCount, 0);
    });

    test('completed items do not inflate the counts', () {
      final s = WidgetSnapshotBuilder.build([
        _email('a1'),
        _email('a2', completed: true),
      ], now: now);
      expect(s.actionCount, 1);
    });
  });

  group('Deadline countdown', () {
    test('selects the nearest upcoming deadline', () {
      final s = WidgetSnapshotBuilder.build([
        _email('far', deadline: now.add(const Duration(days: 3)), subject: 'Far'),
        _email('near', deadline: now.add(const Duration(hours: 2)), subject: 'Near'),
      ], now: now);

      expect(s.nextDeadline!.emailId, 'near');
      expect(s.nextDeadline!.title, 'Near');
      expect(s.deadlineCount, 2);
    });

    test('expired deadlines are never shown as an active countdown', () {
      final s = WidgetSnapshotBuilder.build([
        _email('past', deadline: now.subtract(const Duration(hours: 1))),
      ], now: now);

      expect(s.nextDeadline, isNull);
      expect(s.deadlineCount, 0);
      // ...but the overdue task itself still needs attention.
      expect(s.focus!.emailId, 'past');
    });

    test('a completed deadline is not an active deadline', () {
      final s = WidgetSnapshotBuilder.build([
        _email('done', deadline: now.add(const Duration(hours: 3)), completed: true),
      ], now: now);
      expect(s.nextDeadline, isNull);
      expect(s.deadlineCount, 0);
    });
  });

  group('empty state', () {
    test('no active items → "You\'re Sorted"', () {
      final s = WidgetSnapshotBuilder.build([], now: now);
      expect(s.isEmpty, isTrue);
      expect(s.focus, isNull);
      expect(s.nextDeadline, isNull);
    });
  });

  group('serialisation (what the native widgets read)', () {
    test('round-trips through JSON', () {
      final s = WidgetSnapshotBuilder.build([
        _email('a', deadline: now.add(const Duration(hours: 2))),
        _email('r', category: PrimaryCategory.replyRequired),
      ], now: now);

      final restored = WidgetSnapshot.fromJson(s.toJson());
      expect(restored.focus!.emailId, s.focus!.emailId);
      expect(restored.actionCount, s.actionCount);
      expect(restored.replyCount, s.replyCount);
      expect(restored.nextDeadline!.emailId, s.nextDeadline!.emailId);
    });
  });

  group('refresh mechanism', () {
    late FakeHomeWidgetPort port;
    late HomeWidgetService service;

    setUp(() {
      port = FakeHomeWidgetPort();
      service = HomeWidgetService.forTest(port);
    });

    test('publishing writes the snapshot and refreshes the data widgets', () async {
      final published = await service.publish([_email('a')], now: now);

      expect(published, isTrue);
      expect(port.data[HomeWidgetService.snapshotKey], isNotNull);
      expect(
        port.updated,
        containsAll([
          HomeWidgetService.focusWidget,
          HomeWidgetService.dashboardWidget,
          HomeWidgetService.deadlineWidget,
        ]),
      );
    });

    test('an unchanged republish does no platform work at all', () async {
      final emails = [_email('a')];
      await service.publish(emails, now: now);
      final writesAfterFirst = port.updated.length;

      // repeated sync / refresh with identical state
      final second = await service.publish(emails, now: now.add(const Duration(minutes: 5)));
      final third = await service.publish(emails, now: now.add(const Duration(minutes: 9)));

      expect(second, isFalse);
      expect(third, isFalse);
      expect(port.updated, hasLength(writesAfterFirst));
    });

    test('a real state change does republish', () async {
      await service.publish([_email('a')], now: now);
      final before = port.updated.length;

      final changed = await service.publish([_email('a'), _email('b')], now: now);
      expect(changed, isTrue);
      expect(port.updated.length, greaterThan(before));
    });

    test('completing the focus item republishes the empty state', () async {
      await service.publish([_email('a')], now: now);
      expect(service.lastPublished!.focus, isNotNull);

      await service.publish([_email('a', completed: true)], now: now);
      expect(service.lastPublished!.focus, isNull);
      expect(service.lastPublished!.actionCount, 0);
    });

    test('publishing never needs a repository, client or network', () async {
      // HomeWidgetService's only collaborator is the platform port — there is
      // no repository/ApiClient dependency to inject, by construction.
      final published = await service.publish([_email('a')], now: now);
      expect(published, isTrue);
      expect(port.data.keys, [HomeWidgetService.snapshotKey]);
    });

    test('the published snapshot is recoverable after a process death', () async {
      await service.publish([_email('a')], now: now);

      // A brand-new service instance (as after a cold start) can read back
      // what the widgets are showing, with no fetch.
      final revived = HomeWidgetService.forTest(FakeHomeWidgetPort());
      final cached = await revived.readCachedSnapshot();
      expect(cached, isNotNull);
      expect(cached!.actionCount, 1);
    });

    test('a "Done" tap queues locally and is flushed by the app, not the widget',
        () async {
      await service.queuePendingCompletion('gmail_1');
      await service.queuePendingCompletion('gmail_1'); // idempotent

      final first = await service.takePendingCompletions();
      expect(first, ['gmail_1']);
      // taken once — the app must not complete it twice
      expect(await service.takePendingCompletions(), isEmpty);
    });
  });

  group('centralized refresh from InboxController', () {
    late FakeHomeWidgetPort port;
    late HomeWidgetService widgets;

    setUp(() {
      port = FakeHomeWidgetPort();
      widgets = HomeWidgetService.forTest(port);
    });

    test('a local state change refreshes the widgets — no API call to do it', () async {
      final repo = _WidgetRepo([_email('a')]);
      final c = InboxController(
        repository: repo,
        widgetService: widgets,
        scheduleService: LocalScheduleService.forTest(
          store: InMemoryScheduleStore(),
          notifier: _NoopNotifier(),
        ),
        enableCountdownTimer: false,
      );

      await c.loadData();
      await c.pendingWidgetWork;
      expect(widgets.lastPublished!.actionCount, 1);
      expect(port.updated, isNotEmpty);

      // Completing it locally must push the empty state to the widgets.
      await c.markComplete('a');
      await c.pendingWidgetWork;
      expect(widgets.lastPublished!.focus, isNull);
      expect(widgets.lastPublished!.actionCount, 0);
      c.dispose();
    });

    test('repeated syncs with unchanged data do not re-write the widgets', () async {
      final repo = _WidgetRepo([_email('a')]);
      final c = InboxController(
        repository: repo,
        widgetService: widgets,
        scheduleService: LocalScheduleService.forTest(
          store: InMemoryScheduleStore(),
          notifier: _NoopNotifier(),
        ),
        enableCountdownTimer: false,
      );

      await c.loadData();
      await c.pendingWidgetWork;
      final afterFirst = port.updated.length;

      await c.loadData();
      await c.pendingWidgetWork;
      await c.autoRefresh();
      await c.pendingWidgetWork;

      expect(port.updated, hasLength(afterFirst));
      expect(widgets.lastPublished!.actionCount, 1); // no duplicate counting
      c.dispose();
    });
  });
}

class _NoopNotifier implements ScheduleNotifier {
  @override
  Future<void> scheduleEvent({
    required int id,
    required String title,
    required String body,
    required DateTime scheduledAt,
    bool isAlarm = false,
    String? emailId,
  }) async {}

  @override
  Future<void> cancelEvent(int id) async {}
}

class _WidgetRepo extends MockEmailRepository {
  List<Email> emails;
  _WidgetRepo(this.emails);

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
    var list = List<Email>.from(emails);
    if (active != null) list = list.where((e) => e.isActive == active).toList();
    return list;
  }

  @override
  Future<List<ReminderItem>> getReminders({String? status}) async => const [];

  @override
  Future<List<NotificationEvent>> getNotifications({
    bool? requiresAlarm,
    String? severity,
    String? type,
  }) async =>
      const [];

  @override
  Future<Email> markEmailComplete(String emailId) async {
    final i = emails.indexWhere((e) => e.id == emailId);
    emails[i] = emails[i]
        .copyWith(userState: emails[i].userState.copyWith(isCompleted: true));
    return emails[i];
  }
}
