class NotificationOutDto {
  final int id;
  final String? emailId;
  final String notificationType;
  final String severity;
  final String? reminderLevel;
  final bool requiresAlarm;
  final String status;
  final String? detail;
  final int? deadlineId;
  final int? reminderId;
  final DateTime createdAt;
  final DateTime? sentAt;

  const NotificationOutDto({
    required this.id,
    this.emailId,
    required this.notificationType,
    required this.severity,
    this.reminderLevel,
    this.requiresAlarm = false,
    required this.status,
    this.detail,
    this.deadlineId,
    this.reminderId,
    required this.createdAt,
    this.sentAt,
  });

  factory NotificationOutDto.fromJson(Map<String, dynamic> json) {
    return NotificationOutDto(
      id: json['id'] as int? ?? 0,
      emailId: json['email_id'] as String?,
      notificationType: json['notification_type'] as String? ?? 'new_priority_email',
      severity: json['severity'] as String? ?? 'NORMAL',
      reminderLevel: json['reminder_level'] as String?,
      requiresAlarm: json['requires_alarm'] as bool? ?? false,
      status: json['status'] as String? ?? 'PENDING',
      detail: json['detail'] as String?,
      deadlineId: json['deadline_id'] as int?,
      reminderId: json['reminder_id'] as int?,
      createdAt: json['created_at'] != null
          ? DateTime.parse(json['created_at'] as String)
          : DateTime.now(),
      sentAt: json['sent_at'] != null
          ? DateTime.tryParse(json['sent_at'] as String)
          : null,
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'email_id': emailId,
        'notification_type': notificationType,
        'severity': severity,
        'reminder_level': reminderLevel,
        'requires_alarm': requiresAlarm,
        'status': status,
        'detail': detail,
        'deadline_id': deadlineId,
        'reminder_id': reminderId,
        'created_at': createdAt.toIso8601String(),
        'sent_at': sentAt?.toIso8601String(),
      };
}
