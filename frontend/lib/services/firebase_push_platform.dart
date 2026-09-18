import 'dart:async';

import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';

import 'push_messaging_service.dart';

/// Must be a top-level function (FCM requirement). Our payloads always carry a
/// `notification` block, so Android/iOS render the system notification for
/// background / terminated messages automatically — this handler only needs to
/// exist. Kept side-effect-free on purpose.
@pragma('vm:entry-point')
Future<void> firebaseMessagingBackgroundHandler(RemoteMessage message) async {
  // no-op: system tray handles display; the tap is picked up by
  // getInitialMessage() / onMessageOpenedApp on next launch.
}

Map<String, dynamic> _flatten(RemoteMessage m) {
  final out = <String, dynamic>{...m.data};
  final n = m.notification;
  if (n != null) {
    out['title'] ??= n.title;
    out['body'] ??= n.body;
  }
  return out;
}

/// Real [PushPlatform] backed by FlutterFire. Degrades to "unavailable" when the
/// build has no Firebase config (no google-services.json) so the app still runs.
class FirebasePushPlatform implements PushPlatform {
  FirebaseMessaging? _messaging;

  @override
  Future<bool> initialize() async {
    try {
      if (Firebase.apps.isEmpty) {
        await Firebase.initializeApp();
      }
      _messaging = FirebaseMessaging.instance;
      FirebaseMessaging.onBackgroundMessage(firebaseMessagingBackgroundHandler);
      return true;
    } catch (e) {
      debugPrint('[Push] Firebase not configured on this build: $e');
      return false;
    }
  }

  @override
  Future<bool> requestPermission() async {
    try {
      final settings = await _messaging!.requestPermission();
      return settings.authorizationStatus == AuthorizationStatus.authorized ||
          settings.authorizationStatus == AuthorizationStatus.provisional;
    } catch (_) {
      return false;
    }
  }

  @override
  Future<String?> getToken() async {
    try {
      return await _messaging!.getToken();
    } catch (_) {
      return null;
    }
  }

  @override
  Stream<String> get onTokenRefresh =>
      _messaging?.onTokenRefresh ?? const Stream<String>.empty();

  @override
  Stream<Map<String, dynamic>> get onForegroundMessage =>
      FirebaseMessaging.onMessage.map(_flatten);

  @override
  Stream<Map<String, dynamic>> get onMessageOpenedApp =>
      FirebaseMessaging.onMessageOpenedApp.map(_flatten);

  @override
  Future<Map<String, dynamic>?> getInitialMessage() async {
    try {
      final m = await _messaging!.getInitialMessage();
      return m == null ? null : _flatten(m);
    } catch (_) {
      return null;
    }
  }
}
