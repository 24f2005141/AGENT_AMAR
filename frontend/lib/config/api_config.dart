class ApiConfig {
  /// The backend base URL. Set it at build time with
  /// `--dart-define=API_BASE_URL=...` — this is the ONE place the app decides
  /// where FastAPI lives. Every request (including the Google-login endpoints)
  /// is a relative path joined onto it, so switching between LAN and a public
  /// HTTPS tunnel is a build flag, not a code change:
  ///
  ///   LAN device:  `flutter run --dart-define=API_BASE_URL=http://192.168.1.10:8000`
  ///   Off-LAN:     `flutter run --dart-define=API_BASE_URL=https://your-fastapi-tunnel`
  ///
  /// This is SEPARATE from the Ollama endpoint — the app never talks to Ollama;
  /// FastAPI does, via its own `OLLAMA_BASE_URL`.
  static const String _envBaseUrl =
      String.fromEnvironment('API_BASE_URL', defaultValue: '');

  /// Build environment: `--dart-define=APP_ENV=production` for release
  /// builds. Production applies two hard rules below — no LAN fallback, and
  /// no plaintext HTTP — so a mis-built release can never quietly ship
  /// pointing at a developer's laptop or send bearer tokens in the clear.
  static const String appEnv =
      String.fromEnvironment('APP_ENV', defaultValue: 'development');

  static bool get isProduction => appEnv.toLowerCase() == 'production';

  /// Dev-only fallback used when `--dart-define=API_BASE_URL` is NOT passed.
  /// Never reachable in a production build (see [baseUrl]).
  static const String _devFallbackBaseUrl = 'http://192.168.1.10:8000';

  /// The resolved backend origin.
  ///
  /// Throws in a production build that was not given a valid HTTPS
  /// `API_BASE_URL` — failing at startup is far better than shipping an app
  /// that talks to an unreachable LAN address or over plaintext HTTP.
  static String baseUrl = _resolveBaseUrl();

  static String _resolveBaseUrl() {
    if (isProduction) {
      if (_envBaseUrl.isEmpty) {
        throw StateError(
          'Production build is missing --dart-define=API_BASE_URL=https://…',
        );
      }
      if (!_envBaseUrl.toLowerCase().startsWith('https://')) {
        throw StateError(
          'Production API_BASE_URL must be HTTPS (got a non-HTTPS origin). '
          'Session bearer tokens and email content travel over this link.',
        );
      }
      return _envBaseUrl;
    }
    return _envBaseUrl.isNotEmpty ? _envBaseUrl : _devFallbackBaseUrl;
  }

  /// True when the app is running on the bare dev fallback (no build flag) —
  /// which only works if the backend happens to be at [_devFallbackBaseUrl].
  static bool get usingDevFallback => !isProduction && _envBaseUrl.isEmpty;

  // --- OAuth deep-link callback (Phase 17) ---------------------------------
  //
  // After Google sign-in the backend 302-redirects the system browser to this
  // deep link with `?code=<one-time handoff>&state=<oauth state>` — the app then
  // returns automatically, no manual browser close, no polling. The URL never
  // carries a session or Gmail token. Keep in sync with the backend
  // `APP_AUTH_CALLBACK_URL` and the Android intent-filter in AndroidManifest.xml.
  static const String authCallbackScheme = 'agentamar';
  static const String authCallbackHost = 'auth';
  static const String authCallbackPath = '/callback';

  /// Optional future HTTPS Android App Link origin, e.g.
  /// `--dart-define=AUTH_APP_LINK_ORIGIN=https://app.example.com`. When set,
  /// `https://app.example.com/auth/callback` is accepted **in addition to** the
  /// custom scheme — migrating to App Links becomes config-only.
  static const String authAppLinkOrigin =
      String.fromEnvironment('AUTH_APP_LINK_ORIGIN', defaultValue: '');

  /// Whether [uri] is our OAuth deep-link callback (custom scheme, or the
  /// configured App Link origin). The matcher is intentionally not tied to one
  /// literal string so a future App Link is a drop-in.
  static bool isAuthCallback(Uri uri) {
    if (uri.scheme == authCallbackScheme && uri.host == authCallbackHost) {
      final p = uri.path;
      return p.isEmpty || p == authCallbackPath || p == '$authCallbackPath/';
    }
    if (authAppLinkOrigin.isNotEmpty) {
      final origin = Uri.tryParse(authAppLinkOrigin);
      if (origin != null &&
          uri.scheme == origin.scheme &&
          uri.host == origin.host &&
          uri.port == origin.port) {
        return uri.path == authCallbackPath;
      }
    }
    return false;
  }

  static const Duration connectTimeout = Duration(seconds: 10);
  static const Duration receiveTimeout = Duration(seconds: 20);

  /// Long ceiling for calls that wait on the backend LLM (AI reply drafting).
  /// A small local model on CPU can take well over a minute to draft 3 options,
  /// and the backend gives it up to `LLM_REPLY_TIMEOUT_SECONDS` — the app must
  /// not give up first. Overridable with `--dart-define=AI_TIMEOUT_SECONDS=...`.
  static const Duration aiReceiveTimeout = Duration(
    seconds: int.fromEnvironment('AI_TIMEOUT_SECONDS', defaultValue: 150),
  );

  /// How often the app silently reloads **persisted backend state** (emails /
  /// reminders / notifications) while it is in the foreground. This is a cheap
  /// GET — it does NOT trigger a Gmail sync or any LLM work (the backend
  /// scheduler owns Gmail monitoring). Override at build time with
  /// `--dart-define=FOREGROUND_REFRESH_SECONDS=...`; `0` disables the poll
  /// (resume-only refresh still happens). Default 90s — deliberately not
  /// aggressive.
  static const int _foregroundRefreshSeconds =
      int.fromEnvironment('FOREGROUND_REFRESH_SECONDS', defaultValue: 90);

  static Duration? get foregroundRefreshInterval => _foregroundRefreshSeconds > 0
      ? Duration(seconds: _foregroundRefreshSeconds)
      : null;

  /// Offline demo mode. Force-disabled in production builds so a stray
  /// `useMockData = true` can never ship fake emails to a real user.
  static bool _useMockData = false;
  static bool get useMockData => isProduction ? false : _useMockData;
  static set useMockData(bool value) => _useMockData = value;
}
