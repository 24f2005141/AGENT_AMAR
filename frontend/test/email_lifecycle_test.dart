import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:agent_amar/models/agent_analysis.dart';
import 'package:agent_amar/models/email.dart';
import 'package:agent_amar/models/notification_event.dart';
import 'package:agent_amar/models/reminder.dart';
import 'package:agent_amar/models/user_state.dart';
import 'package:agent_amar/screens/home_inbox_screen.dart';
import 'package:agent_amar/services/api_error.dart';
import 'package:agent_amar/services/email_repository.dart';
import 'package:agent_amar/state/inbox_controller.dart';

Email _email(String id, PrimaryCategory pc, {bool viewed = false}) => Email(
      id: id,
      senderName: 'S $id',
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
      userState: UserState(isViewed: viewed),
    );

class _Repo extends MockEmailRepository {
  List<Email> emails;
  int completeCalls = 0;
  int viewedCalls = 0;
  int clearCalls = 0;
  bool viewedFails = false;

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
  Future<Email> markEmailComplete(String emailId) async {
    completeCalls++;
    return _mutate(
      emailId,
      (e) => e.copyWith(
          userState: e.userState.copyWith(isViewed: true, isCompleted: true)),
    );
  }

  @override
  Future<Email> markEmailViewed(String emailId) async {
    viewedCalls++;
    if (viewedFails) throw ApiException(statusCode: 503, message: 'busy');
    return _mutate(
      emailId,
      (e) => e.copyWith(userState: e.userState.copyWith(isViewed: true)),
    );
  }

  @override
  Future<int> clearAcknowledged() async {
    clearCalls++;
    var n = 0;
    for (var i = 0; i < emails.length; i++) {
      final e = emails[i];
      final nonActionable = e.primaryCategory == PrimaryCategory.important ||
          e.primaryCategory == PrimaryCategory.lowPriority;
      if (nonActionable && e.isActive && !e.userState.isViewed) {
        emails[i] = e.copyWith(userState: e.userState.copyWith(isViewed: true));
        n++;
      }
    }
    return n;
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() => SharedPreferences.setMockInitialValues({}));

  group('markComplete', () {
    test('resolves on the backend and removes the card at once', () async {
      final repo = _Repo([_email('act', PrimaryCategory.actionRequired)]);
      final c = InboxController(repository: repo, enableCountdownTimer: false);
      await pumpEventQueue();

      final updated = await c.markComplete('act');
      expect(updated, isNotNull);
      expect(repo.completeCalls, 1);
      expect(c.allEmails, isEmpty);
      c.dispose();
    });

    test('a completed card does not come back after a reload', () async {
      final repo = _Repo([
        _email('act', PrimaryCategory.actionRequired),
        _email('act2', PrimaryCategory.actionRequired),
      ]);
      final c = InboxController(repository: repo, enableCountdownTimer: false);
      await pumpEventQueue();

      await c.markComplete('act');
      await c.loadData(); // backend reload — authoritative
      await pumpEventQueue();
      expect(c.allEmails.map((e) => e.id), ['act2']);
      c.dispose();
    });

    test('failure keeps the card and surfaces an error', () async {
      final repo = _Repo([_email('act', PrimaryCategory.actionRequired)]);
      final c = InboxController(repository: repo, enableCountdownTimer: false);
      await pumpEventQueue();

      // the repo throws (StateError) for an id it can't find → controller
      // catches it, keeps the card, surfaces an error
      final result = await c.markComplete('missing-id');
      expect(result, isNull);
      expect(c.errorMessage, isNotNull);
      c.dispose();
    });
  });

  group('markViewed error handling', () {
    test('a transient failure keeps the card (no silent hide)', () async {
      final repo = _Repo([_email('low', PrimaryCategory.lowPriority)])
        ..viewedFails = true;
      final c = InboxController(repository: repo, enableCountdownTimer: false);
      await pumpEventQueue();

      await c.markViewed('low');
      expect(repo.viewedCalls, 2); // retried once
      expect(c.allEmails.map((e) => e.id), ['low']); // still shown
      c.dispose();
    });
  });

  group('clearAcknowledged', () {
    test('acknowledges non-actionable, protects action/reply, is idempotent',
        () async {
      final repo = _Repo([
        _email('low', PrimaryCategory.lowPriority),
        _email('imp', PrimaryCategory.important),
        _email('act', PrimaryCategory.actionRequired),
        _email('rep', PrimaryCategory.replyRequired),
      ]);
      final c = InboxController(repository: repo, enableCountdownTimer: false);
      await pumpEventQueue();

      final n = await c.clearAcknowledged();
      expect(n, 2);
      expect(c.allEmails.map((e) => e.id), unorderedEquals(['act', 'rep']));

      final again = await c.clearAcknowledged();
      expect(again, 0);
      c.dispose();
    });
  });

  testWidgets('Clear Resolved shows a confirmation that Gmail is not deleted',
      (tester) async {
    final repo = _Repo([_email('low', PrimaryCategory.lowPriority)]);
    final c = InboxController(repository: repo, enableCountdownTimer: false);
    await c.loadData();
    await tester.pumpWidget(MaterialApp(home: HomeInboxScreen(controller: c)));
    await tester.pump(const Duration(milliseconds: 100));

    await tester.tap(find.byTooltip('Clear Resolved'));
    await tester.pump(const Duration(milliseconds: 100));

    expect(find.text('Clear Resolved'), findsWidgets);
    expect(find.textContaining('Your Gmail emails are not deleted'), findsOneWidget);

    await tester.tap(find.text('Cancel'));
    await tester.pump(const Duration(milliseconds: 100));
    expect(repo.clearCalls, 0); // cancel did nothing

    await tester.tap(find.byTooltip('Clear Resolved'));
    await tester.pump(const Duration(milliseconds: 100));
    await tester.tap(find.text('Clear'));
    await tester.pump(const Duration(milliseconds: 200));
    expect(repo.clearCalls, 1);
    c.dispose();
  });

  // (pull-to-refresh → exactly one incremental `POST /gmail/sync`, and no call to
  // the legacy bulk-unread endpoint, is covered end-to-end in gmail_sync_test.dart.)
}
