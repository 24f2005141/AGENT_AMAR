class ReminderCreateDto {
  final DateTime reminderAt;
  final String? actionRef;
  final String? note;

  const ReminderCreateDto({
    required this.reminderAt,
    this.actionRef,
    this.note,
  });

  Map<String, dynamic> toJson() => {
        'reminder_at': reminderAt.toUtc().toIso8601String(),
        if (actionRef != null && actionRef!.isNotEmpty) 'action_ref': actionRef,
        if (note != null && note!.isNotEmpty) 'note': note,
      };
}

class ReminderOutDto {
  final int id;
  final String? emailId;
  final String? actionRef;
  final DateTime reminderAt;
  final String reminderType;
  final String status;
  final String timezone;
  final String? note;
  final DateTime createdAt;
  final DateTime? triggeredAt;
  final DateTime? cancelledAt;

  const ReminderOutDto({
    required this.id,
    this.emailId,
    this.actionRef,
    required this.reminderAt,
    this.reminderType = 'USER_SCHEDULED',
    this.status = 'PENDING',
    this.timezone = 'UTC',
    this.note,
    required this.createdAt,
    this.triggeredAt,
    this.cancelledAt,
  });

  factory ReminderOutDto.fromJson(Map<String, dynamic> json) {
    return ReminderOutDto(
      id: json['id'] as int? ?? 0,
      emailId: json['email_id'] as String?,
      actionRef: json['action_ref'] as String?,
      reminderAt: json['reminder_at'] != null
          ? DateTime.parse(json['reminder_at'] as String)
          : DateTime.now(),
      reminderType: json['reminder_type'] as String? ?? 'USER_SCHEDULED',
      status: json['status'] as String? ?? 'PENDING',
      timezone: json['timezone'] as String? ?? 'UTC',
      note: json['note'] as String?,
      createdAt: json['created_at'] != null
          ? DateTime.parse(json['created_at'] as String)
          : DateTime.now(),
      triggeredAt: json['triggered_at'] != null
          ? DateTime.tryParse(json['triggered_at'] as String)
          : null,
      cancelledAt: json['cancelled_at'] != null
          ? DateTime.tryParse(json['cancelled_at'] as String)
          : null,
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'email_id': emailId,
        'action_ref': actionRef,
        'reminder_at': reminderAt.toIso8601String(),
        'reminder_type': reminderType,
        'status': status,
        'timezone': timezone,
        'note': note,
        'created_at': createdAt.toIso8601String(),
        'triggered_at': triggeredAt?.toIso8601String(),
        'cancelled_at': cancelledAt?.toIso8601String(),
      };
}
