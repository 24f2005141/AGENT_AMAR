import 'package:flutter/material.dart';
import '../models/agent_analysis.dart';
import '../theme/app_theme.dart';

class PriorityBadge extends StatelessWidget {
  final PriorityLevel priority;
  final bool isCompact;

  const PriorityBadge({
    super.key,
    required this.priority,
    this.isCompact = false,
  });

  @override
  Widget build(BuildContext context) {
    Color bg;
    Color fg;
    Color border;

    switch (priority) {
      case PriorityLevel.critical:
        bg = AppColors.critical.withValues(alpha: 0.18);
        fg = AppColors.critical;
        border = AppColors.critical.withValues(alpha: 0.4);
        break;
      case PriorityLevel.high:
        bg = AppColors.high.withValues(alpha: 0.18);
        fg = AppColors.high;
        border = AppColors.high.withValues(alpha: 0.4);
        break;
      case PriorityLevel.medium:
        bg = AppColors.medium.withValues(alpha: 0.18);
        fg = AppColors.medium;
        border = AppColors.medium.withValues(alpha: 0.35);
        break;
      case PriorityLevel.low:
        bg = AppColors.mutedSlate.withValues(alpha: 0.25);
        fg = AppColors.textSecondary;
        border = AppColors.mutedSlate.withValues(alpha: 0.4);
        break;
    }

    return Container(
      padding: EdgeInsets.symmetric(
        horizontal: isCompact ? 6 : 8,
        vertical: isCompact ? 2 : 4,
      ),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(isCompact ? 4 : 6),
        border: Border.all(color: border, width: 1),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (priority == PriorityLevel.critical) ...[
            Container(
              width: 5,
              height: 5,
              margin: const EdgeInsets.only(right: 4),
              decoration: const BoxDecoration(
                color: AppColors.critical,
                shape: BoxShape.circle,
              ),
            ),
          ],
          Text(
            priority.displayName,
            style: AppTheme.mono(
              fontSize: isCompact ? 9 : 10,
              fontWeight: FontWeight.w700,
              color: fg,
            ),
          ),
        ],
      ),
    );
  }
}
