// Isolation tests (Part 14): the API/refresh layer must never be what makes a
// scheduled notification fire, and repeated backend data must never duplicate
// an alarm.
//
// Everything here runs against fakes — no Gmail, Firebase, Ollama, backend or
// real platform-notification call is made.
import 'package:flutter_test/flutter_test.dart';

import 'package:agent_amar/models/agent_analysis.dart';
import 'package:agent_amar/models/email.dart';
import 'package:agent_amar/models/notification_event.dart';
import 'package:agent_amar/models/reminder.dart';
import 'package:agent_amar/models/user_state.dart';
import 'package:agent_amar/services/email_repository.dart';
import 'package:agent_amar/services/local_schedule_service.dart';
import 'package:agent_amar/services/schedule_notifier.dart';
import 'package:agent_amar/services/scheduled_event_store.dart';
import 'package:agent_amar/state/inbox_controller.dart';

class _FakeNotifier implements ScheduleNotifier {
  final List<int> scheduled = [];
  final List<int> cancelled = [];

  @override
  Future<void> scheduleEvent({
    required int id,
    required String title,
    required String body,
    required DateTime scheduledAt,
    bool isAlarm = false,
    String? emailId,
  }) async =>
      scheduled.add(id);

  @override
  Future<void> cancelEvent(int id) async => cancelled.add(id);
}

Email _email(
  String id, {
  DateTime? deadline,
  bool completed = false,
  PrimaryCategory pc = PrimaryCategory.actionRequired,
}) =>
    Email(
      id: id,
      senderName: 'Sender $id',
      senderEmail: 's@x.com',
      subject: 'Subject $id',
      body: 'preview',
      snippet: 'preview',
      receivedAt: DateTime(2026, 9, 1),
      primaryCategory: pc,
      analysis: AgentAnalysis(
        category: 'Cat',
        priority: PriorityLevel.high,
        actionRequired: true,
        reasoningSummary: 'r',
        deadline: deadline,
      ),
      userState: UserState(isCompleted: completed),
    );

class _Repo extends MockEmailRepository {
  List<Email> emails;
  int getEmailsCalls = 0;

  _Repo(this.emails);

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
    getEmailsCalls++;
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

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late _FakeNotifier notifier;
  late LocalScheduleService schedule;

  setUp(() {
    notifier = _FakeNotifier();
    schedule = LocalScheduleService.forTest(
      store: InMemoryScheduleStore(),
      notifier: notifier,
    );
  });

  InboxController controller(_Repo repo) => InboxController(
        repository: repo,
        scheduleService: schedule,
        enableCountdownTimer: false,
      );

  test('loadData() schedules a newly-learned deadline with the OS, once', () async {
    final deadline = DateTime.now().add(const Duration(days: 2));
    final repo = _Repo([_email('e1', deadline: deadline)]);
    final c = controller(repo);
    await c.loadData();
    await c.pendingScheduleWork;

    expect(schedule.pendingDeadlineEvents, hasLength(3)); // 24h / 1h / due
    expect(notifier.scheduled, hasLength(3));
    c.dispose();
  });

  test('repeated loadData() (pull-to-refresh, resume, poll) never duplicates alarms',
      () async {
    final deadline = DateTime.now().add(const Duration(days: 2));
    final repo = _Repo([_email('e1', deadline: deadline)]);
    final c = controller(repo);

    await c.loadData();
    await c.pendingScheduleWork;
    final afterFirst = notifier.scheduled.length;

    await c.loadData();
    await c.pendingScheduleWork;
    await c.loadData();
    await c.pendingScheduleWork;
    await c.autoRefresh();

    // The backend was queried again and again...
    await c.pendingScheduleWork;
    expect(repo.getEmailsCalls, greaterThan(3));
    // ...but the device schedule was left completely alone.
    expect(schedule.pendingDeadlineEvents, hasLength(3));
    expect(notifier.scheduled, hasLength(afterFirst));
    expect(notifier.cancelled, isEmpty);
    c.dispose();
  });

  test('an email with no deadline schedules nothing at all', () async {
    final repo = _Repo([_email('e1')]);
    final c = controller(repo);
    await c.loadData();
    await c.pendingScheduleWork;

    expect(schedule.all, isEmpty);
    expect(notifier.scheduled, isEmpty);
    c.dispose();
  });

  test('marking complete cancels the future deadline alarms immediately', () async {
    final deadline = DateTime.now().add(const Duration(days: 2));
    final repo = _Repo([_email('e1', deadline: deadline)]);
    final c = controller(repo);
    await c.loadData();
    await c.pendingScheduleWork;
    expect(schedule.pendingDeadlineEvents, hasLength(3));

    await c.markComplete('e1');
    await c.pendingScheduleWork;

    expect(schedule.pendingDeadlineEvents, isEmpty);
    expect(notifier.cancelled, hasLength(3));
    c.dispose();
  });

  test('a user reminder is untouched by loadData / sync reconciliation', () async {
    final deadline = DateTime.now().add(const Duration(days: 2));
    final repo = _Repo([_email('e1', deadline: deadline)]);
    final c = controller(repo);
    final reminder = await schedule.createReminder(
      emailId: 'e1',
      label: 'my nudge',
      scheduledAt: DateTime.now().add(const Duration(hours: 2)),
    );

    await c.loadData();
    await c.pendingScheduleWork;
    await c.loadData();
    await c.pendingScheduleWork;

    expect(schedule.pendingReminderForEmail('e1')!.id, reminder.id);
    expect(notifier.cancelled, isNot(contains(reminder.id)));
    c.dispose();
  });

  test('a backend fetch failure never cancels already-scheduled alarms', () async {
    final deadline = DateTime.now().add(const Duration(days: 2));
    final repo = _FailingAfterFirstLoad([_email('e1', deadline: deadline)]);
    final c = InboxController(
      repository: repo,
      scheduleService: schedule,
      enableCountdownTimer: false,
    );
    await c.loadData();
    await c.pendingScheduleWork;
    expect(schedule.pendingDeadlineEvents, hasLength(3));

    repo.shouldFail = true;
    await c.loadData(); // network blip
    await c.pendingScheduleWork;

    // The alarms the device already owns survive an offline backend.
    expect(schedule.pendingDeadlineEvents, hasLength(3));
    expect(notifier.cancelled, isEmpty);
    c.dispose();
  });

  test('a schedule restored after app restart is not re-created by a later sync',
      () async {
    final deadline = DateTime.now().add(const Duration(days: 2));
    final repo = _Repo([_email('e1', deadline: deadline)]);
    final c = controller(repo);
    await c.loadData();
    await c.pendingScheduleWork;
    c.dispose();

    // App restarts: fresh service + fresh controller over the SAME store.
    final notifier2 = _FakeNotifier();
    final revived = LocalScheduleService.forTest(
      store: schedule.debugStore,
      notifier: notifier2,
    );
    await revived.load();
    final afterRestore = notifier2.scheduled.length;

    final c2 = InboxController(
      repository: repo,
      scheduleService: revived,
      enableCountdownTimer: false,
    );
    await c2.loadData();
    await c2.pendingScheduleWork;

    expect(revived.pendingDeadlineEvents, hasLength(3));
    expect(notifier2.scheduled, hasLength(afterRestore)); // no re-scheduling
    expect(notifier2.cancelled, isEmpty);
    c2.dispose();
  });
}

class _FailingAfterFirstLoad extends _Repo {
  bool shouldFail = false;

  _FailingAfterFirstLoad(super.emails);

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
    if (shouldFail) throw Exception('backend offline');
    return super.getEmails(active: active);
  }
}
