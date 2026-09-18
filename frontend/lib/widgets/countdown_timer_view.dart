import 'package:flutter/material.dart';
import '../theme/app_theme.dart';

class CountdownTimerView extends StatelessWidget {
  final DateTime deadline;
  final bool isLarge;
  final bool showLabel;

  const CountdownTimerView({
    super.key,
    required this.deadline,
    this.isLarge = false,
    this.showLabel = true,
  });

  @override
  Widget build(BuildContext context) {
    final now = DateTime.now();
    final difference = deadline.difference(now);

    if (difference.isNegative) {
      return Text(
        'EXPIRED',
        style: AppTheme.mono(
          fontSize: isLarge ? 14 : 11,
          color: AppColors.critical,
          fontWeight: FontWeight.bold,
        ),
      );
    }

    final hours = difference.inHours;
    final minutes = difference.inMinutes % 60;
    final seconds = difference.inSeconds % 60;

    final formatted = '${hours.toString().padLeft(2, '0')}:${minutes.toString().padLeft(2, '0')}:${seconds.toString().padLeft(2, '0')}';

    final isUrgent = difference.inHours < 1;
    final color = isUrgent ? AppColors.critical : AppColors.warmBeige;

    return Row(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        Icon(
          Icons.timer_outlined,
          size: isLarge ? 16 : 13,
          color: color,
        ),
        const SizedBox(width: 4),
        // The countdown string plus the LEFT suffix can exceed a narrow card
        // at large font scales; let the value shrink before the row breaks.
        Flexible(
          child: Text(
            formatted,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            softWrap: false,
            style: AppTheme.countdown(
              fontSize: isLarge ? 15 : 12,
              color: color,
            ),
          ),
        ),
        if (showLabel) ...[
          const SizedBox(width: 4),
          Flexible(
            child: Text(
              'LEFT',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              softWrap: false,
              style: AppTheme.mono(
              fontSize: isLarge ? 11 : 9,
                color: color.withValues(alpha: 0.8),
                fontWeight: FontWeight.bold,
              ),
            ),
          ),
        ],
      ],
    );
  }
}
