import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:teacher_schedule/core/providers.dart';
import 'package:teacher_schedule/core/storage/token_store.dart';
import 'package:teacher_schedule/features/schedule/import_summary.dart';
import 'package:teacher_schedule/features/schedule/models.dart';
import 'package:teacher_schedule/features/schedule/schedule_controller.dart';
import 'package:teacher_schedule/features/schedule/schedule_repository.dart';

class _FakeScheduleRepository implements ScheduleRepository {
  int weekCalls = 0;

  @override
  Future<WeekSchedule> fetchWeek(DateTime monday) async {
    weekCalls++;
    return WeekSchedule(
      from: DateTime(monday.year, monday.month, monday.day),
      termId: 'term-1',
      termCode: '2026-1',
      sessions: const [],
    );
  }

  @override
  Future<Session> fetchSession(String sessionId) => throw UnimplementedError();

  @override
  Future<TermCalendar> fetchCalendar(String termId) =>
      throw UnimplementedError();

  @override
  Future<List<Offering>> fetchOfferings() async => const [];

  @override
  Future<ImportSummary> fetchBootstrapSummary() async =>
      const ImportSummary(termName: '2026-1', sessionCount: 0, courseCount: 0);
}

void main() {
  test('同一周切换日期只请求一次，跨周才再次请求', () async {
    final repository = _FakeScheduleRepository();
    final container = ProviderContainer(
      overrides: [
        tokenStoreProvider.overrideWithValue(MemoryTokenStore()),
        scheduleRepositoryProvider.overrideWithValue(repository),
      ],
    );
    addTearDown(container.dispose);

    final selected = DateTime(2026, 3, 4);
    container.read(scheduleControllerProvider);
    await container
        .read(scheduleControllerProvider.notifier)
        .ensureWeek(selected);
    await container
        .read(scheduleControllerProvider.notifier)
        .ensureWeek(selected.add(const Duration(days: 1)));
    await container
        .read(scheduleControllerProvider.notifier)
        .selectDate(selected.add(const Duration(days: 2)));

    expect(repository.weekCalls, 1);
    expect(
      container.read(scheduleControllerProvider).selectedDate,
      DateTime(2026, 3, 6),
    );

    await container.read(scheduleControllerProvider.notifier).shiftWeek(1);

    expect(repository.weekCalls, 2);
  });
}
