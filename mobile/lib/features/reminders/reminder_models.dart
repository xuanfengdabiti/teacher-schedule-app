import 'dart:convert';

/// System notification permission as far as the app can verify it.
///
/// [unknown] is used when the platform cannot answer; the UI must not present
/// that as allowed.
enum NotificationPermission { allowed, denied, unknown }

/// The two server preferences that control local class reminders.
class ReminderPreferences {
  const ReminderPreferences({required this.enabled, required this.minutes});

  static const defaults = ReminderPreferences(enabled: true, minutes: 15);

  final bool enabled;

  /// Minutes before the class start. The backend accepts 0–180 and existing
  /// values outside 5/10/15/30 are kept as-is.
  final int minutes;

  ReminderPreferences copyWith({bool? enabled, int? minutes}) {
    return ReminderPreferences(
      enabled: enabled ?? this.enabled,
      minutes: minutes ?? this.minutes,
    );
  }

  factory ReminderPreferences.fromJson(
    Map<String, dynamic> json, {
    ReminderPreferences fallback = defaults,
  }) {
    final enabledValue = json['local_reminder_enabled'];
    final minutesValue = json['local_reminder_minutes'];
    final enabled = enabledValue is bool ? enabledValue : fallback.enabled;
    final parsedMinutes = minutesValue is int
        ? minutesValue
        : minutesValue is num
        ? minutesValue.toInt()
        : int.tryParse(minutesValue?.toString() ?? '');
    final minutes =
        parsedMinutes != null && parsedMinutes >= 0 && parsedMinutes <= 180
        ? parsedMinutes
        : fallback.minutes;
    return ReminderPreferences(enabled: enabled, minutes: minutes);
  }
}

/// Data carried by a class-reminder notification.
///
/// The account id keeps another teacher's old notification from opening a
/// course after an account switch on the same phone. [contentHash] covers the
/// visible text, so a changed classroom or course name re-schedules the same
/// notification slot instead of keeping the old wording.
class ReminderPayload {
  const ReminderPayload({
    required this.accountId,
    required this.sessionId,
    required this.triggerAtMillis,
    this.contentHash = 0,
  });

  final String accountId;
  final String sessionId;
  final int triggerAtMillis;
  final int contentHash;

  bool belongsTo(String accountId) =>
      accountId.isNotEmpty && accountId == this.accountId;

  String encode() {
    return jsonEncode({
      'v': 1,
      'account': accountId,
      'session': sessionId,
      'at': triggerAtMillis,
      'c': contentHash,
    });
  }

  static ReminderPayload? tryParse(String? raw) {
    if (raw == null || raw.isEmpty) {
      return null;
    }
    try {
      final decoded = jsonDecode(raw);
      if (decoded is! Map) {
        return null;
      }
      final account = decoded['account']?.toString() ?? '';
      final session = decoded['session']?.toString() ?? '';
      if (account.isEmpty || session.isEmpty) {
        return null;
      }
      final at = decoded['at'];
      final content = decoded['c'];
      return ReminderPayload(
        accountId: account,
        sessionId: session,
        triggerAtMillis: at is int ? at : int.tryParse('$at') ?? 0,
        contentHash: content is int ? content : int.tryParse('$content') ?? 0,
      );
    } catch (_) {
      return null;
    }
  }
}

/// Stable 31-bit hash of the notification's visible text.
int reminderContentHash(String title, String body) {
  var hash = 0x811c9dc5;
  for (final unit in '$title\n$body'.codeUnits) {
    hash ^= unit;
    hash = (hash * 0x01000193) & 0xFFFFFFFF;
  }
  return hash & 0x7FFFFFFF;
}

class ScheduledNotification {
  const ScheduledNotification({required this.id, this.payload});

  final int id;
  final String? payload;
}
