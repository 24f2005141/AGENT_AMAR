import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Where the application session (bearer) token lives on the device.
///
/// The token is a credential, so the real implementation is Keychain / Keystore
/// backed. Tests inject [InMemoryAuthSessionStore].
abstract class AuthSessionStore {
  Future<String?> read();
  Future<void> write(String token);
  Future<void> clear();
}

class SecureAuthSessionStore implements AuthSessionStore {
  static const _key = 'agent_amar.session_token';
  final FlutterSecureStorage _storage;

  SecureAuthSessionStore({FlutterSecureStorage? storage})
      : _storage = storage ??
            const FlutterSecureStorage(
              aOptions: AndroidOptions(encryptedSharedPreferences: true),
            );

  @override
  Future<String?> read() async {
    try {
      return await _storage.read(key: _key);
    } catch (_) {
      return null;
    }
  }

  @override
  Future<void> write(String token) async {
    try {
      await _storage.write(key: _key, value: token);
    } catch (_) {
      // best-effort; the session still works for this app launch
    }
  }

  @override
  Future<void> clear() async {
    try {
      await _storage.delete(key: _key);
    } catch (_) {}
  }
}

class InMemoryAuthSessionStore implements AuthSessionStore {
  String? _token;

  InMemoryAuthSessionStore([this._token]);

  @override
  Future<String?> read() async => _token;

  @override
  Future<void> write(String token) async => _token = token;

  @override
  Future<void> clear() async => _token = null;
}
