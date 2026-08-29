import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:url_launcher/url_launcher.dart';
import '../dto/email_state_dto.dart';
import '../dto/mappers/dto_mapper.dart';
import '../models/agent_analysis.dart';
import '../models/email.dart';
import '../state/inbox_controller.dart';
import '../theme/app_theme.dart';
import '../widgets/countdown_timer_view.dart';
import '../widgets/priority_badge.dart';
import '../widgets/reminder_bottom_sheet.dart';
import '../widgets/snooze_bottom_sheet.dart';
import 'agent_activity_screen.dart';

class EmailDetailScreen extends StatefulWidget {
  final Email email;
  final InboxController controller;

  const EmailDetailScreen({
    super.key,
    required this.email,
    required this.controller,
  });

  @override
  State<EmailDetailScreen> createState() => _EmailDetailScreenState();
}

class _EmailDetailScreenState extends State<EmailDetailScreen> {
  late Email _currentEmail;
  EmailStateDetailOutDto? _detailDto;
  bool _isLoadingDetail = false;

  @override
  void initState() {
    super.initState();
    _currentEmail = widget.email;
    _fetchDetail();
  }

  Future<void> _fetchDetail() async {
    setState(() {
      _isLoadingDetail = true;
    });

    try {
      final detail = await widget.controller.getEmailDetail(_currentEmail.id);
      if (detail != null && mounted) {
        setState(() {
          _detailDto = detail;
          _currentEmail = DtoMapper.mapEmailState(detail);
        });
      }
    } catch (_) {
      // Fall back to initial email
    } finally {
      if (mounted) {
        setState(() {
          _isLoadingDetail = false;
        });
      }
    }
  }

  void _refreshEmailState() {
    final updated = widget.controller.allEmails.where(
      (e) => e.id == _currentEmail.id,
    ).firstOrNull;
    if (updated != null) {
      setState(() {
        _currentEmail = updated;
      });
    }
  }

