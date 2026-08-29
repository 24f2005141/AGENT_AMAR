import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'screens/email_detail_screen.dart';
import 'screens/main_navigation_screen.dart';
import 'services/notification_service.dart';
import 'state/inbox_controller.dart';
import 'theme/app_theme.dart';

final GlobalKey<NavigatorState> appNavigatorKey = GlobalKey<NavigatorState>();

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
  final InboxController? controller;
  final GlobalKey<NavigatorState>? navigatorKey;
  final bool enableNotifications;

  const AgentAmarApp({
    super.key,
    this.controller,
    this.navigatorKey,
    this.enableNotifications = true,
  });

  @override
  State<AgentAmarApp> createState() => _AgentAmarAppState();
}

class _AgentAmarAppState extends State<AgentAmarApp> {
  late final InboxController _inboxController;
  late final bool _ownsController;

  @override
  void initState() {
    super.initState();
    if (widget.controller != null) {
      _inboxController = widget.controller!;
      _ownsController = false;
    } else {
      _inboxController = InboxController();
      _ownsController = true;
    }

    if (widget.enableNotifications) {
      _setupNotifications();
    }
  }

  Future<void> _setupNotifications() async {
    final notifService = NotificationService();
    await notifService.initialize(
      onNotificationTap: _handleNotificationTap,
    );

    // Request permissions appropriately without blocking
    await notifService.requestPermissions();

    // Check if launched from notification while terminated
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

  Future<void> _handleNotificationTap(String? emailId, Map<String, dynamic> payload) async {
    if (emailId == null || emailId.isEmpty) return;

    try {
      final email = await _inboxController.getEmailForNavigation(emailId);
      if (email != null) {
        final navState = widget.navigatorKey?.currentState ?? appNavigatorKey.currentState;
        if (navState != null) {
          _inboxController.markViewed(email.id);
          navState.push(
            MaterialPageRoute(
              builder: (_) => EmailDetailScreen(
                email: email,
                controller: _inboxController,
              ),
            ),
          );
        }
      }
    } catch (e) {
      debugPrint('[AgentAmarApp] Error navigating on notification tap: $e');
    }
  }

  @override
  void dispose() {
    if (_ownsController) {
      _inboxController.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      navigatorKey: widget.navigatorKey ?? appNavigatorKey,
      title: 'AGENT AMAR',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.darkTheme,
      home: MainNavigationScreen(controller: _inboxController),
    );
  }
}
