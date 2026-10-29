import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:teacher_schedule/app/router.dart';
import 'package:teacher_schedule/core/providers.dart';
import 'package:teacher_schedule/core/storage/token_store.dart';
import 'package:teacher_schedule/features/auth/auth_controller.dart';
import 'package:teacher_schedule/features/auth/me.dart';
import 'package:teacher_schedule/features/reminders/reminder_models.dart';
import 'package:teacher_schedule/features/reminders/reminder_notification_service.dart';
import 'package:teacher_schedule/features/reminders/reminder_plan.dart';
import 'package:teacher_schedule/features/reminders/reminder_repository.dart';
import 'package:teacher_schedule/features/reminders/reminders_controller.dart';
import 'package:teacher_schedule/features/schedule/import_summary.dart';
import 'package:teacher_schedule/features/schedule/models.dart';
import 'package:teacher_schedule/features/schedule/schedule_repository.dart';

class FakeReminderApi implements ReminderRepository {
  ReminderPreferences preferences = ReminderPreferences.defaults;
  List<Session> sessions = const [];
  final List<Map<String, dynamic>> patchBodies = [];
  bool failPatch = false;
  bool failPreferences = false;
  int preferenceReads = 0;
  int sessionReads = 0;

  /// Optional gate so a test can hold the preference read in flight.
  Completer<void>? preferencesGate;

  @override
  Future<ReminderPreferences> fetchPreferences() async {
    preferenceReads++;
    final gate = preferencesGate;
    if (gate != null) {
      await gate.future;
    }
    if (failPreferences) {
      throw StateError('读取失败');
    }
    return preferences;
  }

  @override
  Future<ReminderPreferences> updatePreferences(
    Map<String, dynamic> fields, {
    required ReminderPreferences fallback,
  }) async {
    patchBodies.add(Map<String, dynamic>.from(fields));
    if (failPatch) {
      throw StateError('保存失败');
    }
    preferences = preferences.copyWith(
      enabled: fields['local_reminder_enabled'] as bool? ?? preferences.enabled,
      minutes: fields['local_reminder_minutes'] as int? ?? preferences.minutes,
    );
    return ReminderPreferences(
      enabled: fields.containsKey('local_reminder_enabled')
          ? fields['local_reminder_enabled'] as bool
          : fallback.enabled,
      minutes: fields.containsKey('local_reminder_minutes')
          ? fields['local_reminder_minutes'] as int
          : fallback.minutes,
    );
  }

  @override
  Future<List<Session>> fetchUpcomingSessions({
    required DateTime from,
    required DateTime to,
  }) async {
    sessionReads++;
    return sessions;
  }
}

class FakeNotificationService implements ReminderNotificationService {
  NotificationPermission permission = NotificationPermission.allowed;
  ReminderInitialization initializeResult = (launchPayload: null, error: null);
  final Map<int, ScheduledNotification> scheduled = {};
  final List<PlannedReminder> scheduleCalls = [];
  int cancelAllCalls = 0;
  int testNotifications = 0;
  void Function(String payload)? tapHandler;

  /// Optional gates so a test can hold an async platform step.
  Completer<void>? pendingGate;
  Completer<void>? scheduleGate;

  /// Every platform schedule attempt, recorded before any gate is awaited.
  final List<PlannedReminder> scheduleAttempts = [];

  @override
  void setTapHandler(void Function(String payload) handler) {
    tapHandler = handler;
  }

  @override
  Future<ReminderInitialization> initialize() async => initializeResult;

  @override
  Future<NotificationPermission> permissionStatus() async => permission;

  @override
  Future<bool> requestPermission() async {
    permission = NotificationPermission.allowed;
    return true;
  }

  @override
  Future<void> schedule(PlannedReminder reminder) async {
    scheduleAttempts.add(reminder);
    final gate = scheduleGate;
    scheduleGate = null;
    if (gate != null) {
      await gate.future;
    }
    scheduleCalls.add(reminder);
    scheduled[reminder.notificationId] = ScheduledNotification(
      id: reminder.notificationId,
      payload: reminder.payload,
    );
  }

  @override
  Future<void> showTest({required String title, required String body}) async {
    testNotifications++;
  }

  @override
  Future<void> cancel(int id) async {
    scheduled.remove(id);
  }

  @override
  Future<void> cancelAll() async {
    cancelAllCalls++;
    scheduled.clear();
  }

  @override
  Future<List<ScheduledNotification>> pending() async {
    final gate = pendingGate;
    if (gate != null) {
      await gate.future;
    }
    return scheduled.values.toList(growable: false);
  }
}

