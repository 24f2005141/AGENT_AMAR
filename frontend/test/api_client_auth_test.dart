import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:agent_amar/config/api_config.dart';
import 'package:agent_amar/services/api_client.dart';
import 'package:agent_amar/services/api_error.dart';

http.Response _json(int status, Map<String, dynamic> body) =>
    http.Response(_encode(body), status, headers: {'content-type': 'application/json'});

String _encode(Map<String, dynamic> m) =>
    '{${m.entries.map((e) => '"${e.key}":${e.value is String ? '"${e.value}"' : e.value}').join(',')}}';

void main() {
  test('401 AuthRequiredError triggers onUnauthorized exactly once', () async {
    var calls = 0;
    final client = ApiClient(
      client: MockClient((_) async => _json(401, {'error': 'AuthRequiredError', 'detail': 'x'})),
      tokenProvider: () => 'stale',
      onUnauthorized: () => calls++,
    );

    for (var i = 0; i < 4; i++) {
      await expectLater(client.get('/api/v1/auth/me'), throwsA(isA<ApiException>()));
    }
    expect(calls, 1, reason: 'debounced until the next successful response');
  });

  test('a burst of concurrent 401s still only logs out once', () async {
    var calls = 0;
    final client = ApiClient(
      client: MockClient((_) async => _json(401, {'error': 'AuthRequiredError'})),
      tokenProvider: () => 'stale',
      onUnauthorized: () => calls++,
    );

    await Future.wait([
      client.get('/api/v1/emails').catchError((_) => null),
      client.post('/api/v1/gmail/sync').catchError((_) => null),
      client.get('/api/v1/auth/google/status').catchError((_) => null),
      client.get('/api/v1/notifications').catchError((_) => null),
    ]);
    expect(calls, 1);
  });

  test('GmailNotConnectedError (also 401) does NOT trigger onUnauthorized', () async {
    var calls = 0;
    final client = ApiClient(
      client: MockClient((_) async => _json(401, {'error': 'GmailNotConnectedError'})),
      tokenProvider: () => 'valid',
      onUnauthorized: () => calls++,
    );
    await expectLater(client.post('/api/v1/gmail/sync'), throwsA(
      predicate<ApiException>((e) => e.isGmailNotConnected && !e.isAuthExpired),
    ));
    expect(calls, 0);
  });

  test('a 5xx does not trigger onUnauthorized', () async {
    var calls = 0;
    final client = ApiClient(
      client: MockClient((_) async => _json(502, {'detail': 'upstream'})),
      tokenProvider: () => 'valid',
      onUnauthorized: () => calls++,
    );
    await expectLater(client.get('/api/v1/emails'), throwsA(isA<ApiException>()));
    expect(calls, 0);
  });

  test('a successful response re-arms the debounce', () async {
    var calls = 0;
    var mode = '401';
    final client = ApiClient(
      client: MockClient((_) async =>
          mode == '401' ? _json(401, {'error': 'AuthRequiredError'}) : _json(200, {'ok': true})),
      tokenProvider: () => 't',
      onUnauthorized: () => calls++,
    );

    await client.get('/x').catchError((_) => null); // 401 -> fire (1)
    await client.get('/x').catchError((_) => null); // 401 -> debounced
    expect(calls, 1);

    mode = '200';
    await client.get('/x'); // success -> re-arm

    mode = '401';
    await client.get('/x').catchError((_) => null); // 401 -> fire again (2)
    expect(calls, 2);
  });

  test('no Authorization header when the token is null or empty', () async {
    final seen = <String?>[];
    final client = ApiClient(
      client: MockClient((req) async {
        seen.add(req.headers['Authorization'] ?? req.headers['authorization']);
        return _json(200, {'ok': true});
      }),
      tokenProvider: () => null,
    );
    await client.get('/x');

    final client2 = ApiClient(
      client: MockClient((req) async {
        seen.add(req.headers['Authorization'] ?? req.headers['authorization']);
        return _json(200, {'ok': true});
      }),
      tokenProvider: () => '',
    );
    await client2.get('/x');

    expect(seen, [null, null]); // never "Bearer null" / "Bearer "
  });

  test('Authorization: Bearer <token> is sent when a token is present', () async {
    String? seen;
    final client = ApiClient(
      client: MockClient((req) async {
        seen = req.headers['Authorization'] ?? req.headers['authorization'];
        return _json(200, {'ok': true});
      }),
      tokenProvider: () => 'abc.def',
    );
    await client.get('/x');
    expect(seen, 'Bearer abc.def');
  });

  test('stale token is dropped immediately after the provider returns null', () async {
    String? token = 'old';
    final seen = <String?>[];
    final client = ApiClient(
      client: MockClient((req) async {
        seen.add(req.headers['Authorization'] ?? req.headers['authorization']);
        return _json(200, {'ok': true});
      }),
      tokenProvider: () => token,
    );
    await client.get('/x'); // Bearer old
    token = null; // logout / session cleared
    await client.get('/x'); // no header
    expect(seen, ['Bearer old', null]);
  });

  setUpAll(() {
    ApiConfig.baseUrl = 'http://test.local:8000';
  });
}
