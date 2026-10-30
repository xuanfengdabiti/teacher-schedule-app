import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/json.dart';
import '../../core/network/api_exception.dart';
import '../../core/providers.dart';
import '../auth/auth_controller.dart';
import 'models.dart';

class ScheduleState {
  const ScheduleState({required this.selectedDate, this.weeks = const {}});

  final DateTime selectedDate;
  final Map<String, AsyncValue<WeekSchedule>> weeks;

  String get selectedWeekKey => isoDate(startOfWeek(selectedDate));

  AsyncValue<WeekSchedule>? get selectedWeek => weeks[selectedWeekKey];

  AsyncValue<WeekSchedule>? weekFor(DateTime date) =>
      weeks[isoDate(startOfWeek(date))];

  ScheduleState copyWith({
    DateTime? selectedDate,
    Map<String, AsyncValue<WeekSchedule>>? weeks,
  }) {
    return ScheduleState(
      selectedDate: selectedDate ?? this.selectedDate,
      weeks: weeks ?? this.weeks,
    );
  }
}

class ScheduleController extends Notifier<ScheduleState> {
  final Set<String> _inFlight = {};
  bool _disposed = false;

  @override
  ScheduleState build() {
    _disposed = false;
    _inFlight.clear();
    ref.watch(authControllerProvider.select((session) => session.me?.id));
    ref.onDispose(() => _disposed = true);
    return ScheduleState(selectedDate: DateTime.now());
  }

  Future<void> ensureWeek(DateTime date, {bool force = false}) async {
    final monday = startOfWeek(date);
    final key = isoDate(monday);
    final existing = state.weeks[key];
    if (!force && existing is AsyncData<WeekSchedule>) {
      return;
    }
    if (_inFlight.contains(key)) {
      return;
    }
    _inFlight.add(key);
    state = state.copyWith(weeks: {...state.weeks, key: const AsyncLoading()});
    try {
      final week = await ref.read(scheduleRepositoryProvider).fetchWeek(monday);
      if (_disposed) {
        return;
      }
      state = state.copyWith(weeks: {...state.weeks, key: AsyncData(week)});
    } catch (error, stackTrace) {
      if (_disposed || error is ApiException && error.isUnauthorized) {
        return;
      }
      state = state.copyWith(
        weeks: {...state.weeks, key: AsyncError(error, stackTrace)},
      );
    } finally {
      _inFlight.remove(key);
    }
  }

  Future<void> ensureToday() => ensureWeek(DateTime.now());

  Future<void> selectDate(DateTime date) async {
    state = state.copyWith(selectedDate: date);
    await ensureWeek(date);
  }

  Future<void> shiftWeek(int weeks) async {
    final next = state.selectedDate.add(Duration(days: weeks * 7));
    state = state.copyWith(selectedDate: next);
    await ensureWeek(next);
  }

  Future<void> goToday() async {
    final today = DateTime.now();
    state = state.copyWith(selectedDate: today);
    await ensureWeek(today);
  }

  Future<void> refreshSelectedWeek() async {
    await ensureWeek(state.selectedDate, force: true);
  }

  Future<void> refreshWeek(DateTime date) async {
    await ensureWeek(date, force: true);
  }

  /// Notes were saved successfully. Drop cached weeks so the visible schedule
  /// reloads its server-provided previews through the normal page lifecycle.
  void invalidateWeekCache() {
    state = state.copyWith(weeks: const {});
  }
}

final scheduleControllerProvider =
    NotifierProvider<ScheduleController, ScheduleState>(ScheduleController.new);

final termCalendarProvider = FutureProvider<TermCalendar?>((ref) async {
  final session = ref.watch(authControllerProvider);
  final termId = session.me?.currentTerm?.id;
  if (termId == null || !session.canUseApp) {
    return null;
  }
  return ref.read(scheduleRepositoryProvider).fetchCalendar(termId);
});

final sessionDetailProvider = FutureProvider.autoDispose
    .family<Session, String>((ref, sessionId) {
      return ref.read(scheduleRepositoryProvider).fetchSession(sessionId);
    });

final offeringsProvider = FutureProvider<List<Offering>>((ref) async {
  final session = ref.watch(authControllerProvider);
  if (!session.canUseApp) {
    return const [];
  }
  return ref.read(scheduleRepositoryProvider).fetchOfferings();
});
