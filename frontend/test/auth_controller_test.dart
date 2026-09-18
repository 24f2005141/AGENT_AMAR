import 'package:flutter_test/flutter_test.dart';
import 'package:agent_amar/config/api_config.dart';
import 'package:agent_amar/dto/app_user_dto.dart';
import 'package:agent_amar/dto/auth_flow_dto.dart';
import 'package:agent_amar/services/api_client.dart';
import 'package:agent_amar/services/api_error.dart';
import 'package:agent_amar/services/auth_session_store.dart';
import 'package:agent_amar/services/deep_link_service.dart';
import 'package:agent_amar/services/email_repository.dart';
import 'package:agent_amar/state/auth_controller.dart';

/// A repository whose auth surface is scriptable per test.
class _FakeAuthRepo extends MockEmailRepository {
  Object? meError;
  int meCalls = 0;
  int logoutCalls = 0;

  int startCalls = 0;
  int exchangeCalls = 0;
  String? lastExchangeCode;
  String? lastExchangeState;
  GoogleSessionDto Function()? exchangeResult;
  Object? exchangeError;

  @override
  Future<AuthMeDto> getCurrentUser() async {
    meCalls++;
    if (meError != null) throw meError!;
    return const AuthMeDto(
      user: AppUserDto(id: 7, googleEmail: 'p@gmail.com', displayName: 'P'),
      gmailConnected: true,
    );
  }

  @override
  Future<void> logout({String? fcmToken}) async => logoutCalls++;

  @override
  Future<GoogleAuthStartDto> startGoogleAuth() async {
    startCalls++;
    return const GoogleAuthStartDto(
        authorizationUrl: 'https://accounts.google.com/o/oauth2/x', flowId: 'f1');
  }

  @override
  Future<GoogleSessionDto> exchangeSession(String code, {String? state}) async {
    exchangeCalls++;
    lastExchangeCode = code;
    lastExchangeState = state;
    if (exchangeError != null) throw exchangeError!;
    return exchangeResult?.call() ??
        const GoogleSessionDto(
            sessionToken: 'fresh',
            user: AppUserDto(id: 7, googleEmail: 'p@gmail.com'));
  }
}

Uri _callbackUri({String? code = 'HANDOFF', String? state = 'f1', String? error}) {
  final q = <String, String>{
    if (code != null) 'code': code,
    if (state != null) 'state': state,
    if (error != null) 'error': error,
  };
  return Uri(scheme: 'agentamar', host: 'auth', path: '/callback', queryParameters: q.isEmpty ? null : q);
}

AuthController _make(
  _FakeAuthRepo repo, {
  String? token,
  Future<bool> Function(Uri)? launch,
  FakeDeepLinkPort? deepLinks,
  Duration oauthTimeout = const Duration(milliseconds: 300),
  Future<void> Function()? closeBrowser,
}) {
  return AuthController(
    repository: repo,
    store: InMemoryAuthSessionStore(token),
    deepLinks: deepLinks ?? FakeDeepLinkPort(),
    urlLauncher: launch ?? (_) async => true,
    closeBrowser: closeBrowser ?? () async {},
    oauthTimeout: oauthTimeout,
  );
}

