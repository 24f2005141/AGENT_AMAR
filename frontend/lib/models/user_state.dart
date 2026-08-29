class UserState {
  final bool isViewed;
  final bool isCompleted;
  final DateTime? snoozedUntil;
  final bool isMonitoringActive;

  const UserState({
    this.isViewed = false,
    this.isCompleted = false,
    this.snoozedUntil,
    this.isMonitoringActive = true,
  });

  bool get isSnoozed {
    if (snoozedUntil == null) return false;
    return snoozedUntil!.isAfter(DateTime.now());
  }

  UserState copyWith({
    bool? isViewed,
    bool? isCompleted,
    DateTime? snoozedUntil,
    bool clearSnooze = false,
    bool? isMonitoringActive,
  }) {
    return UserState(
      isViewed: isViewed ?? this.isViewed,
      isCompleted: isCompleted ?? this.isCompleted,
      snoozedUntil: clearSnooze ? null : (snoozedUntil ?? this.snoozedUntil),
      isMonitoringActive: isMonitoringActive ?? this.isMonitoringActive,
    );
  }

  factory UserState.fromJson(Map<String, dynamic> json) {
    return UserState(
      isViewed: json['is_viewed'] ?? json['viewed'] ?? false,
      isCompleted: json['is_completed'] ?? json['completed'] ?? false,
      snoozedUntil: json['snoozed_until'] != null ? DateTime.tryParse(json['snoozed_until']) : null,
      isMonitoringActive: json['is_monitoring_active'] ?? json['monitoring_active'] ?? true,
    );
  }

  Map<String, dynamic> toJson() => {
    'is_viewed': isViewed,
    'is_completed': isCompleted,
    'snoozed_until': snoozedUntil?.toIso8601String(),
    'is_monitoring_active': isMonitoringActive,
  };
}
