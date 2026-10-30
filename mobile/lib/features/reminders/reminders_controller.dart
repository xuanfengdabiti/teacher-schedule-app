import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/network/api_exception.dart';
import '../../core/providers.dart';
import '../auth/auth_controller.dart';
import 'reminder_models.dart';
import 'reminder_notification_service.dart';
import 'reminder_plan.dart';
import 'reminder_repository.dart';

final reminderRepositoryProvider = Provider<ReminderRepository>(
  (ref) => ReminderRepository(ref.watch(apiClientProvider)),
);

final reminderNotificationServiceProvider =
    Provider<ReminderNotificationService>(
      (ref) => AndroidReminderNotificationService(),
    );

class RemindersState {
  const RemindersState({
    this.saved,
    this.draft,
    this.loading = true,
    this.saving = false,
    this.error,
    this.permission = NotificationPermission.unknown,
    this.permissionKnown = false,
    this.unavailableReason,
    this.scheduledCount = 0,
  });

  /// Last preference confirmed by the server; the effective setting.
  final ReminderPreferences? saved;

  /// User input not yet saved. Kept on failure so the page still shows it.
  final ReminderPreferences? draft;

  final bool loading;
  final bool saving;
  final String? error;
  final NotificationPermission permission;
  final bool permissionKnown;

  /// Non-null when the notification plugin cannot run on this device.
  final String? unavailableReason;
  final int scheduledCount;

  ReminderPreferences? get displayed => draft ?? saved;

  RemindersState copyWith({
    ReminderPreferences? saved,
    ReminderPreferences? draft,
    bool? loading,
    bool? saving,
    String? error,
    bool clearError = false,
    bool clearDraft = false,
    NotificationPermission? permission,
    bool? permissionKnown,
    String? unavailableReason,
    int? scheduledCount,
  }) {
    return RemindersState(
      saved: saved ?? this.saved,
      draft: clearDraft ? null : draft ?? this.draft,
      loading: loading ?? this.loading,
      saving: saving ?? this.saving,
      error: clearError ? null : error ?? this.error,
      permission: permission ?? this.permission,
      permissionKnown: permissionKnown ?? this.permissionKnown,
      unavailableReason: unavailableReason ?? this.unavailableReason,
      scheduledCount: scheduledCount ?? this.scheduledCount,
    );
  }
}

/// Loads the two reminder preferences, saves them with a two-field PATCH and
/// keeps the phone's scheduled notifications in sync.
class RemindersController extends Notifier<RemindersState> {
  int _generation = 0;
  Future<void>? _pass;
  bool _applyAgain = false;
  bool _disposed = false;

  ReminderRepository get _repository => ref.read(reminderRepositoryProvider);
  ReminderNotificationService get _notifications =>
      ref.read(reminderNotificationServiceProvider);

  String? get _accountId => ref.read(authControllerProvider).me?.id;

  @override
  RemindersState build() {
    _disposed = false;
    ref.onDispose(() => _disposed = true);
    // Account lifecycle (load, clear, switch) belongs to the root
    // ReminderCoordinator alone. Watching the account id here would rebuild
    // this notifier mid-load, dropping the in-flight result while the
    // coordinator already moved on to the new account, leaving a spinner.
    return const RemindersState();
  }

  /// Called when the plugin could not be initialized. The schedule remains
  /// fully usable; only reminder controls show as unavailable.
  void markUnavailable(String reason) {
    if (_disposed) {
      return;
    }
    state = state.copyWith(unavailableReason: reason, loading: false);
  }

  /// Clears local reminder state after sign-out or an account switch. Pending
  /// notifications have already been cancelled by the coordinator.
  void reset() {
    _generation++;
    if (_disposed) {
      return;
    }
    state = RemindersState(
      unavailableReason: state.unavailableReason,
      loading: false,
    );
  }

