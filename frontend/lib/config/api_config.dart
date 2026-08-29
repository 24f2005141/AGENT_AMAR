class ApiConfig {
  static const String _envBaseUrl = String.fromEnvironment('API_BASE_URL', defaultValue: '');

  /// Default API base URL pointing to the Windows host PC running FastAPI on port 8000.
  /// Overridable at runtime or via `--dart-define=API_BASE_URL=...`.
  static String get _initialBaseUrl {
    if (_envBaseUrl.isNotEmpty) {
      return _envBaseUrl;
    }
    return 'http://192.168.1.10:8000';
  }

  static String baseUrl = _initialBaseUrl;

  static const Duration connectTimeout = Duration(seconds: 10);
  static const Duration receiveTimeout = Duration(seconds: 20);

  // Set to true only if working offline without running FastAPI backend
  static bool useMockData = false;
}
