import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_timezone/flutter_timezone.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:timezone/data/latest_all.dart' as tzdata;
import 'package:timezone/timezone.dart' as tz;
import '../models/notification_event.dart';
import 'schedule_notifier.dart';

typedef NotificationTapCallback = void Function(String? emailId, Map<String, dynamic> payload);

/// Also implements [ScheduleNotifier] — the device-local scheduling used by
/// `LocalScheduleService` for user reminders AND deadline warnings/alarms.
/// Backend-driven notifications and device-scheduled events share this one
/// plugin instance / these channels, but are two separate systems:
/// `showNotificationForEvent`/`showRawPush` (backend-driven, shown the moment
/// the data arrives) vs. `scheduleEvent`/`cancelEvent` (scheduled ahead of
/// time, fires on the device clock with no backend involved).
class NotificationService implements ScheduleNotifier {
  static final NotificationService _instance = NotificationService._internal();
  factory NotificationService() => _instance;
  NotificationService._internal();

  final FlutterLocalNotificationsPlugin _plugin = FlutterLocalNotificationsPlugin();
  bool _isInitialized = false;
  NotificationTapCallback? _onNotificationTap;
  final Set<String> _deliveredNotificationIds = {};
  static const String _prefsKey = 'agent_amar_delivered_notification_ids';

  // Reminder notification ids are offset well clear of backend notification
  // ids (parsed straight from a small DB primary key — see
  // `showNotificationForEvent`) so the two id spaces can never collide and
  // cancel/replace each other's OS notification.
  static const int _reminderNotificationIdBase = 900000000;

  bool _tzReady = false;

  // Android Notification Channels
  static const String channelGeneralId = 'agent_amar_general';
  static const String channelGeneralName = 'Sorted General';
  static const String channelGeneralDesc = 'General notifications, new priority emails, announcements';

  static const String channelRemindersId = 'agent_amar_reminders';
  static const String channelRemindersName = 'Sorted Reminders';
  static const String channelRemindersDesc = 'User-created reminders and scheduled follow-ups';

  static const String channelUrgentId = 'agent_amar_urgent';
  static const String channelUrgentName = 'Sorted Urgent & Deadlines';
  static const String channelUrgentDesc = 'Critical deadlines, approaching placement/internship cutoffs, and alarms';

  bool get isInitialized => _isInitialized;
  Set<String> get deliveredIds => Set.unmodifiable(_deliveredNotificationIds);

  Future<void> initialize({NotificationTapCallback? onNotificationTap}) async {
    if (_isInitialized) {
      _onNotificationTap = onNotificationTap;
      return;
    }
    _onNotificationTap = onNotificationTap;

    // Load persisted deduplication set
    await _loadDeliveredIds();

    const androidSettings = AndroidInitializationSettings('@mipmap/ic_launcher');
    const darwinSettings = DarwinInitializationSettings(
      requestAlertPermission: false,
      requestBadgePermission: false,
      requestSoundPermission: false,
    );
    const linuxSettings = LinuxInitializationSettings(defaultActionName: 'Open Sorted');

    const initSettings = InitializationSettings(
      android: androidSettings,
      iOS: darwinSettings,
      macOS: darwinSettings,
      linux: linuxSettings,
    );

    try {
      await _plugin
          .initialize(
            initSettings,
            onDidReceiveNotificationResponse: _handleNotificationResponse,
          )
          .timeout(const Duration(milliseconds: 1500));
      await _createAndroidChannels();
      _isInitialized = true;
    } catch (e) {
      debugPrint('[NotificationService] Initialization fallback (headless/mock): $e');
      _isInitialized = true;
    }
  }

  Future<void> _createAndroidChannels() async {
    if (kIsWeb) return;

    try {
      final androidPlugin = _plugin.resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin>();

      if (androidPlugin != null) {
        await androidPlugin.createNotificationChannel(
          const AndroidNotificationChannel(
            channelGeneralId,
            channelGeneralName,
            description: channelGeneralDesc,
            importance: Importance.defaultImportance,
          ),
        );

        await androidPlugin.createNotificationChannel(
          const AndroidNotificationChannel(
            channelRemindersId,
            channelRemindersName,
            description: channelRemindersDesc,
            importance: Importance.high,
          ),
        );

        await androidPlugin.createNotificationChannel(
          const AndroidNotificationChannel(
            channelUrgentId,
            channelUrgentName,
            description: channelUrgentDesc,
            importance: Importance.max,
            enableVibration: true,
            playSound: true,
          ),
        );
      }
    } catch (e) {
      debugPrint('[NotificationService] Android channel creation fallback: $e');
    }
  }

