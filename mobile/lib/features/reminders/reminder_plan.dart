import 'package:timezone/data/latest.dart' as tzdata;
import 'package:timezone/timezone.dart' as tz;

import '../../core/json.dart';
import '../schedule/models.dart';
import 'reminder_models.dart';

const shanghaiTimeZoneName = 'Asia/Shanghai';

bool _timeZonesReady = false;

/// The app schedules reminders against the school's time zone, not the
/// phone's current zone.
tz.Location shanghaiTimeZone() {
  if (!_timeZonesReady) {
    tzdata.initializeTimeZones();
    _timeZonesReady = true;
  }
  return tz.getLocation(shanghaiTimeZoneName);
}

/// Stable, non-zero 31-bit notification id derived from the class-session id.
///
/// A refreshed schedule therefore targets the same notification slot instead
/// of creating duplicates.
int reminderNotificationId(String sessionId) {
  var hash = 0x811c9dc5;
  for (final unit in sessionId.codeUnits) {
    hash ^= unit;
    hash = (hash * 0x01000193) & 0xFFFFFFFF;
  }
  final id = hash & 0x7FFFFFFF;
  return id == 0 ? 1 : id;
}

class PlannedReminder {
  const PlannedReminder({
    required this.sessionId,
    required this.notificationId,
    required this.title,
    required this.body,
    required this.triggerAt,
    required this.payload,
  });

  final String sessionId;
  final int notificationId;
  final String title;
  final String body;
  final tz.TZDateTime triggerAt;
  final String payload;
}

/// Builds one-off reminders for the teacher's own, not cancelled classes in
/// the next seven days.
///
/// [now] is treated as an instant; [Session.startAt] is already a local
/// instant from the API. No extra time-zone shift is applied.
List<PlannedReminder> planClassReminders({
  required List<Session> sessions,
  required int minutes,
  required DateTime now,
  required String accountId,
  int limit = 50,
  tz.Location? location,
}) {
  if (accountId.isEmpty || limit <= 0) {
    return const [];
  }
  final zone = location ?? shanghaiTimeZone();
  final horizon = now.add(const Duration(days: 7));
  final seenSessions = <String>{};
  final seenIds = <int>{};
  final planned = <PlannedReminder>[];

  for (final session in sessions) {
    if (!session.isMine || session.status == 'cancelled') {
      continue;
    }
    if (session.id.isEmpty || !seenSessions.add(session.id)) {
      continue;
    }
    final start = session.startAt;
    if (!start.isAfter(now) || start.isAfter(horizon)) {
      continue;
    }
    // The reminder time may already have passed while the app was closed.
    final trigger = tz.TZDateTime.from(
      start,
      zone,
    ).subtract(Duration(minutes: minutes));
    if (!trigger.isAfter(now)) {
      continue;
    }
    final notificationId = reminderNotificationId(session.id);
    if (!seenIds.add(notificationId)) {
      continue;
    }
    final title = '${session.courseName} · ${session.periodsLabel}';
    final body =
        '${timeZh(session.startAt)} · ${session.periodsLabel} · '
        '${session.classroom.displayName}';
    planned.add(
      PlannedReminder(
        sessionId: session.id,
        notificationId: notificationId,
        title: title,
        body: body,
        triggerAt: trigger,
        payload: ReminderPayload(
          accountId: accountId,
          sessionId: session.id,
          triggerAtMillis: trigger.millisecondsSinceEpoch,
          contentHash: reminderContentHash(title, body),
        ).encode(),
      ),
    );
  }

  planned.sort((a, b) => a.triggerAt.compareTo(b.triggerAt));
  return planned.take(limit).toList(growable: false);
}
