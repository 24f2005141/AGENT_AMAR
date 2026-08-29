import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:agent_amar/models/notification_event.dart';
import 'package:agent_amar/services/notification_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('NotificationService Tests', () {
    late NotificationService service;

    setUp(() {
      SharedPreferences.setMockInitialValues({});
      service = NotificationService();
    });

    test('determineChannelId maps severity and alarm flags correctly', () {
      final alarmEvent = NotificationEvent(
        id: '1',
        emailId: 'email_1',
        emailSubject: 'Imminent Deadline',
        notificationType: 'deadline_escalation',
        severity: NotificationSeverity.alarm,
        title: 'DEADLINE ALARM',
        message: '5 minutes remaining',
        createdAt: DateTime.now(),
        requiresAlarm: true,
      );
      expect(service.determineChannelId(alarmEvent), NotificationService.channelUrgentId);

      final urgentEvent = NotificationEvent(
        id: '2',
        emailId: 'email_2',
        emailSubject: 'Urgent Task',
        notificationType: 'deadline_escalation',
        severity: NotificationSeverity.urgent,
        title: 'Deadline Approaching',
        message: 'Due in 3 hours',
        createdAt: DateTime.now(),
        requiresAlarm: false,
      );
      expect(service.determineChannelId(urgentEvent), NotificationService.channelUrgentId);

      final reminderEvent = NotificationEvent(
        id: '3',
        emailId: 'email_3',
        emailSubject: 'User Scheduled Followup',
        notificationType: 'user_reminder',
        severity: NotificationSeverity.reminder,
        title: 'Reminder',
        message: 'Check email',
        createdAt: DateTime.now(),
      );
      expect(service.determineChannelId(reminderEvent), NotificationService.channelRemindersId);

      final normalEvent = NotificationEvent(
        id: '4',
        emailId: 'email_4',
        emailSubject: 'New Email',
        notificationType: 'new_priority_email',
        severity: NotificationSeverity.normal,
        title: 'New Important Email',
        message: 'Placement notice',
        createdAt: DateTime.now(),
      );
      expect(service.determineChannelId(normalEvent), NotificationService.channelGeneralId);
    });

    test('Deduplication prevents re-notifying previously seen events', () async {
      await service.clearDeliveredHistory();

      final event = NotificationEvent(
        id: 'notif_101',
        emailId: 'email_test_1',
        emailSubject: 'Hackathon Registration',
        notificationType: 'deadline_escalation',
        severity: NotificationSeverity.urgent,
        title: 'Action Deadline',
        message: 'Closes at 5 PM',
        createdAt: DateTime.now(),
      );

      expect(service.isDelivered('notif_101'), false);

      // Showing the notification (or marking it delivered) records it in the deduplication set
      final firstShow = await service.showNotificationForEvent(event);
      expect(firstShow, true);
      expect(service.isDelivered('notif_101'), true);

      // Attempting to show the exact same notification again is skipped
      final secondShow = await service.showNotificationForEvent(event);
      expect(secondShow, false);
    });

    test('Notification payload parses correctly for tap navigation', () {
      const payloadString = '{"notification_id":"42","email_id":"email_tcs_01","type":"deadline_escalation","requires_alarm":true}';
      final payloadMap = jsonDecode(payloadString) as Map<String, dynamic>;

      expect(payloadMap['notification_id'], '42');
      expect(payloadMap['email_id'], 'email_tcs_01');
      expect(payloadMap['type'], 'deadline_escalation');
      expect(payloadMap['requires_alarm'], true);
    });
  });
}