  Future<bool> requestPermissions() async {
    if (kIsWeb) return false;

    try {
      if (defaultTargetPlatform == TargetPlatform.android) {
        final androidPlugin = _plugin.resolvePlatformSpecificImplementation<
            AndroidFlutterLocalNotificationsPlugin>();
        final granted = await androidPlugin
            ?.requestNotificationsPermission()
            .timeout(const Duration(milliseconds: 1500));
        return granted ?? false;
      } else if (defaultTargetPlatform == TargetPlatform.iOS ||
          defaultTargetPlatform == TargetPlatform.macOS) {
        final iosPlugin = _plugin.resolvePlatformSpecificImplementation<
            IOSFlutterLocalNotificationsPlugin>();
        final granted = await iosPlugin
            ?.requestPermissions(
              alert: true,
              badge: true,
              sound: true,
            )
            .timeout(const Duration(milliseconds: 1500));
        return granted ?? false;
      }
    } catch (e) {
      debugPrint('[NotificationService] Permission request fallback: $e');
    }
    return false;
  }

  /// Android 12+ (S) requires the user to explicitly grant exact-alarm
  /// scheduling (Settings → Alarms & reminders) for a general-purpose app —
  /// Sorted is not in the "alarm clock" category the OS auto-grants it to.
  /// Ask once at startup; if declined (or on older/other platforms) reminders
  /// still fire via [AndroidScheduleMode.inexactAllowWhileIdle] — OS-batched
  /// to within a few minutes of the requested time, never "only on app
  /// reload". This is an honest platform limitation, not a bug: see Part 4 of
  /// the reminder redesign.
  Future<bool> requestExactAlarmPermission() async {
    if (kIsWeb || defaultTargetPlatform != TargetPlatform.android) return true;
    try {
      final androidPlugin = _plugin.resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin>();
      final granted = await androidPlugin
          ?.requestExactAlarmsPermission()
          .timeout(const Duration(milliseconds: 1500));
      return granted ?? false;
    } catch (e) {
      debugPrint('[NotificationService] Exact alarm permission fallback: $e');
      return false;
    }
  }

  /// Resolve the device's IANA timezone once, at app startup.
  ///
  /// Deliberately NOT done lazily inside [scheduleEvent]: that would put a
  /// platform-channel round-trip on every scheduling call, in the middle of
  /// whatever triggered it. Loading the tz database itself is pure Dart and
  /// stays lazy (see [_ensureTimeZoneData]); if this never runs, scheduling
  /// still works — `tz.local` falls back to UTC and the notification is
  /// scheduled by absolute instant, which is the same moment in real time.
  Future<void> initializeTimeZone() async {
    if (_tzReady) return;
    _ensureTimeZoneData();
    try {
      final name = await FlutterTimezone.getLocalTimezone()
          .timeout(const Duration(milliseconds: 1500));
      tz.setLocalLocation(tz.getLocation(name));
      _tzReady = true;
    } catch (e) {
      debugPrint('[NotificationService] Timezone init fallback (defaulting to UTC): $e');
    }
  }

  bool _tzDataReady = false;

  void _ensureTimeZoneData() {
    if (_tzDataReady) return;
    try {
      tzdata.initializeTimeZones();
      _tzDataReady = true;
    } catch (e) {
      debugPrint('[NotificationService] Timezone database load failed: $e');
    }
  }

  AndroidScheduleMode? _cachedScheduleMode;

  Future<AndroidScheduleMode> _preferredAndroidScheduleMode() async {
    final cached = _cachedScheduleMode;
    if (cached != null) return cached;
    if (kIsWeb || defaultTargetPlatform != TargetPlatform.android) {
      return _cachedScheduleMode = AndroidScheduleMode.inexactAllowWhileIdle;
    }
    try {
      final androidPlugin = _plugin.resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin>();
      final allowed = await androidPlugin
          ?.canScheduleExactNotifications()
          .timeout(const Duration(milliseconds: 1500));
      return _cachedScheduleMode = (allowed ?? false)
          ? AndroidScheduleMode.exactAllowWhileIdle
          : AndroidScheduleMode.inexactAllowWhileIdle;
    } catch (e) {
      debugPrint('[NotificationService] Exact-alarm capability check fallback: $e');
      return _cachedScheduleMode = AndroidScheduleMode.inexactAllowWhileIdle;
    }
  }

  // --- ScheduleNotifier (device-scheduled reminders + deadline alarms) ----
  //
  // Separate in purpose from showNotificationForEvent/showRawPush above
  // (backend-driven, shown the moment the data arrives): these schedule an
  // OS-level alarm ahead of time that fires on the DEVICE CLOCK, with no
  // backend call and no dependency on the app being open. See
  // `LocalScheduleService`.

