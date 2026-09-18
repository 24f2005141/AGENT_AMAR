import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:agent_amar/models/agent_analysis.dart';
import 'package:agent_amar/models/email.dart';
import 'package:agent_amar/models/notification_event.dart';
import 'package:agent_amar/models/reminder.dart';
import 'package:agent_amar/models/user_state.dart';
import 'package:agent_amar/screens/reminders_screen.dart';
import 'package:agent_amar/services/email_repository.dart';
import 'package:agent_amar/services/local_schedule_service.dart';
import 'package:agent_amar/state/inbox_controller.dart';
import 'package:agent_amar/widgets/add_reminder_sheet.dart';

Email _email(String id, String subject, String sender) => Email(
      id: id,
      senderName: sender,
      senderEmail: '$sender@example.com',
      subject: subject,
      body: 'preview',
      snippet: 'preview',
      receivedAt: DateTime(2026, 9, 1),
      primaryCategory: PrimaryCategory.lowPriority,
      analysis: const AgentAnalysis(
        category: 'Cat',
        priority: PriorityLevel.low,
        actionRequired: false,
        reasoningSummary: 'r',
      ),
      userState: const UserState(isViewed: true),
    );

class _Repo extends MockEmailRepository {
  List<Email> emails;

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
    if (active == true) return const [];
    return List<Email>.from(emails);
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
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    // Reminders are device-local now (LocalScheduleService) — each test gets
    // its own isolated instance/store so a reminder created in one test can
    // never leak into another's widget tree (both AddReminderSheet and
    // RemindersScreen read the same app-wide singleton by default).
    SharedPreferences.setMockInitialValues({});
    LocalScheduleService.resetAppInstanceForTest();
  });

  testWidgets('Add Reminder FAB opens the sheet with the email picker',
      (tester) async {
    final repo = _Repo([_email('a', 'Assignment due', 'Prof Rao')]);
    final c = InboxController(repository: repo, enableCountdownTimer: false);
    await c.loadData();
    await tester.pumpWidget(MaterialApp(home: RemindersScreen(controller: c)));
    await tester.pump(const Duration(milliseconds: 100));

    expect(find.text('ADD REMINDER'), findsOneWidget);
    await tester.tap(find.text('ADD REMINDER'));
    await tester.pumpAndSettle();

    expect(find.byType(AddReminderSheet), findsOneWidget);
    expect(find.text('Assignment due'), findsOneWidget);
    c.dispose();
  });

  testWidgets('selecting an email and a time enables Set Reminder, then creates it',
      (tester) async {
    final repo = _Repo([_email('a', 'Assignment due', 'Prof Rao')]);
    final c = InboxController(repository: repo, enableCountdownTimer: false);
    await c.loadData();
    await tester.pumpWidget(MaterialApp(home: RemindersScreen(controller: c)));
    await tester.pump(const Duration(milliseconds: 100));

    await tester.tap(find.text('ADD REMINDER'));
    await tester.pumpAndSettle();

    final scrollable = find
        .descendant(
          of: find.byKey(const Key('addReminderScroll')),
          matching: find.byType(Scrollable),
        )
        .first;

    // Set Reminder is disabled with nothing chosen yet (a widget-tree check,
    // not a tap — no need for it to be on-screen).
    final setButtonFinder = find.widgetWithText(ElevatedButton, 'SET REMINDER');
    expect(tester.widget<ElevatedButton>(setButtonFinder).onPressed, isNull);

    await tester.tap(find.text('Assignment due'));
    await tester.pump();
    // the picker collapses into a selected chip
    expect(find.text('Change'), findsOneWidget);

    await tester.tap(find.text('In 1 hour'));
    await tester.pump();

    expect(tester.widget<ElevatedButton>(setButtonFinder).onPressed, isNotNull);
    await tester.scrollUntilVisible(setButtonFinder, 200, scrollable: scrollable);
    await tester.tap(setButtonFinder);
    // create() awaits real (mocked) shared_preferences platform-channel calls
    // before the widget's own setState — a single pump() isn't guaranteed to
    // drain that, so settle fully before asserting the success view.
    await tester.pumpAndSettle();

    final pending = LocalScheduleService().pendingReminders;
    expect(pending, hasLength(1));
    expect(pending.single.emailId, 'a');
    expect(find.text('Reminder Set!'), findsOneWidget);

    // auto-dismiss after the success delay
    await tester.pump(const Duration(milliseconds: 1500));
    await tester.pumpAndSettle();
    expect(find.byType(AddReminderSheet), findsNothing);
    c.dispose();
  });

  testWidgets('search filters the email picker by subject / sender', (tester) async {
    final repo = _Repo([
      _email('a', 'Assignment due', 'Prof Rao'),
      _email('b', 'Team hackathon roster', 'ACM Chapter'),
    ]);
    final c = InboxController(repository: repo, enableCountdownTimer: false);
    await c.loadData();
    await tester.pumpWidget(MaterialApp(home: RemindersScreen(controller: c)));
    await tester.pump(const Duration(milliseconds: 100));

    await tester.tap(find.text('ADD REMINDER'));
    await tester.pumpAndSettle();

    expect(find.text('Assignment due'), findsOneWidget);
    expect(find.text('Team hackathon roster'), findsOneWidget);

    await tester.enterText(find.byType(TextField).first, 'hackathon');
    await tester.pump();

    expect(find.text('Assignment due'), findsNothing);
    expect(find.text('Team hackathon roster'), findsOneWidget);
    c.dispose();
  });
}
