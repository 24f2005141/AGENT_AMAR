class AgentTrace {
  final String agentName;
  final String status; // 'ok', 'partial', 'error'
  final double confidence;
  final String summary;
  final DateTime timestamp;
  final String? method;
  final Map<String, dynamic>? details;

  const AgentTrace({
    required this.agentName,
    required this.status,
    required this.confidence,
    required this.summary,
    required this.timestamp,
    this.method,
    this.details,
  });

  factory AgentTrace.fromJson(Map<String, dynamic> json) {
    return AgentTrace(
      agentName: json['agent_name'] ?? json['agent'] ?? 'Unknown Agent',
      status: json['status'] ?? 'ok',
      confidence: (json['confidence'] as num?)?.toDouble() ?? 1.0,
      summary: json['summary'] ?? json['reasoning_summary'] ?? '',
      timestamp: json['timestamp'] != null
          ? DateTime.parse(json['timestamp'])
          : DateTime.now(),
      method: json['method'],
      details: json['details'] as Map<String, dynamic>?,
    );
  }

  Map<String, dynamic> toJson() => {
    'agent_name': agentName,
    'status': status,
    'confidence': confidence,
    'summary': summary,
    'timestamp': timestamp.toIso8601String(),
    if (method != null) 'method': method,
    if (details != null) 'details': details,
  };
}