  @override
  Future<void> scheduleEvent({
    required int id,
    required String title,
    required String body,
    required DateTime scheduledAt,
    bool isAlarm = false,
    String? emailId,
  }) async {
    try {
      _ensureTimeZoneData();
      final scheduledTz = tz.TZDateTime.from(scheduledAt, tz.local);
      final scheduleMode = await _preferredAndroidScheduleMode();
      final payload = jsonEncode({
        'type': isAlarm ? 'deadline_alarm' : 'scheduled_event',
        'scheduled_event_id': id,
        'requires_alarm': isAlarm,
        if (emailId != null) 'email_id': emailId,
      });
      final androidDetails = isAlarm
          ? const AndroidNotificationDetails(
              channelUrgentId,
              channelUrgentName,
              channelDescription: channelUrgentDesc,
              importance: Importance.max,
              priority: Priority.max,
              icon: '@mipmap/ic_launcher',
              color: Color(0xFFFF5252),
              category: AndroidNotificationCategory.alarm,
              enableVibration: true,
              playSound: true,
            )
          : const AndroidNotificationDetails(
              channelRemindersId,
              channelRemindersName,
              channelDescription: channelRemindersDesc,
              importance: Importance.high,
              priority: Priority.high,
              icon: '@mipmap/ic_launcher',
              color: Color(0xFFE8C170),
              category: AndroidNotificationCategory.reminder,
            );
      await _plugin.zonedSchedule(
        _reminderNotificationIdBase + id,
        title,
        body,
        scheduledTz,
        NotificationDetails(
          android: androidDetails,
          iOS: const DarwinNotificationDetails(
              presentAlert: true, presentBadge: true, presentSound: true),
          macOS: const DarwinNotificationDetails(
              presentAlert: true, presentBadge: true, presentSound: true),
        ),
        payload: payload,
        androidScheduleMode: scheduleMode,
        uiLocalNotificationDateInterpretation: UILocalNotificationDateInterpretation.absoluteTime,
      );
    } catch (e) {
      debugPrint('[NotificationService] Event scheduling fallback (headless/mock): $e');
    }
  }

  @override
  Future<void> cancelEvent(int id) async {
    try {
      await _plugin.cancel(_reminderNotificationIdBase + id);
    } catch (e) {
      debugPrint('[NotificationService] Event cancel fallback: $e');
    }
  }

