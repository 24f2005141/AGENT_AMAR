import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:agent_amar/models/agent_analysis.dart';
import 'package:agent_amar/models/email.dart';
import 'package:agent_amar/models/notification_event.dart';
import 'package:agent_amar/models/reminder.dart';
import 'package:agent_amar/models/user_state.dart';
import 'package:agent_amar/screens/home_inbox_screen.dart';
import 'package:agent_amar/services/email_repository.dart';
import 'package:agent_amar/state/inbox_controller.dart';

Email _email(
  String id,
  PrimaryCategory pc, {
  bool viewed = false,
  bool completed = false,
  DateTime? snoozedUntil,
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
      autoPrimaryCategory: pc,
      analysis: AgentAnalysis(
        category: 'Cat',
        priority: PriorityLevel.high,
        actionRequired: pc == PrimaryCategory.actionRequired,
        reasoningSummary: 'r',
      ),
      userState: UserState(
        isViewed: viewed,
        isCompleted: completed,
        snoozedUntil: snoozedUntil,
      ),
    );

/// A repo whose `getEmails` honours the `active` filter exactly like the backend,
/// and whose mutations flip the matching lifecycle field + return the new Email.
class _Repo extends MockEmailRepository {
  List<Email> emails;
  final List<bool?> activeArgs = [];

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
    activeArgs.add(active);
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

  Email _mutate(String id, Email Function(Email) f) {
    final i = emails.indexWhere((e) => e.id == id);
    emails[i] = f(emails[i]);
    return emails[i];
  }

  @override
  Future<Email> markEmailViewed(String emailId) async => _mutate(
        emailId,
        (e) => e.copyWith(userState: e.userState.copyWith(isViewed: true)),
      );

  @override
  Future<Email> completeAction(String emailId, String actionRef) async => _mutate(
        emailId,
        (e) => e.copyWith(userState: e.userState.copyWith(isCompleted: true)),
      );

  @override
  Future<Email> snoozeEmail(String emailId, DateTime until) async => _mutate(
        emailId,
        (e) => e.copyWith(userState: e.userState.copyWith(snoozedUntil: until)),
      );
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() => SharedPreferences.setMockInitialValues({}));

  group('Email.isActive', () {
    test('reply / action stay active even after being viewed', () {
      expect(_email('a', PrimaryCategory.replyRequired, viewed: true).isActive, isTrue);
      expect(_email('b', PrimaryCategory.actionRequired, viewed: true).isActive, isTrue);
    });

    test('important / low drop out once viewed', () {
      expect(_email('c', PrimaryCategory.important).isActive, isTrue);
      expect(_email('c', PrimaryCategory.important, viewed: true).isActive, isFalse);
      expect(_email('d', PrimaryCategory.lowPriority).isActive, isTrue);
      expect(_email('d', PrimaryCategory.lowPriority, viewed: true).isActive, isFalse);
    });

    test('completed is never active', () {
      expect(_email('e', PrimaryCategory.actionRequired, completed: true).isActive, isFalse);
      expect(_email('f', PrimaryCategory.replyRequired, completed: true).isActive, isFalse);
    });

    test('a future snooze suppresses it; a past snooze does not', () {
      expect(
        _email('g', PrimaryCategory.actionRequired,
                snoozedUntil: DateTime.now().add(const Duration(hours: 2)))
            .isActive,
        isFalse,
      );
      expect(
        _email('h', PrimaryCategory.actionRequired,
                snoozedUntil: DateTime.now().subtract(const Duration(hours: 2)))
            .isActive,
        isTrue,
      );
    });
  });

  group('InboxController homepage feed', () {
    test('loadData asks the backend for the active feed only', () async {
      final repo = _Repo([_email('x', PrimaryCategory.lowPriority)]);
      final c = InboxController(repository: repo, enableCountdownTimer: false);
      await pumpEventQueue();
      expect(repo.activeArgs, everyElement(isTrue));
      c.dispose();
    });

    test('opening a low-priority email removes it from the feed immediately', () async {
      final repo = _Repo([
        _email('low', PrimaryCategory.lowPriority),
        _email('act', PrimaryCategory.actionRequired),
      ]);
      final c = InboxController(repository: repo, enableCountdownTimer: false);
      await pumpEventQueue();
      expect(c.allEmails.map((e) => e.id), unorderedEquals(['low', 'act']));

      await c.markViewed('low');
      expect(c.allEmails.map((e) => e.id), ['act']); // low is gone
      expect(c.resolvedEmails.map((e) => e.id), contains('low')); // not deleted
      c.dispose();
    });

    test('opening an action email does NOT remove it', () async {
      final repo = _Repo([_email('act', PrimaryCategory.actionRequired)]);
      final c = InboxController(repository: repo, enableCountdownTimer: false);
      await pumpEventQueue();

      await c.markViewed('act');
      expect(c.allEmails.map((e) => e.id), ['act']);
      c.dispose();
    });

    test('completing an action removes it from the feed at once', () async {
      final repo = _Repo([_email('act', PrimaryCategory.actionRequired)]);
      final c = InboxController(repository: repo, enableCountdownTimer: false);
      await pumpEventQueue();

      await c.completeAction('act', 'act_001');
      await pumpEventQueue();
      expect(c.allEmails, isEmpty);
      c.dispose();
    });

    test('snoozing removes it from the feed', () async {
      final repo = _Repo([_email('act', PrimaryCategory.actionRequired)]);
      final c = InboxController(repository: repo, enableCountdownTimer: false);
      await pumpEventQueue();

      await c.snoozeEmail('act', DateTime.now().add(const Duration(hours: 3)));
      expect(c.allEmails, isEmpty);
      c.dispose();
    });

    test('acknowledged items do not come back on the next refresh', () async {
      final repo = _Repo([_email('low', PrimaryCategory.lowPriority)]);
      final c = InboxController(repository: repo, enableCountdownTimer: false);
      await pumpEventQueue();

      await c.markViewed('low');
      await c.loadData();
      await pumpEventQueue();
      expect(c.allEmails, isEmpty);
      c.dispose();
    });

    test('resolved emails are retrievable via loadResolvedEmails', () async {
      final repo = _Repo([
        _email('done', PrimaryCategory.actionRequired, completed: true),
        _email('act', PrimaryCategory.actionRequired),
      ]);
      final c = InboxController(repository: repo, enableCountdownTimer: false);
      await pumpEventQueue();
      expect(c.allEmails.map((e) => e.id), ['act']);

      await c.loadResolvedEmails();
      expect(c.resolvedEmails.map((e) => e.id), ['done']);
      c.dispose();
    });
  });

  testWidgets('the homepage shows a clean "all caught up" state when the feed is empty',
      (tester) async {
    final repo = _Repo([_email('seen', PrimaryCategory.lowPriority, viewed: true)]);
    final c = InboxController(repository: repo, enableCountdownTimer: false);
    await c.loadData();
    await tester.pumpWidget(MaterialApp(home: HomeInboxScreen(controller: c)));
    await tester.pump(const Duration(milliseconds: 100));

    expect(find.text("You're all caught up."), findsOneWidget);
    expect(find.text('UPCOMING DEADLINES'), findsNothing);
    c.dispose();
  });
}