/// Auth state with a signed-in teacher, so reminder code sees a real account
/// id without network restore. [switchTo] simulates a real account change and
/// [startRestoring] models the login restore window.
class FakeSessionController extends AuthController {
  FakeSessionController(this.accountId, {bool startRestoring = false})
    : _restoring = startRestoring;

  String accountId;
  bool signedOut = false;
  bool _restoring;

  @override
  SessionState build() {
    if (_restoring) {
      return const SessionState.loading();
    }
    return signedOut ? const SessionState.signedOut() : _active(accountId);
  }

  /// Restore finished: the account from this phone's login material is active.
  void finishRestoring() {
    _restoring = false;
    state = _active(accountId);
  }

  void switchTo(String next) {
    accountId = next;
    state = _active(next);
  }

  void signOut() {
    signedOut = true;
    state = const SessionState.signedOut();
  }
}

SessionState _active(String id) {
  return SessionState(
    phase: SessionPhase.active,
    me: Me(
      id: id,
      email: 'teacher@example.invalid',
      emailVerified: true,
      teacherStatus: 'active',
      assignmentScopeVersion: 1,
      currentTerm: null,
      teacher: null,
      avatarUrl: null,
    ),
  );
}

SessionState fakeSignedIn(String accountId) => _active(accountId);

const SessionState signedOutSession = SessionState.signedOut();

ProviderContainer reminderContainer({
  FakeReminderApi? api,
  FakeNotificationService? notifications,
  String accountId = 'user-1',
  FakeSessionController? auth,
  bool withFakeAuth = true,
  GoRouter? router,
  ScheduleRepository? scheduleRepository,
}) {
  final container = ProviderContainer(
    overrides: [
      tokenStoreProvider.overrideWithValue(MemoryTokenStore()),
      reminderRepositoryProvider.overrideWithValue(api ?? FakeReminderApi()),
      reminderNotificationServiceProvider.overrideWithValue(
        notifications ?? FakeNotificationService(),
      ),
      if (withFakeAuth)
        authControllerProvider.overrideWith(
          () => auth ?? FakeSessionController(accountId),
        ),
      if (router != null) routerProvider.overrideWithValue(router),
      if (scheduleRepository != null)
        scheduleRepositoryProvider.overrideWithValue(scheduleRepository),
    ],
  );
  addTearDown(container.dispose);
  return container;
}

/// Minimal schedule source for coordinator refresh tests.
class FakeWeekScheduleRepository implements ScheduleRepository {
  FakeWeekScheduleRepository(this.session);

  Session session;
  int weekCalls = 0;

  @override
  Future<WeekSchedule> fetchWeek(DateTime monday) async {
    weekCalls++;
    return WeekSchedule(
      from: DateTime(monday.year, monday.month, monday.day),
      termId: 'term-1',
      termCode: '2026-1',
      sessions: [session],
    );
  }

  @override
  Future<Session> fetchSession(String sessionId) async => session;

  @override
  Future<TermCalendar> fetchCalendar(String termId) =>
      throw UnimplementedError();

  @override
  Future<List<Offering>> fetchOfferings() async => const [];

  @override
  Future<ImportSummary> fetchBootstrapSummary() async =>
      const ImportSummary(termName: '2026-1', sessionCount: 0, courseCount: 0);
}

Session fakeSession({
  required String id,
  required DateTime startAt,
  String courseName = '高等数学',
  String status = 'scheduled',
  bool isMine = true,
  int startPeriod = 1,
  int endPeriod = 2,
  String building = '教学楼A',
  String roomNo = '301',
}) {
  return Session(
    id: id,
    offeringId: 'offering-$id',
    offeringCode: 'OFF-001',
    courseCode: 'MATH101',
    courseName: courseName,
    teachingClass: const TeachingClass(
      id: 'class-1',
      classCode: 'CS2026-1',
      name: '计算机一班',
    ),
    date: DateTime(startAt.year, startAt.month, startAt.day),
    weekday: startAt.weekday,
    teachingWeek: 8,
    startPeriod: startPeriod,
    endPeriod: endPeriod,
    startAt: startAt,
    endAt: startAt.add(const Duration(minutes: 95)),
    classroom: Classroom(
      id: 'room-1',
      classroomCode: 'A301',
      campus: '',
      building: building,
      roomNo: roomNo,
    ),
    teachers: const [],
    status: status,
    statusReason: '',
    isMine: isMine,
    isMoved: false,
    version: 1,
  );
}