  Future<void> load() async {
    final accountId = _accountId;
    if (accountId == null || accountId.isEmpty) {
      return;
    }
    final generation = ++_generation;
    if (!_disposed) {
      state = state.copyWith(loading: true, clearError: true);
    }
    try {
      final preferences = await _repository.fetchPreferences();
      if (_disposed || generation != _generation) {
        return;
      }
      state = state.copyWith(
        saved: preferences,
        draft: null,
        loading: false,
        clearDraft: true,
        clearError: true,
      );
    } on ApiException catch (error) {
      if (_disposed || generation != _generation) {
        return;
      }
      state = state.copyWith(loading: false, error: error.message);
      return;
    } catch (error) {
      if (_disposed || generation != _generation) {
        return;
      }
      state = state.copyWith(
        loading: false,
        error: ApiException.fromUnknown(error).message,
      );
      return;
    }
    await refreshPermission();
    await applySchedule();
  }

  /// Saves the requested settings. Only fields that actually changed are
  /// PATCHed, and only ever `local_reminder_enabled`/`local_reminder_minutes`.
  Future<bool> save(ReminderPreferences next) async {
    if (_disposed) {
      return false;
    }
    final saved = state.saved;
    state = state.copyWith(draft: next, clearError: true);
    if (saved == null) {
      state = state.copyWith(error: '设置尚未加载，请稍后重试。');
      return false;
    }
    final fields = <String, dynamic>{
      if (next.enabled != saved.enabled) 'local_reminder_enabled': next.enabled,
      if (next.minutes != saved.minutes) 'local_reminder_minutes': next.minutes,
    };
    if (fields.isEmpty) {
      state = state.copyWith(clearDraft: true, saving: false);
      return true;
    }
    final generation = _generation;
    final accountId = _accountId;
    state = state.copyWith(saving: true);
    try {
      final confirmed = await _repository.updatePreferences(
        fields,
        fallback: next,
      );
      if (_disposed || generation != _generation || accountId != _accountId) {
        return false;
      }
      state = state.copyWith(
        saved: confirmed,
        saving: false,
        clearDraft: true,
        clearError: true,
      );
    } on ApiException catch (error) {
      if (_disposed || generation != _generation) {
        return false;
      }
      state = state.copyWith(saving: false, error: error.message);
      return false;
    } catch (error) {
      if (_disposed || generation != _generation) {
        return false;
      }
      state = state.copyWith(
        saving: false,
        error: ApiException.fromUnknown(error).message,
      );
      return false;
    }
    await applySchedule();
    return true;
  }

  Future<void> refreshPermission() async {
    final permission = await _notifications.permissionStatus();
    if (_disposed) {
      return;
    }
    state = state.copyWith(permission: permission, permissionKnown: true);
  }

  /// Android 13+ permission prompt. Returns the resulting permission state.
  Future<NotificationPermission> requestPermission() async {
    final granted = await _notifications.requestPermission();
    final permission = granted
        ? NotificationPermission.allowed
        : await _notifications.permissionStatus();
    if (_disposed) {
      return permission;
    }
    state = state.copyWith(permission: permission, permissionKnown: true);
    if (permission == NotificationPermission.allowed) {
      await applySchedule();
    }
    return permission;
  }

  /// Fire an immediate notification so the user can verify the phone side.
  Future<String?> sendTestNotification() async {
    if (state.unavailableReason != null) {
      return state.unavailableReason;
    }
    final permission = await _notifications.permissionStatus();
    if (_disposed) {
      return null;
    }
    state = state.copyWith(permission: permission, permissionKnown: true);
    if (permission != NotificationPermission.allowed) {
      return '通知权限未允许，测试提醒不会显示。';
    }
    try {
      await _notifications.showTest(title: '测试提醒', body: '本机通知正常，正式提醒会在上课前发出。');
      return null;
    } catch (_) {
      return '测试提醒没有发出，请稍后重试。';
    }
  }

  /// Rebuilds the phone's future notifications from the server schedule.
  ///
  /// Existing notifications are matched by stable id and payload (which also
  /// carries a content hash), so an unchanged refresh does not re-schedule,
  /// while a changed classroom or title replaces the same slot.
  Future<void> applySchedule() {
    final running = _pass;
    if (running != null) {
      _applyAgain = true;
      return running;
    }
    final pass = _runPasses();
    _pass = pass;
    return pass.whenComplete(() => _pass = null);
  }

