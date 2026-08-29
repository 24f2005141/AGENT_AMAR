import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';
import '../config/api_config.dart';
import '../models/email.dart';
import '../state/inbox_controller.dart';
import '../theme/app_theme.dart';
import '../widgets/alarm_dialog.dart';
import '../widgets/attention_email_card.dart';
import '../widgets/countdown_timer_view.dart';
import '../widgets/email_list_card.dart';
import '../widgets/pulsing_ai_badge.dart';
import '../widgets/reminder_bottom_sheet.dart';
import '../widgets/snooze_bottom_sheet.dart';
import 'agent_activity_screen.dart';
import 'email_detail_screen.dart';

class HomeInboxScreen extends StatelessWidget {
  final InboxController controller;
  final Function(int tabIndex)? onNavigateTab;

  const HomeInboxScreen({
    super.key,
    required this.controller,
    this.onNavigateTab,
  });

  Future<void> _launchOAuthLogin(BuildContext context) async {
    final loginUrl = Uri.parse('${ApiConfig.baseUrl}/api/v1/auth/google/login');
    try {
      if (await canLaunchUrl(loginUrl)) {
        await launchUrl(loginUrl, mode: LaunchMode.externalApplication);
      } else {
        if (context.mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('Could not open login URL: $loginUrl')),
          );
        }
      }
    } catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Failed to launch browser: $e')),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final criticalEmail = controller.criticalEmail;
    final attentionList = controller.needsAttentionEmails;
    final deadlineList = controller.deadlineEmails;
    final recentList = controller.emails;

    return Scaffold(
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text(
                  'AGENT AMAR',
                  style: AppTheme.brandTitle(
                    fontSize: 20,
                    fontWeight: FontWeight.w900,
                  ),
                ),
                const SizedBox(width: 8),
                PulsingAiBadge(
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
              ],
            ),
            Text(
              controller.connectedAccountEmail != null
                  ? controller.connectedAccountEmail!
                  : 'Autonomous Inbox Intelligence',
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
                    controller.completeAction(controller.activeAlarm!.emailId);
                    ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(content: Text('Marked action as complete!')),
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
          IconButton(
            onPressed: () {
              Navigator.push(
                context,
                MaterialPageRoute(
                  builder: (_) => AgentActivityScreen(
                    traces: criticalEmail?.analysis.traces ?? [],
                    emailSubject: criticalEmail?.subject ?? 'Autonomous Processing Pipeline',
                    emailId: criticalEmail?.id,
                    controller: controller,
                  ),
                ),
              );
            },
            icon: const Icon(Icons.smart_toy_outlined, color: AppColors.warmBeige),
            tooltip: 'View Agent Activity Trace',
          ),
        ],
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
                      onPressed: () => _launchOAuthLogin(context),
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
                        'CONNECT',
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

            // SECTION 1: NEEDS ATTENTION
            if (controller.currentFilter == 'all' || controller.currentFilter == 'action_required') ...[
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
                        onMarkComplete: () => controller.completeAction(email.id),
                        onRemindMe: () => ReminderBottomSheet.show(
                          context,
                          email: email,
                          onSetReminder: (time, note) => controller.createReminder(
                            emailId: email.id,
                            reminderAt: time,
                            note: note,
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
            if (controller.currentFilter == 'all') ...[
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

            // SECTION 3: RECENT MAIL (Smart categorized feed)
            _buildSectionHeader(
              title: controller.currentFilter == 'all' ? 'RECENT MAIL' : 'FILTERED EMAILS',
              countBadge: '${recentList.length} Total',
              badgeColor: AppColors.mutedSlate,
            ),
            const SizedBox(height: 8),

            if (controller.isLoading && recentList.isEmpty)
              _buildLoadingCard()
            else if (recentList.isEmpty)
              _buildEmptyCard('No emails found in this category. Pull to refresh!')
            else
              ...recentList.map(
                (email) => EmailListCard(
                  email: email,
                  onTap: () => _openEmailDetail(context, email),
                ),
              ),

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
