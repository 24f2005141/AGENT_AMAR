import 'package:flutter/material.dart';
import '../theme/app_theme.dart';
import '../theme/responsive.dart';

/// A compact one-line indicator of backend + LLM connectivity, e.g.
///
///   🟢 Backend Online     🟢 AI Online
///   🔴 Backend Offline    ⚪ AI Unknown
///
/// Data comes from `InboxController` (which calls `GET /api/v1/system/status`).
/// The app never contacts an LLM provider directly — FastAPI does the check.
class SystemStatusBar extends StatelessWidget {
  final bool backendOnline;

  /// `online` | `offline` | `unconfigured` | `unknown`
  final String llmStatus;
  final String? llmProvider;
  final String? llmModel;

  const SystemStatusBar({
    super.key,
    required this.backendOnline,
    required this.llmStatus,
    this.llmProvider,
    this.llmModel,
  });

  /// Nominal height at the default font scale. The bar is NOT locked to this:
  /// [preferredHeight] grows it with the user's font scale, because a fixed
  /// 26px strip clipped its own text at 1.5x and above.
  static const double height = 26;

  /// Height to reserve for this bar at the current text scale.
  static double preferredHeight(BuildContext context) {
    final scale = MediaQuery.textScalerOf(context).scale(1.0);
    return height * (scale > 1.0 ? scale : 1.0);
  }

  @override
  Widget build(BuildContext context) {
    final layout = context.layout;
    return Container(
      // No fixed height: a minimum, then free to grow with the text.
      constraints: BoxConstraints(minHeight: height),
      width: double.infinity,
      padding: EdgeInsets.symmetric(
        horizontal: layout.pageGutter,
        vertical: Gap.xs,
      ),
      decoration: const BoxDecoration(
        color: AppColors.surface,
        border: Border(top: BorderSide(color: AppColors.border, width: 0.5)),
      ),
      child: Row(
        children: [
          // Each indicator takes half the bar and ellipsizes rather than
          // pushing the other one off-screen.
          Expanded(
            child: _Dot(
              color: backendOnline ? AppColors.success : AppColors.critical,
              // On the narrowest phones the word "Backend" is redundant next
              // to a coloured dot - drop it rather than truncate mid-word.
              label: layout.isNarrow
                  ? (backendOnline ? 'Online' : 'Offline')
                  : (backendOnline ? 'Backend Online' : 'Backend Offline'),
            ),
          ),
          SizedBox(width: layout.isNarrow ? Gap.sm : Gap.lg),
          Expanded(
            child: Tooltip(
              message: _aiTooltip,
              child: _Dot(color: _aiColor, label: _aiLabel),
            ),
          ),
        ],
      ),
    );
  }

  String get _aiLabel {
    switch (llmStatus) {
      case 'online':
        return 'AI Online';
      case 'offline':
        return 'AI Offline';
      case 'unconfigured':
        return 'AI Unconfigured';
      default:
        return 'AI Unknown';
    }
  }

  Color get _aiColor {
    switch (llmStatus) {
      case 'online':
        return AppColors.success;
      case 'offline':
        return AppColors.critical;
      default: // unconfigured | unknown
        return AppColors.textMuted;
    }
  }

  String get _aiTooltip {
    final parts = <String>[];
    if (llmProvider != null && llmProvider!.isNotEmpty && llmProvider != 'none') {
      parts.add('Provider: $llmProvider');
    }
    if (llmModel != null && llmModel!.isNotEmpty) {
      parts.add('Model: $llmModel');
    }
    if (parts.isEmpty) return 'Checked by the backend';
    return parts.join('  •  ');
  }
}

class _Dot extends StatelessWidget {
  final Color color;
  final String label;

  const _Dot({required this.color, required this.label});

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 8,
          height: 8,
          decoration: BoxDecoration(
            color: color,
            shape: BoxShape.circle,
            boxShadow: [
              BoxShadow(color: color.withValues(alpha: 0.5), blurRadius: 4, spreadRadius: 0.5),
            ],
          ),
        ),
        const SizedBox(width: 6),
        // Flexible + ellipsis: a long label ("AI Unconfigured" at 2.0x font)
        // truncates instead of overflowing the bar.
        Flexible(
          child: Text(
            label,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            softWrap: false,
            style: AppTheme.label(
              fontSize: 10,
              color: AppColors.textSecondary,
              fontWeight: FontWeight.w600,
            ),
          ),
        ),
      ],
    );
  }
}
