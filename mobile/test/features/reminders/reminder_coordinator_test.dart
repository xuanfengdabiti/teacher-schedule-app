import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:teacher_schedule/features/reminders/reminder_coordinator.dart';
import 'package:teacher_schedule/features/reminders/reminder_models.dart';
import 'package:teacher_schedule/features/reminders/reminder_plan.dart';
import 'package:teacher_schedule/features/reminders/reminder_settings_page.dart';
import 'package:teacher_schedule/features/reminders/reminders_controller.dart';
import 'package:teacher_schedule/features/schedule/schedule_controller.dart';

import 'support/fake_reminders.dart';

class _RecordingService extends FakeNotificationService {
  final List<String> events = [];

  @override
  Future<void> cancelAll() async {
    events.add('cancelAll');
    await super.cancelAll();
  }

  @override
  Future<void> schedule(PlannedReminder reminder) async {
    events.add('schedule:${reminder.sessionId}');
    await super.schedule(reminder);
  }

  @override
  Future<void> showTest({required String title, required String body}) async {
    events.add('showTest');
    await super.showTest(title: title, body: body);
  }
}

GoRouter _detailRouter() {
  final router = GoRouter(
    initialLocation: '/home',
    routes: [
      GoRoute(path: '/home', builder: (context, state) => const Text('课表')),
      GoRoute(
        path: '/sessions/:id',
        builder: (context, state) => Text('详情-${state.pathParameters['id']}'),
      ),
    ],
  );
  addTearDown(router.dispose);
  return router;
}

