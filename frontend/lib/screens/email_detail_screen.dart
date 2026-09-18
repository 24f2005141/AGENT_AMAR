import 'package:flutter/foundation.dart' show kDebugMode;
import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:url_launcher/url_launcher.dart';
import '../dto/email_state_dto.dart';
import '../dto/mappers/dto_mapper.dart';
import '../models/agent_analysis.dart';
import '../models/email.dart';
import '../services/local_schedule_service.dart';
import '../state/inbox_controller.dart';
import '../theme/app_theme.dart';
import '../widgets/classification_picker_sheet.dart';
import '../widgets/countdown_timer_view.dart';
import '../widgets/priority_badge.dart';
import '../widgets/reminder_bottom_sheet.dart';
import '../widgets/reply_suggestions_panel.dart';
import '../widgets/snooze_bottom_sheet.dart';
import 'agent_activity_screen.dart';
import 'full_email_screen.dart';

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
  bool _busyReclassify = false;

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

  /// Whether the backend's classification says this email needs a reply.
  /// The backend is the source of truth — no classification logic here, just a
  /// read of fields it already returns (category / action types).
  bool get _needsReply {
    // Canonical: the backend's mutually-exclusive inbox bucket.
    if (_currentEmail.primaryCategory == PrimaryCategory.replyRequired) return true;
    // Fallbacks for a row not yet re-classified by the backend.
    if (_currentEmail.analysis.actionType == 'REPLY') return true;
    final dto = _detailDto;
    if (dto == null) return false;
    if (dto.primaryCategory == 'REPLY_REQUIRED') return true;
    if (dto.finalCategory == 'REPLY_REQUIRED') return true;
    if (dto.primaryActionType == 'REPLY') return true;
    return dto.actions.any((a) => a.actionType == 'REPLY');
  }

  /// A short, user-facing reply status for the analysis card — or null when the
  /// email does not need a reply (or is already done, in which case the STATUS
  /// row covers it).
  String? get _replyStatus {
    if (!_needsReply || _currentEmail.userState.isCompleted) return null;
    return 'Reply requested';
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
    // Device-local reminder for this email, if any (see LocalScheduleService)
    // — read fresh on every build, no separate listenable needed since a
    // create/cancel here always follows with setState.
    final pendingReminder = LocalScheduleService().pendingReminderForEmail(_currentEmail.id);
    final deadlineAlarms =
        LocalScheduleService().pendingDeadlineEventsForEmail(_currentEmail.id);

    return Scaffold(
      appBar: AppBar(
        leading: IconButton(
          icon: const Icon(Icons.arrow_back),
          onPressed: () => Navigator.pop(context),
        ),
        title: Text(
          'EMAIL DETAILS',
          style: AppTheme.brandTitle(fontSize: 16, fontWeight: FontWeight.bold),
        ),
        actions: [
          // Internal multi-agent processing trace — developer/debug builds only,
          // never shown to normal users. The reasoning data itself is untouched
          // on the backend / API and still parsed into the model.
          if (kDebugMode)
            IconButton(
              icon: const Icon(Icons.smart_toy_outlined, color: AppColors.warmBeige),
              tooltip: 'Agent processing trace (debug)',
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
                // ANALYSIS CARD (Core Highlight)
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
                                'ANALYSIS',
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

                      // Clean, user-facing results only — category, priority,
                      // deadline, and any action / reply status. Internal
                      // reasoning text, agent scores, routing and the
                      // multi-agent trace are intentionally not surfaced here.
                      _buildClassificationRow(),
                      const SizedBox(height: 8),
                      _buildAnalysisRow('CATEGORY', analysis.category),
                      const SizedBox(height: 8),
                      _buildAnalysisRow(
                        'PRIORITY',
                        analysis.priority.displayName,
                        valueColor: isCritical ? AppColors.critical : AppColors.textPrimary,
                      ),

                      if (analysis.deadline != null) ...[
                        const SizedBox(height: 8),
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
                      ],

                      if (analysis.actionDescription != null) ...[
                        const SizedBox(height: 8),
                        _buildAnalysisRow(
                          'ACTION REQUIRED',
                          analysis.actionDescription!,
                          valueColor: AppColors.warmBeige,
                        ),
                      ],

                      if (_replyStatus != null) ...[
                        const SizedBox(height: 8),
                        _buildAnalysisRow(
                          'REPLY',
                          _replyStatus!,
                          valueColor: AppColors.warmBeige,
                        ),
                      ],

                      if (isCompleted) ...[
                        const SizedBox(height: 8),
                        _buildAnalysisRow(
                          'STATUS',
                          'Completed',
                          valueColor: AppColors.success,
                        ),
                      ],

                      const SizedBox(height: 12),
                      Align(
                        alignment: Alignment.centerLeft,
                        child: OutlinedButton.icon(
                          onPressed: _busyReclassify ? null : _changeClassification,
                          icon: _busyReclassify
                              ? const SizedBox(
                                  width: 13, height: 13,
                                  child: CircularProgressIndicator(
                                      strokeWidth: 2, color: AppColors.warmBeige))
                              : const Icon(Icons.tune, size: 14, color: AppColors.warmBeige),
                          style: OutlinedButton.styleFrom(
                            foregroundColor: AppColors.warmBeige,
                            side: const BorderSide(color: AppColors.border),
                            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
                            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(9)),
                          ),
                          label: Text('Change Classification',
                              style: AppTheme.label(fontSize: 11, color: AppColors.warmBeige)),
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
                        maxLines: 6,
                        overflow: TextOverflow.ellipsis,
                      ),
                      const SizedBox(height: 12),
                      Align(
                        alignment: Alignment.centerLeft,
                        child: TextButton.icon(
                          onPressed: _openFullEmail,
                          icon: const Icon(Icons.open_in_full, size: 15),
                          label: const Text('View Full Email'),
                          style: TextButton.styleFrom(
                            foregroundColor: AppColors.warmBeige,
                            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                            visualDensity: VisualDensity.compact,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),

                // AI reply suggestions — only for emails the backend flags as
                // needing a reply (no classification logic in Flutter).
                if (_needsReply) ...[
                  const SizedBox(height: 14),
                  ReplySuggestionsPanel(
                    connectedAccountEmail: widget.controller.connectedAccountEmail,
                    onGenerate: () =>
                        widget.controller.generateReplySuggestions(_currentEmail.id),
                    onSend: (body) =>
                        widget.controller.sendReply(_currentEmail.id, body),
                    onSent: () {
                      _refreshEmailState();
                      _fetchDetail();
                    },
                  ),
                ],
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
                if (isCompleted ||
                    isSnoozed ||
                    pendingReminder != null ||
                    deadlineAlarms.isNotEmpty) ...[
                  Padding(
                    padding: const EdgeInsets.only(bottom: 8),
                    child: Wrap(
                      alignment: WrapAlignment.center,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      spacing: 12,
                      runSpacing: 4,
                      children: [
                        if (isCompleted)
                          const Text(
                            '✓ Action Completed',
                            style: TextStyle(color: AppColors.success, fontSize: 12, fontWeight: FontWeight.bold),
                          ),
                        if (isSnoozed)
                          Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
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
                          ),
                        if (pendingReminder != null)
                          Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Text(
                                '🔔 Reminder set: ${DateFormat('EEE, MMM d · hh:mm a').format(pendingReminder.scheduledAt.toLocal())}',
                                style: const TextStyle(color: AppColors.textMuted, fontSize: 12),
                              ),
                              const SizedBox(width: 6),
                              InkWell(
                                onTap: () async {
                                  await LocalScheduleService().cancel(pendingReminder.id);
                                  if (mounted) setState(() {});
                                },
                                child: Text(
                                  '(Cancel)',
                                  style: AppTheme.label(fontSize: 11, color: AppColors.warmBeige),
                                ),
                              ),
                            ],
                          ),
                        // Deadline alarms are scheduled automatically from the
                        // email's own deadline and clear themselves when the
                        // task is done — shown, not controlled, here.
                        if (deadlineAlarms.isNotEmpty)
                          Text(
                            '⏳ ${deadlineAlarms.length} deadline alarm${deadlineAlarms.length == 1 ? '' : 's'} scheduled',
                            style: const TextStyle(color: AppColors.textMuted, fontSize: 12),
                          ),
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
                            final existing = pendingReminder;
                            if (existing != null) {
                              await LocalScheduleService().reschedule(existing.id, time);
                            } else {
                              await LocalScheduleService().createReminder(
                                emailId: _currentEmail.id,
                                label: note?.trim().isNotEmpty == true
                                    ? note!.trim()
                                    : _currentEmail.subject,
                                scheduledAt: time,
                              );
                            }
                            if (mounted) setState(() {});
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
                        if (isCompleted) {
                          await widget.controller.reopenEmail(_currentEmail.id);
                          _refreshEmailState();
                          messenger.showSnackBar(const SnackBar(
                              content: Text('Reopened — back on your dashboard.')));
                          return;
                        }
                        final updated =
                            await widget.controller.markComplete(_currentEmail.id);
                        if (updated != null && mounted) {
                          setState(() => _currentEmail = updated);
                        } else {
                          _refreshEmailState();
                        }
                        messenger.showSnackBar(const SnackBar(
                            content: Text('Marked as done. Removed from your dashboard.')));
                      },
                      icon: Icon(
                        isCompleted ? Icons.check_circle : Icons.check_circle_outline,
                        color: isCompleted ? AppColors.success : AppColors.textPrimary,
                      ),
                      tooltip: isCompleted ? 'Reopen' : 'Mark Done',
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

  /// The canonical primary classification the whole UI filters on, plus a marker
  /// when the user has manually corrected it.
  Widget _buildClassificationRow() {
    final corrected = _currentEmail.primaryCategoryUserCorrected;
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Text('CLASSIFICATION',
            style: AppTheme.label(fontSize: 11, color: AppColors.textMuted)),
        Flexible(
          child: Text(
            corrected
                ? '${primaryCategoryLabel(_currentEmail.primaryCategory)} · corrected'
                : primaryCategoryLabel(_currentEmail.primaryCategory),
            textAlign: TextAlign.right,
            style: AppTheme.bodyMedium(
              fontSize: 12,
              color: corrected ? AppColors.warmBeige : AppColors.textPrimary,
            ),
          ),
        ),
      ],
    );
  }

  Future<void> _changeClassification() async {
    final picked = await ClassificationPickerSheet.show(
      context,
      current: _currentEmail.primaryCategory,
    );
    if (!mounted || picked == null || picked == _currentEmail.primaryCategory) return;

    final messenger = ScaffoldMessenger.of(context);
    setState(() => _busyReclassify = true);
    try {
      final updated = await widget.controller
          .submitClassificationFeedback(_currentEmail.id, picked);
      if (!mounted) return;
      setState(() => _currentEmail = updated);
      messenger.showSnackBar(SnackBar(
        content: Text(
            'Moved to ${primaryCategoryLabel(updated.primaryCategory)}.'),
      ));
    } catch (_) {
      if (!mounted) return;
      messenger.showSnackBar(const SnackBar(
        content: Text('Could not update the classification. Please try again.'),
      ));
    } finally {
      if (mounted) setState(() => _busyReclassify = false);
    }
  }

  void _openFullEmail() {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => FullEmailScreen(
          emailId: _currentEmail.id,
          controller: widget.controller,
        ),
      ),
    ).then((_) {
      _refreshEmailState();
      _fetchDetail();
    });
  }

  String _getInitials(String name) {
    final parts = name.trim().split(RegExp(r'\s+'));
    if (parts.isEmpty) return 'EM';
    if (parts.length == 1) return parts[0].substring(0, parts[0].length.clamp(1, 2)).toUpperCase();
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
}
