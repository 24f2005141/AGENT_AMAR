import 'package:flutter/material.dart';

import '../dto/reply_dto.dart';
import '../services/api_error.dart';
import '../theme/app_theme.dart';

/// AI reply suggestions + send, embedded in the email detail screen.
///
/// The backend decides whether an email needs a reply (this panel is only shown
/// for those), generates exactly 3 options, and sends the final body verbatim
/// from the user's connected Gmail. This widget never contacts an LLM or Gmail;
/// it only calls [onGenerate] / [onSend].
///
/// UI states: idle → generating → choosing (3 cards) → editing (selected reply)
/// → sending → sent. Failures surface inline; a send failure keeps the draft.
class ReplySuggestionsPanel extends StatefulWidget {
  final String? connectedAccountEmail;
  final Future<ReplySuggestionsDto> Function() onGenerate;
  final Future<ReplySendResultDto> Function(String body) onSend;
  final VoidCallback? onSent;

  const ReplySuggestionsPanel({
    super.key,
    required this.onGenerate,
    required this.onSend,
    this.connectedAccountEmail,
    this.onSent,
  });

  @override
  State<ReplySuggestionsPanel> createState() => _ReplySuggestionsPanelState();
}

enum _Phase { idle, generating, choosing, editing, sending, sent }

class _ReplySuggestionsPanelState extends State<ReplySuggestionsPanel> {
  _Phase _phase = _Phase.idle;
  List<ReplySuggestionDto> _suggestions = const [];
  String? _generateError;
  String? _sendError;
  final TextEditingController _editor = TextEditingController();
  String? _selectedLabel;
  ReplySendResultDto? _sendResult;

  @override
  void dispose() {
    _editor.dispose();
    super.dispose();
  }

  Future<void> _generate() async {
    setState(() {
      _phase = _Phase.generating;
      _generateError = null;
    });
    try {
      final result = await widget.onGenerate();
      if (!mounted) return;
      setState(() {
        _suggestions = result.suggestions;
        _phase = _Phase.choosing;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _phase = _Phase.idle;
        _generateError = _friendlyError(e);
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _phase = _Phase.idle;
        _generateError = 'Could not generate suggestions. Please try again.';
      });
    }
  }

  void _select(ReplySuggestionDto s) {
    setState(() {
      _editor.text = s.body;
      _selectedLabel = s.label;
      _sendError = null;
      _phase = _Phase.editing;
    });
  }

  void _cancelEditing() {
    setState(() {
      _sendError = null;
      _phase = _suggestions.isEmpty ? _Phase.idle : _Phase.choosing;
    });
  }

  Future<void> _send() async {
    final body = _editor.text.trim();
    if (body.isEmpty || _phase == _Phase.sending) return;
    setState(() {
      _phase = _Phase.sending;
      _sendError = null;
    });
    try {
      final result = await widget.onSend(body);
      if (!mounted) return;
      setState(() {
        _sendResult = result;
        _phase = _Phase.sent;
      });
      widget.onSent?.call();
    } on ApiException catch (e) {
      if (!mounted) return;
      // keep the draft — the user does not lose their edit
      setState(() {
        _phase = _Phase.editing;
        _sendError = _friendlyError(e);
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _phase = _Phase.editing;
        _sendError = 'Could not send the reply. Your draft is safe — try again.';
      });
    }
  }

  String _friendlyError(ApiException e) {
    if (e.errorType == 'LLMUnavailableError' || (e.statusCode == 503)) {
      return 'The AI service is unavailable right now. Please try again shortly.';
    }
    if (e.errorType == 'LLMResponseError' || e.statusCode == 502) {
      return 'The AI response could not be used. Please try again.';
    }
    if (e.isGmailNotConnected) {
      return 'Gmail needs to be reconnected before you can send a reply.';
    }
    if (e.isNetworkError) {
      return 'Cannot reach Sorted. Check your connection and try again.';
    }
    return e.message;
  }

