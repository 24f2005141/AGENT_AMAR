/// Abstraction over device-level (OS) scheduling of a future notification.
///
/// `NotificationService` is the production implementation (real
/// `flutter_local_notifications` `zonedSchedule`/`cancel`). Splitting this out
/// lets `LocalScheduleService`'s logic — stable ids, idempotent cancellation,
/// replace-not-duplicate reconciliation — be unit-tested with a plain
/// in-memory fake, with no platform channel involved (Part 14: "mock all
/// platform notification APIs").
abstract class ScheduleNotifier {
  /// Schedule an OS notification for event [id] to fire at [scheduledAt]
  /// (device-local wall-clock time, timezone-aware). Calling this again with
  /// the same [id] REPLACES that alarm — it never stacks a second one.
  ///
  /// [isAlarm] routes deadline-moment events to the high-urgency channel.
  Future<void> scheduleEvent({
    required int id,
    required String title,
    required String body,
    required DateTime scheduledAt,
    bool isAlarm = false,
    String? emailId,
  });

  /// Cancel the OS notification for [id]. Idempotent: safe for an id that was
  /// never scheduled, or already cancelled.
  Future<void> cancelEvent(int id);
}
