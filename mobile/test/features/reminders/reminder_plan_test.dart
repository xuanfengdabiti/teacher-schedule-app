import 'package:flutter_test/flutter_test.dart';
import 'package:teacher_schedule/features/reminders/reminder_models.dart';
import 'package:teacher_schedule/features/reminders/reminder_plan.dart';

import 'support/fake_reminders.dart';

void main() {
  final zone = shanghaiTimeZone();

  test('按上海时间提前分钟安排，且不再二次减 8 小时', () {
    // 2026-03-02 01:00 UTC = 09:00 Asia/Shanghai.
    final start = DateTime.utc(2026, 3, 2, 1);
    final now = DateTime.utc(2026, 3, 1, 12);
    final planned = planClassReminders(
      sessions: [fakeSession(id: 's-1', startAt: start)],
      minutes: 15,
      now: now,
      accountId: 'user-1',
      location: zone,
    );

    expect(planned, hasLength(1));
    expect(planned.single.triggerAt.location.name, 'Asia/Shanghai');
    expect(planned.single.triggerAt.year, 2026);
    expect(planned.single.triggerAt.month, 3);
    expect(planned.single.triggerAt.day, 2);
    expect(planned.single.triggerAt.hour, 8, reason: '09:00 提前 15 分钟');
    expect(planned.single.triggerAt.minute, 45);
    expect(
      planned.single.triggerAt.millisecondsSinceEpoch,
      start.millisecondsSinceEpoch - 15 * 60 * 1000,
      reason: '触发时刻只是原时刻减提前分钟',
    );
  });

  test('跳过已取消、他人、过去提醒时间和七天之外的课次', () {
    final now = DateTime.utc(2026, 3, 2, 0);
    final planned = planClassReminders(
      sessions: [
        fakeSession(id: 'future', startAt: DateTime.utc(2026, 3, 3, 1)),
        fakeSession(
          id: 'cancelled',
          startAt: DateTime.utc(2026, 3, 3, 2),
          status: 'cancelled',
        ),
        fakeSession(
          id: 'other-teacher',
          startAt: DateTime.utc(2026, 3, 3, 3),
          isMine: false,
        ),
        // Starts in 10 minutes; the 15-minute reminder time already passed.
        fakeSession(
          id: 'reminder-passed',
          startAt: now.add(const Duration(minutes: 10)),
        ),
        fakeSession(id: 'beyond-week', startAt: DateTime.utc(2026, 3, 12, 1)),
      ],
      minutes: 15,
      now: now,
      accountId: 'user-1',
      location: zone,
    );

    expect(planned.map((item) => item.sessionId), ['future']);
  });

  test('重复课次与稳定 ID 只排一次', () {
    final now = DateTime.utc(2026, 3, 2, 0);
    final session = fakeSession(
      id: 'session-dup',
      startAt: DateTime.utc(2026, 3, 3, 1),
    );
    final planned = planClassReminders(
      sessions: [session, session],
      minutes: 5,
      now: now,
      accountId: 'user-1',
      location: zone,
    );

    expect(planned, hasLength(1));
    expect(
      reminderNotificationId('session-dup'),
      planned.single.notificationId,
      reason: '刷新后仍是同一通知槽位',
    );
    expect(planned.single.notificationId, greaterThan(0));

    // A refreshed plan for the same session reuses the id and payload.
    final again = planClassReminders(
      sessions: [session],
      minutes: 5,
      now: now.add(const Duration(minutes: 1)),
      accountId: 'user-1',
      location: zone,
    );
    expect(again.single.notificationId, planned.single.notificationId);
    expect(again.single.payload, planned.single.payload);
  });

  test('最多安排 50 个未来课次，按时间先后取', () {
    final now = DateTime.utc(2026, 3, 2, 0);
    final sessions = [
      for (var index = 0; index < 60; index++)
        fakeSession(
          id: 'session-$index',
          startAt: DateTime.utc(
            2026,
            3,
            3,
            index % 24,
          ).add(Duration(minutes: index)),
        ),
    ];
    final planned = planClassReminders(
      sessions: sessions,
      minutes: 15,
      now: now,
      accountId: 'user-1',
      location: zone,
    );

    expect(planned, hasLength(50));
    for (var index = 1; index < planned.length; index++) {
      expect(
        planned[index].triggerAt.isBefore(planned[index - 1].triggerAt),
        isFalse,
      );
    }
  });

  test('通知内容为短中文，payload 带账号和课次', () {
    final now = DateTime.utc(2026, 3, 2, 0);
    final planned = planClassReminders(
      sessions: [
        fakeSession(
          id: 's-9',
          startAt: DateTime.utc(2026, 3, 3, 1),
          courseName: '高等数学',
        ),
      ],
      minutes: 10,
      now: now,
      accountId: 'user-1',
      location: zone,
    ).single;

    expect(planned.title, '高等数学 · 1-2节');
    expect(planned.body, contains('1-2节'));
    expect(planned.body, contains('教学楼A'));
    final payload = ReminderPayload.tryParse(planned.payload);
    expect(payload, isNotNull);
    expect(payload!.accountId, 'user-1');
    expect(payload.sessionId, 's-9');
    expect(payload.belongsTo('user-1'), isTrue);
    expect(payload.belongsTo('user-2'), isFalse);
    expect(ReminderPayload.tryParse('not-json'), isNull);
  });
}
