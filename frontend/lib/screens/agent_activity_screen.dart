import 'package:flutter/material.dart';
import '../dto/email_state_dto.dart';
import '../models/agent_trace.dart';
import '../state/inbox_controller.dart';
import '../theme/app_theme.dart';
import '../widgets/agent_timeline.dart';

class AgentActivityScreen extends StatefulWidget {
  final List<AgentTrace> traces;
  final String emailSubject;
  final String? emailId;
  final InboxController? controller;

  const AgentActivityScreen({
    super.key,
    required this.traces,
    this.emailSubject = 'Autonomous Decision Pipeline',
    this.emailId,
    this.controller,
  });

  @override
  State<AgentActivityScreen> createState() => _AgentActivityScreenState();
}

class _AgentActivityScreenState extends State<AgentActivityScreen> {
  List<AgentTrace> _currentTraces = [];
  bool _isLoading = false;

  @override
  void initState() {
    super.initState();
    _currentTraces = widget.traces;
    if (widget.emailId != null && widget.controller != null) {
      _fetchProcessingHistory();
    }
  }

  Future<void> _fetchProcessingHistory() async {
    if (widget.emailId == null || widget.controller == null) return;
    setState(() => _isLoading = true);

    try {
      final detail = await widget.controller!.getEmailDetail(widget.emailId!);
      if (detail != null && detail.latestProcessing != null && mounted) {
        final newTraces = _mapTraceEntries(detail.latestProcessing!, detail.processedAt);
        if (newTraces.isNotEmpty) {
          setState(() {
            _currentTraces = newTraces;
          });
        }
      }
    } catch (_) {
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  List<AgentTrace> _mapTraceEntries(ProcessingRunDto run, DateTime? processedAt) {
    return run.agentTrace.map((t) {
      final summaryText = t.method != null
          ? 'Execution method: ${t.method}${t.durationMs != null ? ' (${t.durationMs}ms)' : ''}'
          : 'Status: ${t.status.toUpperCase()}${t.durationMs != null ? ' (${t.durationMs}ms)' : ''}';
      return AgentTrace(
        agentName: t.agent,
        status: t.status,
        confidence: t.confidence ?? 1.0,
        summary: summaryText,
        timestamp: processedAt ?? DateTime.now(),
        method: t.method,
      );
    }).toList();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'AGENT ACTIVITY',
              style: AppTheme.brandTitle(fontSize: 18, fontWeight: FontWeight.bold),
            ),
            Text(
              'Multi-Agent Reasoning Trace',
              style: AppTheme.label(fontSize: 10, color: AppColors.textMuted),
            ),
          ],
        ),
      ),
      body: _isLoading
          ? const Center(
              child: CircularProgressIndicator(color: AppColors.warmBeige),
            )
          : ListView(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
              children: [
                // Banner introducing autonomous pipeline
                Container(
                  padding: const EdgeInsets.all(16),
                  decoration: BoxDecoration(
                    color: AppColors.secondarySurface.withValues(alpha: 0.5),
                    borderRadius: BorderRadius.circular(16),
                    border: Border.all(color: AppColors.border),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          const Icon(Icons.hub_outlined, color: AppColors.warmBeige, size: 20),
                          const SizedBox(width: 8),
                          Text(
                            'AUTONOMOUS AGENT PIPELINE',
                            style: AppTheme.mono(
                              fontSize: 11,
                              fontWeight: FontWeight.bold,
                              color: AppColors.warmBeige,
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 8),
                      Text(
                        'Trace for: ${widget.emailSubject}',
                        style: AppTheme.heading(fontSize: 14),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        'Specialized AI agents collaboratively ingest, classify, parse deadlines, evaluate priority, and schedule proactive user reminders.',
                        style: AppTheme.body(fontSize: 12, color: AppColors.textSecondary),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 20),

                Text(
                  'EXECUTION TIMELINE',
                  style: AppTheme.label(
                    fontSize: 11,
                    fontWeight: FontWeight.bold,
                    color: AppColors.textSecondary,
                    letterSpacing: 1.0,
                  ),
                ),
                const SizedBox(height: 12),

                // The Multi-Agent Visual Timeline
                AgentTimeline(traces: _currentTraces),
              ],
            ),
    );
  }
}