  Future<void> _loadDeliveredIds() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final list = prefs.getStringList(_prefsKey);
      if (list != null) {
        _deliveredNotificationIds.addAll(list);
      }
    } catch (e) {
      debugPrint('[NotificationService] Failed to load delivered IDs: $e');
    }
  }

  Future<void> _persistDeliveredId(String notificationId) async {
    _deliveredNotificationIds.add(notificationId);
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setStringList(_prefsKey, _deliveredNotificationIds.toList());
    } catch (e) {
      debugPrint('[NotificationService] Failed to persist delivered ID: $e');
    }
  }

  bool isDelivered(String notificationId) {
    return _deliveredNotificationIds.contains(notificationId);
  }

  Future<void> clearDeliveredHistory() async {
    _deliveredNotificationIds.clear();
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove(_prefsKey);
    } catch (_) {}
  }

  String determineChannelId(NotificationEvent event) {
    if (event.requiresAlarm || event.severity == NotificationSeverity.alarm) {
      return channelUrgentId;
    }
    if (event.severity == NotificationSeverity.urgent) {
      return channelUrgentId;
    }
    if (event.severity == NotificationSeverity.reminder ||
        event.notificationType == 'user_reminder') {
      return channelRemindersId;
    }
    return channelGeneralId;
  }

  Future<bool> showNotificationForEvent(NotificationEvent event) async {
    if (event.isDismissed) return false;

    // Deduplication check
    if (isDelivered(event.id)) {
      return false;
    }

    final channelId = determineChannelId(event);
    final notificationId = int.tryParse(event.id) ?? event.id.hashCode.abs();

    final isUrgentOrAlarm = channelId == channelUrgentId;
    final isReminder = channelId == channelRemindersId;

    final androidDetails = AndroidNotificationDetails(
      channelId,
      channelId == channelUrgentId
          ? channelUrgentName
          : (isReminder ? channelRemindersName : channelGeneralName),
      channelDescription: channelId == channelUrgentId
          ? channelUrgentDesc
          : (isReminder ? channelRemindersDesc : channelGeneralDesc),
      importance: isUrgentOrAlarm
          ? Importance.max
          : (isReminder ? Importance.high : Importance.defaultImportance),
      priority: isUrgentOrAlarm
          ? Priority.max
          : (isReminder ? Priority.high : Priority.defaultPriority),
      icon: '@mipmap/ic_launcher',
      color: isUrgentOrAlarm ? const Color(0xFFFF5252) : const Color(0xFFE8C170),
      category: isUrgentOrAlarm
          ? AndroidNotificationCategory.alarm
          : (isReminder ? AndroidNotificationCategory.reminder : AndroidNotificationCategory.email),
    );

    const darwinDetails = DarwinNotificationDetails(
      presentAlert: true,
      presentBadge: true,
      presentSound: true,
    );

    final notificationDetails = NotificationDetails(
      android: androidDetails,
      iOS: darwinDetails,
      macOS: darwinDetails,
    );

    final payloadMap = {
      'notification_id': event.id,
      'email_id': event.emailId,
      'type': event.notificationType,
      'requires_alarm': event.requiresAlarm,
    };
    final payloadString = jsonEncode(payloadMap);

    try {
      await _plugin.show(
        notificationId,
        event.title,
        event.message,
        notificationDetails,
        payload: payloadString,
      );
    } catch (e) {
      debugPrint('[NotificationService] Native display fallback (mock/headless): $e');
    }

    // Record deduplication
    await _persistDeliveredId(event.id);
    return true;
  }

  /// Present a push received while the app is in the FOREGROUND (Phase 16).
  /// The system tray already handles background / terminated display; when the
  /// app is open we surface it locally so behaviour is consistent.
  Future<void> showRawPush({
    required String title,
    required String body,
    required Map<String, dynamic> data,
  }) async {
    final notificationId =
        data['notification_id']?.toString() ?? DateTime.now().millisecondsSinceEpoch.toString();
    if (isDelivered(notificationId)) return;

    final requiresAlarm = data['requires_alarm']?.toString() == 'true';
    final severity = (data['severity']?.toString() ?? 'NORMAL').toUpperCase();
    final channelId = requiresAlarm || severity == 'ALARM' || severity == 'URGENT'
        ? channelUrgentId
        : (data['type']?.toString() == 'user_reminder' || severity == 'REMINDER'
            ? channelRemindersId
            : channelGeneralId);
    final isUrgent = channelId == channelUrgentId;

    final details = NotificationDetails(
      android: AndroidNotificationDetails(
        channelId,
        isUrgent
            ? channelUrgentName
            : (channelId == channelRemindersId ? channelRemindersName : channelGeneralName),
        channelDescription: isUrgent
            ? channelUrgentDesc
            : (channelId == channelRemindersId ? channelRemindersDesc : channelGeneralDesc),
        importance: isUrgent ? Importance.max : Importance.defaultImportance,
        priority: isUrgent ? Priority.max : Priority.defaultPriority,
        icon: '@mipmap/ic_launcher',
      ),
      iOS: const DarwinNotificationDetails(presentAlert: true, presentBadge: true, presentSound: true),
    );

    try {
      await _plugin.show(
        int.tryParse(notificationId) ?? notificationId.hashCode.abs(),
        title,
        body,
        details,
        payload: jsonEncode({
          'notification_id': notificationId,
          'email_id': data['email_id'],
          'type': data['type'],
          'requires_alarm': requiresAlarm,
        }),
      );
    } catch (e) {
      debugPrint('[NotificationService] Foreground push display fallback: $e');
    }
    await _persistDeliveredId(notificationId);
  }

  Future<int> syncBackendNotifications(List<NotificationEvent> events) async {
    int displayedCount = 0;
    for (final event in events) {
      final shown = await showNotificationForEvent(event);
      if (shown) {
        displayedCount++;
      }
    }
    return displayedCount;
  }

  void _handleNotificationResponse(NotificationResponse response) {
    final payload = response.payload;
    if (payload != null && payload.isNotEmpty) {
      try {
        final map = jsonDecode(payload) as Map<String, dynamic>;
        final emailId = map['email_id'] as String?;
        _onNotificationTap?.call(emailId, map);
      } catch (e) {
        debugPrint('[NotificationService] Error decoding notification payload: $e');
        _onNotificationTap?.call(null, {'raw': payload});
      }
    }
  }

  Future<Map<String, dynamic>?> checkLaunchNotification() async {
    try {
      final details = await _plugin
          .getNotificationAppLaunchDetails()
          .timeout(const Duration(milliseconds: 1500));
      if (details != null && details.didNotificationLaunchApp && details.notificationResponse != null) {
        final payload = details.notificationResponse?.payload;
        if (payload != null && payload.isNotEmpty) {
          return jsonDecode(payload) as Map<String, dynamic>;
        }
      }
    } catch (e) {
      debugPrint('[NotificationService] Launch notification fallback: $e');
    }
    return null;
  }
}
