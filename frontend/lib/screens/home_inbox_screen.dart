import 'package:flutter/material.dart';
import '../models/email.dart';
import '../services/local_schedule_service.dart';
import '../state/auth_controller.dart';
import '../state/inbox_controller.dart';
import '../theme/app_theme.dart';
import '../theme/responsive.dart';
import 'profile_screen.dart';
import '../widgets/alarm_dialog.dart';
import '../widgets/attention_email_card.dart';
import '../widgets/countdown_timer_view.dart';
import '../widgets/email_list_card.dart';
import '../widgets/pulsing_ai_badge.dart';
import '../widgets/reminder_bottom_sheet.dart';
import '../widgets/snooze_bottom_sheet.dart';
import '../widgets/system_status_bar.dart';
import 'email_detail_screen.dart';

class HomeInboxScreen extends StatelessWidget {
  final InboxController controller;
  final AuthController? authController;
  final Function(int tabIndex)? onNavigateTab;

  const HomeInboxScreen({
    super.key,
    required this.controller,
    this.authController,
    this.onNavigateTab,
  });

  Future<void> _reconnectGmail(BuildContext context) async {
    final auth = authController;
    if (auth == null) return;
    await auth.reconnectGmail();
    await controller.checkGmailStatus();
    if (context.mounted && auth.errorMessage != null) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(auth.errorMessage!)),
      );
    }
  }

  Future<void> _clearResolved(BuildContext context) async {
    final messenger = ScaffoldMessenger.of(context);
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Clear Resolved'),
        content: const Text(
          'This removes completed and acknowledged items from your Sorted '
          'inbox. Your Gmail emails are not deleted.\n\n'
          'Unresolved Action Required and Reply Required items are kept.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Clear'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    final n = await controller.clearAcknowledged();
    messenger.showSnackBar(SnackBar(
      content: Text(n > 0
          ? 'Cleared $n item${n == 1 ? '' : 's'} from your dashboard.'
          : 'Nothing to clear — your dashboard is already tidy.'),
    ));
  }

  void _openProfile(BuildContext context) {
    final auth = authController;
    if (auth == null) return;
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => ProfileScreen(
          authController: auth,
          inboxController: controller,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final attentionList = controller.needsAttentionEmails;
    final deadlineList = controller.deadlineEmails;
    final recentList = controller.emails;

    // The homepage is an attention dashboard, not an inbox: when nothing needs
    // the user right now, show one clean "all caught up" state instead of a
    // column of empty section cards.
    final allCaughtUp = !controller.isLoading &&
        controller.isGmailConnected &&
        controller.currentFilter == 'all' &&
        attentionList.isEmpty &&
        deadlineList.isEmpty &&
        recentList.isEmpty;

    return Scaffold(
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text(
                  'Sorted',
                  style: AppTheme.brandTitle(
                    fontSize: 20,
                    fontWeight: FontWeight.w900,
                  ),
                ),
                const SizedBox(width: Gap.sm),
                // The badge label ("Connect Gmail") is the longest thing in
                // the bar; it must yield before it pushes the title out.
                Flexible(
                  child: PulsingAiBadge(
                  label: !controller.isGmailConnected
                      ? 'Connect Gmail'
                      : controller.gmailMonitoringActive
                          ? 'AI Online'
                          : 'Connecting…',
                  color: !controller.isGmailConnected
                      ? AppColors.high
                      : controller.gmailMonitoringActive
                          ? AppColors.success
                          : AppColors.warmBeige,
                  ),
                ),
              ],
            ),
            Text(
              controller.connectedAccountEmail != null
                  ? controller.connectedAccountEmail!
                  : 'Your attention, organized.',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              softWrap: false,
              style: AppTheme.label(fontSize: 10, color: AppColors.textMuted),
            ),
          ],
        ),
        actions: [
          // Manual Monitor Check & Alarm Trigger
          IconButton(
            onPressed: () async {
              await controller.triggerMonitorCheck();
              if (controller.activeAlarm != null && context.mounted) {
                AlarmDialog.show(
                  context,
                  event: controller.activeAlarm!,
                  onOpenEmail: () {
                    final email = controller.allEmails.where(
                      (e) => e.id == controller.activeAlarm!.emailId,
                    ).firstOrNull ?? controller.allEmails.firstOrNull;

                    if (email != null) {
                      Navigator.push(
                        context,
                        MaterialPageRoute(
                          builder: (_) => EmailDetailScreen(
                            email: email,
                            controller: controller,
                          ),
                        ),
                      );
                    }
                  },
                  onSnoozeBriefly: () {
                    controller.snoozeEmail(
                      controller.activeAlarm!.emailId,
                      DateTime.now().add(const Duration(minutes: 10)),
                    );
                    ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(content: Text('Snoozed alarm for 10 minutes.')),
                    );
                  },
                  onMarkComplete: () {
                    controller.markComplete(controller.activeAlarm!.emailId);
                    ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(content: Text('Marked as done.')),
                    );
                  },
                  onDismiss: () => controller.dismissActiveAlarm(),
                );
              } else if (context.mounted) {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text('Deadline monitor executed. No new alarms.')),
                );
              }
            },
            icon: const Icon(Icons.alarm_on, color: AppColors.critical),
            tooltip: 'Run Deadline Check (FastAPI)',
          ),
          // Clear Resolved — tidy the Sorted dashboard (never touches Gmail).
          IconButton(
            onPressed: () => _clearResolved(context),
            icon: const Icon(Icons.playlist_add_check, color: AppColors.textSecondary),
            tooltip: 'Clear Resolved',
          ),
          if (authController != null)
            IconButton(
              onPressed: () => _openProfile(context),
              icon: const Icon(Icons.account_circle_outlined, color: AppColors.textSecondary),
              tooltip: 'Account',
            ),
        ],
        bottom: PreferredSize(
          preferredSize: Size.fromHeight(SystemStatusBar.preferredHeight(context)),
          child: SystemStatusBar(
            backendOnline: controller.backendOnline,
            llmStatus: controller.llmStatus,
            llmProvider: controller.llmProvider,
            llmModel: controller.llmModel,
          ),
        ),
      ),
      body: RefreshIndicator(
        onRefresh: controller.refreshInbox,
        color: AppColors.warmBeige,
        backgroundColor: AppColors.surface,
        child: ListView(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
          children: [
            // Gmail Connection Warning Banner if not connected
            if (!controller.isGmailConnected) ...[
              Container(
                padding: const EdgeInsets.all(14),
                margin: const EdgeInsets.only(bottom: 12),
                decoration: BoxDecoration(
                  color: AppColors.high.withValues(alpha: 0.15),
                  borderRadius: BorderRadius.circular(14),
                  border: Border.all(color: AppColors.high.withValues(alpha: 0.4)),
                ),
                child: Row(
                  children: [
                    const Icon(Icons.mark_email_unread_outlined, color: AppColors.high, size: 22),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            'Gmail Not Connected',
                            style: AppTheme.heading(fontSize: 13, color: AppColors.high),
                          ),
                          const SizedBox(height: 2),
                          Text(
                            'Connect your Google account to let AMAR ingest and triage incoming emails.',
                            style: AppTheme.body(fontSize: 11, color: AppColors.textSecondary),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(width: 8),
                    ElevatedButton(
                      onPressed: () => _reconnectGmail(context),
                      style: ElevatedButton.styleFrom(
                        backgroundColor: AppColors.high,
                        foregroundColor: AppColors.textDark,
                        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                        minimumSize: Size.zero,
                        tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(8),
                        ),
                      ),
                      child: Text(
                        'RECONNECT',
                        style: AppTheme.label(fontSize: 10, fontWeight: FontWeight.bold, color: AppColors.textDark),
                      ),
                    ),
                  ],
                ),
              ),
            ],

            // Backend Error Banner if error occurred
            if (controller.errorMessage != null) ...[
              Container(
                padding: const EdgeInsets.all(12),
                margin: const EdgeInsets.only(bottom: 12),
                decoration: BoxDecoration(
                  color: AppColors.critical.withValues(alpha: 0.15),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: AppColors.critical.withValues(alpha: 0.4)),
                ),
                child: Row(
                  children: [
                    const Icon(Icons.error_outline, color: AppColors.critical, size: 20),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        controller.errorMessage!,
                        style: AppTheme.body(fontSize: 12, color: AppColors.textPrimary),
                      ),
                    ),
                    TextButton(
                      onPressed: controller.loadData,
                      style: TextButton.styleFrom(
                        foregroundColor: AppColors.warmBeige,
                        padding: const EdgeInsets.symmetric(horizontal: 8),
                      ),
                      child: const Text('RETRY'),
                    ),
                  ],
                ),
              ),
            ],

            // Filter Chips Bar
            _buildFilterChips(),
            const SizedBox(height: 16),

            if (allCaughtUp) _buildCaughtUpState(),

            // SECTION 1: NEEDS ATTENTION
            if (!allCaughtUp &&
                (controller.currentFilter == 'all' ||
                    controller.currentFilter == 'action_required')) ...[
              _buildSectionHeader(
                title: 'NEEDS ATTENTION',
                countBadge: '${attentionList.length} Tasks',
                onSeeAll: () => onNavigateTab?.call(1),
                badgeColor: AppColors.critical,
              ),
              const SizedBox(height: 8),

              if (controller.isLoading && attentionList.isEmpty)
                _buildLoadingCard()
              else if (attentionList.isEmpty)
                _buildEmptyCard('No pending action items right now. Inbox clear!')
              else
                ...attentionList.take(2).map(
                      (email) => AttentionEmailCard(
                        email: email,
                        isFeatured: email.isCritical,
                        onOpen: () => _openEmailDetail(context, email),
                        onMarkComplete: () => controller.markComplete(email.id),
                        onRemindMe: () => ReminderBottomSheet.show(
                          context,
                          email: email,
                          onSetReminder: (time, note) => LocalScheduleService().createReminder(
                            emailId: email.id,
                            label: note?.trim().isNotEmpty == true ? note!.trim() : email.subject,
                            scheduledAt: time,
                          ),
                        ),
                        onSnooze: () => SnoozeBottomSheet.show(
                          context,
                          email: email,
                          onSnooze: (until) => controller.snoozeEmail(email.id, until),
                        ),
                      ),
                    ),
              const SizedBox(height: 20),
            ],

            // SECTION 2: UPCOMING DEADLINES
            if (!allCaughtUp && controller.currentFilter == 'all') ...[
              _buildSectionHeader(
                title: 'UPCOMING DEADLINES',
                countBadge: '${deadlineList.length} Upcoming',
                onSeeAll: () => onNavigateTab?.call(2),
                badgeColor: AppColors.warmBeige,
              ),
              const SizedBox(height: 8),

              if (controller.isLoading && deadlineList.isEmpty)
                _buildLoadingCard()
              else if (deadlineList.isEmpty)
                _buildEmptyCard('No upcoming deadlines detected.')
              else
                ...deadlineList.take(2).map(
                      (email) => Container(
                        margin: const EdgeInsets.only(bottom: 8),
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                          color: AppColors.surfaceCard,
                          borderRadius: BorderRadius.circular(12),
                          border: Border.all(color: AppColors.border),
                        ),
                        child: InkWell(
                          onTap: () => _openEmailDetail(context, email),
                          child: Row(
                            children: [
                              Container(
                                padding: const EdgeInsets.all(8),
                                decoration: BoxDecoration(
                                  color: AppColors.warmBeige.withValues(alpha: 0.15),
                                  shape: BoxShape.circle,
                                ),
                                child: const Icon(Icons.event, color: AppColors.warmBeige, size: 16),
                              ),
                              const SizedBox(width: 12),
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(
                                      email.subject,
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                      style: AppTheme.heading(fontSize: 13),
                                    ),
                                    Text(
                                      email.senderName,
                                      style: AppTheme.bodyMedium(fontSize: 11, color: AppColors.textMuted),
                                    ),
                                  ],
                                ),
                              ),
                              const SizedBox(width: 8),
                              if (email.analysis.deadline != null)
                                CountdownTimerView(deadline: email.analysis.deadline!),
                            ],
                          ),
                        ),
                      ),
                    ),
              const SizedBox(height: 20),
            ],

            // SECTION 3: ACTIVE MAIL — everything still awaiting attention that
            // isn't already a task or a deadline above (mutually exclusive).
            if (!allCaughtUp) ...[
            _buildSectionHeader(
              title: controller.currentFilter == 'all' ? 'ACTIVE INBOX' : 'FILTERED EMAILS',
              countBadge: '${recentList.length} Active',
              badgeColor: AppColors.mutedSlate,
            ),
            const SizedBox(height: 8),

            if (controller.isLoading && recentList.isEmpty)
              _buildLoadingCard()
            else if (recentList.isEmpty)
              _buildEmptyCard("You're all caught up — nothing needs your attention here.")
            else
              ...recentList.map(
                (email) => EmailListCard(
                  email: email,
                  onTap: () => _openEmailDetail(context, email),
                ),
              ),
            ],

            const SizedBox(height: 24),
          ],
        ),
      ),
    );
  }

  Widget _buildFilterChips() {
    final filters = [
      {'id': 'all', 'label': 'All'},
      {'id': 'action_required', 'label': 'Action Required'},
      {'id': 'reply_needed', 'label': 'Reply Needed'},
      {'id': 'important', 'label': 'Important'},
      {'id': 'low_priority', 'label': 'Low Priority'},
    ];

    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Row(
        children: filters.map((f) {
          final isSelected = controller.currentFilter == f['id'];
          return Padding(
            padding: const EdgeInsets.only(right: 8),
            child: ChoiceChip(
              label: Text(f['label']!),
              selected: isSelected,
              onSelected: (_) => controller.setFilter(f['id']!),
              selectedColor: AppColors.warmBeige,
              backgroundColor: AppColors.surfaceCard,
              labelStyle: AppTheme.label(
                fontSize: 11,
                color: isSelected ? AppColors.textDark : AppColors.textSecondary,
                fontWeight: isSelected ? FontWeight.bold : FontWeight.w500,
              ),
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(18),
                side: BorderSide(
                  color: isSelected ? AppColors.warmBeige : AppColors.border,
                ),
              ),
            ),
          );
        }).toList(),
      ),
    );
  }

  Widget _buildSectionHeader({
    required String title,
    required String countBadge,
    VoidCallback? onSeeAll,
    required Color badgeColor,
  }) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Row(
          children: [
            Text(
              title,
              style: AppTheme.heading(
                fontSize: 13,
                fontWeight: FontWeight.bold,
                color: AppColors.textSecondary,
              ),
            ),
            const SizedBox(width: 8),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
              decoration: BoxDecoration(
                color: badgeColor.withValues(alpha: 0.18),
                borderRadius: BorderRadius.circular(6),
              ),
              child: Text(
                countBadge,
                style: AppTheme.mono(fontSize: 9, color: badgeColor, fontWeight: FontWeight.bold),
              ),
            ),
          ],
        ),
        if (onSeeAll != null)
          InkWell(
            onTap: onSeeAll,
            borderRadius: BorderRadius.circular(4),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
              child: Row(
                children: [
                  Text(
                    'VIEW ALL',
                    style: AppTheme.label(fontSize: 10, color: AppColors.warmBeige),
                  ),
                  const Icon(Icons.chevron_right, size: 14, color: AppColors.warmBeige),
                ],
              ),
            ),
          ),
      ],
    );
  }

  Widget _buildCaughtUpState() {
    return Container(
      margin: const EdgeInsets.only(top: 24),
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 36),
      decoration: BoxDecoration(
        color: AppColors.surfaceCard.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: AppColors.borderLight),
      ),
      child: Column(
        children: [
          const Icon(Icons.check_circle_outline, size: 44, color: AppColors.success),
          const SizedBox(height: 14),
          Text(
            "You're all caught up.",
            style: AppTheme.heading(fontSize: 16, color: AppColors.textPrimary),
          ),
          const SizedBox(height: 6),
          Text(
            'Nothing needs your attention right now. New mail and reminders '
            'will appear here automatically.',
            textAlign: TextAlign.center,
            style: AppTheme.body(fontSize: 12, color: AppColors.textMuted),
          ),
        ],
      ),
    );
  }

  Widget _buildLoadingCard() {
    return Container(
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(
        color: AppColors.surfaceCard,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: AppColors.border),
      ),
      child: const Center(
        child: SizedBox(
          width: 24,
          height: 24,
          child: CircularProgressIndicator(strokeWidth: 2, color: AppColors.warmBeige),
        ),
      ),
    );
  }

  Widget _buildEmptyCard(String message) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.surfaceCard.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.borderLight),
      ),
      child: Center(
        child: Text(
          message,
          style: AppTheme.body(fontSize: 12, color: AppColors.textMuted),
        ),
      ),
    );
  }

  void _openEmailDetail(BuildContext context, Email email) {
    controller.markViewed(email.id);
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => EmailDetailScreen(
          email: email,
          controller: controller,
        ),
      ),
    );
  }
}