  Future<void> _launchTargetLink(String? url) async {
    if (url == null || url.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('No external portal link attached to this email.')),
      );
      return;
    }
    final uri = Uri.tryParse(url);
    if (uri != null && await canLaunchUrl(uri)) {
      await launchUrl(uri, mode: LaunchMode.externalApplication);
    } else {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Could not open link: $url')),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final analysis = _currentEmail.analysis;
    final isCritical = analysis.priority == PriorityLevel.critical;
    final isCompleted = _currentEmail.userState.isCompleted;
    final isSnoozed = _currentEmail.isSnoozed;
    final firstAction = _detailDto?.actions.firstOrNull;

    return Scaffold(
      appBar: AppBar(
        leading: IconButton(
          icon: const Icon(Icons.arrow_back),
          onPressed: () => Navigator.pop(context),
        ),
        title: Text(
          'EMAIL INTELLIGENCE',
          style: AppTheme.brandTitle(fontSize: 16, fontWeight: FontWeight.bold),
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.smart_toy_outlined, color: AppColors.warmBeige),
            tooltip: 'Agent Processing Trace',
            onPressed: () {
              Navigator.push(
                context,
                MaterialPageRoute(
                  builder: (_) => AgentActivityScreen(
                    traces: analysis.traces,
                    emailSubject: _currentEmail.subject,
                    emailId: _currentEmail.id,
                    controller: widget.controller,
                  ),
                ),
              );
            },
          ),
        ],
      ),
      body: Column(
        children: [
          if (_isLoadingDetail)
            const LinearProgressIndicator(
              minHeight: 2,
              color: AppColors.warmBeige,
              backgroundColor: AppColors.surface,
            ),
          Expanded(
            child: ListView(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
              children: [
                // Sender & Metadata Header
                Container(
                  padding: const EdgeInsets.all(16),
                  decoration: BoxDecoration(
                    color: AppColors.surfaceCard,
                    borderRadius: BorderRadius.circular(16),
                    border: Border.all(color: AppColors.border),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Row(
                            children: [
                              Container(
                                width: 36,
                                height: 36,
                                decoration: BoxDecoration(
                                  color: isCritical
                                      ? AppColors.critical.withValues(alpha: 0.2)
                                      : AppColors.secondarySurface,
                                  borderRadius: BorderRadius.circular(8),
                                ),
                                child: Center(
                                  child: Text(
                                    _getInitials(_currentEmail.senderName),
                                    style: AppTheme.brandTitle(
                                      fontSize: 13,
                                      color: isCritical ? AppColors.critical : AppColors.warmBeige,
                                    ),
                                  ),
                                ),
                              ),
                              const SizedBox(width: 10),
                              Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    _currentEmail.senderName,
                                    style: AppTheme.heading(fontSize: 14),
                                  ),
                                  Text(
                                    _currentEmail.senderEmail,
                                    style: AppTheme.label(fontSize: 11, color: AppColors.textMuted),
                                  ),
                                ],
                              ),
                            ],
                          ),
                          Text(
                            DateFormat('MMM d · hh:mm a').format(_currentEmail.receivedAt),
                            style: AppTheme.mono(fontSize: 11, color: AppColors.textMuted),
                          ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      Text(
                        _currentEmail.subject,
                        style: AppTheme.brandTitle(fontSize: 17, fontWeight: FontWeight.bold),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 14),

                // ==========================================
                // AGENT AMAR ANALYSIS CARD (Core Highlight)
                // ==========================================
                Container(
                  padding: const EdgeInsets.all(16),
                  decoration: BoxDecoration(
                    color: const Color(0xFF1E3A50),
                    borderRadius: BorderRadius.circular(16),
                    border: Border.all(
                      color: isCritical ? AppColors.critical : AppColors.warmBeige,
                      width: 1.5,
                    ),
                    boxShadow: [
                      BoxShadow(
                        color: (isCritical ? AppColors.critical : AppColors.warmBeige).withValues(alpha: 0.12),
                        blurRadius: 18,
                        spreadRadius: 1,
                      ),
                    ],
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      // Analysis Header
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Row(
                            children: [
                              Container(
                                width: 8,
                                height: 8,
                                decoration: const BoxDecoration(
                                  color: AppColors.warmBeige,
                                  shape: BoxShape.circle,
                                ),
                              ),
                              const SizedBox(width: 8),
                              Text(
                                'AGENT AMAR ANALYSIS',
                                style: AppTheme.brandTitle(
                                  fontSize: 14,
                                  fontWeight: FontWeight.bold,
                                  color: AppColors.warmBeige,
                                ),
                              ),
                            ],
                          ),
                          PriorityBadge(priority: analysis.priority),
                        ],
                      ),
                      const SizedBox(height: 12),
                      const Divider(color: AppColors.borderLight, height: 1),
                      const SizedBox(height: 12),

                      // Structured Grid
                      _buildAnalysisRow('CATEGORY', analysis.category),
                      const SizedBox(height: 8),
                      _buildAnalysisRow(
                        'PRIORITY',
                        '${analysis.priority.displayName} (${(analysis.confidence * 100).toInt()}% confidence)',
                        valueColor: isCritical ? AppColors.critical : AppColors.textPrimary,
                      ),
                      const SizedBox(height: 8),

                      if (analysis.deadline != null) ...[
                        Row(
                          mainAxisAlignment: MainAxisAlignment.spaceBetween,
                          children: [
                            Text(
                              'DEADLINE',
                              style: AppTheme.label(fontSize: 11, color: AppColors.textMuted),
                            ),
                            Row(
                              children: [
                                Text(
                                  DateFormat('EEEE · hh:mm a').format(analysis.deadline!),
                                  style: AppTheme.mono(fontSize: 12, color: AppColors.textPrimary),
                                ),
                                const SizedBox(width: 8),
                                CountdownTimerView(deadline: analysis.deadline!),
                              ],
                            ),
                          ],
                        ),
                        const SizedBox(height: 8),
                      ],

                      if (analysis.actionDescription != null) ...[
                        _buildAnalysisRow(
                          'ACTION REQUIRED',
                          analysis.actionDescription!,
                          valueColor: AppColors.warmBeige,
                        ),
                        const SizedBox(height: 8),
                      ],

                      const SizedBox(height: 4),
                      Text(
                        'REASONING SUMMARY',
                        style: AppTheme.label(fontSize: 10, color: AppColors.textMuted),
                      ),
                      const SizedBox(height: 4),
                      Container(
                        width: double.infinity,
                        padding: const EdgeInsets.all(10),
                        decoration: BoxDecoration(
                          color: AppColors.background.withValues(alpha: 0.6),
                          borderRadius: BorderRadius.circular(8),
                          border: Border.all(color: AppColors.borderLight),
                        ),
                        child: Text(
                          analysis.reasoningSummary,
                          style: AppTheme.body(fontSize: 12, color: AppColors.textPrimary),
                        ),
                      ),

                      const SizedBox(height: 12),
                      Align(
                        alignment: Alignment.centerRight,
                        child: InkWell(
                          onTap: () {
                            Navigator.push(
                              context,
                              MaterialPageRoute(
                                builder: (_) => AgentActivityScreen(
                                  traces: analysis.traces,
                                  emailSubject: _currentEmail.subject,
                                  emailId: _currentEmail.id,
                                  controller: widget.controller,
                                ),
                              ),
                            );
                          },
                          child: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Text(
                                'View Multi-Agent Reasoning Trace',
                                style: AppTheme.label(fontSize: 11, color: AppColors.warmBeige),
                              ),
                              const Icon(Icons.arrow_forward_ios, size: 10, color: AppColors.warmBeige),
                            ],
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 16),

                // Original Email Body Preview Container
                Container(
                  padding: const EdgeInsets.all(16),
                  decoration: BoxDecoration(
                    color: AppColors.surfaceCard,
                    borderRadius: BorderRadius.circular(16),
                    border: Border.all(color: AppColors.border),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Text(
                            'MESSAGE PREVIEW',
                            style: AppTheme.label(fontSize: 11, color: AppColors.textMuted),
                          ),
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                            decoration: BoxDecoration(
                              color: AppColors.secondarySurface,
                              borderRadius: BorderRadius.circular(4),
                            ),
                            child: Text(
                              'GMAIL INGESTED',
                              style: AppTheme.mono(fontSize: 8, color: AppColors.textSecondary),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 10),
                      Text(
                        _currentEmail.body,
                        style: AppTheme.body(fontSize: 13, height: 1.6),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 20),
              ],
            ),
          ),

          // Bottom Fixed Action Bar
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
            decoration: const BoxDecoration(
              color: AppColors.surface,
              border: Border(top: BorderSide(color: AppColors.border, width: 1)),
            ),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                if (isCompleted || isSnoozed) ...[
                  Padding(
                    padding: const EdgeInsets.only(bottom: 8),
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        if (isCompleted)
                          const Text(
                            '✓ Action Completed',
                            style: TextStyle(color: AppColors.success, fontSize: 12, fontWeight: FontWeight.bold),
                          ),
                        if (isCompleted && isSnoozed) const SizedBox(width: 12),
                        if (isSnoozed) ...[
                          Text(
                            '⏰ Snoozed until ${DateFormat('hh:mm a').format(_currentEmail.userState.snoozedUntil!)}',
                            style: const TextStyle(color: AppColors.textMuted, fontSize: 12),
                          ),
                          const SizedBox(width: 6),
                          InkWell(
                            onTap: () async {
                              await widget.controller.clearSnooze(_currentEmail.id);
                              _refreshEmailState();
                            },
                            child: Text(
                              '(Clear)',
                              style: AppTheme.label(fontSize: 11, color: AppColors.warmBeige),
                            ),
                          ),
                        ],
                      ],
                    ),
                  ),
                ],
                Row(
                  children: [
                    Expanded(
                      child: ElevatedButton(
                        onPressed: () {
                          _launchTargetLink(firstAction?.targetLink);
                        },
                        style: ElevatedButton.styleFrom(
                          backgroundColor: AppColors.warmBeige,
                          foregroundColor: AppColors.textDark,
                          padding: const EdgeInsets.symmetric(vertical: 12),
                          shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(12),
                          ),
                        ),
                        child: Text(
                          firstAction?.targetLink != null ? 'OPEN LINK' : 'OPEN PORTAL',
                          style: AppTheme.heading(fontSize: 12, color: AppColors.textDark),
                        ),
                      ),
                    ),
                    const SizedBox(width: 8),
                    IconButton.filledTonal(
                      onPressed: () {
                        ReminderBottomSheet.show(
                          context,
                          email: _currentEmail,
                          onSetReminder: (time, note) async {
                            await widget.controller.createReminder(
                              emailId: _currentEmail.id,
                              reminderAt: time,
                              actionRef: firstAction?.actionRef,
                              note: note,
                            );
                            _refreshEmailState();
                          },
                        );
                      },
                      icon: const Icon(Icons.notifications_active_outlined),
                      tooltip: 'Remind Me',
                      style: IconButton.styleFrom(
                        backgroundColor: AppColors.secondarySurface,
                        foregroundColor: AppColors.warmBeige,
                      ),
                    ),
                    const SizedBox(width: 4),
                    IconButton.filledTonal(
                      onPressed: () {
                        SnoozeBottomSheet.show(
                          context,
                          email: _currentEmail,
                          onSnooze: (until) async {
                            await widget.controller.snoozeEmail(_currentEmail.id, until);
                            _refreshEmailState();
                          },
                        );
                      },
                      icon: const Icon(Icons.snooze),
                      tooltip: 'Snooze',
                      style: IconButton.styleFrom(
                        backgroundColor: AppColors.secondarySurface,
                        foregroundColor: AppColors.textSecondary,
                      ),
                    ),
                    const SizedBox(width: 4),
                    IconButton.filledTonal(
                      onPressed: () async {
                        final messenger = ScaffoldMessenger.of(context);
                        await widget.controller.completeAction(
                          _currentEmail.id,
                          firstAction?.actionRef ?? 'act_001',
                        );
                        _refreshEmailState();
                        messenger.showSnackBar(
                          const SnackBar(content: Text('Action marked as completed on FastAPI backend!')),
                        );
                      },
                      icon: Icon(
                        isCompleted ? Icons.check_circle : Icons.check_circle_outline,
                        color: isCompleted ? AppColors.success : AppColors.textPrimary,
                      ),
                      tooltip: 'Mark Complete',
                      style: IconButton.styleFrom(
                        backgroundColor: isCompleted ? AppColors.success.withValues(alpha: 0.2) : AppColors.secondarySurface,
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildAnalysisRow(String label, String value, {Color? valueColor}) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Text(
          label,
          style: AppTheme.label(fontSize: 11, color: AppColors.textMuted),
        ),
        Text(
          value,
          style: AppTheme.bodyMedium(
            fontSize: 12,
            color: valueColor ?? AppColors.textPrimary,
          ),
        ),
      ],
    );
  }

  String _getInitials(String name) {
    final parts = name.trim().split(RegExp(r'\s+'));
    if (parts.isEmpty) return 'EM';
    if (parts.length == 1) return parts[0].substring(0, parts[0].length.clamp(1, 2)).toUpperCase();
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
}
