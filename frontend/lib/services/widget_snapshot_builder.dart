import '../models/agent_analysis.dart';
import '../models/email.dart';
import '../models/widget_snapshot.dart';

/// Turns the app's in-memory inbox state into the flat [WidgetSnapshot] the
/// home-screen widgets render.
///
/// Pure and platform-free on purpose: all the "what deserves attention"
/// decisions live here and are unit-tested, so the native widget code stays a
/// dumb renderer. The filtering deliberately reuses the SAME rules as the
/// homepage — `Email.isActive` (excludes completed, snoozed, and acknowledged
/// informational mail) plus the canonical mutually-exclusive
/// `primaryCategory`, so a Reply Needed email can never also be counted as an
/// Action Required one. Spam never reaches the app at all (the backend drops
/// it at every ingestion path), so there is nothing to re-filter here.
class WidgetSnapshotBuilder {
  const WidgetSnapshotBuilder._();

  static WidgetSnapshot build(List<Email> emails, {DateTime? now}) {
    final at = now ?? DateTime.now();
    final active = emails.where((e) => e.isActive).toList();

    final actions = active
        .where((e) => e.primaryCategory == PrimaryCategory.actionRequired)
        .toList();
    final replies = active
        .where((e) => e.primaryCategory == PrimaryCategory.replyRequired)
        .toList();

    // "Upcoming" = still ahead of the device clock. An expired deadline is
    // never advertised as an active countdown (it can still be the focus
    // item — an overdue task is exactly what needs attention).
    final upcoming = active
        .where((e) => e.analysis.deadline != null && e.analysis.deadline!.isAfter(at))
        .toList()
      ..sort((a, b) => a.analysis.deadline!.compareTo(b.analysis.deadline!));

    final focus = _selectFocus(active, at);

    return WidgetSnapshot(
      focus: focus == null ? null : _toFocusItem(focus),
      actionCount: actions.length,
      replyCount: replies.length,
      deadlineCount: upcoming.length,
      nextDeadline: upcoming.isEmpty
          ? null
          : WidgetDeadlineItem(
              emailId: upcoming.first.id,
              title: upcoming.first.subject,
              deadlineAt: upcoming.first.analysis.deadline!,
            ),
      generatedAt: at,
    );
  }

  /// The single item worth interrupting for. Ranked in explicit tiers rather
  /// than one fuzzy score, so the choice is predictable and testable:
  ///   1. overdue work (a passed deadline on an unresolved task)
  ///   2. a deadline inside the next 24h
  ///   3. critical priority
  ///   4. actionable buckets (Reply / Action) over informational ones
  ///   5. priority level, then soonest deadline, then most recent mail
  static Email? _selectFocus(List<Email> active, DateTime now) {
    if (active.isEmpty) return null;
    final ranked = List<Email>.of(active)
      ..sort((a, b) {
        for (final rank in <int Function(Email)>[
          (e) => _isOverdue(e, now) ? 0 : 1,
          (e) => _isDueWithin(e, now, const Duration(hours: 24)) ? 0 : 1,
          (e) => e.isCritical ? 0 : 1,
          (e) => _categoryRank(e.primaryCategory),
          (e) => _priorityRank(e.analysis.priority),
        ]) {
          final cmp = rank(a).compareTo(rank(b));
          if (cmp != 0) return cmp;
        }
        final da = a.analysis.deadline, db = b.analysis.deadline;
        if (da != null && db != null && da != db) return da.compareTo(db);
        if (da != null && db == null) return -1;
        if (da == null && db != null) return 1;
        return b.receivedAt.compareTo(a.receivedAt);
      });
    return ranked.first;
  }

  static bool _isOverdue(Email e, DateTime now) {
    final d = e.analysis.deadline;
    return d != null && d.isBefore(now);
  }

  static bool _isDueWithin(Email e, DateTime now, Duration window) {
    final d = e.analysis.deadline;
    return d != null && d.isAfter(now) && d.difference(now) <= window;
  }

  /// The canonical backend ordering: REPLY_REQUIRED > ACTION_REQUIRED >
  /// IMPORTANT > LOW_PRIORITY.
  static int _categoryRank(PrimaryCategory c) {
    switch (c) {
      case PrimaryCategory.replyRequired:
        return 0;
      case PrimaryCategory.actionRequired:
        return 1;
      case PrimaryCategory.important:
        return 2;
      case PrimaryCategory.lowPriority:
        return 3;
    }
  }

  static int _priorityRank(PriorityLevel p) {
    switch (p) {
      case PriorityLevel.critical:
        return 0;
      case PriorityLevel.high:
        return 1;
      case PriorityLevel.medium:
        return 2;
      case PriorityLevel.low:
        return 3;
    }
  }

  static WidgetFocusItem _toFocusItem(Email e) {
    return WidgetFocusItem(
      emailId: e.id,
      title: _focusTitle(e),
      subtitle: e.subject,
      category: e.primaryCategory.name,
      deadline: e.analysis.deadline,
      isCritical: e.isCritical,
    );
  }

  /// A verb-first line ("Reply to X" / "Action: X") — a widget should say what
  /// to do, not just restate the subject.
  static String _focusTitle(Email e) {
    switch (e.primaryCategory) {
      case PrimaryCategory.replyRequired:
        return 'Reply to ${e.senderName}';
      case PrimaryCategory.actionRequired:
        return e.analysis.actionDescription?.trim().isNotEmpty == true
            ? e.analysis.actionDescription!.trim()
            : 'Action: ${e.senderName}';
      case PrimaryCategory.important:
      case PrimaryCategory.lowPriority:
        return e.senderName;
    }
  }
}