  @override
  Widget build(BuildContext context) {
    return Container(
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
            children: [
              const Icon(Icons.auto_awesome, size: 16, color: AppColors.warmBeige),
              const SizedBox(width: 8),
              Text(
                'AI REPLY SUGGESTIONS',
                style: AppTheme.label(fontSize: 11, color: AppColors.warmBeige),
              ),
            ],
          ),
          const SizedBox(height: 12),
          _buildBody(),
        ],
      ),
    );
  }

  Widget _buildBody() {
    switch (_phase) {
      case _Phase.idle:
        return _buildIdle();
      case _Phase.generating:
        return _buildGenerating();
      case _Phase.choosing:
        return _buildChoosing();
      case _Phase.editing:
      case _Phase.sending:
        return _buildEditor();
      case _Phase.sent:
        return _buildSent();
    }
  }

  Widget _buildIdle() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (_generateError != null) ...[
          _ErrorText(_generateError!),
          const SizedBox(height: 10),
        ],
        ElevatedButton.icon(
          onPressed: _generate,
          icon: const Icon(Icons.auto_awesome, size: 16),
          label: Text(
            _generateError == null ? 'Suggest Replies' : 'Try Again',
            style: AppTheme.heading(fontSize: 13, color: AppColors.textDark),
          ),
          style: ElevatedButton.styleFrom(
            backgroundColor: AppColors.warmBeige,
            foregroundColor: AppColors.textDark,
            padding: const EdgeInsets.symmetric(vertical: 12),
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
          ),
        ),
      ],
    );
  }

  Widget _buildGenerating() {
    return Row(
      children: [
        const SizedBox(
          width: 16,
          height: 16,
          child: CircularProgressIndicator(strokeWidth: 2, color: AppColors.warmBeige),
        ),
        const SizedBox(width: 12),
        Text(
          'Generating reply suggestions…',
          style: AppTheme.body(fontSize: 13, color: AppColors.textSecondary),
        ),
      ],
    );
  }

  Widget _buildChoosing() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: Text(
            'Pick a draft — you can edit it before sending.',
            style: AppTheme.body(fontSize: 11, color: AppColors.textSecondary),
          ),
        ),
        for (final s in _suggestions) ...[
          _SuggestionCard(suggestion: s, onSelect: () => _select(s)),
          const SizedBox(height: 10),
        ],
        TextButton(
          onPressed: _generate,
          child: Text(
            'Regenerate',
            style: AppTheme.label(fontSize: 11, color: AppColors.textMuted),
          ),
        ),
      ],
    );
  }

  Widget _buildEditor() {
    final sending = _phase == _Phase.sending;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: Text(
            _selectedLabel != null && _selectedLabel!.isNotEmpty
                ? 'REVIEW & EDIT · ${_selectedLabel!.toUpperCase()}'
                : 'REVIEW & EDIT YOUR REPLY',
            style: AppTheme.label(fontSize: 10, color: AppColors.textMuted),
          ),
        ),
        Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: Text(
            'This is an AI draft — change anything before you send. Nothing is '
            'sent until you tap Send Reply.',
            style: AppTheme.body(fontSize: 11, color: AppColors.textSecondary),
          ),
        ),
        TextField(
          controller: _editor,
          maxLines: 6,
          minLines: 3,
          enabled: !sending,
          style: AppTheme.body(fontSize: 13, height: 1.5),
          decoration: InputDecoration(
            filled: true,
            fillColor: AppColors.background.withValues(alpha: 0.6),
            border: OutlineInputBorder(
              borderRadius: BorderRadius.circular(10),
              borderSide: const BorderSide(color: AppColors.borderLight),
            ),
            enabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(10),
              borderSide: const BorderSide(color: AppColors.borderLight),
            ),
            contentPadding: const EdgeInsets.all(12),
          ),
        ),
        const SizedBox(height: 8),
        Row(
          children: [
            const Icon(Icons.send_outlined, size: 12, color: AppColors.textMuted),
            const SizedBox(width: 6),
            Expanded(
              child: Text(
                widget.connectedAccountEmail != null && widget.connectedAccountEmail!.isNotEmpty
                    ? 'Sends from ${widget.connectedAccountEmail} · edit before sending'
                    : 'Sends from your connected Gmail account · edit before sending',
                style: AppTheme.label(fontSize: 10, color: AppColors.textMuted),
              ),
            ),
          ],
        ),
        if (_sendError != null) ...[
          const SizedBox(height: 8),
          _ErrorText(_sendError!),
        ],
        const SizedBox(height: 12),
        Row(
          children: [
            TextButton(
              onPressed: sending ? null : _cancelEditing,
              child: Text(
                'Cancel',
                style: AppTheme.label(fontSize: 12, color: AppColors.textMuted),
              ),
            ),
            const Spacer(),
            ElevatedButton.icon(
              // disabled while a send is in flight — no double sends
              onPressed: sending ? null : _send,
              icon: sending
                  ? const SizedBox(
                      width: 14,
                      height: 14,
                      child: CircularProgressIndicator(strokeWidth: 2, color: AppColors.textDark),
                    )
                  : const Icon(Icons.send, size: 15),
              label: Text(
                sending ? 'Sending…' : 'Send Reply',
                style: AppTheme.heading(fontSize: 13, color: AppColors.textDark),
              ),
              style: ElevatedButton.styleFrom(
                backgroundColor: AppColors.warmBeige,
                foregroundColor: AppColors.textDark,
                disabledBackgroundColor: AppColors.warmBeige.withValues(alpha: 0.5),
                padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 11),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
              ),
            ),
          ],
        ),
      ],
    );
  }

  Widget _buildSent() {
    final completed = _sendResult?.emailMarkedCompleted ?? false;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Icon(Icons.check_circle, size: 18, color: AppColors.success),
        const SizedBox(width: 10),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'Reply sent from your Gmail account.',
                style: AppTheme.body(fontSize: 13, color: AppColors.success),
              ),
              if (completed)
                Padding(
                  padding: const EdgeInsets.only(top: 2),
                  child: Text(
                    'This email is now marked as done.',
                    style: AppTheme.label(fontSize: 10, color: AppColors.textMuted),
                  ),
                ),
            ],
          ),
        ),
        TextButton(
          onPressed: () => setState(() {
            _phase = _Phase.idle;
            _suggestions = const [];
            _editor.clear();
            _sendResult = null;
          }),
          child: Text('New reply', style: AppTheme.label(fontSize: 11, color: AppColors.textMuted)),
        ),
      ],
    );
  }
}

