import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import '../models/email.dart';
import '../theme/app_theme.dart';

class SnoozeBottomSheet extends StatefulWidget {
  final Email email;
  final Function(DateTime until) onSnooze;

  const SnoozeBottomSheet({
    super.key,
    required this.email,
    required this.onSnooze,
  });

  static Future<void> show(
    BuildContext context, {
    required Email email,
    required Function(DateTime until) onSnooze,
  }) {
    return showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (context) => SnoozeBottomSheet(
        email: email,
        onSnooze: onSnooze,
      ),
    );
  }

  @override
  State<SnoozeBottomSheet> createState() => _SnoozeBottomSheetState();
}

class _SnoozeBottomSheetState extends State<SnoozeBottomSheet> {
  DateTime? _selectedCustomTime;
  bool _isSuccess = false;
  DateTime? _snoozedUntil;

  void _chooseOption(Duration duration) {
    _confirm(DateTime.now().add(duration));
  }

  void _chooseSpecific(int hour, int minute, {bool nextDay = false}) {
    final now = DateTime.now();
    DateTime target = DateTime(now.year, now.month, now.day, hour, minute);
    if (nextDay || target.isBefore(now)) {
      target = target.add(const Duration(days: 1));
    }
    _confirm(target);
  }

  Future<void> _pickCustomDateTime() async {
    final now = DateTime.now();
    final date = await showDatePicker(
      context: context,
      initialDate: now,
      firstDate: now,
      lastDate: now.add(const Duration(days: 30)),
      builder: (context, child) {
        return Theme(
          data: ThemeData.dark().copyWith(
            colorScheme: const ColorScheme.dark(
              primary: AppColors.mutedSlate,
              onPrimary: AppColors.textPrimary,
              surface: AppColors.surface,
              onSurface: AppColors.textPrimary,
            ),
          ),
          child: child!,
        );
      },
    );

    if (date == null) return;
    if (!mounted) return;

    final time = await showTimePicker(
      context: context,
      initialTime: TimeOfDay.fromDateTime(now.add(const Duration(hours: 1))),
      builder: (context, child) {
        return Theme(
          data: ThemeData.dark().copyWith(
            colorScheme: const ColorScheme.dark(
              primary: AppColors.mutedSlate,
              onPrimary: AppColors.textPrimary,
              surface: AppColors.surface,
              onSurface: AppColors.textPrimary,
            ),
          ),
          child: child!,
        );
      },
    );

    if (time == null) return;
    if (!mounted) return;

    final picked = DateTime(date.year, date.month, date.day, time.hour, time.minute);
    if (picked.isBefore(DateTime.now())) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Please choose a future time to snooze until')),
        );
      }
      return;
    }

    setState(() {
      _selectedCustomTime = picked;
    });
  }

  void _confirm(DateTime time) {
    widget.onSnooze(time);
    setState(() {
      _isSuccess = true;
      _snoozedUntil = time;
    });

    Future.delayed(const Duration(milliseconds: 1300), () {
      if (mounted) {
        Navigator.pop(context);
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: EdgeInsets.only(
        left: 20,
        right: 20,
        top: 20,
        bottom: MediaQuery.of(context).viewInsets.bottom + 28,
      ),
      decoration: const BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
        border: Border(
          top: BorderSide(color: AppColors.border, width: 1),
        ),
      ),
      child: AnimatedCrossFade(
        duration: const Duration(milliseconds: 250),
        crossFadeState: _isSuccess ? CrossFadeState.showSecond : CrossFadeState.showFirst,
        firstChild: _buildSelectionView(),
        secondChild: _buildSuccessView(),
      ),
    );
  }

  Widget _buildSelectionView() {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Center(
          child: Container(
            width: 36,
            height: 4,
            margin: const EdgeInsets.only(bottom: 16),
            decoration: BoxDecoration(
              color: AppColors.mutedSlate.withValues(alpha: 0.5),
              borderRadius: BorderRadius.circular(2),
            ),
          ),
        ),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(
              'SNOOZE EMAIL',
              style: AppTheme.heading(fontSize: 18, fontWeight: FontWeight.bold),
            ),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
              decoration: BoxDecoration(
                color: AppColors.mutedSlate.withValues(alpha: 0.25),
                borderRadius: BorderRadius.circular(12),
              ),
              child: Text(
                'SUPPRESS ALERTS',
                style: AppTheme.mono(fontSize: 9, color: AppColors.textSecondary),
              ),
            ),
          ],
        ),
        const SizedBox(height: 4),
        Text(
          'Temporarily pauses notifications & attention badges until selected time.',
          style: AppTheme.label(fontSize: 12, color: AppColors.textMuted),
        ),
        const SizedBox(height: 16),
        if (widget.email.isSnoozed) ...[
          Container(
            padding: const EdgeInsets.all(12),
            margin: const EdgeInsets.only(bottom: 14),
            decoration: BoxDecoration(
              color: AppColors.secondarySurface.withValues(alpha: 0.4),
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: AppColors.mutedSlate.withValues(alpha: 0.4)),
            ),
            child: Row(
              children: [
                const Icon(Icons.snooze, color: AppColors.textSecondary, size: 20),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    'Currently snoozed until ${DateFormat('hh:mm a').format(widget.email.userState.snoozedUntil!)}',
                    style: AppTheme.mono(fontSize: 12, color: AppColors.textPrimary),
                  ),
                ),
              ],
            ),
          ),
        ],
        _buildOptionTile(
          icon: Icons.snooze_outlined,
          title: '30 minutes',
          subtitle: DateFormat('hh:mm a').format(DateTime.now().add(const Duration(minutes: 30))),
          onTap: () => _chooseOption(const Duration(minutes: 30)),
        ),
        _buildOptionTile(
          icon: Icons.hourglass_empty,
          title: '1 hour',
          subtitle: DateFormat('hh:mm a').format(DateTime.now().add(const Duration(hours: 1))),
          onTap: () => _chooseOption(const Duration(hours: 1)),
        ),
        _buildOptionTile(
          icon: Icons.brightness_4_outlined,
          title: 'Later today',
          subtitle: '6:00 PM',
          onTap: () => _chooseSpecific(18, 0),
        ),
        _buildOptionTile(
          icon: Icons.next_plan_outlined,
          title: 'Tomorrow',
          subtitle: '9:00 AM',
          onTap: () => _chooseSpecific(9, 0, nextDay: true),
        ),
        _buildOptionTile(
          icon: Icons.edit_calendar_outlined,
          title: _selectedCustomTime != null
              ? DateFormat('EEE, MMM d · hh:mm a').format(_selectedCustomTime!)
              : 'Pick date & time...',
          subtitle: _selectedCustomTime != null ? 'Custom snooze active' : 'Select custom snooze expiry',
          isHighlighted: _selectedCustomTime != null,
          onTap: _pickCustomDateTime,
        ),
        if (_selectedCustomTime != null) ...[
          const SizedBox(height: 12),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton(
              onPressed: () => _confirm(_selectedCustomTime!),
              style: ElevatedButton.styleFrom(
                backgroundColor: AppColors.secondarySurface,
                foregroundColor: AppColors.textPrimary,
                padding: const EdgeInsets.symmetric(vertical: 14),
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(12),
                  side: const BorderSide(color: AppColors.mutedSlate),
                ),
              ),
              child: Text(
                'SNOOZE UNTIL SELECTED TIME',
                style: AppTheme.heading(fontSize: 13, color: AppColors.textPrimary),
              ),
            ),
          ),
        ],
      ],
    );
  }

  Widget _buildOptionTile({
    required IconData icon,
    required String title,
    required String subtitle,
    required VoidCallback onTap,
    bool isHighlighted = false,
  }) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: Container(
        margin: const EdgeInsets.only(bottom: 8),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
        decoration: BoxDecoration(
          color: isHighlighted
              ? AppColors.secondarySurface
              : AppColors.surfaceCard,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(
            color: isHighlighted ? AppColors.warmBeige : AppColors.border,
            width: isHighlighted ? 1.5 : 1,
          ),
        ),
        child: Row(
          children: [
            Icon(
              icon,
              size: 20,
              color: isHighlighted ? AppColors.warmBeige : AppColors.textSecondary,
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    title,
                    style: AppTheme.body(
                      fontSize: 14,
                      fontWeight: FontWeight.w600,
                      color: isHighlighted ? AppColors.warmBeige : AppColors.textPrimary,
                    ),
                  ),
                  Text(
                    subtitle,
                    style: AppTheme.label(fontSize: 11, color: AppColors.textMuted),
                  ),
                ],
              ),
            ),
            Icon(
              Icons.chevron_right,
              size: 18,
              color: AppColors.textMuted.withValues(alpha: 0.6),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildSuccessView() {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 24),
      child: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 56,
              height: 56,
              decoration: BoxDecoration(
                color: AppColors.mutedSlate.withValues(alpha: 0.3),
                shape: BoxShape.circle,
                border: Border.all(color: AppColors.mutedSlate, width: 2),
              ),
              child: const Icon(Icons.snooze, color: AppColors.textPrimary, size: 30),
            ),
            const SizedBox(height: 16),
            Text(
              'Email Snoozed',
              style: AppTheme.heading(fontSize: 18, color: AppColors.textPrimary),
            ),
            const SizedBox(height: 6),
            if (_snoozedUntil != null)
              Text(
                'Alerts paused until ${DateFormat('hh:mm a, EEE').format(_snoozedUntil!)}',
                style: AppTheme.mono(fontSize: 13, color: AppColors.textSecondary),
              ),
          ],
        ),
      ),
    );
  }
}
