import 'package:flutter/foundation.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';

import 'reminder_models.dart';
import 'reminder_plan.dart';

/// Result of the one-time plugin setup.
typedef ReminderInitialization = ({String? launchPayload, String? error});

/// The small notification surface the app needs.
///
/// It is an interface so widget tests can run without platform channels.
abstract interface class ReminderNotificationService {
  void setTapHandler(void Function(String payload) handler);

  Future<ReminderInitialization> initialize();

  Future<NotificationPermission> permissionStatus();

  Future<bool> requestPermission();

  Future<void> schedule(PlannedReminder reminder);

  Future<void> showTest({required String title, required String body});

  Future<void> cancel(int id);

  Future<void> cancelAll();

  Future<List<ScheduledNotification>> pending();
}

/// Android implementation backed by flutter_local_notifications 22.x.
///
/// Scheduling uses inexact alarms that may run in idle mode, so the app never
/// asks for exact-alarm access or battery-optimization exemptions.
class AndroidReminderNotificationService
    implements ReminderNotificationService {
  static const _channelId = 'class_reminders';
  static const _channelName = '上课提醒';
  static const _channelDescription = '按课程开始时间提醒';
  static const _iconName = 'ic_notification';
  static const _testNotificationId = 2147483647;

  final FlutterLocalNotificationsPlugin _plugin =
      FlutterLocalNotificationsPlugin();

  void Function(String payload)? _onTap;
  Future<ReminderInitialization>? _initialization;

  @override
  void setTapHandler(void Function(String payload) handler) {
    _onTap = handler;
  }

  @override
  Future<ReminderInitialization> initialize() {
    return _initialization ??= _initialize();
  }

  Future<ReminderInitialization> _initialize() async {
    if (defaultTargetPlatform != TargetPlatform.android) {
      return (launchPayload: null, error: '这台设备暂不支持本地上课提醒。');
    }
    try {
      final ready = await _plugin.initialize(
        settings: const InitializationSettings(
          android: AndroidInitializationSettings(_iconName),
        ),
        onDidReceiveNotificationResponse: (response) {
          final payload = response.payload;
          if (payload != null && payload.isNotEmpty) {
            _onTap?.call(payload);
          }
        },
      );
      if (ready != true) {
        return (launchPayload: null, error: '本机通知暂时不可用。');
      }
      String? launchPayload;
      final launch = await _plugin.getNotificationAppLaunchDetails();
      if (launch?.didNotificationLaunchApp ?? false) {
        launchPayload = launch?.notificationResponse?.payload;
      }
      return (launchPayload: launchPayload, error: null);
    } catch (_) {
      return (launchPayload: null, error: '本机通知暂时不可用，课表仍可正常使用。');
    }
  }

  AndroidFlutterLocalNotificationsPlugin? get _android => _plugin
      .resolvePlatformSpecificImplementation<
        AndroidFlutterLocalNotificationsPlugin
      >();

  @override
  Future<NotificationPermission> permissionStatus() async {
    try {
      final enabled = await _android?.areNotificationsEnabled();
      if (enabled == null) {
        return NotificationPermission.unknown;
      }
      return enabled
          ? NotificationPermission.allowed
          : NotificationPermission.denied;
    } catch (_) {
      return NotificationPermission.unknown;
    }
  }

  @override
  Future<bool> requestPermission() async {
    try {
      final granted = await _android?.requestNotificationsPermission();
      return granted == true;
    } catch (_) {
      return false;
    }
  }

  NotificationDetails get _details => const NotificationDetails(
    android: AndroidNotificationDetails(
      _channelId,
      _channelName,
      channelDescription: _channelDescription,
      importance: Importance.high,
      priority: Priority.high,
      icon: _iconName,
    ),
  );

  @override
  Future<void> schedule(PlannedReminder reminder) {
    return _plugin.zonedSchedule(
      id: reminder.notificationId,
      scheduledDate: reminder.triggerAt,
      notificationDetails: _details,
      androidScheduleMode: AndroidScheduleMode.inexactAllowWhileIdle,
      title: reminder.title,
      body: reminder.body,
      payload: reminder.payload,
    );
  }

  @override
  Future<void> showTest({required String title, required String body}) {
    return _plugin.show(
      id: _testNotificationId,
      title: title,
      body: body,
      notificationDetails: _details,
    );
  }

  @override
  Future<void> cancel(int id) => _plugin.cancel(id: id);

  @override
  Future<void> cancelAll() => _plugin.cancelAll();

  @override
  Future<List<ScheduledNotification>> pending() async {
    final requests = await _plugin.pendingNotificationRequests();
    return requests
        .map(
          (request) =>
              ScheduledNotification(id: request.id, payload: request.payload),
        )
        .toList(growable: false);
  }
}
