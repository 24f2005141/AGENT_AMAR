import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:url_launcher/url_launcher.dart';

import '../dto/full_email_dto.dart';
import '../models/agent_analysis.dart';
import '../services/api_error.dart';
import '../state/inbox_controller.dart';
import '../theme/app_theme.dart';
import '../widgets/classification_picker_sheet.dart';
import '../widgets/reply_suggestions_panel.dart';

/// The complete email. Opened from the detail screen's "View Full Email" (and
/// reachable from a notification/deep-link open via that screen). The body comes
/// from the backend as sanitised **plain text** — the intake pipeline already
/// flattened any HTML, so there is nothing to execute and no markup to strip.
class FullEmailScreen extends StatefulWidget {
  final String emailId;
  final InboxController controller;

  const FullEmailScreen({
    super.key,
    required this.emailId,
    required this.controller,
  });

  @override
  State<FullEmailScreen> createState() => _FullEmailScreenState();
}

class _FullEmailScreenState extends State<FullEmailScreen> {
  FullEmailDto? _email;
  PrimaryCategory? _primaryOverride; // set after a local correction
  bool _loading = true;
  bool _busyReclassify = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final full = await widget.controller.getFullEmail(widget.emailId);
      if (!mounted) return;
      setState(() {
        _email = full;
        _loading = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e.statusCode == 404
            ? 'This email is no longer available.'
            : 'Could not load the full email. Please try again.';
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = 'Could not load the full email. Please try again.';
      });
    }
  }

  PrimaryCategory get _primary =>
      _primaryOverride ?? _email?.primaryCategory ?? PrimaryCategory.lowPriority;

  Future<void> _changeClassification() async {
    final picked = await ClassificationPickerSheet.show(context, current: _primary);
    if (!mounted || picked == null || picked == _primary) return;

    final messenger = ScaffoldMessenger.of(context);
    setState(() => _busyReclassify = true);
    try {
      final updated = await widget.controller
          .submitClassificationFeedback(widget.emailId, picked);
      if (!mounted) return;
      setState(() => _primaryOverride = updated.primaryCategory);
      messenger.showSnackBar(SnackBar(
        content: Text('Moved to ${primaryCategoryLabel(updated.primaryCategory)}.'),
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

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        leading: IconButton(
          icon: const Icon(Icons.arrow_back),
          onPressed: () => Navigator.pop(context),
        ),
        title: Text('FULL EMAIL',
            style: AppTheme.brandTitle(fontSize: 16, fontWeight: FontWeight.bold)),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator(color: AppColors.warmBeige))
          : _error != null
              ? _buildError()
              : _buildContent(_email!),
    );
  }

  Widget _buildError() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(28),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.error_outline, color: AppColors.critical, size: 28),
            const SizedBox(height: 12),
            Text(_error!,
                textAlign: TextAlign.center,
                style: AppTheme.body(fontSize: 13, color: AppColors.textSecondary)),
            const SizedBox(height: 12),
            TextButton(onPressed: _load, child: const Text('Try Again')),
          ],
        ),
      ),
    );
  }

  Widget _buildContent(FullEmailDto email) {
    final replyNeeded = _primary == PrimaryCategory.replyRequired;
    return ListView(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      children: [
        // --- header ---
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
              Text(email.senderName ?? email.senderEmail,
                  style: AppTheme.heading(fontSize: 15)),
              const SizedBox(height: 2),
              Text(email.senderEmail,
                  style: AppTheme.label(fontSize: 11, color: AppColors.textMuted)),
              if (email.receivedAt != null) ...[
                const SizedBox(height: 6),
                Text(
                  DateFormat('EEEE, MMM d, yyyy · hh:mm a').format(email.receivedAt!),
                  style: AppTheme.mono(fontSize: 11, color: AppColors.textMuted),
                ),
              ],
              const SizedBox(height: 12),
              Text(email.subject,
                  style: AppTheme.brandTitle(fontSize: 17, fontWeight: FontWeight.bold)),
            ],
          ),
        ),
        const SizedBox(height: 12),

        // --- classification ---
        Container(
          padding: const EdgeInsets.fromLTRB(16, 12, 12, 12),
          decoration: BoxDecoration(
            color: AppColors.surfaceCard,
            borderRadius: BorderRadius.circular(16),
            border: Border.all(color: AppColors.border),
          ),
          child: Row(
            children: [
              Text('CLASSIFICATION',
                  style: AppTheme.label(fontSize: 11, color: AppColors.textMuted)),
              const SizedBox(width: 10),
              Text(primaryCategoryLabel(_primary),
                  style: AppTheme.bodyMedium(fontSize: 12, color: AppColors.textPrimary)),
              const Spacer(),
              TextButton.icon(
                onPressed: _busyReclassify ? null : _changeClassification,
                icon: _busyReclassify
                    ? const SizedBox(
                        width: 12, height: 12,
                        child: CircularProgressIndicator(
                            strokeWidth: 2, color: AppColors.warmBeige))
                    : const Icon(Icons.tune, size: 14),
                label: const Text('Change'),
                style: TextButton.styleFrom(
                  foregroundColor: AppColors.warmBeige,
                  visualDensity: VisualDensity.compact,
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),

        // --- body ---
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
              if (email.isHtmlConverted) ...[
                Row(
                  children: [
                    const Icon(Icons.article_outlined, size: 13, color: AppColors.textMuted),
                    const SizedBox(width: 6),
                    Text('Converted from a formatted (HTML) email',
                        style: AppTheme.label(fontSize: 10, color: AppColors.textMuted)),
                  ],
                ),
                const SizedBox(height: 10),
              ],
              SelectableText.rich(
                TextSpan(
                  children: _linkified(
                    email.body.isEmpty ? '(This email has no readable text.)' : email.body,
                    AppTheme.body(fontSize: 13, height: 1.6),
                  ),
                ),
              ),
              if (email.isTruncated) ...[
                const SizedBox(height: 10),
                Text('Message truncated for display.',
                    style: AppTheme.label(fontSize: 10, color: AppColors.textMuted)),
              ],
            ],
          ),
        ),

        if (replyNeeded) ...[
          const SizedBox(height: 14),
          ReplySuggestionsPanel(
            connectedAccountEmail: widget.controller.connectedAccountEmail,
            onGenerate: () =>
                widget.controller.generateReplySuggestions(widget.emailId),
            onSend: (body) => widget.controller.sendReply(widget.emailId, body),
          ),
        ],
        const SizedBox(height: 24),
      ],
    );
  }

  // Plain text in, tappable-link spans out. No HTML parsing — the body is
  // already plain text; this only makes bare URLs actionable.
  static final _urlRe = RegExp(r'(https?://[^\s<>()\[\]]+)');

  List<InlineSpan> _linkified(String text, TextStyle base) {
    final spans = <InlineSpan>[];
    var start = 0;
    for (final m in _urlRe.allMatches(text)) {
      if (m.start > start) {
        spans.add(TextSpan(text: text.substring(start, m.start), style: base));
      }
      final url = m.group(0)!;
      spans.add(TextSpan(
        text: url,
        style: base.copyWith(
          color: AppColors.warmBeige,
          decoration: TextDecoration.underline,
        ),
        recognizer: TapGestureRecognizer()
          ..onTap = () {
            final uri = Uri.tryParse(url);
            if (uri != null) {
              launchUrl(uri, mode: LaunchMode.externalApplication);
            }
          },
      ));
      start = m.end;
    }
    if (start < text.length) {
      spans.add(TextSpan(text: text.substring(start), style: base));
    }
    return spans;
  }
}
