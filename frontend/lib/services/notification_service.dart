import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../models/notification_event.dart';

typedef NotificationTapCallback = void Function(String? emailId, Map<String, dynamic> payload);

class NotificationService {
  static final NotificationService _instance = NotificationService._internal();
  factory NotificationService() => _instance;
  NotificationService._internal();

  final FlutterLocalNotificationsPlugin _plugin = FlutterLocalNotificationsPlugin();
  bool _isInitialized = false;
  NotificationTapCallback? _onNotificationTap;
  final Set<String> _deliveredNotificationIds = {};
  static const String _prefsKey = 'agent_amar_delivered_notification_ids';

  // Android Notification Channels
  static const String channelGeneralId = 'agent_amar_general';
  static const String channelGeneralName = 'AGENT AMAR General';
  static const String channelGeneralDesc = 'General notifications, new priority emails, announcements';

  static const String channelRemindersId = 'agent_amar_reminders';
  static const String channelRemindersName = 'AGENT AMAR Reminders';
  static const String channelRemindersDesc = 'User-created reminders and scheduled follow-ups';

  static const String channelUrgentId = 'agent_amar_urgent';
  static const String channelUrgentName = 'AGENT AMAR Urgent & Deadlines';
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
    const linuxSettings = LinuxInitializationSettings(defaultActionName: 'Open AGENT AMAR');

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