Future<void> _pumpCoordinator(
  WidgetTester tester,
  ProviderContainer container,
  GoRouter router,
) async {
  await tester.pumpWidget(
    UncontrolledProviderScope(
      container: container,
      child: MaterialApp.router(
        routerConfig: router,
        builder: (context, child) =>
            ReminderCoordinator(child: child ?? const SizedBox.shrink()),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

Future<void> _pumpPlain(
  WidgetTester tester,
  ProviderContainer container, {
  String label = '课表',
}) async {
  await tester.pumpWidget(
    UncontrolledProviderScope(
      container: container,
      child: MaterialApp(home: ReminderCoordinator(child: Text(label))),
    ),
  );
  await tester.pump(const Duration(milliseconds: 50));
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('首次进入 active 账号即真实加载设置并可用', (tester) async {
    final api = FakeReminderApi()
      ..preferences = const ReminderPreferences(enabled: true, minutes: 15)
      ..sessions = [
        fakeSession(
          id: 'session-first',
          startAt: DateTime.now().add(const Duration(hours: 2)),
        ),
      ];
    final notifications = FakeNotificationService();
    final router = _detailRouter();
    final container = reminderContainer(
      api: api,
      notifications: notifications,
      auth: FakeSessionController('user-1'),
      router: router,
    );
    await _pumpCoordinator(tester, container, router);

    final state = container.read(remindersControllerProvider);
    expect(api.preferenceReads, greaterThanOrEqualTo(1));
    expect(state.saved, isNotNull);
    expect(state.loading, isFalse, reason: '首次加载完成后不能一直转圈');
    expect(state.permissionKnown, isTrue);
    expect(state.permission, NotificationPermission.allowed);
    expect(state.unavailableReason, isNull, reason: '初始化成功后不得显示不可用');
    expect(notifications.scheduled, isNotEmpty);
    expect(state.scheduledCount, 1);
  });

  testWidgets('暂停A→退出→登录B：旧清理完成后B偏好与提醒保留', (tester) async {
    final api = FakeReminderApi()
      ..preferences = const ReminderPreferences(enabled: true, minutes: 10)
      ..sessions = [
        fakeSession(
          id: 'session-a',
          startAt: DateTime.now().add(const Duration(hours: 2)),
        ),
      ];
    final notifications = FakeNotificationService();
    notifications.pendingGate = Completer<void>();
    final auth = FakeSessionController('user-a');
    final router = _detailRouter();
    final container = reminderContainer(
      api: api,
      notifications: notifications,
      auth: auth,
      router: router,
    );
    await _pumpPlain(tester, container);
    expect(notifications.pendingGate, isNotNull, reason: 'A 的计划被挂住');

    auth.signOut();
    await tester.pump(const Duration(milliseconds: 20));

    api.sessions = [
      fakeSession(
        id: 'session-b',
        startAt: DateTime.now().add(const Duration(hours: 4)),
      ),
    ];
    api.preferences = const ReminderPreferences(enabled: true, minutes: 30);
    auth.switchTo('user-b');
    await tester.pump(const Duration(milliseconds: 50));

    // Let the old A pass finish; its late work must not touch B.
    notifications.pendingGate!.complete();
    await tester.pumpAndSettle();

    final state = container.read(remindersControllerProvider);
    expect(state.saved?.minutes, 30, reason: 'B 的偏好不被旧清理擦掉');
    expect(state.scheduledCount, 1);
    expect(notifications.scheduled.keys, [
      reminderNotificationId('session-b'),
    ], reason: '只保留 B 的提醒');
  });

  testWidgets('连续课表刷新后第二次教室改值仍重排', (tester) async {
    final api = FakeReminderApi()
      ..preferences = const ReminderPreferences(enabled: true, minutes: 15)
      ..sessions = [
        fakeSession(
          id: 'session-edit',
          startAt: DateTime.now().add(const Duration(hours: 3)),
          building: '教学楼A',
        ),
      ];
    final notifications = FakeNotificationService();
    final auth = FakeSessionController('user-1');
    final router = _detailRouter();
    final scheduleRepository = FakeWeekScheduleRepository(api.sessions.single);
    final container = reminderContainer(
      api: api,
      notifications: notifications,
      auth: auth,
      router: router,
      scheduleRepository: scheduleRepository,
    );
    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp(home: ReminderCoordinator(child: const Text('课表'))),
      ),
    );
    await tester.pump(const Duration(milliseconds: 50));
    expect(notifications.scheduleCalls, hasLength(1));

    // First refresh: unchanged classroom, no rewrite expected.
    await container
        .read(scheduleControllerProvider.notifier)
        .ensureWeek(DateTime.now());
    await tester.pump(const Duration(milliseconds: 600));
    expect(notifications.scheduleCalls, hasLength(1));

    // Immediate second edit: same session, new classroom.
    api.sessions = [
      fakeSession(
        id: 'session-edit',
        startAt: api.sessions.single.startAt,
        building: '教学楼B',
      ),
    ];
    scheduleRepository.session = api.sessions.single;
    await container
        .read(scheduleControllerProvider.notifier)
        .refreshWeek(DateTime.now());
    await tester.pump(const Duration(milliseconds: 600));
    await tester.pumpAndSettle();

    expect(notifications.scheduleCalls, hasLength(2), reason: '第二次改值不得丢');
    expect(notifications.scheduleCalls.last.body, contains('教学楼B'));
  });

  testWidgets('初始化失败不阻塞界面', (tester) async {
    final notifications = FakeNotificationService()
      ..initializeResult = (launchPayload: null, error: '通知不可用。');
    final container = reminderContainer(
      notifications: notifications,
      auth: FakeSessionController('')..signedOut = true,
    );
    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: const MaterialApp(home: ReminderCoordinator(child: Text('课表'))),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('课表'), findsOneWidget);
    expect(
      container.read(remindersControllerProvider).unavailableReason,
      '通知不可用。',
    );
  });

  testWidgets('冷启动通知 payload 未登录时先挂起，不在无会话时打开详情', (tester) async {
    final notifications = FakeNotificationService()
      ..initializeResult = (
        launchPayload: ReminderPayload(
          accountId: 'user-1',
          sessionId: 'session-cold',
          triggerAtMillis: 0,
        ).encode(),
        error: null,
      );
    final router = _detailRouter();
    final container = reminderContainer(
      notifications: notifications,
      auth: FakeSessionController('')..signedOut = true,
      router: router,
    );
    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp.router(
          routerConfig: router,
          builder: (context, child) =>
              ReminderCoordinator(child: child ?? const SizedBox.shrink()),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('课表'), findsOneWidget);
    expect(find.textContaining('详情-'), findsNothing);
  });

  testWidgets('冷启动 payload 等待登录恢复完成后再打开对应账号的详情', (tester) async {
    final notifications = FakeNotificationService()
      ..initializeResult = (
        launchPayload: ReminderPayload(
          accountId: 'user-1',
          sessionId: 'session-cold',
          triggerAtMillis: 0,
        ).encode(),
        error: null,
      );
    final auth = FakeSessionController('user-1', startRestoring: true);
    final router = _detailRouter();
    final container = reminderContainer(
      notifications: notifications,
      auth: auth,
      router: router,
    );
    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp.router(
          routerConfig: router,
          builder: (context, child) =>
              ReminderCoordinator(child: child ?? const SizedBox.shrink()),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(
      find.textContaining('详情-'),
      findsNothing,
      reason: '恢复期间不得绕过登录直接打开详情',
    );

    auth.finishRestoring();
    await tester.pumpAndSettle();
    expect(find.text('详情-session-cold'), findsOneWidget);
  });

  testWidgets('冷启动 payload 属于已被换掉的账号时不打开旧课次', (tester) async {
    final notifications = FakeNotificationService()
      ..initializeResult = (
        launchPayload: ReminderPayload(
          accountId: 'user-old',
          sessionId: 'session-old',
          triggerAtMillis: 0,
        ).encode(),
        error: null,
      );
    final auth = FakeSessionController('user-new', startRestoring: true);
    final router = _detailRouter();
    final container = reminderContainer(
      notifications: notifications,
      auth: auth,
      router: router,
    );
    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp.router(
          routerConfig: router,
          builder: (context, child) =>
              ReminderCoordinator(child: child ?? const SizedBox.shrink()),
        ),
      ),
    );
    await tester.pumpAndSettle();
    auth.accountId = 'user-new';
    auth.finishRestoring();
    await tester.pumpAndSettle();
    expect(find.textContaining('详情-'), findsNothing);
  });

  testWidgets('换账号先取消旧计划再加载新账号', (tester) async {
    final api = FakeReminderApi()
      ..preferences = const ReminderPreferences(enabled: true, minutes: 10)
      ..sessions = [
        fakeSession(
          id: 'session-a',
          startAt: DateTime.now().add(const Duration(hours: 3)),
        ),
      ];
    final notifications = _RecordingService();
    final auth = FakeSessionController('user-a');
    final router = _detailRouter();
    final container = reminderContainer(
      api: api,
      notifications: notifications,
      auth: auth,
      router: router,
    );
    await _pumpPlain(tester, container);
    await tester.pumpAndSettle();
    expect(notifications.scheduled, isNotEmpty);

    auth.switchTo('user-b');
    await tester.pumpAndSettle();

    final cancelIndex = notifications.events.indexOf('cancelAll');
    expect(cancelIndex, isNonNegative, reason: '换号必须取消旧账号提醒');
    final newScheduleIndex = notifications.events.lastIndexWhere(
      (event) => event.startsWith('schedule:'),
    );
    expect(newScheduleIndex, isNonNegative, reason: '换号后应安排新账号提醒');
    expect(
      cancelIndex < newScheduleIndex,
      isTrue,
      reason: '取消旧计划必须在安排新账号提醒之前完成',
    );
    expect(
      container.read(remindersControllerProvider).saved?.minutes,
      10,
      reason: '换号后加载新账号偏好',
    );
  });

  testWidgets('退出登录取消全部提醒', (tester) async {
    final api = FakeReminderApi()
      ..preferences = const ReminderPreferences(enabled: true, minutes: 5)
      ..sessions = [
        fakeSession(
          id: 'session-b',
          startAt: DateTime.now().add(const Duration(hours: 2)),
        ),
      ];
    final notifications = _RecordingService();
    final auth = FakeSessionController('user-a');
    final router = _detailRouter();
    final container = reminderContainer(
      api: api,
      notifications: notifications,
      auth: auth,
      router: router,
    );
    await _pumpPlain(tester, container);
    await tester.pumpAndSettle();
    expect(notifications.scheduled, isNotEmpty);

    auth.signOut();
    await tester.pumpAndSettle();
    expect(notifications.scheduled, isEmpty);
    expect(notifications.events, contains('cancelAll'));
  });

  testWidgets('正常退出A并完全settle后登录B，提醒页真实显示B且可用', (tester) async {
    final api = FakeReminderApi()
      ..preferences = const ReminderPreferences(enabled: true, minutes: 10)
      ..sessions = [
        fakeSession(
          id: 'session-a',
          startAt: DateTime.now().add(const Duration(hours: 2)),
        ),
      ];
    final notifications = FakeNotificationService();
    final auth = FakeSessionController('user-a');
    final router = _detailRouter();
    final container = reminderContainer(
      api: api,
      notifications: notifications,
      auth: auth,
      router: router,
    );
    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp(
          home: ReminderCoordinator(child: const ReminderSettingsPage()),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(
      container.read(remindersControllerProvider).saved?.minutes,
      10,
      reason: 'A 已真实加载',
    );

    // Normal sign-out, fully settled: no pending cleanup is left over.
    auth.signOut();
    await tester.pumpAndSettle();

    api.preferences = const ReminderPreferences(enabled: true, minutes: 30);
    api.sessions = [
      fakeSession(
        id: 'session-b',
        startAt: DateTime.now().add(const Duration(hours: 5)),
      ),
    ];
    auth.switchTo('user-b');
    await tester.pumpAndSettle();

    final state = container.read(remindersControllerProvider);
    expect(state.saved?.minutes, 30, reason: '登录 B 后加载 B 的偏好');
    expect(state.loading, isFalse, reason: '不能停在 spinner');
    expect(find.byKey(const Key('reminderMinutes-30')), findsOneWidget);
    expect(
      tester
          .widget<SwitchListTile>(
            find.byKey(const Key('reminderEnabledSwitch')),
          )
          .onChanged,
      isNotNull,
      reason: 'B 提醒设置控件可用',
    );
    expect(notifications.scheduled.keys, [
      reminderNotificationId('session-b'),
    ], reason: '通知只保留 B 的课次');
  });

  testWidgets('登录B时偏好读取在途被通知重建打断，仍显示B且不停在spinner', (tester) async {
    final api = FakeReminderApi()
      ..preferences = const ReminderPreferences(enabled: true, minutes: 10)
      ..sessions = [
        fakeSession(
          id: 'session-a',
          startAt: DateTime.now().add(const Duration(hours: 2)),
        ),
      ];
    final notifications = FakeNotificationService();
    final auth = FakeSessionController('user-a');
    final router = _detailRouter();
    final container = reminderContainer(
      api: api,
      notifications: notifications,
      auth: auth,
      router: router,
    );
    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp(
          home: ReminderCoordinator(child: const ReminderSettingsPage()),
        ),
      ),
    );
    await tester.pumpAndSettle();

    auth.signOut();
    await tester.pumpAndSettle();

    api.preferences = const ReminderPreferences(enabled: true, minutes: 30);
    api.sessions = [
      fakeSession(
        id: 'session-b',
        startAt: DateTime.now().add(const Duration(hours: 5)),
      ),
    ];
    final gate = Completer<void>();
    api.preferencesGate = gate;
    auth.switchTo('user-b');
    await tester.pump(const Duration(milliseconds: 20));
    expect(api.preferenceReads, greaterThanOrEqualTo(2), reason: 'B 的读取已发出');

    // Release the in-flight read after the controller may have been rebuilt.
    gate.complete();
    api.preferencesGate = null;
    await tester.pumpAndSettle();

    final state = container.read(remindersControllerProvider);
    expect(state.saved?.minutes, 30, reason: '在途结果不能被重建丢弃');
    expect(state.loading, isFalse, reason: '不能停在 spinner');
    expect(notifications.scheduled.keys, [
      reminderNotificationId('session-b'),
    ], reason: '通知只保留 B 的课次');
  });

  testWidgets('通知点击经现有 router 进入详情，另一账号 payload 不打开', (tester) async {
    final notifications = _RecordingService();
    final auth = FakeSessionController('user-a');
    final router = GoRouter(
      initialLocation: '/home',
      routes: [
        GoRoute(path: '/home', builder: (context, state) => const Text('课表')),
        GoRoute(
          path: '/sessions/:id',
          builder: (context, state) => Text('详情-${state.pathParameters['id']}'),
        ),
      ],
    );
    addTearDown(router.dispose);
    final container = reminderContainer(
      notifications: notifications,
      auth: auth,
      router: router,
    );
    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp.router(
          routerConfig: router,
          builder: (context, child) =>
              ReminderCoordinator(child: child ?? const SizedBox.shrink()),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(notifications.tapHandler, isNotNull);

    // Another account's notification must be ignored entirely.
    notifications.tapHandler!(
      ReminderPayload(
        accountId: 'user-b',
        sessionId: 'session-foreign',
        triggerAtMillis: 0,
      ).encode(),
    );
    await tester.pumpAndSettle();
    expect(find.text('课表'), findsOneWidget);
    expect(find.textContaining('详情-'), findsNothing);

    notifications.tapHandler!(
      ReminderPayload(
        accountId: 'user-a',
        sessionId: 'session-a',
        triggerAtMillis: 0,
      ).encode(),
    );
    await tester.pumpAndSettle();
    expect(find.text('详情-session-a'), findsOneWidget);
  });
}
