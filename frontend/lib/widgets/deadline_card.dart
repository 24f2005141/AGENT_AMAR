import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import '../models/agent_analysis.dart';
import '../models/email.dart';
import '../theme/app_theme.dart';
import 'countdown_timer_view.dart';
import 'priority_badge.dart';

class DeadlineCard extends StatelessWidget {
  final Email email;
  final VoidCallback onOpen;
  final VoidCallback onMarkDone;

  const DeadlineCard({
    super.key,
    required this.email,
    required this.onOpen,
    required this.onMarkDone,
  });

  @override
  Widget build(BuildContext context) {
    final deadline = email.analysis.deadline;
    final isCritical = email.analysis.priority == PriorityLevel.critical;
    final isCompleted = email.userState.isCompleted;

    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      decoration: BoxDecoration(
        color: isCritical
            ? const Color(0xFF1E384D)
            : AppColors.surfaceCard,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(
          color: isCompleted
              ? AppColors.success.withValues(alpha: 0.4)
              : isCritical
                  ? AppColors.critical.withValues(alpha: 0.7)
                  : AppColors.border,
          width: isCritical ? 1.5 : 1,
        ),
      ),
      child: InkWell(
        onTap: onOpen,
        borderRadius: BorderRadius.circular(14),
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              // Left status circle/checkbox
              InkWell(
                onTap: onMarkDone,
                borderRadius: BorderRadius.circular(20),
                child: Container(
                  width: 28,
                  height: 28,
                  decoration: BoxDecoration(
                    color: isCompleted
                        ? AppColors.success.withValues(alpha: 0.2)
                        : Colors.transparent,
                    shape: BoxShape.circle,
                    border: Border.all(
                      color: isCompleted ? AppColors.success : AppColors.mutedSlate,
                      width: 1.5,
                    ),
                  ),
                  child: isCompleted
                      ? const Icon(Icons.check, size: 18, color: AppColors.success)
                      : null,
                ),
              ),
              const SizedBox(width: 12),

              // Center details
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        PriorityBadge(priority: email.analysis.priority, isCompact: true),
                        const SizedBox(width: 6),
                        Text(
                          email.senderName,
                          style: AppTheme.label(
                            fontSize: 11,
                            color: AppColors.textMuted,
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 4),
                    Text(
                      email.subject,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: AppTheme.heading(
                        fontSize: 14,
                        color: isCompleted ? AppColors.textMuted : AppColors.textPrimary,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Row(
                      children: [
                        Icon(Icons.event, size: 12, color: AppColors.textMuted),
                        const SizedBox(width: 4),
                        Text(
                          deadline != null
                              ? DateFormat('EEE · hh:mm a').format(deadline)
                              : 'No deadline',
                          style: AppTheme.mono(fontSize: 11, color: AppColors.textSecondary),
                        ),
                      ],
                    ),
                  ],
                ),
              ),

              // Right countdown
              if (deadline != null) ...[
                const SizedBox(width: 8),
                Column(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    CountdownTimerView(deadline: deadline, isLarge: false),
                    const SizedBox(height: 4),
                    Text(
                      isCompleted ? 'COMPLETED' : 'PENDING',
                      style: AppTheme.mono(
                        fontSize: 9,
                        color: isCompleted ? AppColors.success : AppColors.textMuted,
                      ),
                    ),
                  ],
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
