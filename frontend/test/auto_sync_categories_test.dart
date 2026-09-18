import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:agent_amar/dto/gmail_sync_dto.dart';
import 'package:agent_amar/models/agent_analysis.dart';
import 'package:agent_amar/models/email.dart';
import 'package:agent_amar/models/notification_event.dart';
import 'package:agent_amar/models/reminder.dart';
import 'package:agent_amar/models/user_state.dart';
import 'package:agent_amar/services/email_repository.dart';
import 'package:agent_amar/state/inbox_controller.dart';

Email _email(String id, PrimaryCategory pc, {bool completed = false}) => Email(
      id: id,
      senderName: 'S',
      senderEmail: 's@x.com',
      subject: id,
      body: 'b',
      snippet: 'b',
      receivedAt: DateTime(2026, 9, 1),
      primaryCategory: pc,
      analysis: const AgentAnalysis(
        category: 'X',
        priority: PriorityLevel.high,
        actionRequired: true, // deliberately true for ALL — the point is the UI
        reasoningSummary: 'r', //  ignores this and uses primaryCategory
      ),
      userState: UserState(isCompleted: completed),
    );

/// A repo with a settable email list + call counters, and NO real Gmail/LLM.
class _Repo extends MockEmailRepository {
  List<Email> emails;
  int getEmailsCalls = 0;
  int syncGmailCalls = 0;

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
  Future<GmailSyncResultDto> syncGmail() async {
    syncGmailCalls++;
    return const GmailSyncResultDto(status: 'synced');
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() => SharedPreferences.setMockInitialValues({}));

  group('mutually-exclusive primary categories (UI consumes backend value)', () {
    late _Repo repo;
    late InboxController c;

    setUp(() async {
      repo = _Repo([
        _email('reply1', PrimaryCategory.replyRequired),
        _email('reply2', PrimaryCategory.replyRequired),
        _email('action1', PrimaryCategory.actionRequired),
        _email('important1', PrimaryCategory.important),
        _email('low1', PrimaryCategory.lowPriority),
      ]);
      c = InboxController(repository: repo, enableCountdownTimer: false);
      await pumpEventQueue();
    });

    tearDown(() => c.dispose());

    test('Reply Required emails never appear under Action Required', () {
      final attention = c.needsAttentionEmails.map((e) => e.id).toSet();
      expect(attention, {'action1'});
      expect(attention.contains('reply1'), isFalse);
      expect(attention.contains('reply2'), isFalse);
    });

    test('each filter chip returns exactly its own bucket, disjoint', () {
      final buckets = <String, Set<String>>{};
      for (final entry in {
        'reply_needed': 'reply',
        'action_required': 'action',
        'important': 'important',
        'low_priority': 'low',
      }.entries) {
        c.setFilter(entry.key);
        buckets[entry.key] = c.emails.map((e) => e.id).toSet();
      }
      expect(buckets['reply_needed'], {'reply1', 'reply2'});
      expect(buckets['action_required'], {'action1'});
      expect(buckets['important'], {'important1'});
      expect(buckets['low_priority'], {'low1'});

      // pairwise disjoint + full cover
      final all = <String>{};
      final lists = buckets.values.toList();
      for (var i = 0; i < lists.length; i++) {
        for (var j = i + 1; j < lists.length; j++) {
          expect(lists[i].intersection(lists[j]), isEmpty);
        }
        all.addAll(lists[i]);
      }
      expect(all, {'reply1', 'reply2', 'action1', 'important1', 'low1'});
    });

    test('a completed action email drops out of Action Required', () async {
      repo.emails = [
        _email('action1', PrimaryCategory.actionRequired, completed: true),
        _email('action2', PrimaryCategory.actionRequired),
      ];
      await c.loadData();
      expect(c.needsAttentionEmails.map((e) => e.id), ['action2']);
    });
  });

  group('foreground auto-refresh (persisted state only, no Gmail sync)', () {
    test('autoRefresh reloads persisted state and never calls syncGmail', () async {
      final repo = _Repo([_email('a', PrimaryCategory.important)]);
      final c = InboxController(repository: repo, enableCountdownTimer: false);
      await pumpEventQueue();
      final baseGet = repo.getEmailsCalls;

      await c.autoRefresh();
      await c.autoRefresh();

      expect(repo.getEmailsCalls, greaterThan(baseGet)); // reloaded persisted data
      expect(repo.syncGmailCalls, 0); // NEVER triggers Gmail sync / LLM
      c.dispose();
    });

    test('autoRefresh updates the list when the backend data changed', () async {
      final repo = _Repo([_email('a', PrimaryCategory.lowPriority)]);
      final c = InboxController(repository: repo, enableCountdownTimer: false);
      await pumpEventQueue();

      repo.emails = [
        _email('a', PrimaryCategory.lowPriority),
        _email('b', PrimaryCategory.replyRequired),
      ];
      var notified = false;
      c.addListener(() => notified = true);

      await c.autoRefresh();

      expect(c.allEmails.map((e) => e.id), containsAll(['a', 'b']));
      expect(notified, isTrue);
      c.dispose();
    });

    test('startAutoRefresh is a no-op when the interval is disabled', () {
      final repo = _Repo(const []);
      final c = InboxController(
        repository: repo,
        enableCountdownTimer: false,
        autoRefreshInterval: null,
        autoRefreshOverride: true,
      );
      c.startAutoRefresh(); // must not throw / must not schedule
      c.stopAutoRefresh();
      c.dispose();
    });

    test('the periodic poll fires autoRefresh (reload), not a Gmail sync', () async {
      final repo = _Repo([_email('a', PrimaryCategory.important)]);
      final c = InboxController(
        repository: repo,
        enableCountdownTimer: false,
        autoRefreshInterval: const Duration(milliseconds: 30),
        autoRefreshOverride: true,
      );
      await pumpEventQueue();
      c.startAutoRefresh();
      final base = repo.getEmailsCalls;
      await Future<void>.delayed(const Duration(milliseconds: 100));

      expect(repo.getEmailsCalls, greaterThan(base));
      expect(repo.syncGmailCalls, 0);
      c.dispose();
    });
  });
}
