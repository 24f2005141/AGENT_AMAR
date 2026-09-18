import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:agent_amar/dto/system_status_dto.dart';
import 'package:agent_amar/services/api_error.dart';
import 'package:agent_amar/services/email_repository.dart';
import 'package:agent_amar/state/inbox_controller.dart';
import 'package:agent_amar/widgets/system_status_bar.dart';

/// A [MockEmailRepository] whose system-status response is configurable per test.
class _FakeRepo extends MockEmailRepository {
  SystemStatusDto? next;
  Object? error;
  int calls = 0;

  @override
  Future<SystemStatusDto> getSystemStatus() async {
    calls++;
    if (error != null) throw error!;
    return next ??
        const SystemStatusDto(llmStatus: 'online', llmProvider: 'ollama', llmModel: 'qwen2.5:3b');
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() => SharedPreferences.setMockInitialValues({}));

  group('SystemStatusDto.fromJson', () {
    test('parses a full backend + llm payload', () {
      final dto = SystemStatusDto.fromJson({
        'backend': {'status': 'online'},
        'llm': {
          'status': 'online',
          'provider': 'ollama',
          'model': 'qwen2.5:3b',
          'detail': '2 model(s) available',
        },
      });
      expect(dto.backendOnline, true);
      expect(dto.llmStatus, 'online');
      expect(dto.llmProvider, 'ollama');
      expect(dto.llmModel, 'qwen2.5:3b');
      expect(dto.llmDetail, '2 model(s) available');
    });

    test('unconfigured LLM (LLM_PROVIDER=none)', () {
      final dto = SystemStatusDto.fromJson({
        'backend': {'status': 'online'},
        'llm': {'status': 'unconfigured', 'provider': 'none', 'model': null, 'detail': null},
      });
      expect(dto.llmStatus, 'unconfigured');
      expect(dto.llmProvider, 'none');
      expect(dto.llmModel, isNull);
    });

    test('missing llm block degrades to unknown, never throws', () {
      final dto = SystemStatusDto.fromJson({'backend': {'status': 'online'}});
      expect(dto.backendOnline, true);
      expect(dto.llmStatus, 'unknown');
    });

    test('backendUnreachable constant', () {
      expect(SystemStatusDto.backendUnreachable.backendOnline, false);
      expect(SystemStatusDto.backendUnreachable.llmStatus, 'unknown');
    });
  });

  group('InboxController.refreshSystemStatus', () {
    late _FakeRepo repo;
    late InboxController controller;

    setUp(() async {
      repo = _FakeRepo();
      controller = InboxController(repository: repo, enableCountdownTimer: false);
      await pumpEventQueue(); // settle constructor's fire-and-forget calls
    });

    tearDown(() => controller.dispose());

    test('backend online + AI online', () async {
      repo.next = const SystemStatusDto(
        llmStatus: 'online', llmProvider: 'ollama', llmModel: 'qwen2.5:3b');
      await controller.refreshSystemStatus();
      expect(controller.backendOnline, true);
      expect(controller.llmStatus, 'online');
      expect(controller.llmProvider, 'ollama');
      expect(controller.llmModel, 'qwen2.5:3b');
    });

    test('backend online + AI offline', () async {
      repo.next = const SystemStatusDto(llmStatus: 'offline', llmProvider: 'ollama');
      await controller.refreshSystemStatus();
      expect(controller.backendOnline, true);
      expect(controller.llmStatus, 'offline');
    });

    test('AI unconfigured', () async {
      repo.next = const SystemStatusDto(llmStatus: 'unconfigured', llmProvider: 'none');
      await controller.refreshSystemStatus();
      expect(controller.backendOnline, true);
      expect(controller.llmStatus, 'unconfigured');
    });

    test('backend unreachable -> offline + AI unknown (no guessing)', () async {
      repo.error = ApiException.networkError(Exception('connection refused'));
      await controller.refreshSystemStatus();
      expect(controller.backendOnline, false);
      expect(controller.llmStatus, 'unknown');
    });

    test('recovers when the backend comes back', () async {
      repo.error = ApiException.timeout();
      await controller.refreshSystemStatus();
      expect(controller.backendOnline, false);

      repo.error = null;
      repo.next = const SystemStatusDto(llmStatus: 'online', llmProvider: 'gemini');
      await controller.refreshSystemStatus();
      expect(controller.backendOnline, true);
      expect(controller.llmStatus, 'online');
      expect(controller.llmProvider, 'gemini');
    });

    test('pull-to-refresh also refreshes system status (no polling loop)', () async {
      final before = repo.calls;
      await controller.refreshInbox();
      await pumpEventQueue();
      expect(repo.calls, before + 1);
    });

    test('llmStatus is forced to unknown while backend is down', () async {
      repo.next = const SystemStatusDto(llmStatus: 'online');
      await controller.refreshSystemStatus();
      expect(controller.llmStatus, 'online');

      repo.error = ApiException.networkError(Exception('down'));
      await controller.refreshSystemStatus();
      expect(controller.llmStatus, 'unknown');
    });
  });

  group('SystemStatusBar widget', () {
    Future<void> pump(WidgetTester tester, Widget child) => tester.pumpWidget(
          MaterialApp(home: Scaffold(body: child)),
        );

    testWidgets('backend online + AI online', (tester) async {
      await pump(tester, const SystemStatusBar(backendOnline: true, llmStatus: 'online'));
      expect(find.text('Backend Online'), findsOneWidget);
      expect(find.text('AI Online'), findsOneWidget);
    });

    testWidgets('backend online + AI offline', (tester) async {
      await pump(tester, const SystemStatusBar(backendOnline: true, llmStatus: 'offline'));
      expect(find.text('Backend Online'), findsOneWidget);
      expect(find.text('AI Offline'), findsOneWidget);
    });

    testWidgets('AI unconfigured', (tester) async {
      await pump(tester, const SystemStatusBar(backendOnline: true, llmStatus: 'unconfigured'));
      expect(find.text('AI Unconfigured'), findsOneWidget);
    });

    testWidgets('backend offline + AI unknown', (tester) async {
      await pump(tester, const SystemStatusBar(backendOnline: false, llmStatus: 'unknown'));
      expect(find.text('Backend Offline'), findsOneWidget);
      expect(find.text('AI Unknown'), findsOneWidget);
    });
  });
}
