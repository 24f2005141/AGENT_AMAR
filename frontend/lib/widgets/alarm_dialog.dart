import 'package:flutter/material.dart';
import '../models/notification_event.dart';
import '../theme/app_theme.dart';

class AlarmDialog extends StatefulWidget {
  final NotificationEvent event;
  final VoidCallback onOpenEmail;
  final VoidCallback onSnoozeBriefly;
  final VoidCallback onMarkComplete;
  final VoidCallback onDismiss;

  const AlarmDialog({
    super.key,
    required this.event,
    required this.onOpenEmail,
    required this.onSnoozeBriefly,
    required this.onMarkComplete,
    required this.onDismiss,
  });

  static Future<void> show(
    BuildContext context, {
    required NotificationEvent event,
    required VoidCallback onOpenEmail,
    required VoidCallback onSnoozeBriefly,
    required VoidCallback onMarkComplete,
    required VoidCallback onDismiss,
  }) {
    return showGeneralDialog(
      context: context,
      barrierDismissible: false,
      barrierLabel: 'ALARM',
      barrierColor: Colors.black87,
      transitionDuration: const Duration(milliseconds: 300),
      pageBuilder: (context, anim1, anim2) => AlarmDialog(
        event: event,
        onOpenEmail: onOpenEmail,
        onSnoozeBriefly: onSnoozeBriefly,
        onMarkComplete: onMarkComplete,
        onDismiss: onDismiss,
      ),
      transitionBuilder: (context, anim1, anim2, child) {
        return ScaleTransition(
          scale: CurvedAnimation(parent: anim1, curve: Curves.easeOutBack),
          child: FadeTransition(
            opacity: anim1,
            child: child,
          ),
        );
      },
    );
  }

  @override
  State<AlarmDialog> createState() => _AlarmDialogState();
}

class _AlarmDialogState extends State<AlarmDialog>
    with SingleTickerProviderStateMixin {
  late AnimationController _pulseController;
  late Animation<double> _borderGlowAnimation;

  @override
  void initState() {
    super.initState();
    _pulseController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 900),
    )..repeat(reverse: true);

    _borderGlowAnimation = Tween<double>(begin: 0.3, end: 1.0).animate(
      CurvedAnimation(parent: _pulseController, curve: Curves.easeInOut),
    );
  }

  @override
  void dispose() {
    _pulseController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Material(
        color: Colors.transparent,
        child: AnimatedBuilder(
          animation: _borderGlowAnimation,
          builder: (context, child) {
            return Container(
              margin: const EdgeInsets.symmetric(horizontal: 20),
              padding: const EdgeInsets.all(24),
              decoration: BoxDecoration(
                color: const Color(0xFF1B1B26),
                borderRadius: BorderRadius.circular(24),
                border: Border.all(
                  color: AppColors.critical.withValues(alpha: _borderGlowAnimation.value),
                  width: 2.5,
                ),
                boxShadow: [
                  BoxShadow(
                    color: AppColors.critical.withValues(alpha: _borderGlowAnimation.value * 0.4),
                    blurRadius: 30,
                    spreadRadius: 2,
                  ),
                ],
              ),
              child: child,
            );
          },
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              // Alarm Pulse Header
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                decoration: BoxDecoration(
                  color: AppColors.critical.withValues(alpha: 0.18),
                  borderRadius: BorderRadius.circular(20),
                  border: Border.all(color: AppColors.critical.withValues(alpha: 0.5)),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Icon(Icons.warning_amber_rounded, color: AppColors.critical, size: 18),
                    const SizedBox(width: 6),
                    Text(
                      'DEADLINE ALARM',
                      style: AppTheme.mono(
                        fontSize: 12,
                        fontWeight: FontWeight.bold,
                        color: AppColors.critical,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 16),

              Text(
                'ACTION REQUIRED',
                style: AppTheme.brandTitle(
                  fontSize: 22,
                  fontWeight: FontWeight.w900,
                  color: AppColors.critical,
                ),
              ),
              const SizedBox(height: 8),

              Text(
                widget.event.emailSubject,
                textAlign: TextAlign.center,
                style: AppTheme.heading(
                  fontSize: 16,
                  fontWeight: FontWeight.w700,
                  color: AppColors.textPrimary,
                ),
              ),
              const SizedBox(height: 12),

              Container(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                decoration: BoxDecoration(
                  color: AppColors.surface,
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: AppColors.critical.withValues(alpha: 0.3)),
                ),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    const Icon(Icons.alarm, color: AppColors.critical, size: 18),
                    const SizedBox(width: 8),
                    Text(
                      '5 MINUTES LEFT',
                      style: AppTheme.countdown(
                        fontSize: 16,
                        color: AppColors.critical,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 12),

              Text(
                widget.event.message,
                textAlign: TextAlign.center,
                style: AppTheme.body(
                  fontSize: 13,
                  color: AppColors.textSecondary,
                ),
              ),
              const SizedBox(height: 24),

              // Action Buttons
              SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  onPressed: () {
                    Navigator.of(context).pop();
                    widget.onOpenEmail();
                  },
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AppColors.warmBeige,
                    foregroundColor: AppColors.textDark,
                    padding: const EdgeInsets.symmetric(vertical: 14),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(14),
                    ),
                    elevation: 4,
                  ),
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      const Icon(Icons.open_in_new, size: 18),
                      const SizedBox(width: 8),
                      Text(
                        'OPEN EMAIL',
                        style: AppTheme.heading(fontSize: 14, color: AppColors.textDark),
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 10),

              Row(
                children: [
                  Expanded(
                    child: OutlinedButton(
                      onPressed: () {
                        Navigator.of(context).pop();
                        widget.onSnoozeBriefly();
                      },
                      style: OutlinedButton.styleFrom(
                        foregroundColor: AppColors.textSecondary,
                        side: const BorderSide(color: AppColors.mutedSlate),
                        padding: const EdgeInsets.symmetric(vertical: 12),
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(12),
                        ),
                      ),
                      child: Text(
                        'SNOOZE 10M',
                        style: AppTheme.label(fontSize: 11, color: AppColors.textSecondary),
                      ),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: ElevatedButton(
                      onPressed: () {
                        Navigator.of(context).pop();
                        widget.onMarkComplete();
                      },
                      style: ElevatedButton.styleFrom(
                        backgroundColor: AppColors.surfaceElevated,
                        foregroundColor: AppColors.success,
                        padding: const EdgeInsets.symmetric(vertical: 12),
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(12),
                          side: const BorderSide(color: AppColors.success),
                        ),
                      ),
                      child: Row(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          const Icon(Icons.check, size: 16, color: AppColors.success),
                          const SizedBox(width: 4),
                          Text(
                            'MARK DONE',
                            style: AppTheme.label(fontSize: 11, color: AppColors.success),
                          ),
                        ],
                      ),
                    ),
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
