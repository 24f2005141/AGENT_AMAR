import 'email_state_dto.dart';

class UpcomingDeadlineDto extends DeadlineStateDto {
  final String emailId;
  final String subject;
  final String priorityLevel;

  const UpcomingDeadlineDto({
    required super.deadlineRef,
    super.deadlineDatetime,
    super.sourceText,
    super.timezone,
    super.dateOnly,
    super.confidence,
    super.isAmbiguous,
    super.ambiguityReason,
    super.isPast,
    super.actionContext,
    super.relatedActionRef,
    super.isMonitoring,
    super.monitoringStartedAt,
    super.monitoringStoppedAt,
    required this.emailId,
    required this.subject,
    required this.priorityLevel,
  });

  factory UpcomingDeadlineDto.fromJson(Map<String, dynamic> json) {
    final base = DeadlineStateDto.fromJson(json);
    return UpcomingDeadlineDto(
      deadlineRef: base.deadlineRef,
      deadlineDatetime: base.deadlineDatetime,
      sourceText: base.sourceText,
      timezone: base.timezone,
      dateOnly: base.dateOnly,
      confidence: base.confidence,
      isAmbiguous: base.isAmbiguous,
      ambiguityReason: base.ambiguityReason,
      isPast: base.isPast,
      actionContext: base.actionContext,
      relatedActionRef: base.relatedActionRef,
      isMonitoring: base.isMonitoring,
      monitoringStartedAt: base.monitoringStartedAt,
      monitoringStoppedAt: base.monitoringStoppedAt,
      emailId: json['email_id'] as String? ?? '',
      subject: json['subject'] as String? ?? '',
      priorityLevel: json['priority_level'] as String? ?? 'LOW',
    );
  }

  @override
  Map<String, dynamic> toJson() {
    final map = super.toJson();
    map['email_id'] = emailId;
    map['subject'] = subject;
    map['priority_level'] = priorityLevel;
    return map;
  }
}
