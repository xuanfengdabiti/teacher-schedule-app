import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:teacher_schedule/features/reminders/reminder_models.dart';
import 'package:teacher_schedule/features/reminders/reminder_plan.dart';
import 'package:teacher_schedule/features/reminders/reminders_controller.dart';

import 'support/fake_reminders.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('同课次同触发时间但教室变化时重排，通知正文为新教室', () async {
    final start = DateTime.now().add(const Duration(hours: 3));
    final api = FakeReminderApi()
      ..preferences = const ReminderPreferences(enabled: true, minutes: 15);
    final notifications = FakeNotificationService();
    final container = reminderContainer(api: api, notifications: notifications);

    api.sessions = [
      fakeSession(id: 's-room', startAt: start, building: '教学楼A'),
    ];
    final notifier = container.read(remindersControllerProvider.notifier);
    await notifier.load();
    expect(notifications.scheduleCalls, hasLength(1));
    expect(notifications.scheduleCalls.single.body, contains('教学楼A'));

    // Unchanged refresh: same id, same payload -> no new platform call.
    await notifier.applySchedule();
    expect(notifications.scheduleCalls, hasLength(1), reason: '未变化不重排');

    // Only the classroom changed; trigger time and session id stay the same.
    api.sessions = [
      fakeSession(id: 's-room', startAt: start, building: '教学楼B'),
    ];
    await notifier.applySchedule();
    expect(notifications.scheduleCalls, hasLength(2), reason: '显示变化需重排');
    expect(
      notifications.scheduleCalls.last.notificationId,
      notifications.scheduleCalls.first.notificationId,
      reason: '仍是同一通知槽位',
    );
    expect(notifications.scheduleCalls.last.body, contains('教学楼B'));
    expect(
      notifications.scheduled.values.single.payload,
      contains('"c"'),
      reason: 'payload 带内容指纹',
    );
  });

  test('在途 pending 被退出打断后不再安排旧账号提醒', () async {
    final api = FakeReminderApi()
      ..preferences = const ReminderPreferences(enabled: true, minutes: 10)
      ..sessions = [
        fakeSession(
          id: 's-old',
          startAt: DateTime.now().add(const Duration(hours: 2)),
        ),
      ];
    final notifications = FakeNotificationService();
    notifications.pendingGate = Completer<void>();
    final container = reminderContainer(api: api, notifications: notifications);
    final notifier = container.read(remindersControllerProvider.notifier);

    final loading = notifier.load();
    await Future<void>.delayed(const Duration(milliseconds: 20));
    expect(notifications.pendingGate, isNotNull);

    final clearing = notifier.clearForSignOut();
    await Future<void>.delayed(const Duration(milliseconds: 10));
    notifications.pendingGate!.complete();
    await loading;
    await clearing;

    expect(notifications.scheduleAttempts, isEmpty, reason: '旧流程不得再排提醒');
    expect(notifications.scheduled, isEmpty);
    expect(container.read(remindersControllerProvider).saved, isNull);
  });

  test('第一笔 schedule 在途时退出，不继续排剩余旧课次也不取消新账号提醒', () async {
    final start = DateTime.now().add(const Duration(hours: 2));
    final api = FakeReminderApi()
      ..preferences = const ReminderPreferences(enabled: true, minutes: 10)
      ..sessions = [
        fakeSession(id: 's-first', startAt: start),
        fakeSession(
          id: 's-second',
          startAt: start.add(const Duration(hours: 1)),
        ),
      ];
    final notifications = FakeNotificationService();
    final scheduleGate = Completer<void>();
    notifications.scheduleGate = scheduleGate;
    final container = reminderContainer(api: api, notifications: notifications);
    final notifier = container.read(remindersControllerProvider.notifier);

    final loading = notifier.load();
    await Future<void>.delayed(const Duration(milliseconds: 20));
    expect(notifications.scheduleAttempts, hasLength(1));

    // A new account's reminder is already on the phone when the old pass ends.
    final newAccountId = reminderNotificationId('s-new-account');
    notifications.scheduled[newAccountId] = ScheduledNotification(
      id: newAccountId,
      payload: 'new-account',
    );

    final clearing = notifier.clearForSignOut();
    await Future<void>.delayed(const Duration(milliseconds: 10));
    scheduleGate.complete();
    await loading;
    await clearing;

    expect(notifications.scheduleAttempts, hasLength(1), reason: '不得继续排第二节');
    expect(notifications.scheduled, isEmpty, reason: '退出后全部取消');
    expect(
      notifications.scheduled.keys.contains(newAccountId),
      isFalse,
      reason: '统一取消不会被旧流程反向污染',
    );
  });
}
