import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/router.dart';
import '../auth/auth_controller.dart';
import '../schedule/schedule_controller.dart';
import 'reminder_models.dart';
import 'reminder_notification_service.dart';
import 'reminders_controller.dart';

/// Thin app-level glue: initializes the notification plugin once, keeps the
/// phone's reminders in sync when the session or schedule changes, and routes
/// a tapped reminder through the existing GoRouter.
///
/// It deliberately adds no background polling. Reminders are refreshed when
/// the teacher opens the app, returns to it, or the visible schedule reloads.
class ReminderCoordinator extends ConsumerStatefulWidget {
  const ReminderCoordinator({super.key, required this.child});

  final Widget child;

  @override
  ConsumerState<ReminderCoordinator> createState() =>
      _ReminderCoordinatorState();
}

class _ReminderCoordinatorState extends ConsumerState<ReminderCoordinator>
    with WidgetsBindingObserver {
  String? _accountId;
  String? _pendingSessionId;
  String? _pendingPayloadAccount;
  bool _initialized = false;
  bool _navigationScheduled = false;
  bool _clearedForSignOut = false;
  Timer? _refreshDebounce;

  /// The sign-out/switch cancel currently running. A new account must wait for
  /// it before loading, or the old cleanup would wipe the new account's plan.
  Future<void>? _clearing;

  /// Bumped on every account change/clear so late continuations can stop.
  int _switchGeneration = 0;

  ReminderNotificationService get _notifications =>
      ref.read(reminderNotificationServiceProvider);

  RemindersController get _reminders =>
      ref.read(remindersControllerProvider.notifier);

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _notifications.setTapHandler(_handlePayload);
    _initialize();
    ref.listenManual(authControllerProvider, (previous, next) {
      _handleSession(next);
    }, fireImmediately: true);
    ref.listenManual(scheduleControllerProvider, (previous, next) {
      final changed = !identical(previous?.weeks, next.weeks);
      if (changed && !_isInitialEmpty(previous, next)) {
        _scheduleRefresh();
      }
    });
  }

  /// The controller starts with an empty map; that is not a schedule reload.
  bool _isInitialEmpty(ScheduleState? previous, ScheduleState next) {
    return (previous?.weeks.isEmpty ?? true) && next.weeks.isEmpty;
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _refreshDebounce?.cancel();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed && _initialized) {
      _scheduleRefresh(immediate: true);
    }
  }

  Future<void> _initialize() async {
    final result = await _notifications.initialize();
    if (!mounted) {
      return;
    }
    _initialized = true;
    if (result.error != null) {
      _reminders.markUnavailable(result.error!);
    }
    final launchPayload = result.launchPayload;
    if (launchPayload != null) {
      final parsed = ReminderPayload.tryParse(launchPayload);
      if (parsed != null) {
        _pendingSessionId = parsed.sessionId;
        _pendingPayloadAccount = parsed.accountId;
      }
    }
    // Auth restore may have completed before initialization did.
    _handleSession(ref.read(authControllerProvider));
  }

  void _handleSession(SessionState session) {
    // The plugin setup finishes first; _initialize re-runs this once ready.
    if (!_initialized) {
      return;
    }
    // While login restore is still running, keep a launch payload that belongs
    // to the account being restored. It is navigated after restore finishes.
    if (session.phase == SessionPhase.loading ||
        session.phase == SessionPhase.error) {
      return;
    }
    final nextId = session.me?.id;
    final usable =
        nextId != null &&
        nextId.isNotEmpty &&
        session.phase == SessionPhase.active &&
        session.canUseApp;

    if (!usable) {
      _clearRemindersOnce();
      return;
    }
    _clearedForSignOut = false;
    final previous = _accountId;
    if (previous == nextId) {
      _tryNavigate();
      return;
    }
    _accountId = nextId;
    if (previous != null) {
      // A real account switch: the previous account's pending target and its
      // launch payload must not open a course under the new account.
      _pendingSessionId = null;
      _pendingPayloadAccount = null;
    }
    final generation = ++_switchGeneration;
    unawaited(_activate(nextId, generation, hadAccount: previous != null));
    _tryNavigate();
  }

  /// Waits for any earlier cleanup, cancels the previous account's plan when
  /// switching accounts, then loads this account's preferences and schedule.
  Future<void> _activate(
    String accountId,
    int generation, {
    required bool hadAccount,
  }) async {
    final clearing = _clearing;
    if (clearing != null) {
      try {
        await clearing;
      } catch (_) {
        // The cleanup reports its own failure; loading still proceeds.
      }
      if (!mounted || generation != _switchGeneration) {
        return;
      }
    }
    if (hadAccount) {
      await _reminders.clearForSignOut();
      if (!mounted || generation != _switchGeneration) {
        return;
      }
    }
    final active = ref.read(authControllerProvider);
    if (active.me?.id != accountId ||
        active.phase != SessionPhase.active ||
        !active.canUseApp) {
      return;
    }
    await _reminders.load();
  }

  /// Sign-out and the restore path can report several times; the phone work
  /// runs once until the next usable session. The returned future is kept so
  /// the next sign-in can wait for this cancel to finish.
  void _clearRemindersOnce() {
    if (_clearedForSignOut) {
      return;
    }
    _clearedForSignOut = true;
    _accountId = null;
    _pendingSessionId = null;
    _pendingPayloadAccount = null;
    _refreshDebounce?.cancel();
    _switchGeneration++;
    final future = _reminders.clearForSignOut();
    _clearing = future;
    unawaited(
      future.whenComplete(() {
        if (identical(_clearing, future)) {
          _clearing = null;
        }
      }),
    );
  }

  void _handlePayload(String payload) {
    final parsed = ReminderPayload.tryParse(payload);
    if (parsed == null) {
      return;
    }
    final currentId = ref.read(authControllerProvider).me?.id ?? '';
    if (currentId.isNotEmpty && !parsed.belongsTo(currentId)) {
      // A reminder created for a previous account: never open its course.
      return;
    }
    // During login restore the account id is still empty; keep the payload so
    // _handleSession can navigate once the matching account is active.
    _pendingSessionId = parsed.sessionId;
    _pendingPayloadAccount = parsed.accountId;
    _tryNavigate();
  }

  void _tryNavigate() {
    if (_navigationScheduled) {
      return;
    }
    final session = ref.read(authControllerProvider);
    final pending = _pendingSessionId;
    if (pending == null ||
        session.phase != SessionPhase.active ||
        !session.canUseApp) {
      return;
    }
    final currentId = session.me?.id;
    if (_pendingPayloadAccount == null ||
        currentId == null ||
        _pendingPayloadAccount != currentId) {
      _pendingSessionId = null;
      _pendingPayloadAccount = null;
      return;
    }
    _navigationScheduled = true;
    _pendingSessionId = null;
    _pendingPayloadAccount = null;
    // The router's own redirect still applies, so an unauthenticated app
    // cannot jump straight to a course detail page.
    unawaited(
      Future<void>.microtask(() {
        _navigationScheduled = false;
        if (!mounted) {
          return;
        }
        ref.read(routerProvider).push('/sessions/$pending');
      }),
    );
  }

  /// Coalesces schedule-change refreshes. A resume refreshes immediately
  /// because it is the natural catch-up point after the app was backgrounded.
  void _scheduleRefresh({bool immediate = false}) {
    if (!_initialized) {
      return;
    }
    _refreshDebounce?.cancel();
    if (immediate) {
      _refreshNow();
      return;
    }
    _refreshDebounce = Timer(const Duration(milliseconds: 400), _refreshNow);
  }

  void _refreshNow() {
    final session = ref.read(authControllerProvider);
    if (session.phase != SessionPhase.active || !session.canUseApp) {
      return;
    }
    // No extra throttle here: a quick second schedule edit (e.g. another
    // classroom move) must still reach the phone. The 400ms debounce above
    // already collapses bursts.
    unawaited(_reminders.applySchedule());
  }

  @override
  Widget build(BuildContext context) {
    final session = ref.watch(authControllerProvider);
    if (session.phase == SessionPhase.active && session.canUseApp) {
      _tryNavigate();
    }
    return widget.child;
  }
}
