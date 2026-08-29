import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import '../models/reminder.dart';
import '../state/inbox_controller.dart';
import '../theme/app_theme.dart';
import 'email_detail_screen.dart';

class RemindersScreen extends StatelessWidget {
  final InboxController controller;

  const RemindersScreen({
    super.key,
    required this.controller,
  });

  @override
  Widget build(BuildContext context) {
    final allReminders = controller.reminders;
    final userScheduled = allReminders
        .where((r) => r.reminderType == ReminderType.userScheduled)
        .toList();
    final systemReminders = allReminders
        .where((r) => r.reminderType != ReminderType.userScheduled)
        .toList();

    return Scaffold(
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'REMINDERS',
              style: AppTheme.brandTitle(fontSize: 18, fontWeight: FontWeight.bold),
            ),
            Text(
              'Scheduled nudges & system alarms',
              style: AppTheme.label(fontSize: 10, color: AppColors.textMuted),
            ),
          ],
        ),
      ),
      body: RefreshIndicator(
        onRefresh: controller.loadData,
        color: AppColors.warmBeige,
        backgroundColor: AppColors.surface,
        child: allReminders.isEmpty
            ? Center(
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    const Icon(Icons.notifications_none_outlined, size: 48, color: AppColors.mutedSlate),
                    const SizedBox(height: 12),
                    Text(
                      'No active reminders set.',
                      style: AppTheme.heading(fontSize: 15, color: AppColors.textSecondary),
                    ),
                    const SizedBox(height: 6),
                    Text(
                      'Tap "Remind Me" on any email to schedule a nudge.',
                      style: AppTheme.body(fontSize: 12, color: AppColors.textMuted),
                    ),
                  ],
                ),
              )
            : ListView(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                children: [
                  // USER SCHEDULED REMINDERS
                  if (userScheduled.isNotEmpty) ...[
                    _buildSectionHeader('USER-SCHEDULED REMINDERS', userScheduled.length, AppColors.warmBeige),
                    const SizedBox(height: 8),
                    ...userScheduled.map((r) => _buildReminderCard(context, r, isUser: true)),
                    const SizedBox(height: 18),
                  ],

                  // SYSTEM REMINDERS
                  if (systemReminders.isNotEmpty) ...[
                    _buildSectionHeader('SYSTEM DEADLINE REMINDERS', systemReminders.length, AppColors.high),
                    const SizedBox(height: 8),
                    ...systemReminders.map((r) => _buildReminderCard(context, r, isUser: false)),
                    const SizedBox(height: 18),
                  ],
                ],
              ),
      ),
    );
  }

  Widget _buildSectionHeader(String title, int count, Color color) {
    return Row(
      children: [
        Text(
          title,
          style: AppTheme.heading(
            fontSize: 12,
            fontWeight: FontWeight.bold,
            color: AppColors.textSecondary,
          ),
        ),
        const SizedBox(width: 8),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
          decoration: BoxDecoration(
            color: color.withValues(alpha: 0.18),
            borderRadius: BorderRadius.circular(6),
          ),
          child: Text(
            '$count',
            style: AppTheme.mono(fontSize: 10, color: color, fontWeight: FontWeight.bold),
          ),
        ),
      ],
    );
  }

  Widget _buildReminderCard(BuildContext context, ReminderItem reminder, {required bool isUser}) {
    final isPast = reminder.reminderAt.isBefore(DateTime.now());

    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      decoration: BoxDecoration(
        color: AppColors.surfaceCard,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(
          color: isUser ? AppColors.warmBeige.withValues(alpha: 0.5) : AppColors.border,
        ),
      ),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Row(
                  children: [
                    Icon(
                      isUser ? Icons.alarm : Icons.shield_outlined,
                      size: 14,
                      color: isUser ? AppColors.warmBeige : AppColors.high,
                    ),
                    const SizedBox(width: 6),
                    Text(
                      reminder.reminderType.displayName,
                      style: AppTheme.mono(
                        fontSize: 9,
                        fontWeight: FontWeight.bold,
                        color: isUser ? AppColors.warmBeige : AppColors.high,
                      ),
                    ),
                  ],
                ),
                Text(
                  DateFormat('EEE, MMM d · hh:mm a').format(reminder.reminderAt),
                  style: AppTheme.mono(
                    fontSize: 11,
                    color: isPast ? AppColors.critical : AppColors.textPrimary,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Text(
              reminder.emailSubject,
              style: AppTheme.heading(fontSize: 14),
            ),
            if (reminder.actionDescription != null) ...[
              const SizedBox(height: 4),
              Text(
                reminder.actionDescription!,
                style: AppTheme.body(fontSize: 12, color: AppColors.textMuted),
              ),
            ],
            const SizedBox(height: 10),
            const Divider(color: AppColors.borderLight, height: 1),
            const SizedBox(height: 6),

            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                TextButton.icon(
                  onPressed: () {
                    final email = controller.allEmails.where(
                      (e) => e.id == reminder.emailId,
                    ).firstOrNull ?? controller.allEmails.firstOrNull;

                    if (email != null) {
                      Navigator.push(
                        context,
                        MaterialPageRoute(
                          builder: (_) => EmailDetailScreen(
                            email: email,
                            controller: controller,
                          ),
                        ),
                      );
                    } else {
                      ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(content: Text('Related email row not found in current inbox.')),
                      );
                    }
                  },
                  icon: const Icon(Icons.open_in_new, size: 14),
                  label: const Text('Open Email'),
                  style: TextButton.styleFrom(
                    foregroundColor: AppColors.warmBeige,
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                    visualDensity: VisualDensity.compact,
                  ),
                ),
                if (isUser)
                  TextButton.icon(
                    onPressed: () async {
                      final reminderId = int.tryParse(reminder.id) ?? 0;
                      await controller.cancelReminder(reminder.emailId, reminderId);
                      if (context.mounted) {
                        ScaffoldMessenger.of(context).showSnackBar(
                          const SnackBar(content: Text('Cancelled scheduled reminder on backend.')),
                        );
                      }
                    },
                    icon: const Icon(Icons.close, size: 14),
                    label: const Text('Cancel Reminder'),
                    style: TextButton.styleFrom(
                      foregroundColor: AppColors.critical,
                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                      visualDensity: VisualDensity.compact,
                    ),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
