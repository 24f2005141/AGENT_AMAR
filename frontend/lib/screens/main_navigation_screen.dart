import 'package:flutter/material.dart';
import '../state/inbox_controller.dart';
import '../theme/app_theme.dart';
import 'deadlines_screen.dart';
import 'home_inbox_screen.dart';
import 'needs_attention_screen.dart';
import 'reminders_screen.dart';

class MainNavigationScreen extends StatefulWidget {
  final InboxController controller;

  const MainNavigationScreen({
    super.key,
    required this.controller,
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
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      // Sync notifications and data when app comes back to foreground
      widget.controller.loadData();
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
      listenable: widget.controller,
      builder: (context, _) {
        final pendingAttentionCount = widget.controller.needsAttentionEmails.length;
        final pendingRemindersCount = widget.controller.reminders.length;

        final screens = [
          HomeInboxScreen(
            controller: widget.controller,
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
