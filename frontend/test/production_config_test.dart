// Production build configuration guards. These encode deployment rules that
// are otherwise only discoverable by shipping a broken release:
//   * a production build must be pointed at an HTTPS API,
//   * it must never fall back to a developer LAN address,
//   * it must never serve mock data.
//
// The dart-defines are compile-time, so a test binary runs in the default
// (development) environment; what is asserted here is the resolution logic
// and the invariants that hold in every build.
import 'package:flutter_test/flutter_test.dart';
import 'package:agent_amar/config/api_config.dart';

void main() {
  group('ApiConfig environment', () {
    test('development is the default and keeps the LAN fallback usable', () {
      expect(ApiConfig.isProduction, isFalse);
      expect(ApiConfig.baseUrl, isNotEmpty);
    });

    test('mock data can be toggled in development', () {
      final original = ApiConfig.useMockData;
      ApiConfig.useMockData = true;
      expect(ApiConfig.useMockData, isTrue);
      ApiConfig.useMockData = original;
    });

    test('the OAuth deep-link matcher accepts only our callback', () {
      expect(
        ApiConfig.isAuthCallback(Uri.parse('agentamar://auth/callback?code=x')),
        isTrue,
      );
      // A widget deep link is NOT an auth callback — the two must not be
      // confused, or a widget tap could be treated as a sign-in handoff.
      expect(
        ApiConfig.isAuthCallback(Uri.parse('agentamar://widget/tab?name=inbox')),
        isFalse,
      );
      expect(
        ApiConfig.isAuthCallback(Uri.parse('https://evil.example.com/callback')),
        isFalse,
      );
    });

    test('the configured API origin is never a plaintext public host', () {
      // Loopback/LAN over HTTP is fine in development; a public HTTP origin
      // never is, in any environment.
      final uri = Uri.parse(ApiConfig.baseUrl);
      if (uri.scheme == 'http') {
        final host = uri.host;
        final isPrivate = host == 'localhost' ||
            host == '127.0.0.1' ||
            host == '10.0.2.2' ||
            host.startsWith('192.168.') ||
            host.startsWith('10.') ||
            host.startsWith('172.');
        expect(isPrivate, isTrue,
            reason: 'plaintext HTTP is only acceptable to a private address');
      }
    });
  });
}
