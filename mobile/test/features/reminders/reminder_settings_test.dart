import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:teacher_schedule/features/reminders/reminder_models.dart';
import 'package:teacher_schedule/features/reminders/reminder_settings_page.dart';
import 'package:teacher_schedule/features/reminders/reminders_controller.dart';

import 'support/fake_reminders.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('保存只 PATCH 变化的两个提醒字段', () async {
    final api = FakeReminderApi()
      ..preferences = const ReminderPreferences(enabled: true, minutes: 15);
    final notifications = FakeNotificationService();
    final container = reminderContainer(api: api, notifications: notifications);
    await container.read(remindersControllerProvider.notifier).load();

    final saved = await container
        .read(remindersControllerProvider.notifier)
        .save(const ReminderPreferences(enabled: false, minutes: 15));

    expect(saved, isTrue);
    expect(api.patchBodies, hasLength(1));
    expect(api.patchBodies.single.keys, ['local_reminder_enabled']);
    expect(api.patchBodies.single['local_reminder_enabled'], false);
    expect(api.preferences.enabled, isFalse);
    expect(container.read(remindersControllerProvider).saved?.enabled, isFalse);
  });

  test('保存失败保留原生效设置与用户输入，可重试', () async {
    final api = FakeReminderApi()
      ..preferences = const ReminderPreferences(enabled: true, minutes: 15);
    final container = reminderContainer(api: api);
    await container.read(remindersControllerProvider.notifier).load();

    api.failPatch = true;
    final ok = await container
        .read(remindersControllerProvider.notifier)
        .save(const ReminderPreferences(enabled: true, minutes: 30));

    expect(ok, isFalse);
    final state = container.read(remindersControllerProvider);
    expect(state.saved?.minutes, 15, reason: '生效设置保持服务端旧值');
    expect(state.draft?.minutes, 30, reason: '用户输入保留');
    expect(state.error, isNotNull);

    api.failPatch = false;
    expect(
      await container
          .read(remindersControllerProvider.notifier)
          .save(const ReminderPreferences(enabled: true, minutes: 30)),
      isTrue,
    );
    expect(container.read(remindersControllerProvider).saved?.minutes, 30);
    expect(container.read(remindersControllerProvider).draft, isNull);
  });

  testWidgets('页面显示服务端已有的非预设提前分钟值且不擅自改', (tester) async {
    final api = FakeReminderApi()
      ..preferences = const ReminderPreferences(enabled: true, minutes: 45);
    final container = reminderContainer(api: api);
    await container.read(remindersControllerProvider.notifier).load();

    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: const MaterialApp(home: ReminderSettingsPage()),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('提前45分钟'), findsOneWidget);
    expect(find.byKey(const Key('reminderMinutes-45')), findsOneWidget);
    expect(api.patchBodies, isEmpty, reason: '仅加载不写回');
  });

  test('关闭开关后取消全部已安排提醒', () async {
    final now = DateTime.now();
    final api = FakeReminderApi()
      ..preferences = const ReminderPreferences(enabled: true, minutes: 15)
      ..sessions = [
        fakeSession(id: 's-1', startAt: now.add(const Duration(hours: 2))),
      ];
    final notifications = FakeNotificationService();
    final container = reminderContainer(api: api, notifications: notifications);
    await container.read(remindersControllerProvider.notifier).load();

    expect(notifications.scheduled, isNotEmpty);
    expect(container.read(remindersControllerProvider).scheduledCount, 1);

    await container
        .read(remindersControllerProvider.notifier)
        .save(const ReminderPreferences(enabled: false, minutes: 15));
    expect(notifications.cancelAllCalls, greaterThanOrEqualTo(1));
    expect(notifications.scheduled, isEmpty);
  });

  test('退出或换号取消全部提醒并清空状态', () async {
    final api = FakeReminderApi()
      ..preferences = const ReminderPreferences(enabled: true, minutes: 5)
      ..sessions = [
        fakeSession(
          id: 's-2',
          startAt: DateTime.now().add(const Duration(hours: 3)),
        ),
      ];
    final notifications = FakeNotificationService();
    final container = reminderContainer(api: api, notifications: notifications);
    await container.read(remindersControllerProvider.notifier).load();
    expect(notifications.scheduled, isNotEmpty);

    await container
        .read(remindersControllerProvider.notifier)
        .clearForSignOut();
    expect(notifications.cancelAllCalls, greaterThanOrEqualTo(1));
    expect(notifications.scheduled, isEmpty);
    expect(container.read(remindersControllerProvider).saved, isNull);
  });

  test('通知权限未允许时不排提醒也不虚构', () async {
    final api = FakeReminderApi()
      ..preferences = const ReminderPreferences(enabled: true, minutes: 10)
      ..sessions = [
        fakeSession(
          id: 's-3',
          startAt: DateTime.now().add(const Duration(hours: 5)),
        ),
      ];
    final notifications = FakeNotificationService()
      ..permission = NotificationPermission.denied;
    final container = reminderContainer(api: api, notifications: notifications);
    await container.read(remindersControllerProvider.notifier).load();

    expect(notifications.scheduled, isEmpty);
    expect(container.read(remindersControllerProvider).scheduledCount, 0);
    expect(
      container.read(remindersControllerProvider).permission,
      NotificationPermission.denied,
    );
  });

  testWidgets('插件初始化失败时页面显示不可用状态且课表不受影响', (tester) async {
    final api = FakeReminderApi()
      ..preferences = const ReminderPreferences(enabled: true, minutes: 15);
    final notifications = FakeNotificationService()
      ..initializeResult = (launchPayload: null, error: '本机通知暂时不可用。');
    final container = reminderContainer(api: api, notifications: notifications);
    final result = await notifications.initialize();
    container
        .read(remindersControllerProvider.notifier)
        .markUnavailable(result.error!);

    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: const MaterialApp(home: ReminderSettingsPage()),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('reminderUnavailable')), findsOneWidget);
    expect(find.text('本机通知暂时不可用。'), findsWidgets);
    expect(
      tester
          .widget<SwitchListTile>(
            find.byKey(const Key('reminderEnabledSwitch')),
          )
          .onChanged,
      isNull,
      reason: '不可用时不能假装已启用',
    );
  });
}
