/// A single device-scheduled future event (Category A: already known to the
/// device, so it must fire from the OS scheduler on the device clock — never
/// from an API fetch, a poll, or the app being open).
///
/// One type covers both user reminders and AI-extracted deadline alarms so
/// there is exactly ONE local scheduling system (see `LocalScheduleService`).
enum ScheduledEventType {
  /// "Remind me about this at 6pm" — created explicitly by the user.
  userReminder,

  /// "Your deadline is in 24h / 1h" — derived from an email's deadline.
  deadlineWarning,

  /// The deadline moment itself.
  deadlineDue;

  bool get isDeadline =>
      this == ScheduledEventType.deadlineWarning || this == ScheduledEventType.deadlineDue;

  static ScheduledEventType fromName(String? name) {
    switch (name) {
      case 'deadlineWarning':
        return ScheduledEventType.deadlineWarning;
      case 'deadlineDue':
        return ScheduledEventType.deadlineDue;
      case 'userReminder':
      default:
        return ScheduledEventType.userReminder;
    }
  }
}

enum ScheduledEventStatus {
  pending,
  fired,
  cancelled;

  static ScheduledEventStatus fromName(String? name) {
    switch (name) {
      case 'fired':
        return ScheduledEventStatus.fired;
      case 'cancelled':
        return ScheduledEventStatus.cancelled;
      case 'pending':
      default:
        return ScheduledEventStatus.pending;
    }
  }
}

class ScheduledEvent {
  /// Stable for the event's lifetime; also the OS notification id (offset by
  /// `NotificationService`), so cancel/reschedule always hit the same alarm.
  final int id;

  final ScheduledEventType type;

  /// Optional — a reminder need not belong to any email.
  final String? emailId;

  /// Stable identity used for reconciliation, so repeated syncs recognise an
  /// event they already scheduled instead of creating a duplicate:
  ///   * user reminder   → `reminder:<id>`
  ///   * deadline event  → `deadline:<emailId>:<offsetMinutes>`
  final String sourceKey;

  /// Notification title (e.g. "Sorted Reminder", "Deadline in 1 hour").
  final String title;

  /// Notification body — what the event is about.
  final String body;

  /// Always stored in UTC; `.toLocal()` for display and for scheduling.
  final DateTime scheduledAt;

  final ScheduledEventStatus status;
  final DateTime createdAt;
  final DateTime updatedAt;

  const ScheduledEvent({
    required this.id,
    required this.type,
    this.emailId,
    required this.sourceKey,
    required this.title,
    required this.body,
    required this.scheduledAt,
    this.status = ScheduledEventStatus.pending,
    required this.createdAt,
    required this.updatedAt,
  });

  bool get isPending => status == ScheduledEventStatus.pending;

  /// Deadline-at-time events are the only ones that ring as a full alarm.
  bool get isAlarm => type == ScheduledEventType.deadlineDue;

  ScheduledEvent copyWith({
    String? emailId,
    String? title,
    String? body,
    DateTime? scheduledAt,
    ScheduledEventStatus? status,
    DateTime? updatedAt,
  }) {
    return ScheduledEvent(
      id: id,
      type: type,
      emailId: emailId ?? this.emailId,
      sourceKey: sourceKey,
      title: title ?? this.title,
      body: body ?? this.body,
      scheduledAt: scheduledAt ?? this.scheduledAt,
      status: status ?? this.status,
      createdAt: createdAt,
      updatedAt: updatedAt ?? DateTime.now().toUtc(),
    );
  }

  factory ScheduledEvent.fromJson(Map<String, dynamic> json) {
    final created = json['created_at'] != null
        ? DateTime.parse(json['created_at'] as String).toUtc()
        : DateTime.now().toUtc();
    return ScheduledEvent(
      id: json['id'] as int,
      type: ScheduledEventType.fromName(json['type'] as String?),
      emailId: json['email_id'] as String?,
      sourceKey: json['source_key'] as String? ?? 'reminder:${json['id']}',
      title: json['title'] as String? ?? 'Sorted Reminder',
      body: json['body'] as String? ?? '',
      scheduledAt: DateTime.parse(json['scheduled_at'] as String).toUtc(),
      status: ScheduledEventStatus.fromName(json['status'] as String?),
      createdAt: created,
      updatedAt: json['updated_at'] != null
          ? DateTime.parse(json['updated_at'] as String).toUtc()
          : created,
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'type': type.name,
        'email_id': emailId,
        'source_key': sourceKey,
        'title': title,
        'body': body,
        'scheduled_at': scheduledAt.toUtc().toIso8601String(),
        'status': status.name,
        'created_at': createdAt.toUtc().toIso8601String(),
        'updated_at': updatedAt.toUtc().toIso8601String(),
      };
}

/// What `LocalScheduleService.syncDeadlines` needs to know about one email.
/// Deliberately a plain value type — the scheduling layer never depends on
/// `Email`, `InboxController`, or any DTO.
class DeadlineTarget {
  final String emailId;
  final String subject;
  final DateTime deadlineAt;
  final bool isCompleted;

  const DeadlineTarget({
    required this.emailId,
    required this.subject,
    required this.deadlineAt,
    this.isCompleted = false,
  });
}
