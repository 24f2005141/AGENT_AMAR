import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:home_widget/home_widget.dart';
import 'screens/email_detail_screen.dart';
import 'screens/login_screen.dart';
import 'screens/main_navigation_screen.dart';
import 'screens/splash_screen.dart';
import 'services/api_client.dart';
import 'services/auth_session_store.dart';
import 'services/deep_link_service.dart';
import 'services/email_repository.dart';
import 'models/widget_snapshot.dart';
import 'services/firebase_push_platform.dart';
import 'services/home_widget_service.dart';
import 'services/local_schedule_service.dart';
import 'services/notification_service.dart';
import 'services/push_messaging_service.dart';
import 'services/widget_deep_links.dart';
import 'state/auth_controller.dart';
import 'state/inbox_controller.dart';
import 'theme/app_theme.dart';

final GlobalKey<NavigatorState> appNavigatorKey = GlobalKey<NavigatorState>();

/// Runs in a BACKGROUND isolate when a home-screen widget button is tapped
/// (currently only Focus Now's "Done"). It must stay tiny and offline: the
/// tap is recorded locally and the widgets are re-rendered optimistically —
/// the backend, which owns completion state, is updated by the app itself on
/// its next foreground (`InboxController.flushPendingWidgetCompletions`).
@pragma('vm:entry-point')
Future<void> widgetBackgroundCallback(Uri? uri) async {
  if (uri == null) return;
  final route = WidgetRoute.parse(uri);
  if (route.kind != WidgetRouteKind.done || route.emailId == null) return;
  final widgets = HomeWidgetService();
  await widgets.queuePendingCompletion(route.emailId!);
  // Clear the item from the widget straight away so the tap feels immediate.
  final cached = await widgets.readCachedSnapshot();
  if (cached != null && cached.focus?.emailId == route.emailId) {
    await widgets.publishSnapshot(WidgetSnapshot(
      actionCount: cached.actionCount > 0 ? cached.actionCount - 1 : 0,
      replyCount: cached.replyCount,
      deadlineCount: cached.deadlineCount,
      nextDeadline: cached.nextDeadline,
      generatedAt: DateTime.now().toUtc(),
    ));
  }
}

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  SystemChrome.setSystemUIOverlayStyle(
    const SystemUiOverlayStyle(
      statusBarColor: Colors.transparent,
      statusBarIconBrightness: Brightness.light,
      systemNavigationBarColor: Color(0xFF142838),
      systemNavigationBarIconBrightness: Brightness.light,
    ),
  );

  runApp(const AgentAmarApp());
}

class AgentAmarApp extends StatefulWidget {
  final AuthController? authController;
  final InboxController? controller;
  final PushMessagingService? pushService;
  final DeepLinkPort? deepLinkPort;
  final GlobalKey<NavigatorState>? navigatorKey;
  final bool enableNotifications;
  final bool enablePush;

  const AgentAmarApp({
    super.key,
    this.authController,
    this.controller,
    this.pushService,
    this.deepLinkPort,
    this.navigatorKey,
    this.enableNotifications = true,
    this.enablePush = true,
  });

  @override
  State<AgentAmarApp> createState() => _AgentAmarAppState();
}

class _AgentAmarAppState extends State<AgentAmarApp> {
  late final AuthController _authController;
  late final EmailRepository _repository;
  late final bool _ownsAuthController;
  InboxController? _injectedController;
  PushMessagingService? _pushService;
  bool _ownsPushService = false;
  DeepLinkPort? _deepLinks;
  StreamSubscription<Uri>? _widgetLinkSub;

