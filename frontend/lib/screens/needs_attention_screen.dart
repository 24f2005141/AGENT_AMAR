import 'package:flutter/material.dart';
import '../state/inbox_controller.dart';
import '../theme/app_theme.dart';
import '../widgets/attention_email_card.dart';
import '../widgets/reminder_bottom_sheet.dart';
import '../widgets/snooze_bottom_sheet.dart';
import 'email_detail_screen.dart';

class NeedsAttentionScreen extends StatefulWidget {
  final InboxController controller;

  const NeedsAttentionScreen({
    super.key,
    required this.controller,
  });

  @override
  State<NeedsAttentionScreen> createState() => _NeedsAttentionScreenState();
}

class _NeedsAttentionScreenState extends State<NeedsAttentionScreen> {
  bool _showCompleted = false;

  @override
  Widget build(BuildContext context) {
    final allActionEmails = widget.controller.allEmails.where((e) => e.analysis.actionRequired).toList();
    final pendingItems = allActionEmails.where((e) => !e.userState.isCompleted).toList();
    final completedItems = allActionEmails.where((e) => e.userState.isCompleted).toList();

    final displayList = _showCompleted ? completedItems : pendingItems;

    return Scaffold(
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'NEEDS ATTENTION',
              style: AppTheme.brandTitle(fontSize: 18, fontWeight: FontWeight.bold),
            ),
            Text(
              'Autonomous action triage by AMAR',
              style: AppTheme.label(fontSize: 10, color: AppColors.textMuted),
            ),
          ],
        ),
      ),
      body: RefreshIndicator(
        onRefresh: widget.controller.loadData,
        color: AppColors.warmBeige,
        backgroundColor: AppColors.surface,
        child: Column(
          children: [
            // Filter Toggle: Pending vs Completed
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
              child: Row(
                children: [
                  Expanded(
                    child: InkWell(
                      onTap: () => setState(() => _showCompleted = false),
                      borderRadius: BorderRadius.circular(10),
                      child: Container(
                        padding: const EdgeInsets.symmetric(vertical: 8),
                        decoration: BoxDecoration(
                          color: !_showCompleted
                              ? AppColors.warmBeige
                              : AppColors.surfaceCard,
                          borderRadius: BorderRadius.circular(10),
                          border: Border.all(
                            color: !_showCompleted ? AppColors.warmBeige : AppColors.border,
                          ),
                        ),
                        child: Center(
                          child: Text(
                            'PENDING (${pendingItems.length})',
                            style: AppTheme.label(
                              fontSize: 11,
                              fontWeight: FontWeight.bold,
                              color: !_showCompleted ? AppColors.textDark : AppColors.textSecondary,
                            ),
                          ),
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: InkWell(
                      onTap: () => setState(() => _showCompleted = true),
                      borderRadius: BorderRadius.circular(10),
                      child: Container(
                        padding: const EdgeInsets.symmetric(vertical: 8),
                        decoration: BoxDecoration(
                          color: _showCompleted
                              ? AppColors.secondarySurface
                              : AppColors.surfaceCard,
                          borderRadius: BorderRadius.circular(10),
                          border: Border.all(
                            color: _showCompleted ? AppColors.warmBeige : AppColors.border,
                          ),
                        ),
                        child: Center(
                          child: Text(
                            'COMPLETED (${completedItems.length})',
                            style: AppTheme.label(
                              fontSize: 11,
                              fontWeight: FontWeight.bold,
                              color: _showCompleted ? AppColors.textPrimary : AppColors.textSecondary,
                            ),
                          ),
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),

            // List of Action Items
            Expanded(
              child: displayList.isEmpty
                  ? Center(
                      child: Padding(
                        padding: const EdgeInsets.all(24),
                        child: Column(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            Icon(
                              _showCompleted ? Icons.check_circle_outline : Icons.task_alt,
                              size: 48,
                              color: AppColors.mutedSlate,
                            ),
                            const SizedBox(height: 12),
                            Text(
                              _showCompleted
                                  ? 'No completed tasks yet.'
                                  : 'All caught up! No urgent tasks pending.',
                              style: AppTheme.heading(fontSize: 15, color: AppColors.textSecondary),
                            ),
                            const SizedBox(height: 6),
                            Text(
                              'AMAR is actively monitoring incoming mail for deadlines.',
                              textAlign: TextAlign.center,
                              style: AppTheme.body(fontSize: 12, color: AppColors.textMuted),
                            ),
                          ],
                        ),
                      ),
                    )
                  : ListView.builder(
                      padding: const EdgeInsets.all(16),
                      itemCount: displayList.length,
                      itemBuilder: (context, index) {
                        final email = displayList[index];
                        return AttentionEmailCard(
                          email: email,
                          isFeatured: email.isCritical && !_showCompleted,
                          onOpen: () {
                            widget.controller.markViewed(email.id);
                            Navigator.push(
                              context,
                              MaterialPageRoute(
                                builder: (_) => EmailDetailScreen(
                                  email: email,
                                  controller: widget.controller,
                                ),
                              ),
                            );
                          },
                          onMarkComplete: () => widget.controller.completeAction(email.id),
                          onRemindMe: () => ReminderBottomSheet.show(
                            context,
                            email: email,
                            onSetReminder: (time, note) => widget.controller.createReminder(
                              emailId: email.id,
                              reminderAt: time,
                              note: note,
                            ),
                          ),
                          onSnooze: () => SnoozeBottomSheet.show(
                            context,
                            email: email,
                            onSnooze: (until) => widget.controller.snoozeEmail(email.id, until),
                          ),
                        );
                      },
                    ),
            ),
          ],
        ),
      ),
    );
  }
}
