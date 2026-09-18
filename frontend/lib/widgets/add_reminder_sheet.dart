import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import '../models/email.dart';
import '../services/local_schedule_service.dart';
import '../state/inbox_controller.dart';
import '../theme/app_theme.dart';

/// Manually schedule a reminder from the Reminders tab — no need to open an
/// email first. Picks one of the user's emails (optional — the picker just
/// narrows what the reminder is "about") and a time, then schedules it
/// through [LocalScheduleService]: a device-local OS notification, no
/// backend call, fires even if Sorted is closed or the backend is offline.
class AddReminderSheet extends StatefulWidget {
  final InboxController controller;

  const AddReminderSheet({super.key, required this.controller});

  static Future<void> show(
    BuildContext context, {
    required InboxController controller,
  }) {
    return showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (context) => AddReminderSheet(controller: controller),
    );
  }

  @override
  State<AddReminderSheet> createState() => _AddReminderSheetState();
}

class _AddReminderSheetState extends State<AddReminderSheet> {
  final TextEditingController _searchController = TextEditingController();
  final TextEditingController _noteController = TextEditingController();
  Email? _selectedEmail;
  DateTime? _selectedTime;
  bool _isSubmitting = false;
  bool _isSuccess = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    // Reminders can be attached to a resolved / already-acknowledged email too
    // — pull the history list in alongside the active feed for the picker.
    widget.controller.loadResolvedEmails();
  }

  @override
  void dispose() {
    _searchController.dispose();
    _noteController.dispose();
    super.dispose();
  }

  List<Email> _pool() {
    final byId = <String, Email>{};
    for (final e in widget.controller.allEmails) {
      byId[e.id] = e;
    }
    for (final e in widget.controller.resolvedEmails) {
      byId.putIfAbsent(e.id, () => e);
    }
    final list = byId.values.toList()
      ..sort((a, b) => b.receivedAt.compareTo(a.receivedAt));
    final query = _searchController.text.trim().toLowerCase();
    if (query.isEmpty) return list;
    return list
        .where((e) =>
            e.subject.toLowerCase().contains(query) ||
            e.senderName.toLowerCase().contains(query))
        .toList();
  }

  Future<void> _pickCustomDateTime() async {
    final now = DateTime.now();
    final date = await showDatePicker(
      context: context,
      initialDate: now,
      firstDate: now,
      lastDate: now.add(const Duration(days: 60)),
      builder: (context, child) => Theme(
        data: ThemeData.dark().copyWith(
          colorScheme: const ColorScheme.dark(
            primary: AppColors.warmBeige,
            onPrimary: AppColors.textDark,
            surface: AppColors.surface,
            onSurface: AppColors.textPrimary,
          ),
        ),
        child: child!,
      ),
    );
    if (date == null || !mounted) return;

    final time = await showTimePicker(
      context: context,
      initialTime: TimeOfDay.fromDateTime(now.add(const Duration(hours: 1))),
      builder: (context, child) => Theme(
        data: ThemeData.dark().copyWith(
          colorScheme: const ColorScheme.dark(
            primary: AppColors.warmBeige,
            onPrimary: AppColors.textDark,
            surface: AppColors.surface,
            onSurface: AppColors.textPrimary,
          ),
        ),
        child: child!,
      ),
    );
    if (time == null || !mounted) return;

    final picked = DateTime(date.year, date.month, date.day, time.hour, time.minute);
    if (picked.isBefore(DateTime.now())) {
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('Please choose a future time')));
      return;
    }
    setState(() => _selectedTime = picked);
  }

  void _quickTime(Duration duration) {
    setState(() => _selectedTime = DateTime.now().add(duration));
  }

  void _quickSpecific(int hour, int minute, {bool nextDay = false}) {
    final now = DateTime.now();
    DateTime target = DateTime(now.year, now.month, now.day, hour, minute);
    if (nextDay || target.isBefore(now)) target = target.add(const Duration(days: 1));
    setState(() => _selectedTime = target);
  }

  Future<void> _submit() async {
    final email = _selectedEmail;
    final time = _selectedTime;
    if (email == null || time == null) return;
    setState(() {
      _isSubmitting = true;
      _error = null;
    });
    try {
      final note = _noteController.text.trim();
      await LocalScheduleService().createReminder(
        emailId: email.id,
        label: note.isNotEmpty ? note : email.subject,
        scheduledAt: time,
      );
      if (!mounted) return;
      setState(() {
        _isSubmitting = false;
        _isSuccess = true;
      });
      Future.delayed(const Duration(milliseconds: 1400), () {
        if (mounted) Navigator.pop(context);
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _isSubmitting = false;
        _error = 'Could not schedule the reminder. Please try again.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      constraints: BoxConstraints(maxHeight: MediaQuery.of(context).size.height * 0.85),
      padding: EdgeInsets.only(
        left: 20,
        right: 20,
        top: 20,
        bottom: MediaQuery.of(context).viewInsets.bottom + 28,
      ),
      decoration: const BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
        border: Border(top: BorderSide(color: AppColors.border, width: 1)),
      ),
      child: AnimatedBuilder(
        animation: widget.controller,
        builder: (context, _) => _isSuccess ? _buildSuccessView() : _buildForm(),
      ),
    );
  }

  Widget _buildForm() {
    final pool = _pool();
    return SingleChildScrollView(
      key: const Key('addReminderScroll'),
      child: Column(
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
          Text('ADD REMINDER',
              style: AppTheme.heading(fontSize: 18, fontWeight: FontWeight.bold)),
          const SizedBox(height: 4),
          Text(
            'Schedule a nudge for one of your emails.',
            style: AppTheme.body(fontSize: 12, color: AppColors.textMuted),
          ),
          const SizedBox(height: 16),

          Text('FOR EMAIL', style: AppTheme.label(fontSize: 11, color: AppColors.textMuted)),
          const SizedBox(height: 6),
          if (_selectedEmail != null)
            _buildSelectedEmailChip(_selectedEmail!)
          else ...[
            TextField(
              controller: _searchController,
              onChanged: (_) => setState(() {}),
              style: AppTheme.body(fontSize: 13, color: AppColors.textPrimary),
              decoration: InputDecoration(
                hintText: 'Search by subject or sender…',
                hintStyle: AppTheme.body(fontSize: 12, color: AppColors.textMuted),
                prefixIcon: const Icon(Icons.search, size: 18, color: AppColors.textMuted),
                filled: true,
                fillColor: AppColors.surfaceCard,
                contentPadding: const EdgeInsets.symmetric(vertical: 10),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(12),
                  borderSide: const BorderSide(color: AppColors.border),
                ),
              ),
            ),
            const SizedBox(height: 8),
            if (pool.isEmpty)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 12),
                child: Text(
                  'No emails found to attach a reminder to.',
                  style: AppTheme.body(fontSize: 12, color: AppColors.textMuted),
                ),
              )
            else
              ConstrainedBox(
                constraints: const BoxConstraints(maxHeight: 220),
                child: ListView.builder(
                  shrinkWrap: true,
                  itemCount: pool.length,
                  itemBuilder: (context, i) => _buildEmailTile(pool[i]),
                ),
              ),
          ],
          const SizedBox(height: 16),

          Text('WHEN', style: AppTheme.label(fontSize: 11, color: AppColors.textMuted)),
          const SizedBox(height: 6),
          _buildTimeTile(
            icon: Icons.timer_outlined,
            title: 'In 30 minutes',
            subtitle: DateFormat('hh:mm a').format(DateTime.now().add(const Duration(minutes: 30))),
            onTap: () => _quickTime(const Duration(minutes: 30)),
            selected: false,
          ),
          _buildTimeTile(
            icon: Icons.hourglass_top_outlined,
            title: 'In 1 hour',
            subtitle: DateFormat('hh:mm a').format(DateTime.now().add(const Duration(hours: 1))),
            onTap: () => _quickTime(const Duration(hours: 1)),
            selected: false,
          ),
          _buildTimeTile(
            icon: Icons.wb_sunny_outlined,
            title: 'Tomorrow morning',
            subtitle: '9:00 AM',
            onTap: () => _quickSpecific(9, 0, nextDay: true),
            selected: false,
          ),
          _buildTimeTile(
            icon: Icons.event_available_outlined,
            title: _selectedTime != null
                ? DateFormat('EEE, MMM d · hh:mm a').format(_selectedTime!)
                : 'Pick date & time…',
            subtitle: _selectedTime != null ? 'Custom time selected' : 'Choose an exact schedule',
            onTap: _pickCustomDateTime,
            selected: _selectedTime != null,
          ),
          const SizedBox(height: 16),

          Text('NOTE (OPTIONAL)', style: AppTheme.label(fontSize: 11, color: AppColors.textMuted)),
          const SizedBox(height: 6),
          TextField(
            controller: _noteController,
            maxLength: 200,
            maxLines: 2,
            style: AppTheme.body(fontSize: 13, color: AppColors.textPrimary),
            decoration: InputDecoration(
              hintText: 'What should this reminder tell you?',
              hintStyle: AppTheme.body(fontSize: 12, color: AppColors.textMuted),
              filled: true,
              fillColor: AppColors.surfaceCard,
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(12),
                borderSide: const BorderSide(color: AppColors.border),
              ),
            ),
          ),

          if (_error != null) ...[
            const SizedBox(height: 8),
            Text(_error!, style: AppTheme.body(fontSize: 12, color: AppColors.critical)),
          ],

          const SizedBox(height: 16),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton(
              onPressed: (_selectedEmail != null && _selectedTime != null && !_isSubmitting)
                  ? _submit
                  : null,
              style: ElevatedButton.styleFrom(
                backgroundColor: AppColors.warmBeige,
                foregroundColor: AppColors.textDark,
                disabledBackgroundColor: AppColors.surfaceCard,
                padding: const EdgeInsets.symmetric(vertical: 14),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
              ),
              child: _isSubmitting
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2, color: AppColors.textDark),
                    )
                  : Text('SET REMINDER',
                      style: AppTheme.heading(fontSize: 14, color: AppColors.textDark)),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildSelectedEmailChip(Email email) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: AppColors.warmBeige.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.warmBeige, width: 1.2),
      ),
      child: Row(
        children: [
          const Icon(Icons.mail_outline, size: 16, color: AppColors.warmBeige),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(email.subject,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: AppTheme.bodyMedium(fontSize: 13, color: AppColors.textPrimary)),
                Text(email.senderName,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: AppTheme.label(fontSize: 10, color: AppColors.textMuted)),
              ],
            ),
          ),
          TextButton(
            onPressed: () => setState(() => _selectedEmail = null),
            child: Text('Change',
                style: AppTheme.label(fontSize: 11, color: AppColors.warmBeige)),
          ),
        ],
      ),
    );
  }

  Widget _buildEmailTile(Email email) {
    return InkWell(
      onTap: () => setState(() => _selectedEmail = email),
      borderRadius: BorderRadius.circular(10),
      child: Container(
        margin: const EdgeInsets.only(bottom: 6),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
        decoration: BoxDecoration(
          color: AppColors.surfaceCard,
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: AppColors.border),
        ),
        child: Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(email.subject,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: AppTheme.bodyMedium(fontSize: 13, color: AppColors.textPrimary)),
                  Text(email.senderName,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: AppTheme.label(fontSize: 10, color: AppColors.textMuted)),
                ],
              ),
            ),
            Text(DateFormat('MMM d').format(email.receivedAt),
                style: AppTheme.mono(fontSize: 10, color: AppColors.textMuted)),
          ],
        ),
      ),
    );
  }

  Widget _buildTimeTile({
    required IconData icon,
    required String title,
    required String subtitle,
    required VoidCallback onTap,
    required bool selected,
  }) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: Container(
        margin: const EdgeInsets.only(bottom: 8),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
        decoration: BoxDecoration(
          color: selected ? AppColors.warmBeige.withValues(alpha: 0.12) : AppColors.surfaceCard,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(
            color: selected ? AppColors.warmBeige : AppColors.border,
            width: selected ? 1.5 : 1,
          ),
        ),
        child: Row(
          children: [
            Icon(icon, size: 20, color: selected ? AppColors.warmBeige : AppColors.textSecondary),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title,
                      style: AppTheme.body(
                        fontSize: 14,
                        fontWeight: FontWeight.w600,
                        color: selected ? AppColors.warmBeige : AppColors.textPrimary,
                      )),
                  Text(subtitle, style: AppTheme.label(fontSize: 11, color: AppColors.textMuted)),
                ],
              ),
            ),
            Icon(Icons.chevron_right, size: 18, color: AppColors.textMuted.withValues(alpha: 0.6)),
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
                color: AppColors.success.withValues(alpha: 0.2),
                shape: BoxShape.circle,
                border: Border.all(color: AppColors.success, width: 2),
              ),
              child: const Icon(Icons.check, color: AppColors.success, size: 30),
            ),
            const SizedBox(height: 16),
            Text('Reminder Set!', style: AppTheme.heading(fontSize: 18, color: AppColors.textPrimary)),
            const SizedBox(height: 6),
            if (_selectedTime != null)
              Text(
                DateFormat('EEEE, MMM d · hh:mm a').format(_selectedTime!),
                style: AppTheme.mono(fontSize: 13, color: AppColors.warmBeige),
              ),
          ],
        ),
      ),
    );
  }
}
