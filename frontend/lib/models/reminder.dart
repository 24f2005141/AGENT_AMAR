enum ReminderType {
  userScheduled,
  system,
  deadlineEscalation;

  String get displayName {
    switch (this) {
      case ReminderType.userScheduled:
        return 'USER-SCHEDULED';
      case ReminderType.system:
        return 'SYSTEM REMINDER';
      case ReminderType.deadlineEscalation:
        return 'DEADLINE ESCALATION';
    }
  }
}

enum ReminderStatus {
  pending,
  triggered,
  dismissed;
}

class ReminderItem {
  final String id;
  final String emailId;
  final String emailSubject;
  final String senderName;
  final DateTime reminderAt;
  final ReminderType reminderType;
  final ReminderStatus status;
  final String? actionDescription;

  const ReminderItem({
    required this.id,
    required this.emailId,
    required this.emailSubject,
    required this.senderName,
    required this.reminderAt,
    this.reminderType = ReminderType.userScheduled,
    this.status = ReminderStatus.pending,
    this.actionDescription,
  });

  bool get isDue => reminderAt.isBefore(DateTime.now());

  ReminderItem copyWith({
    String? id,
    String? emailId,
    String? emailSubject,
    String? senderName,
    DateTime? reminderAt,
    ReminderType? reminderType,
    ReminderStatus? status,
    String? actionDescription,
  }) {
    return ReminderItem(
      id: id ?? this.id,
      emailId: emailId ?? this.emailId,
      emailSubject: emailSubject ?? this.emailSubject,
      senderName: senderName ?? this.senderName,
      reminderAt: reminderAt ?? this.reminderAt,
      reminderType: reminderType ?? this.reminderType,
      status: status ?? this.status,
      actionDescription: actionDescription ?? this.actionDescription,
    );
  }

  factory ReminderItem.fromJson(Map<String, dynamic> json) {
    return ReminderItem(
      id: json['id'] ?? '',
      emailId: json['email_id'] ?? '',
      emailSubject: json['email_subject'] ?? '',
      senderName: json['sender_name'] ?? '',
      reminderAt: DateTime.parse(json['reminder_at']),
      reminderType: _parseType(json['reminder_type']),
      status: _parseStatus(json['status']),
      actionDescription: json['action_description'],
    );
  }

  static ReminderType _parseType(String? type) {
    switch (type?.toUpperCase()) {
      case 'SYSTEM':
        return ReminderType.system;
      case 'DEADLINE_ESCALATION':
        return ReminderType.deadlineEscalation;
      case 'USER_SCHEDULED':
      default:
        return ReminderType.userScheduled;
    }
  }

  static ReminderStatus _parseStatus(String? status) {
    switch (status?.toUpperCase()) {
      case 'TRIGGERED':
        return ReminderStatus.triggered;
      case 'DISMISSED':
        return ReminderStatus.dismissed;
      case 'PENDING':
      default:
        return ReminderStatus.pending;
    }
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'email_id': emailId,
    'email_subject': emailSubject,
    'sender_name': senderName,
    'reminder_at': reminderAt.toIso8601String(),
    'reminder_type': reminderType.displayName,
    'status': status.name,
    if (actionDescription != null) 'action_description': actionDescription,
  };
}
