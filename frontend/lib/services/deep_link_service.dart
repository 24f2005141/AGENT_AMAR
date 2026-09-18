import 'dart:async';

import 'package:app_links/app_links.dart';

/// The inbound deep-link surface. Abstracted (like `PushPlatform`) so the auth
/// flow is testable without the platform channel — tests inject [FakeDeepLinkPort].
///
/// Covers all three delivery paths:
///   * app **terminated** → [getInitialLink] (the link that cold-launched us)
///   * app **running / backgrounded** → [linkStream] (delivered via onNewIntent
///     because MainActivity is `singleTop`)
abstract class DeepLinkPort {
  /// The link that launched the app from a terminated state, or null.
  Future<Uri?> getInitialLink();

  /// Links received while the app is already running or backgrounded.
  Stream<Uri> get linkStream;
}

/// Real implementation backed by the `app_links` plugin. Never throws to the
/// caller — a platform hiccup degrades to "no link".
class AppLinksDeepLinkPort implements DeepLinkPort {
  AppLinksDeepLinkPort({AppLinks? appLinks}) : _appLinks = appLinks ?? AppLinks();

  final AppLinks _appLinks;

  @override
  Future<Uri?> getInitialLink() async {
    try {
      return await _appLinks.getInitialLink();
    } catch (_) {
      return null;
    }
  }

  @override
  Stream<Uri> get linkStream =>
      _appLinks.uriLinkStream.handleError((Object _) {});
}

/// Test double: push URIs through [emit]; set [initial] for a cold-start link.
class FakeDeepLinkPort implements DeepLinkPort {
  FakeDeepLinkPort({this.initial});

  Uri? initial;
  final StreamController<Uri> _controller = StreamController<Uri>.broadcast();

  void emit(Uri uri) => _controller.add(uri);

  @override
  Future<Uri?> getInitialLink() async => initial;

  @override
  Stream<Uri> get linkStream => _controller.stream;

  Future<void> dispose() => _controller.close();
}
