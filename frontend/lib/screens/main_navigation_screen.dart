import 'package:flutter/material.dart';
import '../services/local_schedule_service.dart';
import '../state/auth_controller.dart';
import '../state/inbox_controller.dart';
import '../theme/app_theme.dart';
import 'deadlines_screen.dart';
import 'home_inbox_screen.dart';
import 'needs_attention_screen.dart';
import 'reminders_screen.dart';

/// The tab a home-screen widget (or any other external entry point) asked to
/// open. Reuses the EXISTING bottom-nav rather than adding a second
/// navigation architecture — `MainNavigationScreen` listens and switches.
final ValueNotifier<int?> mainNavigationTab = ValueNotifier<int?>(null);

class MainNavigationScreen extends StatefulWidget {
  final InboxController controller;
  final AuthController? authController;

  const MainNavigationScreen({
    super.key,
    required this.controller,
    this.authController,
  });

  @override
  State<MainNavigationScreen> createState() => _MainNavigationScreenState();
}

class _MainNavigationScreenState extends State<MainNavigationScreen>
    with WidgetsBindingObserver {
  int _currentIndex = 0;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    mainNavigationTab.addListener(_onExternalTabRequest);
    _onExternalTabRequest(); // a widget tap may have cold-started the app
  }

  @override
  void dispose() {
    mainNavigationTab.removeListener(_onExternalTabRequest);
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  /// Honour a tab requested from outside the widget tree (home-screen widget
  /// deep link), then clear it so it is applied once.
  void _onExternalTabRequest() {
    final requested = mainNavigationTab.value;
    if (requested == null) return;
    mainNavigationTab.value = null;
    if (requested < 0 || requested > 3 || !mounted) return;
    setState(() => _currentIndex = requested);
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      // Back to the foreground: silently reload persisted backend state (the
      // backend scheduler has been syncing Gmail while we were away — no Gmail
      // sync / LLM is triggered here), re-check the connection status, and
      // resume the lightweight foreground poll.
      widget.controller.autoRefresh();
      widget.controller.refreshSystemStatus();
      widget.controller.startAutoRefresh();
    } else if (state == AppLifecycleState.paused ||
        state == AppLifecycleState.hidden ||
        state == AppLifecycleState.detached) {
      // Don't poll while backgrounded — push notifications cover new mail then.
      widget.controller.stopAutoRefresh();
    }
  }

  void _onTabSelected(int index) {
    setState(() {
      _currentIndex = index;
    });
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      // Two independent sources: InboxController (backend-derived email/
      // notification state) and LocalScheduleService (device-local
      // reminders) — merged so the Reminders badge updates on create/cancel
      // without coupling the two systems together.
      listenable: Listenable.merge([widget.controller, LocalScheduleService()]),
      builder: (context, _) {
        final pendingAttentionCount = widget.controller.needsAttentionEmails.length;
        final pendingRemindersCount = LocalScheduleService().pendingReminders.length;

        final screens = [
          HomeInboxScreen(
            controller: widget.controller,
            authController: widget.authController,
            onNavigateTab: _onTabSelected,
          ),
          NeedsAttentionScreen(
            controller: widget.controller,
          ),
          DeadlinesScreen(
            controller: widget.controller,
          ),
          RemindersScreen(
            controller: widget.controller,
          ),
        ];

        return Scaffold(
          body: IndexedStack(
            index: _currentIndex,
            children: screens,
          ),
          bottomNavigationBar: Container(
            decoration: const BoxDecoration(
              border: Border(top: BorderSide(color: AppColors.border, width: 1)),
            ),
            child: BottomNavigationBar(
              currentIndex: _currentIndex,
              onTap: _onTabSelected,
              items: [
                const BottomNavigationBarItem(
                  icon: Icon(Icons.home_outlined),
                  activeIcon: Icon(Icons.home),
                  label: 'Home',
                ),
                BottomNavigationBarItem(
                  icon: Badge(
                    isLabelVisible: pendingAttentionCount > 0,
                    label: Text('$pendingAttentionCount'),
                    backgroundColor: AppColors.critical,
                    child: const Icon(Icons.task_alt_outlined),
                  ),
                  activeIcon: Badge(
                    isLabelVisible: pendingAttentionCount > 0,
                    label: Text('$pendingAttentionCount'),
                    backgroundColor: AppColors.critical,
                    child: const Icon(Icons.task_alt),
                  ),
                  label: 'Attention',
                ),
                const BottomNavigationBarItem(
                  icon: Icon(Icons.event_outlined),
                  activeIcon: Icon(Icons.event),
                  label: 'Deadlines',
                ),
                BottomNavigationBarItem(
                  icon: Badge(
                    isLabelVisible: pendingRemindersCount > 0,
                    label: Text('$pendingRemindersCount'),
                    backgroundColor: AppColors.warmBeige,
                    textColor: AppColors.textDark,
                    child: const Icon(Icons.notifications_none_outlined),
                  ),
                  activeIcon: Badge(
                    isLabelVisible: pendingRemindersCount > 0,
                    label: Text('$pendingRemindersCount'),
                    backgroundColor: AppColors.warmBeige,
                    textColor: AppColors.textDark,
                    child: const Icon(Icons.notifications),
                  ),
                  label: 'Reminders',
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}
