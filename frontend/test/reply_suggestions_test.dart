import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:agent_amar/dto/email_state_dto.dart';
import 'package:agent_amar/dto/reply_dto.dart';
import 'package:agent_amar/models/agent_analysis.dart';
import 'package:agent_amar/models/email.dart';
import 'package:agent_amar/models/user_state.dart';
import 'package:agent_amar/screens/email_detail_screen.dart';
import 'package:agent_amar/services/api_error.dart';
import 'package:agent_amar/services/email_repository.dart';
import 'package:agent_amar/state/inbox_controller.dart';
import 'package:agent_amar/widgets/reply_suggestions_panel.dart';

ReplySuggestionsDto _threeSuggestions() => const ReplySuggestionsDto(
      emailId: 'gmail_1',
      suggestions: [
        ReplySuggestionDto(id: 'option_1', label: 'Direct', body: 'Yes, I will attend.'),
        ReplySuggestionDto(
            id: 'option_2', label: 'Professional', body: 'Thank you — I expect to be able to join.'),
        ReplySuggestionDto(
            id: 'option_3', label: 'Alternative', body: 'Unfortunately I cannot make it; please share updates.'),
      ],
    );

Widget _host(Widget child) => MaterialApp(home: Scaffold(body: SingleChildScrollView(child: child)));

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() => SharedPreferences.setMockInitialValues({}));

  group('reply DTO parsing', () {
    test('ReplySuggestionsDto.fromJson reads exactly the 3 options', () {
      final dto = ReplySuggestionsDto.fromJson({
        'email_id': 'gmail_9',
        'suggestions': [
          {'id': 'option_1', 'label': 'Direct', 'body': 'A'},
          {'id': 'option_2', 'label': 'Professional', 'body': 'B'},
          {'id': 'option_3', 'label': 'Alternative', 'body': 'C'},
        ],
      });
      expect(dto.emailId, 'gmail_9');
      expect(dto.suggestions.map((s) => s.body).toList(), ['A', 'B', 'C']);
    });

    test('ReplySendResultDto.fromJson reads threading + dedup flags', () {
      final dto = ReplySendResultDto.fromJson({
        'email_id': 'gmail_9',
        'thread_id': 't1',
        'gmail_message_id': 'sent_1',
        'reply_action_completed': true,
        'duplicate_suppressed': true,
      });
      expect(dto.threadId, 't1');
      expect(dto.gmailMessageId, 'sent_1');
      expect(dto.replyActionCompleted, isTrue);
      expect(dto.duplicateSuppressed, isTrue);
    });
  });

  group('ReplySuggestionsPanel', () {
    testWidgets('idle shows a Suggest Replies button; tapping shows loading then 3 cards',
        (tester) async {
      await tester.pumpWidget(_host(ReplySuggestionsPanel(
        onGenerate: () async {
          await Future<void>.delayed(const Duration(milliseconds: 50));
          return _threeSuggestions();
        },
        onSend: (_) async => const ReplySendResultDto(emailId: 'gmail_1'),
      )));

      expect(find.text('Suggest Replies'), findsOneWidget);
      await tester.tap(find.text('Suggest Replies'));
      await tester.pump();
      expect(find.text('Generating reply suggestions…'), findsOneWidget);

      await tester.pump(const Duration(milliseconds: 80));
      expect(find.text('Generating reply suggestions…'), findsNothing);
      expect(find.text('Edit & send'), findsNWidgets(3));
      expect(find.text('Yes, I will attend.'), findsOneWidget);
    });

    testWidgets('selecting a suggestion opens the editor pre-filled; it is editable',
        (tester) async {
      await tester.pumpWidget(_host(ReplySuggestionsPanel(
        connectedAccountEmail: 'me@gmail.com',
        onGenerate: () async => _threeSuggestions(),
        onSend: (_) async => const ReplySendResultDto(emailId: 'gmail_1'),
      )));
      await tester.tap(find.text('Suggest Replies'));
      await tester.pumpAndSettle();

      await tester.tap(find.text('Edit & send').first);
      await tester.pump();

      final field = find.byType(TextField);
      expect(field, findsOneWidget);
      expect(find.widgetWithText(TextField, 'Yes, I will attend.'), findsOneWidget);
      expect(find.textContaining('Sends from me@gmail.com'), findsOneWidget);

      await tester.enterText(field, 'Yes, I will attend. See you at 3pm.');
      await tester.pump();
      expect(find.widgetWithText(TextField, 'Yes, I will attend. See you at 3pm.'), findsOneWidget);
    });

    testWidgets('Send calls onSend with the edited body, disables while sending, then shows success',
        (tester) async {
      String? sentBody;
      var sendCount = 0;

      await tester.pumpWidget(_host(ReplySuggestionsPanel(
        onGenerate: () async => _threeSuggestions(),
        onSend: (body) async {
          sendCount++;
          sentBody = body;
          await Future<void>.delayed(const Duration(milliseconds: 50));
          return const ReplySendResultDto(emailId: 'gmail_1', gmailMessageId: 'sent_1');
        },
      )));
      await tester.tap(find.text('Suggest Replies'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Edit & send').first);
      await tester.pump();
      await tester.enterText(find.byType(TextField), 'Edited reply body');
      await tester.pump();

      await tester.tap(find.text('Send Reply'));
      await tester.pump();
      // sending state — button disabled, spinner label
      expect(find.text('Sending…'), findsOneWidget);
      final btn = tester.widget<ButtonStyleButton>(
        find.ancestor(
          of: find.text('Sending…'),
          matching: find.bySubtype<ButtonStyleButton>(),
        ),
      );
      expect(btn.enabled, isFalse); // disabled: no double sends

      // a second tap while sending must not trigger another send
      await tester.tap(find.text('Sending…'), warnIfMissed: false);
      await tester.pump(const Duration(milliseconds: 80));
      expect(sentBody, 'Edited reply body');
      expect(sendCount, 1);
      expect(find.text('Reply sent from your Gmail account.'), findsOneWidget);
    });

    testWidgets('the editor invites an edit and shows the AI-draft disclaimer',
        (tester) async {
      await tester.pumpWidget(_host(ReplySuggestionsPanel(
        onGenerate: () async => _threeSuggestions(),
        onSend: (_) async => const ReplySendResultDto(emailId: 'gmail_1'),
      )));
      await tester.tap(find.text('Suggest Replies'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Edit & send').first);
      await tester.pump();

      expect(find.textContaining('This is an AI draft'), findsOneWidget);
      expect(find.textContaining('REVIEW & EDIT'), findsOneWidget);
    });

    testWidgets('after a send that completes the email, the panel says it is done',
        (tester) async {
      await tester.pumpWidget(_host(ReplySuggestionsPanel(
        onGenerate: () async => _threeSuggestions(),
        onSend: (_) async => const ReplySendResultDto(
          emailId: 'gmail_1',
          gmailMessageId: 'sent_1',
          emailMarkedCompleted: true,
        ),
      )));
      await tester.tap(find.text('Suggest Replies'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Edit & send').first);
      await tester.pump();
      await tester.enterText(find.byType(TextField), 'Done, thanks.');
      await tester.pump();
      await tester.tap(find.text('Send Reply'));
      await tester.pumpAndSettle();

      expect(find.text('Reply sent from your Gmail account.'), findsOneWidget);
      expect(find.text('This email is now marked as done.'), findsOneWidget);
    });

    testWidgets('a send failure keeps the draft and shows an inline error', (tester) async {
      await tester.pumpWidget(_host(ReplySuggestionsPanel(
        onGenerate: () async => _threeSuggestions(),
        onSend: (_) async => throw ApiException(
          statusCode: 502,
          message: 'Gmail API request failed.',
        ),
      )));
      await tester.tap(find.text('Suggest Replies'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Edit & send').first);
      await tester.pump();
      await tester.enterText(find.byType(TextField), 'My careful draft');
      await tester.pump();

      await tester.tap(find.text('Send Reply'));
      await tester.pumpAndSettle();

      // draft preserved, error shown, still in the editor
      expect(find.widgetWithText(TextField, 'My careful draft'), findsOneWidget);
      expect(find.text('Send Reply'), findsOneWidget);
      expect(find.textContaining('try again', findRichText: true), findsWidgets);
    });

    testWidgets('an unavailable AI shows an error with a Try Again action', (tester) async {
      var attempts = 0;
      await tester.pumpWidget(_host(ReplySuggestionsPanel(
        onGenerate: () async {
          attempts++;
          if (attempts == 1) {
            throw ApiException(
              statusCode: 503,
              message: 'unavailable',
              errorType: 'LLMUnavailableError',
            );
          }
          return _threeSuggestions();
        },
        onSend: (_) async => const ReplySendResultDto(emailId: 'gmail_1'),
      )));

      await tester.tap(find.text('Suggest Replies'));
      await tester.pumpAndSettle();
      expect(find.textContaining('AI service is unavailable'), findsOneWidget);
      expect(find.text('Try Again'), findsOneWidget);

      await tester.tap(find.text('Try Again'));
      await tester.pumpAndSettle();
      expect(find.text('Edit & send'), findsNWidgets(3));
    });
  });

  group('EmailDetailScreen — panel visibility (backend-driven)', () {
    Email baseEmail() => Email(
          id: 'gmail_1',
          senderName: 'Prof',
          senderEmail: 'prof@college.edu',
          subject: 'Meeting tomorrow',
          body: 'Can you confirm you will attend?',
          snippet: 'Can you confirm you will attend?',
          receivedAt: DateTime(2026, 9, 1),
          analysis: const AgentAnalysis(
            category: 'Reply Required',
            priority: PriorityLevel.high,
            actionRequired: true,
            reasoningSummary: 'Needs a reply.',
          ),
          userState: const UserState(),
        );

    testWidgets('shows the panel when the backend detail says a reply is needed',
        (tester) async {
      final controller = InboxController(
        repository: _DetailRepo(replyNeeded: true),
        enableCountdownTimer: false,
      );
      await tester.pumpWidget(MaterialApp(
        home: EmailDetailScreen(email: baseEmail(), controller: controller),
      ));
      await tester.pumpAndSettle();

      await tester.scrollUntilVisible(
        find.text('AI REPLY SUGGESTIONS'),
        400,
        scrollable: find.byType(Scrollable).first,
      );
      expect(find.text('AI REPLY SUGGESTIONS'), findsOneWidget);
      expect(find.text('Suggest Replies'), findsOneWidget);
      controller.dispose();
    });

    testWidgets('hides the panel when the backend detail has no reply signal',
        (tester) async {
      final controller = InboxController(
        repository: _DetailRepo(replyNeeded: false),
        enableCountdownTimer: false,
      );
      await tester.pumpWidget(MaterialApp(
        home: EmailDetailScreen(email: baseEmail(), controller: controller),
      ));
      await tester.pumpAndSettle();

      expect(find.text('AI REPLY SUGGESTIONS'), findsNothing);
      controller.dispose();
    });
  });

  group('EmailDetailScreen — internal reasoning is hidden', () {
    Email baseEmail() => Email(
          id: 'gmail_1',
          senderName: 'Prof',
          senderEmail: 'prof@college.edu',
          subject: 'Meeting tomorrow',
          body: 'Can you confirm you will attend?',
          snippet: 'Can you confirm you will attend?',
          receivedAt: DateTime(2026, 9, 1),
          analysis: const AgentAnalysis(
            category: 'Reply Required',
            priority: PriorityLevel.high,
            actionRequired: true,
            confidence: 0.91,
            reasoningSummary: 'INTERNAL: escalated to LLM; conflict rule fired.',
          ),
          userState: const UserState(),
        );

    testWidgets('no reasoning summary, trace link, scores or routing on the card',
        (tester) async {
      final controller = InboxController(
        repository: _DetailRepo(replyNeeded: true),
        enableCountdownTimer: false,
      );
      await tester.pumpWidget(MaterialApp(
        home: EmailDetailScreen(email: baseEmail(), controller: controller),
      ));
      await tester.pumpAndSettle();

      expect(find.text('REASONING SUMMARY'), findsNothing);
      expect(find.textContaining('INTERNAL: escalated'), findsNothing);
      expect(find.text('View Multi-Agent Reasoning Trace'), findsNothing);
      expect(find.textContaining('% confidence'), findsNothing);

      // clean results are still there
      expect(find.text('CATEGORY'), findsOneWidget);
      expect(find.text('PRIORITY'), findsOneWidget);
      expect(find.text('Reply requested'), findsOneWidget);
      controller.dispose();
    });
  });
}

/// A [MockEmailRepository] whose email detail carries (or not) a REPLY signal.
class _DetailRepo extends MockEmailRepository {
  final bool replyNeeded;
  _DetailRepo({required this.replyNeeded});

  @override
  Future<EmailStateDetailOutDto?> getEmailDetailDto(String id) async {
    return EmailStateDetailOutDto(
      emailId: id,
      senderEmail: 'prof@college.edu',
      senderName: 'Prof',
      subject: 'Meeting tomorrow',
      snippet: 'Can you confirm you will attend?',
      finalCategory: replyNeeded ? 'REPLY_REQUIRED' : 'FACULTY_ANNOUNCEMENT',
      priorityLevel: 'HIGH',
      priorityScore: 60,
      folderLabel: 'AMAR/Inbox',
      actionRequired: replyNeeded,
      primaryActionType: replyNeeded ? 'REPLY' : 'READ_AND_ACKNOWLEDGE',
      actions: replyNeeded
          ? const [ActionStateDto(actionRef: 'act_001', actionType: 'REPLY', status: 'PENDING')]
          : const [],
    );
  }
}

