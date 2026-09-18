import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:agent_amar/dto/email_state_dto.dart';
import 'package:agent_amar/dto/full_email_dto.dart';
import 'package:agent_amar/models/agent_analysis.dart';
import 'package:agent_amar/models/email.dart';
import 'package:agent_amar/models/notification_event.dart';
import 'package:agent_amar/models/reminder.dart';
import 'package:agent_amar/models/user_state.dart';
import 'package:agent_amar/screens/email_detail_screen.dart';
import 'package:agent_amar/screens/full_email_screen.dart';
import 'package:agent_amar/services/api_error.dart';
import 'package:agent_amar/services/email_repository.dart';
import 'package:agent_amar/state/inbox_controller.dart';

Email _email(String id, PrimaryCategory pc) => Email(
      id: id,
      senderName: 'News Team',
      senderEmail: 'news@example.com',
      subject: 'The subject line',
      body: 'short preview',
      snippet: 'short preview',
      receivedAt: DateTime(2026, 9, 1, 10, 30),
      primaryCategory: pc,
      autoPrimaryCategory: pc,
      analysis: const AgentAnalysis(
        category: 'Newsletter',
        priority: PriorityLevel.low,
        actionRequired: false,
        reasoningSummary: 'r',
      ),
      userState: const UserState(),
    );

class _Repo extends MockEmailRepository {
  final Email email;
  FullEmailDto? full;
  Object? error;
  int fullCalls = 0;

  _Repo(this.email, {this.full, this.error});

  @override
  Future<List<Email>> getEmails({
    String? priority, String? category, bool? actionRequired,
    bool? viewed, bool? completed, bool? active, int limit = 100,
  }) async => [email];

  @override
  Future<List<ReminderItem>> getReminders({String? status}) async => const [];

  @override
  Future<List<NotificationEvent>> getNotifications({
    bool? requiresAlarm, String? severity, String? type,
  }) async => const [];

  @override
  Future<EmailStateDetailOutDto?> getEmailDetailDto(String id) async =>
      EmailStateDetailOutDto(
        emailId: email.id, senderEmail: email.senderEmail, senderName: email.senderName,
        subject: email.subject, snippet: email.snippet, finalCategory: 'NEWSLETTER',
        primaryCategory: email.primaryCategory.wire,
        priorityLevel: 'LOW', priorityScore: 10, folderLabel: 'AMAR/Inbox',
      );

  @override
  Future<FullEmailDto> getFullEmail(String emailId) async {
    fullCalls++;
    if (error != null) throw error!;
    return full ??
        FullEmailDto(
          emailId: emailId,
          subject: 'The subject line',
          senderName: 'News Team',
          senderEmail: 'news@example.com',
          receivedAt: DateTime(2026, 9, 1, 10, 30),
          body: 'Hello reader.\nThis is the full body.\nVisit https://example.com/x now.',
          bodyFormat: 'text',
          primaryCategory: email.primaryCategory,
        );
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() => SharedPreferences.setMockInitialValues({}));

  testWidgets('View Full Email navigates from the detail screen', (tester) async {
    final repo = _Repo(_email('e1', PrimaryCategory.lowPriority));
    final c = InboxController(repository: repo, enableCountdownTimer: false);
    await tester.pumpWidget(MaterialApp(
      home: EmailDetailScreen(email: repo.email, controller: c),
    ));
    await tester.pumpAndSettle();

    await tester.scrollUntilVisible(find.text('View Full Email'), 300,
        scrollable: find.byType(Scrollable).first);
    await tester.tap(find.text('View Full Email'));
    await tester.pumpAndSettle();

    expect(find.text('FULL EMAIL'), findsOneWidget);
    expect(repo.fullCalls, 1);
    c.dispose();
  });

  testWidgets('renders sender, subject and the full plain-text body', (tester) async {
    final repo = _Repo(_email('e2', PrimaryCategory.lowPriority));
    final c = InboxController(repository: repo, enableCountdownTimer: false);
    await tester.pumpWidget(MaterialApp(
      home: FullEmailScreen(emailId: 'e2', controller: c),
    ));
    await tester.pumpAndSettle();

    expect(find.text('News Team'), findsOneWidget);
    expect(find.text('news@example.com'), findsOneWidget);
    expect(find.text('The subject line'), findsOneWidget);
    expect(find.textContaining('This is the full body.'), findsOneWidget);
    c.dispose();
  });

  testWidgets('an HTML-converted email shows the safe-conversion note', (tester) async {
    final repo = _Repo(
      _email('e3', PrimaryCategory.lowPriority),
      full: const FullEmailDto(
        emailId: 'e3',
        subject: 'Formatted',
        senderEmail: 'news@example.com',
        body: 'Newsletter\n\nHello reader, visit (https://example.com/x).',
        bodyFormat: 'html_converted',
      ),
    );
    final c = InboxController(repository: repo, enableCountdownTimer: false);
    await tester.pumpWidget(MaterialApp(
      home: FullEmailScreen(emailId: 'e3', controller: c),
    ));
    await tester.pumpAndSettle();

    expect(find.textContaining('Converted from a formatted'), findsOneWidget);
    expect(find.textContaining('Newsletter'), findsOneWidget);
    // no markup / scripts ever reach the widget tree (body is already plain text)
    expect(find.textContaining('<'), findsNothing);
    c.dispose();
  });

  testWidgets('reply suggestions panel is shown for a Reply Required email', (tester) async {
    final repo = _Repo(
      _email('e4', PrimaryCategory.replyRequired),
      full: const FullEmailDto(
        emailId: 'e4', subject: 'Please reply', senderEmail: 'p@x.com',
        body: 'Can you confirm?', primaryCategory: PrimaryCategory.replyRequired,
      ),
    );
    final c = InboxController(repository: repo, enableCountdownTimer: false);
    await tester.pumpWidget(MaterialApp(
      home: FullEmailScreen(emailId: 'e4', controller: c),
    ));
    await tester.pumpAndSettle();

    await tester.scrollUntilVisible(find.text('AI REPLY SUGGESTIONS'), 300,
        scrollable: find.byType(Scrollable).first);
    expect(find.text('AI REPLY SUGGESTIONS'), findsOneWidget);
    c.dispose();
  });

  testWidgets('a 404 renders a safe error state with retry', (tester) async {
    final repo = _Repo(
      _email('e5', PrimaryCategory.lowPriority),
      error: ApiException(statusCode: 404, message: 'gone'),
    );
    final c = InboxController(repository: repo, enableCountdownTimer: false);
    await tester.pumpWidget(MaterialApp(
      home: FullEmailScreen(emailId: 'e5', controller: c),
    ));
    await tester.pumpAndSettle();

    expect(find.textContaining('no longer available'), findsOneWidget);
    expect(find.text('Try Again'), findsOneWidget);
    c.dispose();
  });
}
