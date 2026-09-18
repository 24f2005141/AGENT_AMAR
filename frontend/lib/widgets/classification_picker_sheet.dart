import 'package:flutter/material.dart';

import '../models/agent_analysis.dart';
import '../theme/app_theme.dart';

/// Bottom sheet to manually correct an email's primary classification.
///
/// Returns the chosen [PrimaryCategory] on Save, or `null` if dismissed. The
/// four options are the canonical mutually-exclusive buckets — nothing else.
class ClassificationPickerSheet extends StatefulWidget {
  final PrimaryCategory current;

  const ClassificationPickerSheet({super.key, required this.current});

  static Future<PrimaryCategory?> show(
    BuildContext context, {
    required PrimaryCategory current,
  }) {
    return showModalBottomSheet<PrimaryCategory>(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) => ClassificationPickerSheet(current: current),
    );
  }

  @override
  State<ClassificationPickerSheet> createState() =>
      _ClassificationPickerSheetState();
}

class _ClassificationPickerSheetState extends State<ClassificationPickerSheet> {
  late PrimaryCategory _selected = widget.current;

  static const _order = [
    PrimaryCategory.replyRequired,
    PrimaryCategory.actionRequired,
    PrimaryCategory.important,
    PrimaryCategory.lowPriority,
  ];

  static String label(PrimaryCategory c) {
    switch (c) {
      case PrimaryCategory.replyRequired:
        return 'Reply Required';
      case PrimaryCategory.actionRequired:
        return 'Action Required';
      case PrimaryCategory.important:
        return 'Important';
      case PrimaryCategory.lowPriority:
        return 'Low Priority';
    }
  }

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 16),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Center(
              child: Container(
                width: 36,
                height: 4,
                decoration: BoxDecoration(
                  color: AppColors.border,
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
            ),
            const SizedBox(height: 14),
            Text('SELECT CLASSIFICATION',
                style: AppTheme.label(fontSize: 11, color: AppColors.warmBeige)),
            const SizedBox(height: 4),
            Text(
              'This email belongs in exactly one section. Your choice becomes the '
              'current classification.',
              style: AppTheme.body(fontSize: 11, color: AppColors.textMuted),
            ),
            const SizedBox(height: 12),
            for (final c in _order)
              InkWell(
                onTap: () => setState(() => _selected = c),
                borderRadius: BorderRadius.circular(10),
                child: Padding(
                  padding: const EdgeInsets.symmetric(vertical: 10, horizontal: 4),
                  child: Row(
                    children: [
                      Icon(
                        _selected == c
                            ? Icons.radio_button_checked
                            : Icons.radio_button_unchecked,
                        size: 20,
                        color: _selected == c ? AppColors.warmBeige : AppColors.textMuted,
                      ),
                      const SizedBox(width: 12),
                      Text(label(c),
                          style: AppTheme.bodyMedium(
                              fontSize: 13, color: AppColors.textPrimary)),
                      if (c == widget.current) ...[
                        const SizedBox(width: 8),
                        Text('· current',
                            style: AppTheme.label(fontSize: 10, color: AppColors.textMuted)),
                      ],
                    ],
                  ),
                ),
              ),
            const SizedBox(height: 8),
            Row(
              children: [
                TextButton(
                  onPressed: () => Navigator.pop(context),
                  child: Text('Cancel',
                      style: AppTheme.label(fontSize: 12, color: AppColors.textMuted)),
                ),
                const Spacer(),
                ElevatedButton(
                  onPressed: _selected == widget.current
                      ? null
                      : () => Navigator.pop(context, _selected),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AppColors.warmBeige,
                    foregroundColor: AppColors.textDark,
                    disabledBackgroundColor: AppColors.warmBeige.withValues(alpha: 0.4),
                    padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 10),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                  ),
                  child: Text('Save',
                      style: AppTheme.heading(fontSize: 13, color: AppColors.textDark)),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

/// User-facing label for a [PrimaryCategory] (shared with the detail screen).
String primaryCategoryLabel(PrimaryCategory c) =>
    _ClassificationPickerSheetState.label(c);
