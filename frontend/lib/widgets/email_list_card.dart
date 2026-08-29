import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import '../models/agent_analysis.dart';
import '../models/email.dart';
import '../theme/app_theme.dart';
import 'priority_badge.dart';

class EmailListCard extends StatelessWidget {
  final Email email;
  final VoidCallback onTap;

  const EmailListCard({
    super.key,
    required this.email,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final isLowPriority = email.analysis.priority == PriorityLevel.low;
    final isCritical = email.analysis.priority == PriorityLevel.critical;

    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      decoration: BoxDecoration(
        color: isLowPriority
            ? AppColors.surface.withValues(alpha: 0.4)
            : isCritical
                ? const Color(0xFF1E384D)
                : AppColors.surfaceCard,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(
          color: isCritical
              ? AppColors.critical.withValues(alpha: 0.5)
              : isLowPriority
                  ? AppColors.borderLight
                  : AppColors.border,
          width: 1,
        ),
      ),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(14),
        child: Padding(
          padding: EdgeInsets.symmetric(
            horizontal: 14,
            vertical: isLowPriority ? 10 : 12,
          ),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Sender Avatar / Initials
              Container(
                width: isLowPriority ? 28 : 34,
                height: isLowPriority ? 28 : 34,
                margin: const EdgeInsets.only(top: 2),
                decoration: BoxDecoration(
                  color: isCritical
                      ? AppColors.critical.withValues(alpha: 0.2)
                      : isLowPriority
                          ? AppColors.mutedSlate.withValues(alpha: 0.2)
                          : AppColors.secondarySurface,
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(
                    color: isCritical
                        ? AppColors.critical.withValues(alpha: 0.4)
                        : AppColors.borderLight,
                  ),
                ),
                child: Center(
                  child: Text(
                    _getInitials(email.senderName),
                    style: AppTheme.brandTitle(
                      fontSize: isLowPriority ? 10 : 12,
                      fontWeight: FontWeight.bold,
                      color: isCritical ? AppColors.critical : AppColors.warmBeige,
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 12),

              // Email summary & details
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Expanded(
                          child: Text(
                            email.senderName.toUpperCase(),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: AppTheme.label(
                              fontSize: isLowPriority ? 10 : 11,
                              color: isLowPriority ? AppColors.textMuted : AppColors.textPrimary,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                        ),
                        Text(
                          DateFormat('hh:mm a').format(email.receivedAt),
                          style: AppTheme.mono(fontSize: 10, color: AppColors.textMuted),
                        ),
                      ],
                    ),
                    const SizedBox(height: 2),
                    Text(
                      email.subject,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: AppTheme.heading(
                        fontSize: isLowPriority ? 12 : 14,
                        fontWeight: FontWeight.w600,
                        color: isLowPriority ? AppColors.textSecondary : AppColors.textPrimary,
                      ),
                    ),
                    const SizedBox(height: 3),
                    Text(
                      email.snippet,
                      maxLines: isLowPriority ? 1 : 2,
                      overflow: TextOverflow.ellipsis,
                      style: AppTheme.body(
                        fontSize: isLowPriority ? 11 : 12,
                        color: AppColors.textMuted,
                      ),
                    ),
                    const SizedBox(height: 6),
                    Row(
                      children: [
                        PriorityBadge(priority: email.analysis.priority, isCompact: true),
                        const SizedBox(width: 6),
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                          decoration: BoxDecoration(
                            color: AppColors.surface,
                            borderRadius: BorderRadius.circular(4),
                          ),
                          child: Text(
                            email.analysis.category,
                            style: AppTheme.label(
                              fontSize: 9,
                              color: AppColors.textMuted,
                            ),
                          ),
                        ),
                        if (email.analysis.actionRequired && !email.userState.isCompleted) ...[
                          const SizedBox(width: 6),
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                            decoration: BoxDecoration(
                              color: AppColors.warmBeige.withValues(alpha: 0.15),
                              borderRadius: BorderRadius.circular(4),
                            ),
                            child: Text(
                              'ACTION',
                              style: AppTheme.mono(
                                fontSize: 9,
                                color: AppColors.warmBeige,
                                fontWeight: FontWeight.bold,
                              ),
                            ),
                          ),
                        ],
                      ],
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  String _getInitials(String name) {
    final parts = name.trim().split(RegExp(r'\s+'));
    if (parts.isEmpty) return 'EM';
    if (parts.length == 1) return parts[0].substring(0, parts[0].length.clamp(1, 2)).toUpperCase();
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
}