  @override
  void initState() {
    super.initState();
    if (widget.authController != null) {
      _authController = widget.authController!;
      _ownsAuthController = false;
      _repository = _resolveRepository();
    } else {
      final apiClient = ApiClient(
        tokenProvider: () => _authController.sessionToken,
        // Any protected request that comes back "session expired" clears the
        // token + auth state and drops to Login — once, no matter how many
        // requests fail at the same time.
        onUnauthorized: () => _authController.onSessionExpired(),
      );
      _repository = ApiEmailRepository(client: apiClient);
      _deepLinks = widget.deepLinkPort ?? AppLinksDeepLinkPort();
      _authController = AuthController(
        repository: _repository,
        store: SecureAuthSessionStore(),
        // Deep-link OAuth handoff (Phase 17): the backend 302s the browser to
        // `agentamar://auth/callback?code=…` and the app returns automatically.
        deepLinks: _deepLinks,
      );
      _ownsAuthController = true;
      _authController.bootstrap();
    }
    _injectedController = widget.controller;
    _subscribeToWidgetLinks();

    _pushService = widget.pushService;
    if (_pushService == null && widget.enablePush) {
      _pushService = PushMessagingService(
        repository: _repository,
        platform: FirebasePushPlatform(),
      );
      _ownsPushService = true;
    }
    if (_pushService != null) {
      _authController.onAuthenticated = _pushService!.onAuthenticated;
      _authController.onBeforeLogout = _pushService!.onLogout;
      _pushService!.attachNavigator(_handleNotificationTap);
      _pushService!.initialize();
    }

    if (widget.enableNotifications) {
      _setupNotifications();
    }
  }

  EmailRepository _resolveRepository() {
    // In tests an InboxController is usually injected with its own repository;
    // fall back to a plain API repository otherwise.
    return ApiEmailRepository(
      client: ApiClient(
        tokenProvider: () => _authController.sessionToken,
        onUnauthorized: () => _authController.onSessionExpired(),
      ),
    );
  }

  Future<void> _setupNotifications() async {
    final notifService = NotificationService();
    await notifService.initialize(onNotificationTap: _handleNotificationTap);
    await notifService.requestPermissions();
    // Best-effort — reminders still fire (inexact-but-timely) if declined;
    // see NotificationService.requestExactAlarmPermission.
    unawaited(notifService.requestExactAlarmPermission());

    // Resolve the device timezone once here, so scheduling calls later never
    // pay for a platform round-trip (see NotificationService).
    await notifService.initializeTimeZone();

    // Home-screen widgets: register the background handler for the Focus Now
    // "Done" button and make sure the static Quick Actions widget is bound.
    unawaited(_setupHomeWidgets());

    // Device-local schedule (reminders + deadline alarms): loaded here, once,
    // at startup — never from InboxController/loadData — to reconcile what is
    // persisted with what the OS has scheduled. Depends on neither the
    // backend nor any Gmail/email fetch.
    unawaited(LocalScheduleService().load());

    final launchPayload = await notifService.checkLaunchNotification();
    if (launchPayload != null) {
      final emailId = launchPayload['email_id'] as String?;
      if (emailId != null && emailId.isNotEmpty) {
        WidgetsBinding.instance.addPostFrameCallback((_) {
          _handleNotificationTap(emailId, launchPayload);
        });
      }
    }
  }

  Future<void> _setupHomeWidgets() async {
    try {
      await HomeWidget.registerInteractivityCallback(widgetBackgroundCallback);
      await HomeWidgetService().refreshQuickActions();
    } catch (e) {
      // No widgets on the home screen / unsupported platform — harmless.
      debugPrint('[AgentAmarApp] home widget setup skipped: $e');
    }
  }

  /// Home-screen widget taps arrive on the same deep-link surface as the auth
  /// callback (`agentamar://`), routed on host. AuthController ignores
  /// anything that is not its callback, so the two coexist on one stream.
  void _subscribeToWidgetLinks() {
    final links = _deepLinks;
    if (links == null || _widgetLinkSub != null) return;
    _widgetLinkSub = links.linkStream.listen(_handleWidgetLink);
    // A tap can also cold-start the app.
    links.getInitialLink().then((uri) {
      if (uri != null) _handleWidgetLink(uri);
    });
  }

  Future<void> _handleWidgetLink(Uri uri) async {
    final route = WidgetRoute.parse(uri);
    if (!route.isKnown) return;
    switch (route.kind) {
      case WidgetRouteKind.email:
        await _handleNotificationTap(route.emailId, const {});
        break;
      case WidgetRouteKind.tab:
        final tab = route.tab;
        if (tab == null) return;
        final inbox = _injectedController;
        if (inbox != null && tab.inboxFilter != null) {
          inbox.setFilter(tab.inboxFilter!);
        }
        mainNavigationTab.value = tab.tabIndex;
        break;
      case WidgetRouteKind.done:
        // Completion queued by the widget while the app was closed; the app
        // (not the widget) performs it against the authoritative backend.
        await _injectedController?.flushPendingWidgetCompletions();
        break;
      case WidgetRouteKind.unknown:
        break;
    }
  }

