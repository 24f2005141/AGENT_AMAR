enum NotificationSeverity {
  normal,
  reminder,
  urgent,
  alarm;

  String get displayName {
    switch (this) {
      case NotificationSeverity.normal:
        return 'NORMAL';
      case NotificationSeverity.reminder:
        return 'REMINDER';
      case NotificationSeverity.urgent:
        return 'URGENT';
      case NotificationSeverity.alarm:
        return 'DEADLINE ALARM';
    }
  }
}

class NotificationEvent {
  final String id;
  final String emailId;
  final String emailSubject;
  final String notificationType;
  final NotificationSeverity severity;
  final String title;
  final String message;
  final DateTime createdAt;
  final bool requiresAlarm;
  final bool isDismissed;

  const NotificationEvent({
    required this.id,
    required this.emailId,
    required this.emailSubject,
    required this.notificationType,
    required this.severity,
    required this.title,
    required this.message,
    required this.createdAt,
    this.requiresAlarm = false,
    this.isDismissed = false,
  });

  NotificationEvent copyWith({
    String? id,
    String? emailId,
    String? emailSubject,
    String? notificationType,
    NotificationSeverity? severity,
    String? title,
    String? message,
    DateTime? createdAt,
    bool? requiresAlarm,
    bool? isDismissed,
  }) {
    return NotificationEvent(
      id: id ?? this.id,
      emailId: emailId ?? this.emailId,
      emailSubject: emailSubject ?? this.emailSubject,
      notificationType: notificationType ?? this.notificationType,
      severity: severity ?? this.severity,
      title: title ?? this.title,
      message: message ?? this.message,
      createdAt: createdAt ?? this.createdAt,
      requiresAlarm: requiresAlarm ?? this.requiresAlarm,
      isDismissed: isDismissed ?? this.isDismissed,
    );
  }

  factory NotificationEvent.fromJson(Map<String, dynamic> json) {
    return NotificationEvent(
      id: json['id'] ?? '',
      emailId: json['email_id'] ?? '',
      emailSubject: json['email_subject'] ?? '',
      notificationType: json['notification_type'] ?? 'NORMAL',
      severity: _parseSeverity(json['severity'] ?? json['notification_type']),
      title: json['title'] ?? '',
      message: json['message'] ?? '',
      createdAt: json['created_at'] != null ? DateTime.parse(json['created_at']) : DateTime.now(),
      requiresAlarm: json['requires_alarm'] ?? false,
      isDismissed: json['is_dismissed'] ?? false,
    );
  }

  static NotificationSeverity _parseSeverity(String? val) {
    switch (val?.toUpperCase()) {
      case 'ALARM':
      case 'DEADLINE_ALARM':
        return NotificationSeverity.alarm;
      case 'URGENT':
        return NotificationSeverity.urgent;
      case 'REMINDER':
      case 'USER_SCHEDULED_REMINDER':
        return NotificationSeverity.reminder;
      case 'NORMAL':
      default:
        return NotificationSeverity.normal;
    }
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'email_id': emailId,
    'email_subject': emailSubject,
    'notification_type': notificationType,
    'severity': severity.name,
    'title': title,
    'message': message,
    'created_at': createdAt.toIso8601String(),
    'requires_alarm': requiresAlarm,
    'is_dismissed': isDismissed,
  };
}
