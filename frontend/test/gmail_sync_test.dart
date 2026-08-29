import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:agent_amar/dto/gmail_sync_dto.dart';
import 'package:agent_amar/services/api_error.dart';
import 'package:agent_amar/services/email_repository.dart';
import 'package:agent_amar/state/inbox_controller.dart';

/// A [MockEmailRepository] whose Gmail sync behaviour is configurable per test.
class _FakeSyncRepository extends MockEmailRepository {
  GmailSyncResultDto? nextResult;
  Object? nextError;
  int syncCallCount = 0;

  @override
  Future<GmailSyncResultDto> syncGmail() async {
    syncCallCount++;
    if (nextError != null) throw nextError!;
    return nextResult ?? const GmailSyncResultDto(status: 'synced');
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() => SharedPreferences.setMockInitialValues({}));

  group('GmailSyncResultDto.fromJson', () {
    test('parses "baselined" (no historical processing)', () {
      final dto = GmailSyncResultDto.fromJson({
        'status': 'baselined',
        'monitoring_started_at': '2026-08-29T06:00:00Z',
        'last_history_id': '184092',
        'processed': 0,
        'new_message_ids': <dynamic>[],
        'errors': <dynamic>[],
      });
      expect(dto.isBaselined, true);
      expect(dto.processed, 0);
      expect(dto.processedNewMail, false);
      expect(dto.lastHistoryId, '184092');
    });

    test('parses "synced" with processed messages', () {
      final dto = GmailSyncResultDto.fromJson({
        'status': 'synced',
        'from_history_id': '184092',
        'last_history_id': '184310',
        'last_sync_at': '2026-08-29T06:02:00Z',
        'new_message_ids': ['18f', '18a'],
        'processed': 2,
        'results': [
          {'email_id': 'gmail_18f', 'created': true, 'priority_level': 'URGENT'},
        ],
        'errors': <dynamic>[],
      });
      expect(dto.isSynced, true);
      expect(dto.processed, 2);
      expect(dto.processedNewMail, true);
      expect(dto.newMessageIds, ['18f', '18a']);
      expect(dto.fromHistoryId, '184092');
      expect(dto.lastSyncAt, isNotNull);
    });

    test('parses "history_expired_rebaselined"', () {
      final dto = GmailSyncResultDto.fromJson({
        'status': 'history_expired_rebaselined',
        'from_history_id': '100',
        'last_history_id': '9000',
        'processed': 0,
        'new_message_ids': <dynamic>[],
        'errors': <dynamic>[],
      });
      expect(dto.isHistoryExpiredRebaselined, true);
      expect(dto.processed, 0);
      expect(dto.lastHistoryId, '9000');
    });

    test('parses "skipped_locked"', () {
      final dto = GmailSyncResultDto.fromJson({
        'status': 'skipped_locked',
        'processed': 0,
        'new_message_ids': <dynamic>[],
        'errors': <dynamic>[],
      });
      expect(dto.isSkippedLocked, true);
      expect(dto.processed, 0);
    });

    test('tolerates missing / malformed fields', () {
      final dto = GmailSyncResultDto.fromJson({});
      expect(dto.status, 'synced');
      expect(dto.processed, 0);
      expect(dto.newMessageIds, isEmpty);
      expect(dto.lastHistoryId, isNull);
    });
  });

  group('GmailSyncStatusDto.fromJson', () {
    test('parses an active monitoring state', () {
      final dto = GmailSyncStatusDto.fromJson({
        'monitoring': true,
        'account_email': 'you@gmail.com',
        'monitoring_started_at': '2026-08-29T06:00:00Z',
        'last_sync_at': '2026-08-29T06:02:00Z',
        'last_history_id': '184310',
      });
      expect(dto.monitoring, true);
      expect(dto.accountEmail, 'you@gmail.com');
      expect(dto.lastHistoryId, '184310');
      expect(dto.lastSyncAt, isNotNull);
    });

    test('parses the "not established yet" state', () {
      final dto = GmailSyncStatusDto.fromJson({
        'monitoring': false,
        'account_email': null,
        'monitoring_started_at': null,
        'last_sync_at': null,
        'last_history_id': null,
      });
      expect(dto.monitoring, false);
      expect(dto.accountEmail, isNull);
      expect(dto.lastHistoryId, isNull);
    });
  });

