import 'package:flutter/material.dart';
import '../models/email.dart';
import '../state/inbox_controller.dart';
import '../theme/app_theme.dart';
import '../widgets/deadline_card.dart';
import 'email_detail_screen.dart';

class DeadlinesScreen extends StatelessWidget {
  final InboxController controller;

  const DeadlinesScreen({
    super.key,
    required this.controller,
  });

  @override
  Widget build(BuildContext context) {
    final now = DateTime.now();
    final todayEnd = DateTime(now.year, now.month, now.day, 23, 59, 59);
    final tomorrowEnd = DateTime(now.year, now.month, now.day + 1, 23, 59, 59);
    final weekEnd = now.add(const Duration(days: 7));

    final allDeadlines = controller.deadlineEmails;

    final todayItems = allDeadlines.where((e) {
      final d = e.analysis.deadline!;
      return d.isBefore(todayEnd);
    }).toList();

    final tomorrowItems = allDeadlines.where((e) {
      final d = e.analysis.deadline!;
      return d.isAfter(todayEnd) && d.isBefore(tomorrowEnd);
    }).toList();

    final thisWeekItems = allDeadlines.where((e) {
      final d = e.analysis.deadline!;
      return d.isAfter(tomorrowEnd) && d.isBefore(weekEnd);
    }).toList();

    final laterItems = allDeadlines.where((e) {
      final d = e.analysis.deadline!;
      return d.isAfter(weekEnd);
    }).toList();

    return Scaffold(
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'DEADLINES',
              style: AppTheme.brandTitle(fontSize: 18, fontWeight: FontWeight.bold),
            ),
            Text(
              'Chronological commitment monitor',
              style: AppTheme.label(fontSize: 10, color: AppColors.textMuted),
            ),
          ],
        ),
      ),
      body: RefreshIndicator(
        onRefresh: controller.loadData,
        color: AppColors.warmBeige,
        backgroundColor: AppColors.surface,
        child: allDeadlines.isEmpty
            ? Center(
                child: Text(
                  'No active deadlines detected in inbox.',
                  style: AppTheme.body(fontSize: 14, color: AppColors.textMuted),
                ),
              )
            : ListView(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                children: [
                  if (todayItems.isNotEmpty) ...[
                    _buildSectionHeader('TODAY', todayItems.length, isUrgent: true),
                    const SizedBox(height: 8),
                    ...todayItems.map((e) => DeadlineCard(
                          email: e,
                          onOpen: () => _openEmail(context, e),
                          onMarkDone: () => controller.completeAction(e.id),
                        )),
                    const SizedBox(height: 16),
                  ],
                  if (tomorrowItems.isNotEmpty) ...[
                    _buildSectionHeader('TOMORROW', tomorrowItems.length),
                    const SizedBox(height: 8),
                    ...tomorrowItems.map((e) => DeadlineCard(
                          email: e,
                          onOpen: () => _openEmail(context, e),
                          onMarkDone: () => controller.completeAction(e.id),
                        )),
                    const SizedBox(height: 16),
                  ],
                  if (thisWeekItems.isNotEmpty) ...[
                    _buildSectionHeader('THIS WEEK', thisWeekItems.length),
                    const SizedBox(height: 8),
                    ...thisWeekItems.map((e) => DeadlineCard(
                          email: e,
                          onOpen: () => _openEmail(context, e),
                          onMarkDone: () => controller.completeAction(e.id),
                        )),
                    const SizedBox(height: 16),
                  ],
                  if (laterItems.isNotEmpty) ...[
                    _buildSectionHeader('LATER', laterItems.length),
                    const SizedBox(height: 8),
                    ...laterItems.map((e) => DeadlineCard(
                          email: e,
                          onOpen: () => _openEmail(context, e),
                          onMarkDone: () => controller.completeAction(e.id),
                        )),
                    const SizedBox(height: 16),
                  ],
                ],
              ),
      ),
    );
  }

  Widget _buildSectionHeader(String title, int count, {bool isUrgent = false}) {
    return Row(
      children: [
        if (isUrgent)
          Container(
            width: 8,
            height: 8,
            margin: const EdgeInsets.only(right: 6),
            decoration: const BoxDecoration(
              color: AppColors.critical,
              shape: BoxShape.circle,
            ),
          ),
        Text(
          title,
          style: AppTheme.heading(
            fontSize: 13,
            fontWeight: FontWeight.bold,
            color: isUrgent ? AppColors.critical : AppColors.textSecondary,
          ),
        ),
        const SizedBox(width: 6),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
          decoration: BoxDecoration(
            color: isUrgent ? AppColors.critical.withValues(alpha: 0.2) : AppColors.surfaceCard,
            borderRadius: BorderRadius.circular(4),
          ),
          child: Text(
            '$count',
            style: AppTheme.mono(
              fontSize: 10,
              fontWeight: FontWeight.bold,
              color: isUrgent ? AppColors.critical : AppColors.textMuted,
            ),
          ),
        ),
      ],
    );
  }

  void _openEmail(BuildContext context, Email email) {
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
