import 'dart:async';

import 'package:flutter/foundation.dart';

import 'email_repository.dart';
import 'notification_service.dart';

/// A pending push the app must navigate to once it is fully initialised
/// (typically a terminated-state launch, before the navigator exists).
class PendingPush {
  final String? emailId;
  final Map<String, dynamic> data;
  const PendingPush(this.emailId, this.data);
}

/// The platform-messaging surface (Firebase Cloud Messaging). Abstracted so the
/// service logic is testable without the Firebase plugins.
abstract class PushPlatform {
  /// Initialise Firebase. Returns false (disabled) if it is not configured on
  /// this build — the app must keep working.
  Future<bool> initialize();
  Future<bool> requestPermission();
  Future<String?> getToken();
  Stream<String> get onTokenRefresh;

  /// Foreground data messages (`{notification_id, email_id, type, ...}`).
  Stream<Map<String, dynamic>> get onForegroundMessage;

  /// The user tapped a notification while the app was backgrounded.
  Stream<Map<String, dynamic>> get onMessageOpenedApp;

  /// The notification that launched the app from a terminated state (or null).
  Future<Map<String, dynamic>?> getInitialMessage();
}

typedef PushNavigate = void Function(String? emailId, Map<String, dynamic> data);

/// Bridges FCM ⇄ backend device registration ⇄ local presentation.
///
/// * registers the device's FCM token with the authenticated backend
/// * re-registers on token refresh
/// * foreground pushes are shown via [NotificationService] (unchanged local layer)
/// * background / terminated taps route to the relevant email once the app is ready
class PushMessagingService {
  final EmailRepository _repository;
  final PushPlatform _platform;
  final NotificationService _localNotifications;
  final void Function(String title, String body, Map<String, dynamic> data)? _onForeground;
  PushNavigate? _navigate;

  bool _available = false;
  bool _started = false;
  String? _token;
  bool _authenticated = false;
  final List<StreamSubscription<dynamic>> _subs = [];

  final ValueNotifier<PendingPush?> pendingPush = ValueNotifier<PendingPush?>(null);

  PushMessagingService({
    required EmailRepository repository,
    required PushPlatform platform,
    NotificationService? localNotifications,
    void Function(String title, String body, Map<String, dynamic> data)? onForeground,
  })  : _repository = repository,
        _platform = platform,
        _localNotifications = localNotifications ?? NotificationService(),
        _onForeground = onForeground;

  bool get isAvailable => _available;
  String? get token => _token;

  /// Wire the navigator callback (available once the widget tree is built).
  void attachNavigator(PushNavigate navigate) {
    _navigate = navigate;
    _flushPending();
  }

  /// Call once at startup. Safe to call when Firebase is not configured.
  Future<void> initialize() async {
    if (_started) return;
    _started = true;
    try {
      _available = await _platform.initialize();
    } catch (e) {
      debugPrint('[Push] Firebase init unavailable: $e');
      _available = false;
    }
    if (!_available) return;

    try {
      await _platform.requestPermission();
      _token = await _platform.getToken();
      await _registerCurrentToken();

      _subs.add(_platform.onTokenRefresh.listen((fresh) async {
        final old = _token;
        _token = fresh;
        await _registerCurrentToken(previousToken: old != fresh ? old : null);
      }));
      _subs.add(_platform.onForegroundMessage.listen(_handleForeground));
      _subs.add(_platform.onMessageOpenedApp.listen((data) => _route(data)));

      final initial = await _platform.getInitialMessage();
      if (initial != null) _route(initial); // terminated-launch → pending until ready
    } catch (e) {
      debugPrint('[Push] setup error: $e');
    }
  }

  /// The user just signed in — (re)register the token we already hold.
  Future<void> onAuthenticated() async {
    _authenticated = true;
    await _registerCurrentToken();
  }

  /// The user signed out — tell the backend to stop pushing to this device.
  Future<void> onLogout() async {
    _authenticated = false;
    final t = _token;
    if (t != null) {
      try {
        await _repository.unregisterDevice(t);
      } catch (_) {}
    }
  }

  Future<void> _registerCurrentToken({String? previousToken}) async {
    final t = _token;
    if (t == null || t.isEmpty || !_authenticated) return;
    try {
      await _repository.registerDevice(
        fcmToken: t,
        platform: defaultTargetPlatform == TargetPlatform.iOS ? 'ios' : 'android',
        previousToken: previousToken,
      );
    } catch (e) {
      debugPrint('[Push] token registration failed: $e');
    }
  }

  void _handleForeground(Map<String, dynamic> data) {
    // Preserve the existing local-notification presentation while the app is open.
    final title = data['title']?.toString() ?? 'Sorted';
    final body = data['body']?.toString() ?? 'You have a new notification.';
    if (_onForeground != null) {
      _onForeground(title, body, data);
    } else {
      _localNotifications.showRawPush(title: title, body: body, data: data);
    }
  }

  void _route(Map<String, dynamic> data) {
    final emailId = (data['email_id'] as String?)?.isNotEmpty == true
        ? data['email_id'] as String
        : null;
    if (_navigate != null) {
      _navigate!(emailId, data);
    } else {
      pendingPush.value = PendingPush(emailId, data);
    }
  }

  void _flushPending() {
    final p = pendingPush.value;
    if (p != null && _navigate != null) {
      _navigate!(p.emailId, p.data);
      pendingPush.value = null;
    }
  }

  void dispose() {
    for (final s in _subs) {
      s.cancel();
    }
    _subs.clear();
    pendingPush.dispose();
  }
}

/// In-memory [PushPlatform] for tests / when Firebase is unavailable.
class FakePushPlatform implements PushPlatform {
  final bool available;
  String? currentToken;
  final _refresh = StreamController<String>.broadcast();
  final _foreground = StreamController<Map<String, dynamic>>.broadcast();
  final _opened = StreamController<Map<String, dynamic>>.broadcast();
  Map<String, dynamic>? initialMessage;

  FakePushPlatform({this.available = true, this.currentToken = 'fake-token'});

  @override
  Future<bool> initialize() async => available;
  @override
  Future<bool> requestPermission() async => true;
  @override
  Future<String?> getToken() async => currentToken;
  @override
  Stream<String> get onTokenRefresh => _refresh.stream;
  @override
  Stream<Map<String, dynamic>> get onForegroundMessage => _foreground.stream;
  @override
  Stream<Map<String, dynamic>> get onMessageOpenedApp => _opened.stream;
  @override
  Future<Map<String, dynamic>?> getInitialMessage() async => initialMessage;

  void emitTokenRefresh(String t) => _refresh.add(t);
  void emitForeground(Map<String, dynamic> d) => _foreground.add(d);
  void emitOpened(Map<String, dynamic> d) => _opened.add(d);
}