  Future<void> _handleNotificationTap(String? emailId, Map<String, dynamic> payload) async {
    if (emailId == null || emailId.isEmpty) return;
    final inbox = _injectedController;
    if (inbox == null) {
      // App still initialising / user not authenticated yet — hold the payload
      // and replay it once the inbox is ready (terminated-launch flow).
      _pushService?.pendingPush.value = PendingPush(emailId, payload);
      return;
    }
    try {
      final email = await inbox.getEmailForNavigation(emailId);
      if (email != null) {
        final navState = widget.navigatorKey?.currentState ?? appNavigatorKey.currentState;
        if (navState != null) {
          inbox.markViewed(email.id);
          navState.push(MaterialPageRoute(
            builder: (_) => EmailDetailScreen(email: email, controller: inbox),
          ));
        }
      }
    } catch (e) {
      debugPrint('[AgentAmarApp] Error navigating on notification tap: $e');
    }
  }

  @override
  void dispose() {
    if (_ownsAuthController) _authController.dispose();
    if (_ownsPushService) _pushService?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      navigatorKey: widget.navigatorKey ?? appNavigatorKey,
      title: 'Sorted',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.darkTheme,
      home: AuthGate(
        authController: _authController,
        repository: _repository,
        injectedInboxController: _injectedController,
        onInboxControllerCreated: (c) {
          _injectedController = c;
          // inbox is ready — replay any push that arrived during startup
          _pushService?.attachNavigator(_handleNotificationTap);
        },
      ),
    );
  }
}

/// Decides between the login screen and the main app based on the session state.
class AuthGate extends StatefulWidget {
  final AuthController authController;
  final EmailRepository repository;
  final InboxController? injectedInboxController;
  final ValueChanged<InboxController>? onInboxControllerCreated;

  const AuthGate({
    super.key,
    required this.authController,
    required this.repository,
    this.injectedInboxController,
    this.onInboxControllerCreated,
  });

  @override
  State<AuthGate> createState() => _AuthGateState();
}

class _AuthGateState extends State<AuthGate> {
  InboxController? _inbox;
  bool _ownsInbox = false;

  @override
  void initState() {
    super.initState();
    _inbox = widget.injectedInboxController;
    widget.authController.addListener(_onAuth);
    _onAuth();
  }

  void _onAuth() {
    final authed = widget.authController.isAuthenticated;
    if (authed && _inbox == null) {
      // 401 handling is centralised in ApiClient.onUnauthorized (wired to
      // authController.onSessionExpired), so InboxController needs no callback.
      _inbox = InboxController(repository: widget.repository);
      _ownsInbox = true;
      widget.onInboxControllerCreated?.call(_inbox!);
    } else if (!authed && _ownsInbox) {
      _inbox?.dispose();
      _inbox = null;
      _ownsInbox = false;
    }
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    widget.authController.removeListener(_onAuth);
    if (_ownsInbox) _inbox?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: widget.authController,
      builder: (context, _) {
        return AnimatedSwitcher(
          // A short, subtle crossfade between splash → login/home — no
          // artificial delay, this only smooths what is already a state change.
          duration: const Duration(milliseconds: 250),
          child: _buildForState(widget.authController.state),
        );
      },
    );
  }

  Widget _buildForState(AuthState state) {
    switch (state) {
      case AuthState.unknown:
        // The stored session is still being validated — the ONLY normal
        // in-app screen that shows the full Sorted mark.
        return const SplashScreen(key: ValueKey('splash'));
      case AuthState.authenticated:
        final inbox = _inbox;
        if (inbox == null) {
          return const SplashScreen(key: ValueKey('splash'));
        }
        return MainNavigationScreen(
          key: const ValueKey('main'),
          controller: inbox,
          authController: widget.authController,
        );
      case AuthState.unauthenticated:
      case AuthState.authenticating:
        return LoginScreen(
          key: const ValueKey('login'),
          authController: widget.authController,
        );
    }
  }
}
