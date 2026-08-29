class SnoozeRequestDto {
  final DateTime snoozedUntil;

  const SnoozeRequestDto({required this.snoozedUntil});

  Map<String, dynamic> toJson() => {
        'snoozed_until': snoozedUntil.toUtc().toIso8601String(),
      };
}
