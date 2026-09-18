import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:url_launcher/url_launcher.dart';

import '../config/api_config.dart';
import '../dto/app_user_dto.dart';
import '../services/api_error.dart';
import '../services/auth_session_store.dart';
import '../services/deep_link_service.dart';
import '../services/email_repository.dart';

enum AuthState { unknown, unauthenticated, authenticating, authenticated }

/// Owns the application session (Phase 15 / Phase 17):
///
///   "Continue with Google"  →  secure system browser / Custom Tab
///                           →  Google consent
///                           →  FastAPI callback completes OAuth
///                           →  302 back to `agentamar://auth/callback?code=…`
///                           →  this controller redeems the one-time code for
///                              the opaque application session (bearer)
///
/// There is **no polling** in the mobile flow — the deep link returns the user
/// automatically. The bearer token lives only in [AuthSessionStore]
/// (Keychain / Keystore); the Google client secret and Gmail tokens never reach
/// the app.
class AuthController extends ChangeNotifier {
  final EmailRepository _repository;
  final AuthSessionStore _store;
  final DeepLinkPort? _deepLinks;
  final Future<bool> Function(Uri url) _launch;
  final Future<void> Function() _closeBrowser;
  final Duration _oauthTimeout;

  /// Runs while the session is still valid, just before logout revokes it
  /// (Phase 16: used to unregister this device's push token).
  Future<void> Function()? onBeforeLogout;
  /// Runs right after a successful sign-in (Phase 16: (re)register push token).
  Future<void> Function()? onAuthenticated;

  AuthState _state = AuthState.unknown;
  String? _sessionToken;
  AppUserDto? _currentUser;
  bool _gmailConnected = false;
  String? _errorMessage;

  StreamSubscription<Uri>? _linkSub;
  Completer<void>? _pendingOAuth;
  bool _disposed = false;

  AuthController({
    required EmailRepository repository,
    required AuthSessionStore store,
    DeepLinkPort? deepLinks,
    Future<bool> Function(Uri url)? urlLauncher,
    Future<void> Function()? closeBrowser,
    Duration oauthTimeout = const Duration(minutes: 3),
  })  : _repository = repository,
        _store = store,
        _deepLinks = deepLinks,
        _launch = urlLauncher ?? _defaultLaunch,
        _closeBrowser = closeBrowser ?? _defaultCloseBrowser,
        _oauthTimeout = oauthTimeout;

  AuthState get state => _state;
  String? get sessionToken => _sessionToken;
  AppUserDto? get currentUser => _currentUser;
  bool get gmailConnected => _gmailConnected;
  String? get errorMessage => _errorMessage;
  bool get isAuthenticated => _state == AuthState.authenticated;

  // -- default platform hooks (Custom Tab; fall back to the full browser) -----

  static Future<bool> _defaultLaunch(Uri uri) async {
    try {
      // Custom Tab on Android / SFSafariViewController on iOS — a *secure system
      // browser*, never an in-app WebView. Shares the browser's session, and we
      // dismiss it ourselves once the deep link returns.
      return await launchUrl(uri, mode: LaunchMode.inAppBrowserView);
    } catch (_) {
      return await launchUrl(uri, mode: LaunchMode.externalApplication);
    }
  }

  static Future<void> _defaultCloseBrowser() async {
    try {
      await closeInAppWebView();
    } catch (_) {}
  }

  /// Called once at startup: restore a stored session, subscribe to deep links,
  /// and handle a link that cold-launched the app.
  Future<void> bootstrap() async {
    _subscribeToDeepLinks();

    final stored = await _store.read();
    if (stored != null && stored.isNotEmpty) {
      _sessionToken = stored;
      try {
        final me = await _repository.getCurrentUser();
        _currentUser = me.user;
        _gmailConnected = me.gmailConnected;
        _set(state: AuthState.authenticated);
        await _fireAuthenticated();
        return;
      } on ApiException catch (e) {
        if (e.isAuthExpired) {
          await _clearSession();
          _set(state: AuthState.unauthenticated);
        } else {
          _set(state: AuthState.unauthenticated, error: 'Could not reach Sorted.');
        }
      } catch (_) {
        _set(state: AuthState.unauthenticated, error: 'Could not reach Sorted.');
      }
    } else {
      _set(state: AuthState.unauthenticated);
    }

    // A deep link may have launched the app from a terminated state (e.g. the
    // user finished Google sign-in while the app was killed).
    final initial = await _deepLinks?.getInitialLink();
    if (initial != null && ApiConfig.isAuthCallback(initial)) {
      await _completeFromCallback(initial);
    }
  }

  void _subscribeToDeepLinks() {
    if (_deepLinks == null || _linkSub != null) return;
    _linkSub = _deepLinks.linkStream.listen((uri) {
      if (ApiConfig.isAuthCallback(uri)) {
        // running / backgrounded: complete the sign-in in progress
        _completeFromCallback(uri);
      }
    });
  }

  /// "Continue with Google": open the consent screen in a secure browser and
  /// wait for the deep-link callback to bring the user back.
  Future<void> startGoogleLogin() => _runOAuth();

  /// Re-authorise Gmail (token expired / revoked). Same OAuth grant.
  Future<void> reconnectGmail() => _runOAuth();

