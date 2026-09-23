import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:home_widget/home_widget.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../models/email.dart';
import '../models/widget_snapshot.dart';
import 'widget_snapshot_builder.dart';

/// The platform surface the home-screen widgets are driven through.
/// Abstracted (like `PushPlatform` / `DeepLinkPort` / `ScheduleNotifier`) so
/// the refresh logic is unit-testable with no platform channel — Part
/// "mock platform-specific widget APIs" of the brief.
abstract class HomeWidgetPort {
  /// Persist one value into the shared store the Android widgets read.
  Future<void> saveData(String key, String value);

  /// Ask Android to re-render the given AppWidgetProvider.
  Future<void> updateWidget(String androidName);
}

/// Real implementation over the `home_widget` plugin. Never throws to the
/// caller: a home-screen widget failing to update must never break the app.
class HomeWidgetPlatform implements HomeWidgetPort {
  const HomeWidgetPlatform();

  @override
  Future<void> saveData(String key, String value) async {
    try {
      await HomeWidget.saveWidgetData<String>(key, value);
    } catch (e) {
      debugPrint('[HomeWidget] saveData($key) failed: $e');
    }
  }

  @override
  Future<void> updateWidget(String androidName) async {
    try {
      await HomeWidget.updateWidget(androidName: androidName);
    } catch (e) {
      debugPrint('[HomeWidget] updateWidget($androidName) failed: $e');
    }
  }
}

/// In-memory port for tests — records what would have been written/refreshed.
class FakeHomeWidgetPort implements HomeWidgetPort {
  final Map<String, String> data = {};
  final List<String> updated = [];

  @override
  Future<void> saveData(String key, String value) async => data[key] = value;

  @override
  Future<void> updateWidget(String androidName) async =>
      updated.add(androidName);
}

/// THE single place home-screen widgets are refreshed from.
///
/// Nothing else in the app talks to the widget platform: callers hand this
/// service the current inbox state and it decides what (if anything) the
/// widgets need to know. Two properties matter:
///
///  * **No backend involvement.** The snapshot is built from already-loaded
///    local state and cached in `shared_preferences`; the widgets themselves
///    only ever read that cache. They never call FastAPI, Gmail, Ollama, or
///    the ML classifier, and they keep rendering the last known snapshot
///    offline and across app restarts.
///  * **No redundant work.** A publish whose content matches what the widgets
///    already show is skipped entirely, so the once-a-second countdown
///    rebuilds and repeated syncs cost nothing.
class HomeWidgetService {
  static HomeWidgetService? _appInstance;

  /// Android plugin prefixes these names with the application package. The
  /// providers live in its `widgets` subpackage, not at the package root.
  static const String focusWidget = 'widgets.FocusNowWidgetProvider';
  static const String dashboardWidget =
      'widgets.AttentionDashboardWidgetProvider';
  static const String deadlineWidget =
      'widgets.DeadlineCountdownWidgetProvider';
  static const String quickActionsWidget = 'widgets.QuickActionsWidgetProvider';

  static const String snapshotKey = 'sorted_widget_snapshot';
  static const String _pendingDoneKey = 'sorted_widget_pending_done';

  factory HomeWidgetService() =>
      _appInstance ??= HomeWidgetService._(const HomeWidgetPlatform());

  @visibleForTesting
  factory HomeWidgetService.forTest(HomeWidgetPort port) =>
      HomeWidgetService._(port);

  HomeWidgetService._(this._port);

  final HomeWidgetPort _port;

  WidgetSnapshot? _lastPublished;

  @visibleForTesting
  WidgetSnapshot? get lastPublished => _lastPublished;

  /// Build a snapshot from [emails] and push it to the widgets if — and only
  /// if — what the user would see actually changed.
  ///
  /// Returns true when the platform was written to.
  Future<bool> publish(List<Email> emails, {DateTime? now}) async {
    final snapshot = WidgetSnapshotBuilder.build(emails, now: now);
    return publishSnapshot(snapshot);
  }

  Future<bool> publishSnapshot(WidgetSnapshot snapshot) async {
    final previous = _lastPublished;
    if (previous != null && previous.sameContentAs(snapshot)) return false;
    _lastPublished = snapshot;

    final encoded = jsonEncode(snapshot.toJson());
    await _port.saveData(snapshotKey, encoded);
    await _persistLocally(encoded);

    for (final name in const [focusWidget, dashboardWidget, deadlineWidget]) {
      await _port.updateWidget(name);
    }
    return true;
  }

  /// The Quick Actions widget is static shortcuts only — it never needs a data
  /// refresh, but it does need one nudge after a fresh install/update.
  Future<void> refreshQuickActions() => _port.updateWidget(quickActionsWidget);

  /// Cache the snapshot in the app's own storage too, so the app can restore
  /// what the widgets are showing after a process death without a fetch.
  Future<void> _persistLocally(String encoded) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(snapshotKey, encoded);
    } catch (e) {
      debugPrint('[HomeWidget] local snapshot persist failed: $e');
    }
  }

  /// The last snapshot this device published, if any.
  Future<WidgetSnapshot?> readCachedSnapshot() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(snapshotKey);
      if (raw == null || raw.isEmpty) return null;
      return WidgetSnapshot.fromJson(jsonDecode(raw) as Map<String, dynamic>);
    } catch (e) {
      debugPrint('[HomeWidget] cached snapshot read failed: $e');
      return null;
    }
  }

  // --- "Done" tapped on the Focus Now widget --------------------------
  //
  // A widget must not call the backend, and the backend is authoritative for
  // completion — so the tap records the intent locally, optimistically clears
  // the item from the widget, and the app flushes it on next foreground (see
  // InboxController.flushPendingWidgetCompletions).

  Future<void> queuePendingCompletion(String emailId) async {
    if (emailId.isEmpty) return;
    try {
      final prefs = await SharedPreferences.getInstance();
      final pending = prefs.getStringList(_pendingDoneKey) ?? <String>[];
      if (!pending.contains(emailId)) {
        pending.add(emailId);
        await prefs.setStringList(_pendingDoneKey, pending);
      }
    } catch (e) {
      debugPrint('[HomeWidget] queueing completion failed: $e');
    }
  }

  Future<List<String>> takePendingCompletions() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final pending = prefs.getStringList(_pendingDoneKey) ?? <String>[];
      if (pending.isNotEmpty) await prefs.remove(_pendingDoneKey);
      return pending;
    } catch (e) {
      debugPrint('[HomeWidget] reading pending completions failed: $e');
      return const [];
    }
  }

  @visibleForTesting
  static void resetAppInstanceForTest() => _appInstance = null;
}
