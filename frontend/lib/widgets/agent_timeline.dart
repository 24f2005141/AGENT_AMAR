import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import '../models/agent_trace.dart';
import '../theme/app_theme.dart';

class AgentTimeline extends StatefulWidget {
  final List<AgentTrace> traces;
  final bool isCompact;

  const AgentTimeline({
    super.key,
    required this.traces,
    this.isCompact = false,
  });

  @override
  State<AgentTimeline> createState() => _AgentTimelineState();
}

class _AgentTimelineState extends State<AgentTimeline> {
  final Set<int> _expandedIndices = {};

  @override
  Widget build(BuildContext context) {
    if (widget.traces.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Text(
            'No agent activity traces logged for this item yet.',
            style: AppTheme.body(fontSize: 13, color: AppColors.textMuted),
          ),
        ),
      );
    }

    return Column(
      children: List.generate(widget.traces.length, (index) {
        final trace = widget.traces[index];
        final isLast = index == widget.traces.length - 1;
        final isExpanded = _expandedIndices.contains(index);

        return IntrinsicHeight(
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Timeline vertical track + glowing node
              SizedBox(
                width: 24,
                child: Column(
                  children: [
                    Container(
                      width: 12,
                      height: 12,
                      margin: const EdgeInsets.only(top: 4),
                      decoration: BoxDecoration(
                        color: _getNodeColor(trace.agentName),
                        shape: BoxShape.circle,
                        boxShadow: [
                          BoxShadow(
                            color: _getNodeColor(trace.agentName).withValues(alpha: 0.5),
                            blurRadius: 6,
                            spreadRadius: 1,
                          ),
                        ],
                      ),
                    ),
                    if (!isLast)
                      Expanded(
                        child: Container(
                          width: 2,
                          color: AppColors.mutedSlate.withValues(alpha: 0.4),
                        ),
                      ),
                  ],
                ),
              ),
              const SizedBox(width: 12),

              // Step Content Card
              Expanded(
                child: Container(
                  margin: const EdgeInsets.only(bottom: 14),
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: AppColors.surfaceCard,
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: AppColors.borderLight),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Row(
                            children: [
                              Text(
                                trace.agentName.toUpperCase(),
                                style: AppTheme.mono(
                                  fontSize: 11,
                                  fontWeight: FontWeight.bold,
                                  color: _getNodeColor(trace.agentName),
                                ),
                              ),
                              const SizedBox(width: 6),
                              Container(
                                padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
                                decoration: BoxDecoration(
                                  color: AppColors.surface,
                                  borderRadius: BorderRadius.circular(4),
                                ),
                                child: Text(
                                  '${(trace.confidence * 100).toInt()}% conf',
                                  style: AppTheme.mono(
                                    fontSize: 8,
                                    color: AppColors.textSecondary,
                                  ),
                                ),
                              ),
                            ],
                          ),
                          Text(
                            DateFormat('HH:mm:ss').format(trace.timestamp),
                            style: AppTheme.mono(fontSize: 10, color: AppColors.textMuted),
                          ),
                        ],
                      ),
                      const SizedBox(height: 6),
                      Text(
                        trace.summary,
                        style: AppTheme.body(fontSize: 13, color: AppColors.textPrimary),
                      ),

                      // Expandable Technical Details
                      if (trace.method != null || trace.details != null) ...[
                        const SizedBox(height: 6),
                        InkWell(
                          onTap: () {
                            setState(() {
                              if (isExpanded) {
                                _expandedIndices.remove(index);
                              } else {
                                _expandedIndices.add(index);
                              }
                            });
                          },
                          child: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Text(
                                isExpanded ? 'Hide Technical Metadata' : 'View Technical Metadata',
                                style: AppTheme.label(fontSize: 10, color: AppColors.warmBeige),
                              ),
                              Icon(
                                isExpanded ? Icons.keyboard_arrow_up : Icons.keyboard_arrow_down,
                                size: 14,
                                color: AppColors.warmBeige,
                              ),
                            ],
                          ),
                        ),
                        if (isExpanded) ...[
                          const SizedBox(height: 6),
                          Container(
                            width: double.infinity,
                            padding: const EdgeInsets.all(8),
                            decoration: BoxDecoration(
                              color: AppColors.background,
                              borderRadius: BorderRadius.circular(8),
                              border: Border.all(color: AppColors.borderLight),
                            ),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                if (trace.method != null)
                                  Text(
                                    'Method: ${trace.method}',
                                    style: AppTheme.mono(fontSize: 10, color: AppColors.textSecondary),
                                  ),
                                Text(
                                  'Execution Status: ${trace.status.toUpperCase()}',
                                  style: AppTheme.mono(fontSize: 10, color: AppColors.success),
                                ),
                              ],
                            ),
                          ),
                        ],
                      ],
                    ],
                  ),
                ),
              ),
            ],
          ),
        );
      }),
    );
  }

  Color _getNodeColor(String agentName) {
    if (agentName.contains('Intake')) return AppColors.info;
    if (agentName.contains('Triage')) return AppColors.warmBeige;
    if (agentName.contains('Action')) return AppColors.high;
    if (agentName.contains('Deadline')) return AppColors.medium;
    if (agentName.contains('Priority')) return AppColors.critical;
    if (agentName.contains('Orchestrator')) return AppColors.success;
    return AppColors.mutedSlate;
  }
}
