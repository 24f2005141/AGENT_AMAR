import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:agent_amar/config/api_config.dart';
import 'package:agent_amar/services/api_client.dart';
import 'package:agent_amar/services/email_repository.dart';

http.Response _ok(String body) =>
    http.Response(body, 200, headers: {'content-type': 'application/json'});

void main() {
  final original = ApiConfig.baseUrl;
  tearDown(() => ApiConfig.baseUrl = original);

  test('with no --dart-define the app uses the dev fallback (overridable)', () {
    // No --dart-define=API_BASE_URL in the test build.
    expect(ApiConfig.usingDevFallback, isTrue);
    expect(ApiConfig.baseUrl, isNotEmpty);
    // never the Ollama host — that is FastAPI's concern, not the app's
    expect(ApiConfig.baseUrl.contains('ollamaserver.com'), isFalse);
    expect(ApiConfig.baseUrl, startsWith('http'));
  });

  test('every request path is joined onto the configured backend URL', () async {
    for (final base in [
      'http://192.168.1.10:8000',              // LAN dev
      'https://amar-api.example.com',           // FastAPI tunnel
      'https://amar-api.example.com/',          // trailing slash tolerated
    ]) {
      ApiConfig.baseUrl = base;
      Uri? seen;
      final client = ApiClient(client: MockClient((req) async {
        seen = req.url;
        return _ok('{"ok":true}');
      }));
      await client.get('/api/v1/auth/me');
      final expectedBase = base.replaceAll(RegExp(r'/$'), '');
      expect(seen!.toString(), '$expectedBase/api/v1/auth/me');
      expect(seen!.toString().contains('//api/v1'), isFalse);
    }
  });

  test('Google login (/start) is built from the configured backend URL', () async {
    ApiConfig.baseUrl = 'https://amar-api.example.com';
    Uri? seen;
    final repo = ApiEmailRepository(
      client: ApiClient(client: MockClient((req) async {
        seen = req.url;
        return _ok('{"authorization_url":"https://accounts.google.com/o/oauth2/x","flow_id":"f1"}');
      })),
    );

    final dto = await repo.startGoogleAuth();

    expect(seen!.toString(), 'https://amar-api.example.com/api/v1/auth/google/start');
    expect(dto.authorizationUrl, startsWith('https://accounts.google.com/'));
    // the app opens the URL the backend returns — it never constructs a Google URL
    expect(dto.authorizationUrl.contains('localhost'), isFalse);
  });

  test('session polling (/session) also uses the configured backend URL', () async {
    ApiConfig.baseUrl = 'https://amar-api.example.com';
    Uri? seen;
    final repo = ApiEmailRepository(
      client: ApiClient(client: MockClient((req) async {
        seen = req.url;
        return _ok('{"status":"pending"}');
      })),
    );
    await repo.pollGoogleSession('f1');
    expect(seen!.path, '/api/v1/auth/google/session');
    expect(seen!.host, 'amar-api.example.com');
    expect(seen!.scheme, 'https');
  });

  test('existing LAN configuration still works', () async {
    ApiConfig.baseUrl = 'http://192.168.1.10:8000';
    Uri? seen;
    final client = ApiClient(client: MockClient((req) async {
      seen = req.url;
      return _ok('{}');
    }));
    await client.post('/api/v1/gmail/sync');
    expect(seen!.toString(), 'http://192.168.1.10:8000/api/v1/gmail/sync');
  });

  test('session exchange (/session/exchange) uses the configured backend URL', () async {
    ApiConfig.baseUrl = 'https://amar-api.example.com';
    Uri? seen;
    Object? body;
    final repo = ApiEmailRepository(
      client: ApiClient(client: MockClient((req) async {
        seen = req.url;
        body = req.body;
        return _ok('{"status":"ready","session_token":"tok","user":{"id":1}}');
      })),
    );
    final dto = await repo.exchangeSession('HANDOFF', state: 'st');
    expect(seen!.toString(), 'https://amar-api.example.com/api/v1/auth/session/exchange');
    expect((body as String).contains('HANDOFF'), isTrue);
    expect(dto.sessionToken, 'tok');
  });

  group('OAuth deep-link callback config (Phase 17)', () {
    test('the callback is a custom scheme, not an http(s) localhost URL', () {
      expect(ApiConfig.authCallbackScheme, 'agentamar');
      expect(ApiConfig.authCallbackScheme, isNot(anyOf('http', 'https')));
      expect(ApiConfig.authCallbackHost, 'auth');
      expect(ApiConfig.authCallbackPath, '/callback');
    });

    test('isAuthCallback matches our deep link and rejects everything else', () {
      expect(ApiConfig.isAuthCallback(
          Uri.parse('agentamar://auth/callback?code=x&state=y')), isTrue);
      expect(ApiConfig.isAuthCallback(Uri.parse('agentamar://auth/callback')), isTrue);
      expect(ApiConfig.isAuthCallback(
          Uri.parse('agentamar://other/path')), isFalse);
      expect(ApiConfig.isAuthCallback(
          Uri.parse('http://localhost:8000/api/v1/auth/google/callback?code=x')), isFalse);
      expect(ApiConfig.isAuthCallback(
          Uri.parse('https://accounts.google.com/o/oauth2/auth')), isFalse);
    });
  });
}
