import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import '../models/scheduled_event.dart';
import '../services/local_schedule_service.dart';
import '../state/inbox_controller.dart';
import '../theme/app_theme.dart';
import '../widgets/add_reminder_sheet.dart';
import 'email_detail_screen.dart';

/// Device-local reminders (see `LocalScheduleService`) — entirely independent
/// of the backend. `controller` is only used here to resolve an email for
/// "Open Email" / to power the email picker in [AddReminderSheet]; it is
/// never the source of the reminder list itself.
class RemindersScreen extends StatelessWidget {
  final InboxController controller;

  const RemindersScreen({
    super.key,
    required this.controller,
  });

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: LocalScheduleService(),
      builder: (context, _) {
        final reminders = LocalScheduleService().reminders;
        final pending = reminders.where((r) => r.isPending).toList();
        final past = reminders.where((r) => !r.isPending).toList();
        // Deadline warnings/alarms are scheduled automatically from the
        // email's own deadline — shown read-only here so the user can see
        // what will fire; they are managed by completing the task, not by
        // hand (see LocalScheduleService.syncDeadlines).
        final deadlineAlarms = LocalScheduleService().pendingDeadlineEvents;
        final all = [...reminders, ...deadlineAlarms];

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
                  'Scheduled on this device',
                  style: AppTheme.label(fontSize: 10, color: AppColors.textMuted),
                ),
              ],
            ),
          ),
          floatingActionButton: FloatingActionButton.extended(
            onPressed: () => AddReminderSheet.show(context, controller: controller),
            backgroundColor: AppColors.warmBeige,
            foregroundColor: AppColors.textDark,
            icon: const Icon(Icons.add_alarm),
            label: Text(
              'ADD REMINDER',
              style: AppTheme.label(fontSize: 11, fontWeight: FontWeight.bold, color: AppColors.textDark),
            ),
          ),
          body: all.isEmpty
              ? Center(
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      const Icon(Icons.notifications_none_outlined, size: 48, color: AppColors.mutedSlate),
                      const SizedBox(height: 12),
                      Text(
                        'No reminders set.',
                        style: AppTheme.heading(fontSize: 15, color: AppColors.textSecondary),
                      ),
                      const SizedBox(height: 6),
                      Text(
                        'Tap "Remind Me" on any email, or "Add Reminder" below.',
                        style: AppTheme.body(fontSize: 12, color: AppColors.textMuted),
                      ),
                    ],
                  ),
                )
              : ListView(
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                  children: [
                    if (pending.isNotEmpty) ...[
                      _buildSectionHeader('UPCOMING', pending.length, AppColors.warmBeige),
                      const SizedBox(height: 8),
                      ...pending.map((r) => _buildReminderCard(context, r)),
                      const SizedBox(height: 18),
                    ],
                    if (deadlineAlarms.isNotEmpty) ...[
                      _buildSectionHeader(
                          'DEADLINE ALARMS', deadlineAlarms.length, AppColors.high),
                      const SizedBox(height: 8),
                      ...deadlineAlarms.map((r) => _buildReminderCard(context, r)),
                      const SizedBox(height: 18),
                    ],
                    if (past.isNotEmpty) ...[
                      _buildSectionHeader('PAST', past.length, AppColors.mutedSlate),
                      const SizedBox(height: 8),
                      ...past.map((r) => _buildReminderCard(context, r)),
                    ],
                  ],
                ),
        );
      },
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

  Widget _buildReminderCard(BuildContext context, ScheduledEvent reminder) {
    final isPending = reminder.isPending;
    final isDeadline = reminder.type.isDeadline;
    final local = reminder.scheduledAt.toLocal();
    final accent = isDeadline ? AppColors.high : AppColors.warmBeige;

    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      decoration: BoxDecoration(
        color: AppColors.surfaceCard,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(
          color: isPending ? accent.withValues(alpha: 0.5) : AppColors.border,
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
                      !isPending
                          ? Icons.history
                          : (isDeadline ? Icons.event_busy_outlined : Icons.alarm),
                      size: 14,
                      color: isPending ? accent : AppColors.mutedSlate,
                    ),
                    const SizedBox(width: 6),
                    Text(
                      isPending && isDeadline
                          ? reminder.title.toUpperCase()
                          : reminder.status.name.toUpperCase(),
                      style: AppTheme.mono(
                        fontSize: 9,
                        fontWeight: FontWeight.bold,
                        color: isPending ? accent : AppColors.mutedSlate,
                      ),
                    ),
                  ],
                ),
                Text(
                  DateFormat('EEE, MMM d · hh:mm a').format(local),
                  style: AppTheme.mono(
                    fontSize: 11,
                    color: AppColors.textPrimary,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Text(
              reminder.body,
              style: AppTheme.heading(fontSize: 14),
            ),
            // Deadline alarms are derived from the email's own deadline —
            // they clear themselves when the task is completed or the
            // deadline changes, so they get no manual edit/cancel controls.
            if (isPending && !isDeadline) ...[
              const SizedBox(height: 10),
              const Divider(color: AppColors.borderLight, height: 1),
              const SizedBox(height: 6),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  if (reminder.emailId != null)
                    TextButton.icon(
                      onPressed: () => _openEmail(context, reminder.emailId!),
                      icon: const Icon(Icons.open_in_new, size: 14),
                      label: const Text('Open Email'),
                      style: TextButton.styleFrom(
                        foregroundColor: AppColors.warmBeige,
                        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                        visualDensity: VisualDensity.compact,
                      ),
                    )
                  else
                    const SizedBox.shrink(),
                  Row(
                    children: [
                      TextButton.icon(
                        onPressed: () => _reschedule(context, reminder),
                        icon: const Icon(Icons.edit_calendar_outlined, size: 14),
                        label: const Text('Reschedule'),
                        style: TextButton.styleFrom(
                          foregroundColor: AppColors.textSecondary,
                          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                          visualDensity: VisualDensity.compact,
                        ),
                      ),
                      TextButton.icon(
                        onPressed: () => LocalScheduleService().cancel(reminder.id),
                        icon: const Icon(Icons.close, size: 14),
                        label: const Text('Cancel'),
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
            ],
          ],
        ),
      ),
    );
  }

  void _openEmail(BuildContext context, String emailId) {
    final email = controller.allEmails.where((e) => e.id == emailId).firstOrNull ??
        controller.resolvedEmails.where((e) => e.id == emailId).firstOrNull;
    if (email != null) {
      Navigator.push(
        context,
        MaterialPageRoute(
          builder: (_) => EmailDetailScreen(email: email, controller: controller),
        ),
      );
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('That email is no longer in the current inbox.')),
      );
    }
  }

  Future<void> _reschedule(BuildContext context, ScheduledEvent reminder) async {
    final now = DateTime.now();
    final date = await showDatePicker(
      context: context,
      initialDate: reminder.scheduledAt.toLocal().isAfter(now) ? reminder.scheduledAt.toLocal() : now,
      firstDate: now,
      lastDate: now.add(const Duration(days: 60)),
      builder: (context, child) => Theme(
        data: ThemeData.dark().copyWith(
          colorScheme: const ColorScheme.dark(
            primary: AppColors.warmBeige,
            onPrimary: AppColors.textDark,
            surface: AppColors.surface,
            onSurface: AppColors.textPrimary,
          ),
        ),
        child: child!,
      ),
    );
    if (date == null || !context.mounted) return;

    final time = await showTimePicker(
      context: context,
      initialTime: TimeOfDay.fromDateTime(now.add(const Duration(hours: 1))),
      builder: (context, child) => Theme(
        data: ThemeData.dark().copyWith(
          colorScheme: const ColorScheme.dark(
            primary: AppColors.warmBeige,
            onPrimary: AppColors.textDark,
            surface: AppColors.surface,
            onSurface: AppColors.textPrimary,
          ),
        ),
        child: child!,
      ),
    );
    if (time == null || !context.mounted) return;

    final picked = DateTime(date.year, date.month, date.day, time.hour, time.minute);
    if (picked.isBefore(DateTime.now())) {
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('Please choose a future time')));
      return;
    }
    await LocalScheduleService().reschedule(reminder.id, picked);
  }
}
