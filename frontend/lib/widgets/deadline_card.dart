import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import '../models/agent_analysis.dart';
import '../models/email.dart';
import '../theme/app_theme.dart';
import '../theme/responsive.dart';
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
          // Checkbox + details + countdown is three columns of content. That
          // fits a tablet and a comfortable phone, but not a 320px screen (and
          // not any phone at 2x font), so below `stackAt` the countdown moves
          // onto its own line under the details instead of being crushed.
          child: LayoutBuilder(builder: (context, constraints) {
          // The threshold scales with the font: three columns need more room
          // when every label is 1.5-2x bigger, so a 375px phone at 1.5x
          // stacks just like a 320px phone at 1.0x does.
          final textScale = MediaQuery.textScalerOf(context).scale(1.0);
          final stackAt = 340.0 * (textScale > 1.0 ? textScale : 1.0);
          final stackCountdown =
              constraints.hasBoundedWidth && constraints.maxWidth < stackAt;
          final countdown = deadline == null
              ? null
              : Column(
                  crossAxisAlignment: stackCountdown
                      ? CrossAxisAlignment.start
                      : CrossAxisAlignment.end,
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
                );
          return Row(
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
                    // The badge grows with the font scale too, so this is a
                    // Wrap: badge and sender flow onto a second line rather
                    // than fighting over one.
                    Wrap(
                      spacing: 6,
                      runSpacing: 2,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      children: [
                        PriorityBadge(priority: email.analysis.priority, isCompact: true),
                        ConstrainedBox(
                          constraints: BoxConstraints(maxWidth: constraints.maxWidth * 0.6),
                          child: Text(
                            email.senderName,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            softWrap: false,
                            style: AppTheme.label(
                              fontSize: 11,
                              color: AppColors.textMuted,
                            ),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 4),
                    Text(
                      email.subject,
                      maxLines: 2,
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
                        Flexible(
                          child: Text(
                            deadline != null
                                ? DateFormat('EEE · hh:mm a').format(deadline)
                                : 'No deadline',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            softWrap: false,
                            style: AppTheme.mono(fontSize: 11, color: AppColors.textSecondary),
                          ),
                        ),
                      ],
                    ),
                    // Narrow card: the countdown sits under the details.
                    if (stackCountdown && countdown != null) ...[
                      const SizedBox(height: Gap.sm),
                      countdown,
                    ],
                  ],
                ),
              ),

              // Wide enough: keep the countdown in its own right-hand column.
              if (!stackCountdown && countdown != null) ...[
                const SizedBox(width: 8),
                countdown,
              ],
            ],
          );
          }),
        ),
      ),
    );
  }
}
