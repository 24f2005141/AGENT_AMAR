import 'package:flutter_test/flutter_test.dart';
import 'package:agent_amar/services/email_repository.dart';
import 'package:agent_amar/services/push_messaging_service.dart';

class _SpyRepo extends MockEmailRepository {
  final List<Map<String, String?>> registered = [];
  final List<String> unregistered = [];

  @override
  Future<void> registerDevice({
    required String fcmToken,
    String platform = 'android',
    String? deviceLabel,
    String? appVersion,
    String? previousToken,
  }) async {
    registered.add({'token': fcmToken, 'previous': previousToken});
  }

  @override
  Future<void> unregisterDevice(String fcmToken) async => unregistered.add(fcmToken);
}

PushMessagingService _svc(
  _SpyRepo repo,
  FakePushPlatform platform, {
  void Function(String, String, Map<String, dynamic>)? onForeground,
}) {
  return PushMessagingService(
    repository: repo,
    platform: platform,
    onForeground: onForeground ?? (_, __, ___) {},
  );
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('unavailable platform -> service disabled, nothing registered', () async {
    final repo = _SpyRepo();
    final svc = _svc(repo, FakePushPlatform(available: false));
    await svc.initialize();
    await svc.onAuthenticated();
    expect(svc.isAvailable, false);
    expect(repo.registered, isEmpty);
  });

  test('registers the FCM token once the user is authenticated', () async {
    final repo = _SpyRepo();
    final platform = FakePushPlatform(currentToken: 'tok-1');
    final svc = _svc(repo, platform);

    await svc.initialize();
    expect(repo.registered, isEmpty); // not authenticated yet

    await svc.onAuthenticated();
    expect(repo.registered.single['token'], 'tok-1');
  });

  test('token refresh re-registers with the previous token', () async {
    final repo = _SpyRepo();
    final platform = FakePushPlatform(currentToken: 'tok-old');
    final svc = _svc(repo, platform);
    await svc.initialize();
    await svc.onAuthenticated();

    platform.emitTokenRefresh('tok-new');
    await Future<void>.delayed(Duration.zero);

    expect(repo.registered.last, {'token': 'tok-new', 'previous': 'tok-old'});
  });

  test('logout unregisters this device', () async {
    final repo = _SpyRepo();
    final svc = _svc(repo, FakePushPlatform(currentToken: 'tok-x'));
    await svc.initialize();
    await svc.onAuthenticated();

    await svc.onLogout();
    expect(repo.unregistered, ['tok-x']);
  });

  test('foreground message is surfaced locally, not routed', () async {
    final repo = _SpyRepo();
    final platform = FakePushPlatform();
    final seen = <Map<String, dynamic>>[];
    final routed = <String?>[];
    final svc = _svc(repo, platform, onForeground: (t, b, d) => seen.add(d));
    svc.attachNavigator((emailId, data) => routed.add(emailId));
    await svc.initialize();
    await svc.onAuthenticated();

    platform.emitForeground(
      {'title': 'Important email', 'body': 'x', 'email_id': 'gmail_9', 'type': 'new_priority_email'},
    );
    await Future<void>.delayed(Duration.zero);

    expect(seen.single['email_id'], 'gmail_9');
    expect(routed, isEmpty); // foreground never auto-navigates
  });

  test('background tap routes to the email when the navigator is attached', () async {
    final repo = _SpyRepo();
    final platform = FakePushPlatform();
    final routed = <String?>[];
    final svc = _svc(repo, platform);
    svc.attachNavigator((emailId, data) => routed.add(emailId));
    await svc.initialize();

    platform.emitOpened({'email_id': 'gmail_42', 'type': 'deadline_escalation'});
    await Future<void>.delayed(Duration.zero);

    expect(routed, ['gmail_42']);
  });

  test('terminated-launch payload is held pending until the navigator is ready', () async {
    final repo = _SpyRepo();
    final platform = FakePushPlatform()
      ..initialMessage = {'email_id': 'gmail_boot', 'type': 'new_priority_email'};
    final svc = _svc(repo, platform);

    await svc.initialize(); // no navigator yet
    expect(svc.pendingPush.value?.emailId, 'gmail_boot');

    final routed = <String?>[];
    svc.attachNavigator((emailId, data) => routed.add(emailId));
    expect(routed, ['gmail_boot']);
    expect(svc.pendingPush.value, isNull);
  });
}
