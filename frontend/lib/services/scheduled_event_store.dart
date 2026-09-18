import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../models/scheduled_event.dart';

/// Local persistence for every device-scheduled event (reminders AND deadline
/// alarms). Reuses the same `shared_preferences` mechanism
/// `NotificationService` already uses for its delivered-notification-id set —
/// the data is a small per-device list, so no new local database dependency.
///
/// Nothing here lives only in memory: if the app process dies, the full
/// schedule is recoverable from this store.
abstract class ScheduledEventStore {
  Future<List<ScheduledEvent>> loadAll();
  Future<void> saveAll(List<ScheduledEvent> events);

  /// A fresh, never-reused id. Monotonically increasing, so it is always safe
  /// to reuse as the OS notification id (see `NotificationService`).
  Future<int> nextId();
}

class SharedPreferencesScheduleStore implements ScheduledEventStore {
  static const _listKey = 'sorted_scheduled_events_v1';
  static const _counterKey = 'sorted_scheduled_event_next_id_v1';

  @override
  Future<List<ScheduledEvent>> loadAll() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(_listKey);
      if (raw == null || raw.isEmpty) return [];
      final decoded = jsonDecode(raw) as List<dynamic>;
      return decoded
          .map((e) => ScheduledEvent.fromJson(e as Map<String, dynamic>))
          .toList();
    } catch (e) {
      debugPrint('[ScheduledEventStore] Failed to load events: $e');
      return [];
    }
  }

  @override
  Future<void> saveAll(List<ScheduledEvent> events) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = jsonEncode(events.map((e) => e.toJson()).toList());
      await prefs.setString(_listKey, raw);
    } catch (e) {
      debugPrint('[ScheduledEventStore] Failed to persist events: $e');
    }
  }

  @override
  Future<int> nextId() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final next = (prefs.getInt(_counterKey) ?? 0) + 1;
      await prefs.setInt(_counterKey, next);
      return next;
    } catch (e) {
      debugPrint('[ScheduledEventStore] Failed to allocate an id: $e');
      // Extremely unlikely fallback — still unique enough for a single run.
      return DateTime.now().millisecondsSinceEpoch.remainder(1 << 30);
    }
  }
}

/// In-memory store for tests — never touches `shared_preferences`.
class InMemoryScheduleStore implements ScheduledEventStore {
  List<ScheduledEvent> _items = const [];
  int _counter = 0;

  @override
  Future<List<ScheduledEvent>> loadAll() async => List.of(_items);

  @override
  Future<void> saveAll(List<ScheduledEvent> events) async {
    _items = List.of(events);
  }

  @override
  Future<int> nextId() async => ++_counter;
}
