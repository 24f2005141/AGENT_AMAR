import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:agent_amar/dto/email_state_dto.dart';
import 'package:agent_amar/models/agent_analysis.dart';
import 'package:agent_amar/models/email.dart';
import 'package:agent_amar/models/notification_event.dart';
import 'package:agent_amar/models/reminder.dart';
import 'package:agent_amar/models/user_state.dart';
import 'package:agent_amar/screens/email_detail_screen.dart';
import 'package:agent_amar/services/api_error.dart';
import 'package:agent_amar/services/email_repository.dart';
import 'package:agent_amar/state/inbox_controller.dart';

Email _email(String id, PrimaryCategory pc) => Email(
      id: id,
      senderName: 'Prof',
      senderEmail: 'prof@x.edu',
      subject: 'Subject $id',
      body: 'preview',
      snippet: 'preview',
      receivedAt: DateTime(2026, 9, 1),
      primaryCategory: pc,
      autoPrimaryCategory: pc,
      analysis: const AgentAnalysis(
        category: 'Internship',
        priority: PriorityLevel.high,
        actionRequired: true,
        reasoningSummary: 'r',
      ),
      userState: const UserState(),
    );

class _Repo extends MockEmailRepository {
  List<Email> emails;
  int feedbackCalls = 0;
  PrimaryCategory? lastCategory;
  bool fail = false;

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

  @override
  Future<Email> submitClassificationFeedback(
      String emailId, PrimaryCategory category) async {
    feedbackCalls++;
    lastCategory = category;
    if (fail) throw ApiException(statusCode: 422, message: 'invalid');
    final i = emails.indexWhere((e) => e.id == emailId);
    final updated = emails[i].copyWith(
      primaryCategory: category,
      primaryCategoryUserCorrected: true,
    );
    emails[i] = updated;
    return updated;
  }

  @override
  Future<EmailStateDetailOutDto?> getEmailDetailDto(String id) async {
    final e = emails.firstWhere((x) => x.id == id);
    return EmailStateDetailOutDto(
      emailId: e.id,
      senderEmail: e.senderEmail,
      senderName: e.senderName,
      subject: e.subject,
      snippet: e.snippet,
      finalCategory: 'INTERNSHIP',
      primaryCategory: e.primaryCategory.wire,
      autoPrimaryCategory: e.autoPrimaryCategory.wire,
      primaryCategorySource: e.primaryCategoryUserCorrected ? 'user' : 'auto',
      priorityLevel: 'HIGH',
      priorityScore: 60,
      folderLabel: 'AMAR/Inbox',
      actionRequired: true,
    );
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() => SharedPreferences.setMockInitialValues({}));

  group('InboxController.submitClassificationFeedback', () {
    late _Repo repo;
    late InboxController c;

    setUp(() async {
      repo = _Repo([
        _email('e_action', PrimaryCategory.actionRequired),
        _email('e_low', PrimaryCategory.lowPriority),
      ]);
      c = InboxController(repository: repo, enableCountdownTimer: false);
      await pumpEventQueue();
    });
    tearDown(() => c.dispose());

    test('correction moves the email out of its old bucket into the new one', () async {
      c.setFilter('action_required');
      expect(c.emails.map((e) => e.id), contains('e_action'));

      final updated = await c.submitClassificationFeedback(
          'e_action', PrimaryCategory.important);
      expect(updated.primaryCategory, PrimaryCategory.important);
      expect(repo.lastCategory, PrimaryCategory.important);

      c.setFilter('action_required');
      expect(c.emails.map((e) => e.id), isNot(contains('e_action')));
      c.setFilter('important');
      expect(c.emails.map((e) => e.id), contains('e_action'));
    });

    test('email still belongs to exactly one bucket after correction', () async {
      await c.submitClassificationFeedback('e_action', PrimaryCategory.important);
      final buckets = <String>[];
      for (final f in ['reply_needed', 'action_required', 'important', 'low_priority']) {
        c.setFilter(f);
        if (c.emails.any((e) => e.id == 'e_action')) buckets.add(f);
      }
      expect(buckets, ['important']);
    });

    test('a failing API call throws and does not mutate local state', () async {
      repo.fail = true;
      await expectLater(
        c.submitClassificationFeedback('e_action', PrimaryCategory.important),
        throwsA(isA<ApiException>()),
      );
      c.setFilter('action_required');
      expect(c.emails.map((e) => e.id), contains('e_action'));
    });
  });

  group('EmailDetailScreen — Change Classification', () {
    testWidgets('control is visible and the picker corrects the category', (tester) async {
      final repo = _Repo([_email('e1', PrimaryCategory.actionRequired)]);
      final c = InboxController(repository: repo, enableCountdownTimer: false);
      await tester.pumpWidget(MaterialApp(
        home: EmailDetailScreen(email: repo.emails.first, controller: c),
      ));
      await tester.pumpAndSettle();

      final scrollable = find.byType(Scrollable).first;
      await tester.scrollUntilVisible(
          find.text('Change Classification'), 300, scrollable: scrollable);
      expect(find.text('Change Classification'), findsOneWidget);

      await tester.tap(find.text('Change Classification'));
      await tester.pumpAndSettle();
      // the four canonical options, nothing else
      expect(find.text('Reply Required'), findsOneWidget);
      expect(find.text('Action Required'), findsWidgets);
      expect(find.text('Important'), findsOneWidget);
      expect(find.text('Low Priority'), findsOneWidget);

      await tester.tap(find.text('Important'));
      await tester.pump();
      await tester.tap(find.text('Save'));
      await tester.pumpAndSettle();

      expect(repo.feedbackCalls, 1);
      expect(repo.lastCategory, PrimaryCategory.important);
      expect(find.textContaining('Moved to Important'), findsOneWidget);
      c.dispose();
    });

    testWidgets('an API failure surfaces a snackbar, no crash', (tester) async {
      final repo = _Repo([_email('e2', PrimaryCategory.actionRequired)])..fail = true;
      final c = InboxController(repository: repo, enableCountdownTimer: false);
      await tester.pumpWidget(MaterialApp(
        home: EmailDetailScreen(email: repo.emails.first, controller: c),
      ));
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(find.text('Change Classification'), 300,
          scrollable: find.byType(Scrollable).first);
      await tester.tap(find.text('Change Classification'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Low Priority'));
      await tester.pump();
      await tester.tap(find.text('Save'));
      await tester.pumpAndSettle();

      expect(find.textContaining('Could not update'), findsOneWidget);
      c.dispose();
    });
  });
}
