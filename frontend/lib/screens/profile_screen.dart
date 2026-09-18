import 'package:flutter/material.dart';

import '../state/auth_controller.dart';
import '../state/inbox_controller.dart';
import '../theme/app_theme.dart';

/// Account / settings screen (Phase 15). Shows the connected Google identity and
/// the logout / reconnect / disconnect actions — deliberately kept off the inbox.
class ProfileScreen extends StatelessWidget {
  final AuthController authController;
  final InboxController inboxController;

  const ProfileScreen({
    super.key,
    required this.authController,
    required this.inboxController,
  });

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Account')),
      body: ListenableBuilder(
        listenable: authController,
        builder: (context, _) {
          final user = authController.currentUser;
          final gmailOk = authController.gmailConnected;
          return ListView(
            padding: const EdgeInsets.all(20),
            children: [
              Row(
                children: [
                  CircleAvatar(
                    radius: 26,
                    backgroundColor: AppColors.surfaceElevated,
                    child: const Icon(Icons.person, color: AppColors.warmBeige),
                  ),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(user?.displayName ?? 'Signed in',
                            style: AppTheme.heading(fontSize: 16)),
                        const SizedBox(height: 2),
                        Text(user?.googleEmail ?? '—',
                            style: AppTheme.body(fontSize: 12, color: AppColors.textSecondary)),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 24),
              Container(
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                  color: AppColors.surfaceCard,
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: AppColors.border),
                ),
                child: Row(
                  children: [
                    Icon(gmailOk ? Icons.check_circle : Icons.error_outline,
                        color: gmailOk ? AppColors.success : AppColors.high, size: 20),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        gmailOk
                            ? 'Gmail connected — AMAR is monitoring new mail.'
                            : 'Gmail authorization expired. Reconnect to resume monitoring.',
                        style: AppTheme.body(fontSize: 12),
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 20),
              if (!gmailOk)
                _action(context, Icons.link, 'Reconnect Gmail', AppColors.warmBeige, () async {
                  await authController.reconnectGmail();
                  await inboxController.checkGmailStatus();
                }),
              _action(context, Icons.link_off, 'Disconnect Gmail', AppColors.textSecondary,
                  () async {
                await inboxController.disconnectGmail();
                await authController.refreshMe();
              }),
              _action(context, Icons.logout, 'Log out', AppColors.critical, () async {
                await authController.logout();
                if (context.mounted) Navigator.of(context).pop();
              }),
            ],
          );
        },
      ),
    );
  }

  Widget _action(BuildContext context, IconData icon, String label, Color color,
      Future<void> Function() onTap) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: ListTile(
        tileColor: AppColors.surfaceCard,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
          side: const BorderSide(color: AppColors.border),
        ),
        leading: Icon(icon, color: color, size: 20),
        title: Text(label, style: AppTheme.body(fontSize: 13, color: color)),
        onTap: () => onTap(),
      ),
    );
  }
}
