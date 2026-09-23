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
        listenable: Listenable.merge([authController, inboxController]),
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
                        Text(
                          user?.displayName ?? 'Signed in',
                          style: AppTheme.heading(fontSize: 16),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          user?.googleEmail ?? '—',
                          style: AppTheme.body(
                            fontSize: 12,
                            color: AppColors.textSecondary,
                          ),
                        ),
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
                    Icon(
                      gmailOk ? Icons.check_circle : Icons.error_outline,
                      color: gmailOk ? AppColors.success : AppColors.high,
                      size: 20,
                    ),
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
              _aiModeCard(context),
              const SizedBox(height: 20),
              if (!gmailOk)
                _action(
                  context,
                  Icons.link,
                  'Reconnect Gmail',
                  AppColors.warmBeige,
                  () async {
                    await authController.reconnectGmail();
                    await inboxController.checkGmailStatus();
                  },
                ),
              _action(
                context,
                Icons.link_off,
                'Disconnect Gmail',
                AppColors.textSecondary,
                () async {
                  await inboxController.disconnectGmail();
                  await authController.refreshMe();
                },
              ),
              _action(
                context,
                Icons.logout,
                'Log out',
                AppColors.critical,
                () async {
                  await authController.logout();
                  if (context.mounted) Navigator.of(context).pop();
                },
              ),
            ],
          );
        },
      ),
    );
  }

  Widget _aiModeCard(BuildContext context) {
    final mode = inboxController.aiMode;
    final options = mode?.options ?? const [];
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: AppColors.surfaceCard,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('AI processing mode', style: AppTheme.heading(fontSize: 14)),
          const SizedBox(height: 4),
          Text(
            'Choose how AMAR classifies uncertain email. Reply drafting still uses Gemini with Groq fallback.',
            style: AppTheme.body(fontSize: 11, color: AppColors.textSecondary),
          ),
          const SizedBox(height: 12),
          if (mode == null)
            const LinearProgressIndicator()
          else
            InputDecorator(
              decoration: const InputDecoration(
                labelText: 'Active approach',
                border: OutlineInputBorder(),
              ),
              child: DropdownButtonHideUnderline(
                child: DropdownButton<String>(
                  key: const ValueKey('ai-mode-selector'),
                  value: mode.selected,
                  isExpanded: true,
                  isDense: true,
                  items: [
                    for (final option in options)
                      DropdownMenuItem<String>(
                        value: option.id,
                        enabled: option.available,
                        child: Text(
                          option.available
                              ? option.label
                              : '${option.label} (unavailable)',
                        ),
                      ),
                  ],
                  onChanged: inboxController.isAiModeUpdating
                      ? null
                      : (value) async {
                          if (value == null) return;
                          final ok = await inboxController.selectAiMode(value);
                          if (!ok && context.mounted) {
                            ScaffoldMessenger.of(context).showSnackBar(
                              SnackBar(
                                content: Text(
                                  inboxController.aiModeError ??
                                      'Could not change AI mode',
                                ),
                              ),
                            );
                          }
                        },
                ),
              ),
            ),
          if (inboxController.isAiModeUpdating) ...[
            const SizedBox(height: 8),
            const LinearProgressIndicator(),
          ],
          if (mode != null) ...[
            const SizedBox(height: 10),
            Text(
              options
                      .where((option) => option.id == mode.selected)
                      .firstOrNull
                      ?.detail ??
                  '',
              style: AppTheme.body(
                fontSize: 11,
                color: AppColors.textSecondary,
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _action(
    BuildContext context,
    IconData icon,
    String label,
    Color color,
    Future<void> Function() onTap,
  ) {
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
