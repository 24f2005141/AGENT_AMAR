class GmailSyncResultDto {
  final String status;
  final String? fromHistoryId;
  final String? lastHistoryId;
  final DateTime? lastSyncAt;
  final DateTime? monitoringStartedAt;
  final List<String> newMessageIds;
  final int processed;
  final List<dynamic> results;
  final List<dynamic> errors;

  const GmailSyncResultDto({
    required this.status,
    this.fromHistoryId,
    this.lastHistoryId,
    this.lastSyncAt,
    this.monitoringStartedAt,
    this.newMessageIds = const [],
    this.processed = 0,
    this.results = const [],
    this.errors = const [],
  });

  bool get isBaselined => status == 'baselined';
  bool get isSynced => status == 'synced';
  bool get isHistoryExpiredRebaselined => status == 'history_expired_rebaselined';
  bool get isSkippedLocked => status == 'skipped_locked';
  bool get processedNewMail => processed > 0;

  factory GmailSyncResultDto.fromJson(Map<String, dynamic> json) {
    return GmailSyncResultDto(
      status: json['status'] as String? ?? 'synced',
      fromHistoryId: json['from_history_id'] as String?,
      lastHistoryId: json['last_history_id'] as String?,
      lastSyncAt: json['last_sync_at'] != null
          ? DateTime.tryParse(json['last_sync_at'] as String)
          : null,
      monitoringStartedAt: json['monitoring_started_at'] != null
          ? DateTime.tryParse(json['monitoring_started_at'] as String)
          : null,
      newMessageIds: (json['new_message_ids'] as List<dynamic>?)
              ?.map((e) => e.toString())
              .toList() ??
          const [],
      processed: json['processed'] as int? ?? 0,
      results: json['results'] as List<dynamic>? ?? const [],
      errors: json['errors'] as List<dynamic>? ?? const [],
    );
  }

  Map<String, dynamic> toJson() => {
        'status': status,
        'from_history_id': fromHistoryId,
        'last_history_id': lastHistoryId,
        'last_sync_at': lastSyncAt?.toIso8601String(),
        'monitoring_started_at': monitoringStartedAt?.toIso8601String(),
        'new_message_ids': newMessageIds,
        'processed': processed,
        'results': results,
        'errors': errors,
      };
}

class GmailSyncStatusDto {
  final bool monitoring;
  final String? accountEmail;
  final DateTime? monitoringStartedAt;
  final DateTime? lastSyncAt;
  final String? lastHistoryId;

  const GmailSyncStatusDto({
    required this.monitoring,
    this.accountEmail,
    this.monitoringStartedAt,
    this.lastSyncAt,
    this.lastHistoryId,
  });

  factory GmailSyncStatusDto.fromJson(Map<String, dynamic> json) {
    return GmailSyncStatusDto(
      monitoring: json['monitoring'] as bool? ?? false,
      accountEmail: json['account_email'] as String?,
      monitoringStartedAt: json['monitoring_started_at'] != null
          ? DateTime.tryParse(json['monitoring_started_at'] as String)
          : null,
      lastSyncAt: json['last_sync_at'] != null
          ? DateTime.tryParse(json['last_sync_at'] as String)
          : null,
      lastHistoryId: json['last_history_id'] as String?,
    );
  }

  Map<String, dynamic> toJson() => {
        'monitoring': monitoring,
        'account_email': accountEmail,
        'monitoring_started_at': monitoringStartedAt?.toIso8601String(),
        'last_sync_at': lastSyncAt?.toIso8601String(),
        'last_history_id': lastHistoryId,
      };
}

class MonitorSchedulerStatusDto {
  final String scheduler;
  final bool enabled;
  final DateTime? startedAt;
  final int deadlineCheckIntervalSeconds;
  final int reminderCheckIntervalSeconds;
  final DateTime? lastDeadlineCheck;
  final DateTime? lastReminderCheck;
  final int deadlineCycles;
  final int reminderCycles;
  final int deadlineFailures;
  final int reminderFailures;
  final String? lastError;

  const MonitorSchedulerStatusDto({
    required this.scheduler,
    required this.enabled,
    this.startedAt,
    this.deadlineCheckIntervalSeconds = 60,
    this.reminderCheckIntervalSeconds = 60,
    this.lastDeadlineCheck,
    this.lastReminderCheck,
    this.deadlineCycles = 0,
    this.reminderCycles = 0,
    this.deadlineFailures = 0,
    this.reminderFailures = 0,
    this.lastError,
  });

  factory MonitorSchedulerStatusDto.fromJson(Map<String, dynamic> json) {
    return MonitorSchedulerStatusDto(
      scheduler: json['scheduler'] as String? ?? 'stopped',
      enabled: json['enabled'] as bool? ?? false,
      startedAt: json['started_at'] != null
          ? DateTime.tryParse(json['started_at'] as String)
          : null,
      deadlineCheckIntervalSeconds:
          json['deadline_check_interval_seconds'] as int? ?? 60,
      reminderCheckIntervalSeconds:
          json['reminder_check_interval_seconds'] as int? ?? 60,
      lastDeadlineCheck: json['last_deadline_check'] != null
          ? DateTime.tryParse(json['last_deadline_check'] as String)
          : null,
      lastReminderCheck: json['last_reminder_check'] != null
          ? DateTime.tryParse(json['last_reminder_check'] as String)
          : null,
      deadlineCycles: json['deadline_cycles'] as int? ?? 0,
      reminderCycles: json['reminder_cycles'] as int? ?? 0,
      deadlineFailures: json['deadline_failures'] as int? ?? 0,
      reminderFailures: json['reminder_failures'] as int? ?? 0,
      lastError: json['last_error'] as String?,
    );
  }

  Map<String, dynamic> toJson() => {
        'scheduler': scheduler,
        'enabled': enabled,
        'started_at': startedAt?.toIso8601String(),
        'deadline_check_interval_seconds': deadlineCheckIntervalSeconds,
        'reminder_check_interval_seconds': reminderCheckIntervalSeconds,
        'last_deadline_check': lastDeadlineCheck?.toIso8601String(),
        'last_reminder_check': lastReminderCheck?.toIso8601String(),
        'deadline_cycles': deadlineCycles,
        'reminder_cycles': reminderCycles,
        'deadline_failures': deadlineFailures,
        'reminder_failures': reminderFailures,
        'last_error': lastError,
      };
}
