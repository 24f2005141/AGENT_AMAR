import 'package:flutter_test/flutter_test.dart';
import 'package:agent_amar/models/agent_analysis.dart';
import 'package:agent_amar/services/email_repository.dart';

void main() {
  group('MockEmailRepository Tests', () {
    late MockEmailRepository repository;

    setUp(() {
      repository = MockEmailRepository();
    });

    test('getEmails returns initialized mock emails', () async {
      final emails = await repository.getEmails();
      expect(emails.isNotEmpty, true);
      expect(emails.any((e) => e.analysis.priority == PriorityLevel.critical), true);
    });

    test('getNeedsAttentionEmails filters and sorts actionable emails', () async {
      final attention = await repository.getNeedsAttentionEmails();
      expect(attention.isNotEmpty, true);
      expect(attention.first.isCritical, true);
    });

    test('getDeadlineEmails returns emails with deadlines', () async {
      final deadlines = await repository.getDeadlineEmails();
      expect(deadlines.isNotEmpty, true);
      expect(deadlines.every((e) => e.analysis.deadline != null), true);
    });

    test('completeAction updates email completion state', () async {
      final initial = await repository.getEmailById('email_tcs_01');
      expect(initial?.userState.isCompleted, false);

      final updated = await repository.completeAction('email_tcs_01', 'act_001');
      expect(updated.userState.isCompleted, true);

      final fetched = await repository.getEmailById('email_tcs_01');
      expect(fetched?.userState.isCompleted, true);
    });

    test('snoozeEmail and clearSnooze update snooze state', () async {
      final until = DateTime.now().add(const Duration(hours: 2));
      final snoozed = await repository.snoozeEmail('email_robotics_02', until);
      expect(snoozed.userState.snoozedUntil, until);

      final cleared = await repository.clearSnooze('email_robotics_02');
      expect(cleared.userState.snoozedUntil, null);
    });

    test('createReminder and cancelReminder manage reminder items', () async {
      final remTime = DateTime.now().add(const Duration(days: 1));
      final reminder = await repository.createReminder(
        emailId: 'email_tcs_01',
        reminderAt: remTime,
        note: 'Follow up on placement form',
      );

      expect(reminder.emailId, 'email_tcs_01');
      expect(reminder.actionDescription, 'Follow up on placement form');

      final listBefore = await repository.getReminders();
      expect(listBefore.any((r) => r.id == reminder.id), true);

      await repository.cancelReminder('email_tcs_01', int.parse(reminder.id));
      final listAfter = await repository.getReminders();
      expect(listAfter.any((r) => r.id == reminder.id), false);
    });

    test('runDeadlineCheck returns decision results', () async {
      final check = await repository.runDeadlineCheck();
      expect(check.results.isNotEmpty, true);
      expect(check.results.first.requiresAlarm, true);
    });

    test('syncGmail returns an incremental sync result (Phase 12/13)', () async {
      final result = await repository.syncGmail();
      expect(result.status, 'synced');
      expect(result.isSynced, true);
    });

    test('getGmailSyncStatus reports the monitoring baseline', () async {
      final status = await repository.getGmailSyncStatus();
      expect(status.monitoring, true);
      expect(status.lastHistoryId, isNotNull);
    });
  });
}
