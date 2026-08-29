import 'agent_trace.dart';

enum PriorityLevel {
  low,
  medium,
  high,
  critical;

  static PriorityLevel fromString(String? value) {
    switch (value?.toUpperCase()) {
      case 'CRITICAL':
        return PriorityLevel.critical;
      case 'HIGH':
        return PriorityLevel.high;
      case 'MEDIUM':
        return PriorityLevel.medium;
      case 'LOW':
      default:
        return PriorityLevel.low;
    }
  }

  String get displayName {
    switch (this) {
      case PriorityLevel.critical:
        return 'CRITICAL';
      case PriorityLevel.high:
        return 'HIGH';
      case PriorityLevel.medium:
        return 'MEDIUM';
      case PriorityLevel.low:
        return 'LOW';
    }
  }
}

class AgentAnalysis {
  final String category;
  final PriorityLevel priority;
  final bool actionRequired;
  final String? actionType;
  final String? actionDescription;
  final DateTime? deadline;
  final double confidence;
  final String reasoningSummary;
  final List<AgentTrace> traces;
  final Map<String, dynamic>? metadata;

  const AgentAnalysis({
    required this.category,
    required this.priority,
    required this.actionRequired,
    this.actionType,
    this.actionDescription,
    this.deadline,
    this.confidence = 1.0,
    required this.reasoningSummary,
    this.traces = const [],
    this.metadata,
  });

  factory AgentAnalysis.fromJson(Map<String, dynamic> json) {
    return AgentAnalysis(
      category: json['category'] ?? json['final_category'] ?? 'GENERAL',
      priority: PriorityLevel.fromString(json['priority'] ?? json['priority_level']),
      actionRequired: json['action_required'] ?? false,
      actionType: json['action_type'] ?? json['primary_action_type'],
      actionDescription: json['action_description'],
      deadline: json['deadline'] != null ? DateTime.tryParse(json['deadline']) : null,
      confidence: (json['confidence'] as num?)?.toDouble() ?? 1.0,
      reasoningSummary: json['reasoning_summary'] ?? '',
      traces: (json['agent_trace'] as List<dynamic>?)
              ?.map((t) => AgentTrace.fromJson(t as Map<String, dynamic>))
              .toList() ??
          [],
      metadata: json['metadata'] as Map<String, dynamic>?,
    );
  }

  Map<String, dynamic> toJson() => {
    'category': category,
    'priority': priority.displayName,
    'action_required': actionRequired,
    'action_type': actionType,
    'action_description': actionDescription,
    'deadline': deadline?.toIso8601String(),
    'confidence': confidence,
    'reasoning_summary': reasoningSummary,
    'agent_trace': traces.map((t) => t.toJson()).toList(),
    if (metadata != null) 'metadata': metadata,
  };
}