class _SuggestionCard extends StatelessWidget {
  final ReplySuggestionDto suggestion;
  final VoidCallback onSelect;

  const _SuggestionCard({required this.suggestion, required this.onSelect});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.background.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: AppColors.borderLight),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            suggestion.label.isNotEmpty ? suggestion.label.toUpperCase() : 'OPTION',
            style: AppTheme.label(fontSize: 10, color: AppColors.warmBeige),
          ),
          const SizedBox(height: 6),
          Text(
            suggestion.body,
            style: AppTheme.body(fontSize: 13, height: 1.5, color: AppColors.textPrimary),
          ),
          const SizedBox(height: 10),
          Align(
            alignment: Alignment.centerRight,
            child: OutlinedButton.icon(
              onPressed: onSelect,
              icon: const Icon(Icons.edit_outlined, size: 13, color: AppColors.warmBeige),
              style: OutlinedButton.styleFrom(
                foregroundColor: AppColors.warmBeige,
                side: const BorderSide(color: AppColors.border),
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
              ),
              label: Text('Edit & send',
                  style: AppTheme.label(fontSize: 11, color: AppColors.warmBeige)),
            ),
          ),
        ],
      ),
    );
  }
}

class _ErrorText extends StatelessWidget {
  final String message;
  const _ErrorText(this.message);

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Icon(Icons.error_outline, size: 14, color: AppColors.critical),
        const SizedBox(width: 6),
        Expanded(
          child: Text(
            message,
            style: AppTheme.body(fontSize: 12, color: AppColors.critical),
          ),
        ),
      ],
    );
  }
}