  group('InboxController.refreshInbox — Phase 13 incremental sync', () {
    late _FakeSyncRepository repo;
    late InboxController controller;

    setUp(() async {
      repo = _FakeSyncRepository();
      controller = InboxController(
        repository: repo,
        enableCountdownTimer: false,
      );
      // settle the constructor's fire-and-forget checkGmailStatus() + loadData()
      await pumpEventQueue();
    });

    tearDown(() => controller.dispose());

    test('calls POST /gmail/sync exactly once and refreshes data', () async {
      repo.nextResult = GmailSyncResultDto(
        status: 'synced',
        processed: 1,
        newMessageIds: const ['gmail_new'],
        lastSyncAt: DateTime.now(),
      );

      await controller.refreshInbox();

      expect(repo.syncCallCount, 1); // one sync, no polling loop
      expect(controller.errorMessage, isNull);
      expect(controller.lastSyncStatus, 'synced');
      expect(controller.emails, isNotEmpty); // loadData ran
      expect(controller.isRefreshing, false);
    });

    test('"baselined" is not an error and does not flood historical mail', () async {
      repo.nextResult = const GmailSyncResultDto(status: 'baselined', processed: 0);
      final before = (await repo.getEmails()).length;

      await controller.refreshInbox();

      expect(controller.errorMessage, isNull);
      expect(controller.lastSyncStatus, 'baselined');
      expect(controller.emails.length, before); // no sudden historical emails
      expect(controller.gmailMonitoringActive, true);
    });

    test('"skipped_locked" is safe — no error, no retry storm', () async {
      repo.nextResult = const GmailSyncResultDto(status: 'skipped_locked');

      await controller.refreshInbox();
      await controller.refreshInbox();

      expect(controller.errorMessage, isNull);
      expect(controller.lastSyncStatus, 'skipped_locked');
      expect(repo.syncCallCount, 2); // one per explicit pull, never auto-retried
    });

    test('"history_expired_rebaselined" is handled silently', () async {
      repo.nextResult = const GmailSyncResultDto(
        status: 'history_expired_rebaselined',
        lastHistoryId: '9000',
      );

      await controller.refreshInbox();

      expect(controller.errorMessage, isNull);
      expect(controller.lastSyncStatus, 'history_expired_rebaselined');
      expect(controller.emails, isNotEmpty);
    });

    test('Gmail not connected (401) is graceful', () async {
      repo.nextError = ApiException(
        statusCode: 401,
        message: 'Gmail is not connected.',
        errorType: 'GmailNotConnectedError',
      );

      await controller.refreshInbox();

      expect(controller.isGmailConnected, false);
      expect(controller.gmailMonitoringActive, false);
      expect(controller.errorMessage, isNull); // not surfaced as a hard error
      expect(controller.isRefreshing, false);
    });

    test('network failure sets an error but does not crash', () async {
      repo.nextError = ApiException.networkError('SocketException');

      await controller.refreshInbox();

      expect(controller.errorMessage, isNotNull);
      expect(controller.isRefreshing, false);
    });

    test('backend 5xx flows through the existing error architecture', () async {
      repo.nextError = ApiException(statusCode: 502, message: 'Gmail API request failed.');

      await controller.refreshInbox();

      expect(controller.errorMessage, 'Gmail API request failed.');
      expect(controller.isRefreshing, false);
    });
  });

  group('MockEmailRepository sync surface', () {
    test('syncGmail returns a synced result', () async {
      final result = await MockEmailRepository().syncGmail();
      expect(result.status, 'synced');
    });

    test('getGmailSyncStatus reports active monitoring', () async {
      final status = await MockEmailRepository().getGmailSyncStatus();
      expect(status.monitoring, true);
      expect(status.accountEmail, isNotNull);
    });
  });

  group('Production audit', () {
    const banned = [
      'unread/process',
      'processUnreadGmail',
      'processUnreadEmails',
      'processUnread(',
    ];

    test('no production Dart file references the old bulk-ingest endpoint', () {
      final offenders = <String>[];
      for (final entity in Directory('lib').listSync(recursive: true)) {
        if (entity is! File || !entity.path.endsWith('.dart')) continue;
        final text = entity.readAsStringSync();
        for (final needle in banned) {
          if (text.contains(needle)) {
            offenders.add('${entity.path} :: "$needle"');
          }
        }
      }
      expect(offenders, isEmpty,
          reason: 'Legacy Gmail processing found in production code:\n'
              '${offenders.join('\n')}');
    });
  });
}
