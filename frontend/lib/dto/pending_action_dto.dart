import 'email_state_dto.dart';

class PendingActionDto extends ActionStateDto {
  final String emailId;
  final String subject;
  final String priorityLevel;

  const PendingActionDto({
    required super.actionRef,
    required super.actionType,
    super.description,
    super.blocking,
    super.targetLink,
    super.confidence,
    super.status,
    super.createdAt,
    super.completedAt,
    required this.emailId,
    required this.subject,
    required this.priorityLevel,
  });

  factory PendingActionDto.fromJson(Map<String, dynamic> json) {
    final base = ActionStateDto.fromJson(json);
    return PendingActionDto(
      actionRef: base.actionRef,
      actionType: base.actionType,
      description: base.description,
      blocking: base.blocking,
      targetLink: base.targetLink,
      confidence: base.confidence,
      status: base.status,
      createdAt: base.createdAt,
      completedAt: base.completedAt,
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
