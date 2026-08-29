class MonitorDecisionOutDto {
  final String emailId;
  final String? deadlineRef;
  final String decision;
  final String reason;
  final int? notificationId;
  final bool requiresAlarm;

  const MonitorDecisionOutDto({
    required this.emailId,
    this.deadlineRef,
    required this.decision,
    required this.reason,
    this.notificationId,
    this.requiresAlarm = false,
  });

  factory MonitorDecisionOutDto.fromJson(Map<String, dynamic> json) {
    return MonitorDecisionOutDto(
      emailId: json['email_id'] as String? ?? '',
      deadlineRef: json['deadline_ref'] as String?,
      decision: json['decision'] as String? ?? 'NO_CHANGE',
      reason: json['reason'] as String? ?? '',
      notificationId: json['notification_id'] as int?,
      requiresAlarm: json['requires_alarm'] as bool? ?? false,
    );
  }

  Map<String, dynamic> toJson() => {
        'email_id': emailId,
        'deadline_ref': deadlineRef,
        'decision': decision,
        'reason': reason,
        'notification_id': notificationId,
        'requires_alarm': requiresAlarm,
      };
}

class MonitorCheckResultDto {
  final DateTime checkedAt;
  final int deadlinesEvaluated;
  final int remindersEvaluated;
  final int notificationsCreated;
  final List<MonitorDecisionOutDto> results;

  const MonitorCheckResultDto({
    required this.checkedAt,
    required this.deadlinesEvaluated,
    required this.remindersEvaluated,
    required this.notificationsCreated,
    this.results = const [],
  });

  factory MonitorCheckResultDto.fromJson(Map<String, dynamic> json) {
    return MonitorCheckResultDto(
      checkedAt: json['checked_at'] != null
          ? DateTime.parse(json['checked_at'] as String)
          : DateTime.now(),
      deadlinesEvaluated: json['deadlines_evaluated'] as int? ?? 0,
      remindersEvaluated: json['reminders_evaluated'] as int? ?? 0,
      notificationsCreated: json['notifications_created'] as int? ?? 0,
      results: (json['results'] as List<dynamic>?)
              ?.map((e) => MonitorDecisionOutDto.fromJson(e as Map<String, dynamic>))
              .toList() ??
          const [],
    );
  }

  Map<String, dynamic> toJson() => {
        'checked_at': checkedAt.toIso8601String(),
        'deadlines_evaluated': deadlinesEvaluated,
        'reminders_evaluated': remindersEvaluated,
        'notifications_created': notificationsCreated,
        'results': results.map((e) => e.toJson()).toList(),
      };
}
