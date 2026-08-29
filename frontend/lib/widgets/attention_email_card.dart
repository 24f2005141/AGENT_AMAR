import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import '../models/agent_analysis.dart';
import '../models/email.dart';
import '../theme/app_theme.dart';
import 'countdown_timer_view.dart';
import 'priority_badge.dart';

class AttentionEmailCard extends StatelessWidget {
  final Email email;
  final VoidCallback onOpen;
  final VoidCallback onMarkComplete;
  final VoidCallback onRemindMe;
  final VoidCallback onSnooze;
  final bool isFeatured;

  const AttentionEmailCard({
    super.key,
    required this.email,
    required this.onOpen,
    required this.onMarkComplete,
    required this.onRemindMe,
    required this.onSnooze,
    this.isFeatured = false,
  });

  @override
  Widget build(BuildContext context) {
    final isCritical = email.analysis.priority == PriorityLevel.critical;
    final isSnoozed = email.isSnoozed;

    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      decoration: BoxDecoration(
        color: isCritical
            ? const Color(0xFF1E384D)
            : AppColors.surfaceCard,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: isCritical
              ? AppColors.critical.withValues(alpha: 0.7)
              : AppColors.border,
          width: isCritical ? 1.5 : 1,
        ),
        boxShadow: isCritical
            ? [
                BoxShadow(
                  color: AppColors.critical.withValues(alpha: 0.18),
                  blurRadius: 16,
                  offset: const Offset(0, 4),
                ),
              ]
            : null,
      ),
      child: InkWell(
        onTap: onOpen,
        borderRadius: BorderRadius.circular(16),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Header Row
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Row(
                    children: [
                      PriorityBadge(priority: email.analysis.priority),
                      const SizedBox(width: 8),
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                        decoration: BoxDecoration(
                          color: AppColors.mutedSlate.withValues(alpha: 0.2),
                          borderRadius: BorderRadius.circular(4),
                        ),
                        child: Text(
                          email.analysis.category.toUpperCase(),
                          style: AppTheme.mono(
                            fontSize: 9,
                            color: AppColors.textSecondary,
                          ),
                        ),
                      ),
                    ],
                  ),
                  if (email.analysis.deadline != null)
                    CountdownTimerView(deadline: email.analysis.deadline!),
                ],
              ),
              const SizedBox(height: 10),

              // Title / Subject
              Text(
                email.subject,
                style: AppTheme.heading(
                  fontSize: isFeatured ? 16 : 15,
                  fontWeight: FontWeight.w700,
                  color: AppColors.textPrimary,
                ),
              ),
              const SizedBox(height: 4),

              // Sender info
              Text(
                'From: ${email.senderName}',
                style: AppTheme.bodyMedium(
                  fontSize: 12,
                  color: AppColors.textMuted,
                ),
              ),
              const SizedBox(height: 8),

              // Action Required callout box
              if (email.analysis.actionDescription != null)
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                  decoration: BoxDecoration(
                    color: AppColors.background.withValues(alpha: 0.7),
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(color: AppColors.borderLight),
                  ),
                  child: Row(
                    children: [
                      Icon(
                        Icons.touch_app_outlined,
                        size: 16,
                        color: isCritical ? AppColors.critical : AppColors.warmBeige,
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          email.analysis.actionDescription!,
                          style: AppTheme.bodyMedium(
                            fontSize: 12,
                            color: isCritical ? AppColors.textPrimary : AppColors.warmBeige,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),

              // Snooze info if active
              if (isSnoozed) ...[
                const SizedBox(height: 8),
                Row(
                  children: [
                    const Icon(Icons.snooze, size: 14, color: AppColors.textMuted),
                    const SizedBox(width: 6),
                    Text(
                      'Snoozed until ${DateFormat('hh:mm a').format(email.userState.snoozedUntil!)}',
                      style: AppTheme.mono(fontSize: 11, color: AppColors.textMuted),
                    ),
                  ],
                ),
              ],

              const SizedBox(height: 12),
              const Divider(color: AppColors.borderLight, height: 1),
              const SizedBox(height: 8),

              // Quick Actions Row
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  TextButton.icon(
                    onPressed: onOpen,
                    icon: const Icon(Icons.visibility_outlined, size: 16),
                    label: const Text('Open'),
                    style: TextButton.styleFrom(
                      foregroundColor: AppColors.warmBeige,
                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                      visualDensity: VisualDensity.compact,
                    ),
                  ),
                  TextButton.icon(
                    onPressed: onRemindMe,
                    icon: const Icon(Icons.notifications_active_outlined, size: 16),
                    label: const Text('Remind'),
                    style: TextButton.styleFrom(
                      foregroundColor: AppColors.textSecondary,
                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                      visualDensity: VisualDensity.compact,
                    ),
                  ),
                  TextButton.icon(
                    onPressed: onSnooze,
                    icon: const Icon(Icons.snooze, size: 16),
                    label: const Text('Snooze'),
                    style: TextButton.styleFrom(
                      foregroundColor: AppColors.textSecondary,
                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                      visualDensity: VisualDensity.compact,
                    ),
                  ),
                  IconButton(
                    onPressed: onMarkComplete,
                    icon: const Icon(Icons.check_circle_outline, color: AppColors.success, size: 20),
                    tooltip: 'Mark Completed',
                    visualDensity: VisualDensity.compact,
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}