  Future<void> _runPasses() async {
    do {
      _applyAgain = false;
      await _applyOnce();
    } while (_applyAgain && !_disposed);
  }

  Future<void> _applyOnce() async {
    final preferences = state.saved;
    final accountId = _accountId;
    if (_disposed ||
        preferences == null ||
        accountId == null ||
        accountId.isEmpty ||
        state.unavailableReason != null) {
      return;
    }
    final generation = _generation;
    if (!preferences.enabled) {
      await _cancelAllSafely();
      if (!_disposed && generation == _generation) {
        state = state.copyWith(scheduledCount: 0);
      }
      return;
    }
    final permission = await _notifications.permissionStatus();
    if (_disposed || generation != _generation) {
      return;
    }
    state = state.copyWith(permission: permission, permissionKnown: true);
    // Without a confirmed permission the page must not claim reminders are
    // active, and no new notification is scheduled.
    if (permission != NotificationPermission.allowed) {
      if (permission == NotificationPermission.denied) {
        await _cancelAllSafely();
      }
      if (!_disposed && generation == _generation) {
        state = state.copyWith(scheduledCount: 0);
      }
      return;
    }
    final now = DateTime.now();
    final from = DateTime(now.year, now.month, now.day);
    try {
      final sessions = await _repository.fetchUpcomingSessions(
        from: from,
        to: from.add(const Duration(days: 8)),
      );
      if (_disposed || generation != _generation) {
        return;
      }
      final planned = planClassReminders(
        sessions: sessions,
        minutes: preferences.minutes,
        now: now,
        accountId: accountId,
      );
      final pending = await _notifications.pending();
      if (_isStale(generation, accountId)) {
        return;
      }
      final pendingPayloads = {
        for (final item in pending) item.id: item.payload,
      };
      final desiredIds = {for (final item in planned) item.notificationId};
      for (final existing in pending) {
        if (_isStale(generation, accountId)) {
          return;
        }
        if (!desiredIds.contains(existing.id)) {
          await _notifications.cancel(existing.id);
        }
      }
      for (final item in planned) {
        // A sign-out/switch may have cancelled everything while this loop was
        // awaiting the platform; never re-schedule the old account's plan.
        if (_isStale(generation, accountId)) {
          return;
        }
        if (pendingPayloads[item.notificationId] == item.payload) {
          continue;
        }
        await _notifications.schedule(item);
      }
      if (!_isStale(generation, accountId)) {
        state = state.copyWith(scheduledCount: planned.length);
      }
    } catch (_) {
      if (!_isStale(generation, accountId)) {
        state = state.copyWith(error: '提醒安排没有更新成功，请稍后重试。');
      }
    }
  }

  /// True when a newer load/sign-out invalidated this pass, or the account
  /// changed while awaiting the notification platform.
  bool _isStale(int generation, String accountId) {
    return _disposed || generation != _generation || accountId != _accountId;
  }

  Future<void> _cancelAllSafely() async {
    try {
      await _notifications.cancelAll();
    } catch (_) {
      // Visibility stays degraded in the page; nothing else depends on it.
    }
  }

  /// Cancels every scheduled reminder and clears state for sign-out/switch.
  ///
  /// Cancels immediately so the user sees old reminders gone; then waits for
  /// an in-flight scheduling pass to stop and cancels once more, so a platform
  /// call that landed late cannot resurrect the previous account's plan.
  Future<void> clearForSignOut() async {
    _generation++;
    await _cancelAllSafely();
    final running = _pass;
    if (running != null) {
      try {
        await running;
      } catch (_) {
        // The pass reports its own failure; cancel still proceeds.
      }
    }
    await _cancelAllSafely();
    reset();
  }
}

final remindersControllerProvider =
    NotifierProvider<RemindersController, RemindersState>(
      RemindersController.new,
    );