  Future<void> _runOAuth() async {
    _subscribeToDeepLinks();
    _set(state: AuthState.authenticating, error: null);

    final completer = Completer<void>();
    _pendingOAuth = completer;

    try {
      final start = await _repository.startGoogleAuth();
      final launched = await _launch(Uri.parse(start.authorizationUrl));
      if (!launched) {
        _finishOAuth(completer);
        _fail('Could not open a browser for Google sign-in.');
        return;
      }
    } on ApiException catch (e) {
      _finishOAuth(completer);
      _fail(e.message);
      return;
    } catch (e) {
      _finishOAuth(completer);
      _fail('Sign-in failed: $e');
      return;
    }

    try {
      await completer.future.timeout(_oauthTimeout);
    } on TimeoutException {
      _finishOAuth(completer);
      if (_state == AuthState.authenticating) {
        _fail('Sign-in was cancelled or timed out. Please try again.');
      }
    }
  }

  /// Handle an inbound `agentamar://auth/callback…` deep link. Public so a host
  /// (main.dart) can forward links it receives before this controller subscribes.
  Future<void> handleAuthCallback(Uri uri) async {
    if (!ApiConfig.isAuthCallback(uri)) return;
    await _completeFromCallback(uri);
  }

  bool _exchangeInFlight = false;

  Future<void> _completeFromCallback(Uri uri) async {
    // A duplicate intent (the OS can redeliver) must not double-exchange.
    if (_exchangeInFlight) return;
    final completer = _pendingOAuth;

    final error = uri.queryParameters['error'];
    if (error != null && error.isNotEmpty) {
      await _closeBrowser();
      _fail(_friendlyCallbackError(error));
      _finishOAuth(completer);
      return;
    }

    final code = uri.queryParameters['code'];
    if (code == null || code.isEmpty) {
      await _closeBrowser();
      _fail('The sign-in link was invalid. Please try again.');
      _finishOAuth(completer);
      return;
    }

    _exchangeInFlight = true;
    _set(state: AuthState.authenticating, error: null);
    try {
      final session = await _repository.exchangeSession(
        code,
        state: uri.queryParameters['state'],
      );
      _sessionToken = session.sessionToken;
      await _store.write(session.sessionToken);
      _currentUser = session.user;
      await _refreshMeQuietly();
      await _closeBrowser();
      _set(state: AuthState.authenticated, error: null);
      await _fireAuthenticated();
    } on ApiException catch (e) {
      await _closeBrowser();
      _fail(_friendlyExchangeError(e));
    } catch (_) {
      await _closeBrowser();
      _fail('Could not complete sign-in. Please try again.');
    } finally {
      _exchangeInFlight = false;
      _finishOAuth(completer);
    }
  }

  void _finishOAuth(Completer<void>? completer) {
    if (completer != null && !completer.isCompleted) completer.complete();
    if (identical(_pendingOAuth, completer)) _pendingOAuth = null;
  }

  String _friendlyCallbackError(String code) {
    switch (code) {
      case 'access_denied':
        return 'Google sign-in was cancelled.';
      case 'expired_state':
        return 'The sign-in link expired. Please try again.';
      default:
        return 'Google sign-in did not complete. Please try again.';
    }
  }

  String _friendlyExchangeError(ApiException e) {
    if (e.statusCode == 400 || e.statusCode == 410) {
      return 'That sign-in link expired or was already used. Please try again.';
    }
    if (e.isNetworkError) {
      return 'Cannot reach Sorted. Check your connection and try again.';
    }
    return e.message;
  }

  Future<void> refreshMe() async {
    if (_sessionToken == null) return;
    try {
      final me = await _repository.getCurrentUser();
      _currentUser = me.user;
      _gmailConnected = me.gmailConnected;
      notifyListeners();
    } on ApiException catch (e) {
      if (e.isAuthExpired) {
        await onSessionExpired();
      }
    } catch (_) {}
  }

  Future<void> _refreshMeQuietly() async {
    try {
      final me = await _repository.getCurrentUser();
      _currentUser = me.user;
      _gmailConnected = me.gmailConnected;
    } catch (_) {}
  }

  Future<void> _fireAuthenticated() async {
    try {
      await onAuthenticated?.call();
    } catch (_) {}
  }

  Future<void> logout() async {
    try {
      await onBeforeLogout?.call();
    } catch (_) {}
    try {
      await _repository.logout(fcmToken: null);
    } catch (_) {}
    await _clearSession();
    _set(state: AuthState.unauthenticated, error: null);
  }

  /// A protected request came back "session expired" — drop back to Login.
  /// Idempotent: a burst of concurrent 401s only clears state once.
  Future<void> onSessionExpired() async {
    if (_state == AuthState.unauthenticated && _sessionToken == null) return;
    await _clearSession();
    _set(state: AuthState.unauthenticated);
  }

  Future<void> _clearSession() async {
    _sessionToken = null;
    _currentUser = null;
    _gmailConnected = false;
    await _store.clear();
  }

  void _fail(String message) => _set(state: AuthState.unauthenticated, error: message);

  void _set({required AuthState state, Object? error = _sentinel}) {
    if (_disposed) return;
    _state = state;
    if (!identical(error, _sentinel)) _errorMessage = error as String?;
    notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _linkSub?.cancel();
    super.dispose();
  }
}

const Object _sentinel = Object();
