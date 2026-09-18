/// The exact, already-decided content the Android home-screen widgets render.
///
/// This is deliberately a *flat, tiny* projection of app state, not a copy of
/// the inbox: the widgets are a glance surface, and everything they show has
/// already been filtered and ranked on the Flutter side (see
/// `WidgetSnapshotBuilder`). A widget never re-derives anything, and never
/// talks to the backend — it only renders these fields.
library;

class WidgetFocusItem {
  final String emailId;

  /// What the user must do, e.g. "Reply to Project Manager".
  final String title;

  /// One supporting line — sender, or the action description.
  final String subtitle;

  /// Canonical backend bucket, already mutually exclusive
  /// (`REPLY_REQUIRED` / `ACTION_REQUIRED` / …).
  final String category;

  final DateTime? deadline;
  final bool isCritical;

  const WidgetFocusItem({
    required this.emailId,
    required this.title,
    required this.subtitle,
    required this.category,
    this.deadline,
    this.isCritical = false,
  });

  Map<String, dynamic> toJson() => {
        'email_id': emailId,
        'title': title,
        'subtitle': subtitle,
        'category': category,
        'deadline': deadline?.toUtc().toIso8601String(),
        'is_critical': isCritical,
      };

  factory WidgetFocusItem.fromJson(Map<String, dynamic> json) => WidgetFocusItem(
        emailId: json['email_id'] as String? ?? '',
        title: json['title'] as String? ?? '',
        subtitle: json['subtitle'] as String? ?? '',
        category: json['category'] as String? ?? '',
        deadline: json['deadline'] != null
            ? DateTime.tryParse(json['deadline'] as String)?.toUtc()
            : null,
        isCritical: json['is_critical'] as bool? ?? false,
      );
}

class WidgetDeadlineItem {
  final String emailId;
  final String title;
  final DateTime deadlineAt;

  const WidgetDeadlineItem({
    required this.emailId,
    required this.title,
    required this.deadlineAt,
  });

  Map<String, dynamic> toJson() => {
        'email_id': emailId,
        'title': title,
        'deadline_at': deadlineAt.toUtc().toIso8601String(),
      };

  factory WidgetDeadlineItem.fromJson(Map<String, dynamic> json) => WidgetDeadlineItem(
        emailId: json['email_id'] as String? ?? '',
        title: json['title'] as String? ?? '',
        deadlineAt:
            DateTime.tryParse(json['deadline_at'] as String? ?? '')?.toUtc() ??
                DateTime.now().toUtc(),
      );
}

class WidgetSnapshot {
  /// The single item worth interrupting the user for, or null when nothing
  /// needs attention ("You're Sorted").
  final WidgetFocusItem? focus;

  /// Counts of ACTIVE items per canonical bucket. Mutually exclusive by
  /// construction — a Reply Needed email is never also counted as an Action.
  final int actionCount;
  final int replyCount;

  /// Upcoming (not expired, not completed) deadlines.
  final int deadlineCount;

  final WidgetDeadlineItem? nextDeadline;

  final DateTime generatedAt;

  const WidgetSnapshot({
    this.focus,
    this.actionCount = 0,
    this.replyCount = 0,
    this.deadlineCount = 0,
    this.nextDeadline,
    required this.generatedAt,
  });

  static WidgetSnapshot empty(DateTime now) => WidgetSnapshot(generatedAt: now);

  bool get isEmpty =>
      focus == null && actionCount == 0 && replyCount == 0 && deadlineCount == 0;

  Map<String, dynamic> toJson() => {
        'focus': focus?.toJson(),
        'action_count': actionCount,
        'reply_count': replyCount,
        'deadline_count': deadlineCount,
        'next_deadline': nextDeadline?.toJson(),
        'generated_at': generatedAt.toUtc().toIso8601String(),
      };

  factory WidgetSnapshot.fromJson(Map<String, dynamic> json) => WidgetSnapshot(
        focus: json['focus'] != null
            ? WidgetFocusItem.fromJson(json['focus'] as Map<String, dynamic>)
            : null,
        actionCount: json['action_count'] as int? ?? 0,
        replyCount: json['reply_count'] as int? ?? 0,
        deadlineCount: json['deadline_count'] as int? ?? 0,
        nextDeadline: json['next_deadline'] != null
            ? WidgetDeadlineItem.fromJson(json['next_deadline'] as Map<String, dynamic>)
            : null,
        generatedAt:
            DateTime.tryParse(json['generated_at'] as String? ?? '')?.toUtc() ??
                DateTime.now().toUtc(),
      );

  /// Content equality, ignoring [generatedAt] — used to skip a platform write
  /// when nothing a user could see has actually changed.
  bool sameContentAs(WidgetSnapshot other) {
    return actionCount == other.actionCount &&
        replyCount == other.replyCount &&
        deadlineCount == other.deadlineCount &&
        focus?.emailId == other.focus?.emailId &&
        focus?.title == other.focus?.title &&
        focus?.subtitle == other.focus?.subtitle &&
        focus?.deadline == other.focus?.deadline &&
        nextDeadline?.emailId == other.nextDeadline?.emailId &&
        nextDeadline?.deadlineAt == other.nextDeadline?.deadlineAt &&
        nextDeadline?.title == other.nextDeadline?.title;
  }
}