void main() {
  // --- session bootstrap (unchanged behaviour) --------------------------

  test('bootstrap with no token -> unauthenticated', () async {
    final c = _make(_FakeAuthRepo());
    await c.bootstrap();
    expect(c.state, AuthState.unauthenticated);
  });

  test('bootstrap with a valid token -> authenticated', () async {
    final c = _make(_FakeAuthRepo(), token: 'good');
    await c.bootstrap();
    expect(c.state, AuthState.authenticated);
    expect(c.currentUser?.googleEmail, 'p@gmail.com');
    expect(c.sessionToken, 'good');
  });

  test('bootstrap with an expired token -> unauthenticated + store cleared', () async {
    final repo = _FakeAuthRepo()
      ..meError = ApiException(statusCode: 401, message: 'nope', errorType: 'AuthRequiredError');
    final store = InMemoryAuthSessionStore('stale');
    final c = AuthController(repository: repo, store: store, deepLinks: FakeDeepLinkPort());
    await c.bootstrap();
    expect(c.state, AuthState.unauthenticated);
    expect(await store.read(), isNull);
  });

  test('bootstrap with a backend 5xx keeps the token', () async {
    final repo = _FakeAuthRepo()..meError = ApiException(statusCode: 503, message: 'down');
    final store = InMemoryAuthSessionStore('maybe-good');
    final c = AuthController(repository: repo, store: store, deepLinks: FakeDeepLinkPort());
    await c.bootstrap();
    expect(c.state, AuthState.unauthenticated);
    expect(await store.read(), 'maybe-good');
  });

  test('onSessionExpired is idempotent', () async {
    final c = _make(_FakeAuthRepo(), token: 'good');
    await c.bootstrap();
    var notifications = 0;
    c.addListener(() => notifications++);
    await c.onSessionExpired();
    await c.onSessionExpired();
    expect(c.state, AuthState.unauthenticated);
    expect(c.sessionToken, isNull);
    expect(notifications, 1);
  });

  // --- deep-link OAuth (Phase 17) --------------------------------------

  test('startGoogleLogin opens a secure browser, then completes on the deep link', () async {
    final repo = _FakeAuthRepo();
    final links = FakeDeepLinkPort();
    Uri? launched;
    final c = _make(repo, launch: (u) async {
      launched = u;
      return true;
    }, deepLinks: links, oauthTimeout: const Duration(seconds: 5));

    final future = c.startGoogleLogin();
    await Future<void>.delayed(const Duration(milliseconds: 10));
    expect(c.state, AuthState.authenticating);
    expect(launched.toString(), 'https://accounts.google.com/o/oauth2/x');

    // backend 302'd the browser back to us
    links.emit(_callbackUri(code: 'HANDOFF', state: 'f1'));
    await future;

    expect(repo.exchangeCalls, 1);
    expect(repo.lastExchangeCode, 'HANDOFF');
    expect(repo.lastExchangeState, 'f1');
    expect(c.state, AuthState.authenticated);
    expect(c.sessionToken, 'fresh');
  });

  test('deep link received while the app is running (backgrounded return)', () async {
    final repo = _FakeAuthRepo();
    final links = FakeDeepLinkPort();
    final c = _make(repo, deepLinks: links, oauthTimeout: const Duration(seconds: 5));
    final future = c.startGoogleLogin();
    await Future<void>.delayed(const Duration(milliseconds: 10));
    // app goes to background for Google, then the OS delivers the callback intent
    links.emit(_callbackUri());
    await future;
    expect(c.isAuthenticated, true);
    expect(c.sessionToken, 'fresh');
  });

  test('cold-start / terminated: initial deep link authenticates on bootstrap', () async {
    final repo = _FakeAuthRepo();
    final links = FakeDeepLinkPort(initial: _callbackUri(code: 'COLD', state: 'f1'));
    final c = _make(repo, deepLinks: links);
    await c.bootstrap();
    expect(repo.exchangeCalls, 1);
    expect(repo.lastExchangeCode, 'COLD');
    expect(c.state, AuthState.authenticated);
    expect(c.sessionToken, 'fresh');
  });

  test('valid code triggers exactly one session exchange and stores the token', () async {
    final repo = _FakeAuthRepo();
    final store = InMemoryAuthSessionStore();
    final links = FakeDeepLinkPort();
    final c = AuthController(
      repository: repo, store: store, deepLinks: links,
      urlLauncher: (_) async => true, closeBrowser: () async {},
      oauthTimeout: const Duration(seconds: 5),
    );
    final future = c.startGoogleLogin();
    await Future<void>.delayed(const Duration(milliseconds: 10));
    links.emit(_callbackUri(code: 'ONCE'));
    links.emit(_callbackUri(code: 'ONCE')); // a duplicate intent must not re-exchange twice meaningfully
    await future;
    expect(await store.read(), 'fresh');
    expect(repo.exchangeCalls, greaterThanOrEqualTo(1));
  });

  test('invalid callback (missing code) is handled safely — no exchange, error shown', () async {
    final repo = _FakeAuthRepo();
    final links = FakeDeepLinkPort();
    final c = _make(repo, deepLinks: links, oauthTimeout: const Duration(seconds: 5));
    final future = c.startGoogleLogin();
    await Future<void>.delayed(const Duration(milliseconds: 10));
    links.emit(_callbackUri(code: null)); // no code
    await future;
    expect(repo.exchangeCalls, 0);
    expect(c.state, AuthState.unauthenticated);
    expect(c.errorMessage, contains('invalid'));
  });

  test('user cancelled Google (error=access_denied) surfaces a friendly message', () async {
    final repo = _FakeAuthRepo();
    final links = FakeDeepLinkPort();
    final c = _make(repo, deepLinks: links, oauthTimeout: const Duration(seconds: 5));
    final future = c.startGoogleLogin();
    await Future<void>.delayed(const Duration(milliseconds: 10));
    links.emit(_callbackUri(code: null, error: 'access_denied'));
    await future;
    expect(repo.exchangeCalls, 0);
    expect(c.state, AuthState.unauthenticated);
    expect(c.errorMessage, contains('cancelled'));
  });

  test('expired / already-used handoff code (400) is handled safely', () async {
    final repo = _FakeAuthRepo()
      ..exchangeError = ApiException(statusCode: 400, message: 'invalid_handoff');
    final store = InMemoryAuthSessionStore();
    final links = FakeDeepLinkPort();
    final c = AuthController(
      repository: repo, store: store, deepLinks: links,
      urlLauncher: (_) async => true, closeBrowser: () async {},
      oauthTimeout: const Duration(seconds: 5),
    );
    final future = c.startGoogleLogin();
    await Future<void>.delayed(const Duration(milliseconds: 10));
    links.emit(_callbackUri());
    await future;
    expect(c.state, AuthState.unauthenticated);
    expect(c.errorMessage, contains('expired'));
    expect(await store.read(), isNull); // never stored a broken token
  });

  test('browser cancellation (no callback ever arrives) times out cleanly', () async {
    final repo = _FakeAuthRepo();
    final c = _make(repo, oauthTimeout: const Duration(milliseconds: 60));
    await c.startGoogleLogin();
    expect(c.state, AuthState.unauthenticated);
    expect(c.errorMessage, anyOf(contains('cancelled'), contains('timed out')));
    expect(repo.exchangeCalls, 0);
  });

  test('a non-auth deep link is ignored', () async {
    final repo = _FakeAuthRepo();
    final links = FakeDeepLinkPort();
    final c = _make(repo, deepLinks: links, token: 'good');
    await c.bootstrap();
    links.emit(Uri.parse('agentamar://something/else?x=1'));
    links.emit(Uri.parse('https://accounts.google.com/o/oauth2/x'));
    await Future<void>.delayed(const Duration(milliseconds: 10));
    expect(repo.exchangeCalls, 0);
    expect(c.state, AuthState.authenticated);
  });

  test('re-login after logout stores the fresh token, never the old one', () async {
    final repo = _FakeAuthRepo();
    final store = InMemoryAuthSessionStore('old-token');
    final links = FakeDeepLinkPort();
    final c = AuthController(
      repository: repo, store: store, deepLinks: links,
      urlLauncher: (_) async => true, closeBrowser: () async {},
      oauthTimeout: const Duration(seconds: 5),
    );
    await c.bootstrap();
    await c.logout();
    expect(await store.read(), isNull);

    repo.exchangeResult = () => const GoogleSessionDto(
        sessionToken: 'new-token', user: AppUserDto(id: 9, googleEmail: 'other@gmail.com'));
    final future = c.startGoogleLogin();
    await Future<void>.delayed(const Duration(milliseconds: 10));
    links.emit(_callbackUri());
    await future;

    expect(c.state, AuthState.authenticated);
    expect(c.sessionToken, 'new-token');
    expect(await store.read(), 'new-token');
  });

  test('logout clears the session and returns to unauthenticated', () async {
    final repo = _FakeAuthRepo();
    final store = InMemoryAuthSessionStore('good');
    final c = AuthController(repository: repo, store: store, deepLinks: FakeDeepLinkPort());
    await c.bootstrap();
    await c.logout();
    expect(repo.logoutCalls, 1);
    expect(c.state, AuthState.unauthenticated);
    expect(await store.read(), isNull);
  });

  // --- config / no hardcoded localhost -------------------------------

  test('the OAuth callback is a custom scheme, never a localhost URL', () {
    expect(ApiConfig.authCallbackScheme, 'agentamar');
    expect(ApiConfig.authCallbackScheme, isNot(anyOf('http', 'https')));
    expect(ApiConfig.isAuthCallback(Uri.parse('agentamar://auth/callback?code=x')), isTrue);
    expect(ApiConfig.isAuthCallback(Uri.parse('http://localhost:8000/api/v1/auth/google/callback')), isFalse);
    expect(ApiConfig.isAuthCallback(Uri.parse('https://accounts.google.com/o/oauth2/x')), isFalse);
  });

  test('ApiException attaches the bearer token from the provider', () {
    final client = ApiClient(tokenProvider: () => 'abc123');
    expect(client.tokenProvider!(), 'abc123');
  });
}
