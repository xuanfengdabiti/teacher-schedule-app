import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/theme.dart';
import '../../core/json.dart';
import '../../shared/status_views.dart';
import 'models.dart';
import 'schedule_controller.dart';
import 'session_card.dart';
import 'week_overview.dart';

class WeekPage extends ConsumerStatefulWidget {
  const WeekPage({super.key});

  @override
  ConsumerState<WeekPage> createState() => _WeekPageState();
}

class _WeekPageState extends ConsumerState<WeekPage> {
  bool _overview = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(scheduleControllerProvider.notifier).ensureToday();
    });
  }

  Future<void> _pickDate() async {
    final state = ref.read(scheduleControllerProvider);
    final picked = await showDatePicker(
      context: context,
      initialDate: state.selectedDate,
      firstDate: DateTime(2020),
      lastDate: DateTime(2100),
      helpText: '选择日期',
      cancelText: '取消',
      confirmText: '查看',
    );
    if (picked != null) {
      await ref.read(scheduleControllerProvider.notifier).selectDate(picked);
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(scheduleControllerProvider);
    final selectedDate = state.selectedDate;
    final weekAsync = state.selectedWeek;
    final calendar = ref.watch(termCalendarProvider).value;
    final week = weekAsync?.value;
    final sessions = week?.sessionsOn(selectedDate) ?? const <Session>[];
    final sessionWeeks = (week?.sessions ?? const <Session>[])
        .map((session) => session.teachingWeek)
        .whereType<int>()
        .toList();
    final teachingWeek =
        calendar?.teachingWeekFor(selectedDate) ??
        (sessionWeeks.isEmpty ? null : sessionWeeks.first);
    final monday = startOfWeek(selectedDate);

    return Scaffold(
      appBar: AppBar(
        title: const Text('课程表'),
        actions: [
          IconButton(
            tooltip: '选择日期',
            onPressed: _pickDate,
            icon: const Icon(Icons.calendar_month_outlined),
          ),
          IconButton(
            tooltip: '刷新本周',
            onPressed: () => ref
                .read(scheduleControllerProvider.notifier)
                .refreshWeek(selectedDate),
            icon: const Icon(Icons.refresh_rounded),
          ),
        ],
      ),
      body: SafeArea(
        top: false,
        child: Column(
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(14, 2, 14, 10),
              child: Row(
                children: [
                  IconButton.filledTonal(
                    tooltip: '上一周',
                    onPressed: () => ref
                        .read(scheduleControllerProvider.notifier)
                        .shiftWeek(-1),
                    icon: const Icon(Icons.chevron_left),
                  ),
                  Expanded(
                    child: Column(
                      children: [
                        Text(
                          teachingWeek == null ? '学期外' : '第$teachingWeek周',
                          style: const TextStyle(
                            fontSize: 20,
                            fontWeight: FontWeight.w700,
                            color: appInk,
                          ),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          '${monthDayZh(monday)} - ${monthDayZh(monday.add(const Duration(days: 6)))}',
                          style: const TextStyle(fontSize: 14, color: appMuted),
                        ),
                      ],
                    ),
                  ),
                  IconButton.filledTonal(
                    tooltip: '下一周',
                    onPressed: () => ref
                        .read(scheduleControllerProvider.notifier)
                        .shiftWeek(1),
                    icon: const Icon(Icons.chevron_right),
                  ),
                  const SizedBox(width: 4),
                  TextButton(
                    onPressed: () =>
                        ref.read(scheduleControllerProvider.notifier).goToday(),
                    child: const Text('回今天'),
                  ),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 14),
              child: SizedBox(
                width: double.infinity,
                child: SegmentedButton<bool>(
                  key: const Key('weekViewToggle'),
                  segments: const [
                    ButtonSegment(
                      value: false,
                      label: Text('按日列表'),
                      icon: Icon(Icons.view_day_outlined, size: 18),
                    ),
                    ButtonSegment(
                      value: true,
                      label: Text('整周总览'),
                      icon: Icon(Icons.calendar_view_week_outlined, size: 18),
                    ),
                  ],
                  selected: {_overview},
                  showSelectedIcon: false,
                  onSelectionChanged: (selection) {
                    setState(() => _overview = selection.first);
                  },
                ),
              ),
            ),
            const SizedBox(height: 8),
            if (!_overview)
              SizedBox(
                height: 76,
                child: ListView.separated(
                  padding: const EdgeInsets.symmetric(horizontal: 14),
                  scrollDirection: Axis.horizontal,
                  itemCount: 7,
                  separatorBuilder: (context, index) =>
                      const SizedBox(width: 8),
                  itemBuilder: (context, index) {
                    final date = monday.add(Duration(days: index));
                    final selected = sameDay(date, selectedDate);
                    final today = sameDay(date, DateTime.now());
                    final hasSessions =
                        week?.sessionsOn(date).isNotEmpty ?? false;
                    return _DayChip(
                      key: ValueKey('dayTab-${isoDate(date)}'),
                      date: date,
                      selected: selected,
                      isToday: today,
                      hasSessions: hasSessions,
                      onTap: () => ref
                          .read(scheduleControllerProvider.notifier)
                          .selectDate(date),
                    );
                  },
                ),
              ),
            const SizedBox(height: 10),
            Expanded(
              child: weekAsync == null || weekAsync.isLoading
                  ? const LoadingView(message: '正在加载本周课程…')
                  : weekAsync.hasError
                  ? ErrorView(
                      message: '${weekAsync.error}',
                      onRetry: () => ref
                          .read(scheduleControllerProvider.notifier)
                          .refreshWeek(selectedDate),
                    )
                  : _overview
                  ? WeekOverview(
                      monday: monday,
                      sessions: week?.sessions ?? const [],
                      periods: calendar?.periods ?? const [],
                      onOpenSession: (session) =>
                          context.push('/sessions/${session.id}'),
                    )
                  : _WeekContent(
                      date: selectedDate,
                      week: week,
                      sessions: sessions,
                    ),
            ),
          ],
        ),
      ),
    );
  }
}

class _WeekContent extends StatelessWidget {
  const _WeekContent({
    required this.date,
    required this.week,
    required this.sessions,
  });

  final DateTime date;
  final WeekSchedule? week;
  final List<Session> sessions;

  @override
  Widget build(BuildContext context) {
    if (sessions.isEmpty) {
      final wholeWeekEmpty = week?.sessions.isEmpty ?? true;
      return EmptyView(
        title: wholeWeekEmpty ? '本周暂无课程' : '${monthDayZh(date)} 没有课',
        detail: wholeWeekEmpty
            ? (week?.termId == null
                  ? '这一周不在学期教学周内。'
                  : '若学期已开始但一直为空，可能尚未导入课表，请联系教务。')
            : '可以点上面的日期查看本周其他课程。',
        icon: wholeWeekEmpty
            ? Icons.event_busy_outlined
            : Icons.free_cancellation_outlined,
      );
    }
    return ListView.builder(
      key: ValueKey('day-list-${isoDate(date)}'),
      padding: const EdgeInsets.fromLTRB(16, 2, 16, 24),
      itemCount: sessions.length + 1,
      itemBuilder: (context, index) {
        if (index == 0) {
          return Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: Row(
              children: [
                Text(
                  '${monthDayZh(date)} ${weekdayZh(date.weekday)}',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                const Spacer(),
                Text(
                  '${sessions.length} 节',
                  style: const TextStyle(fontSize: 14, color: appMuted),
                ),
              ],
            ),
          );
        }
        final session = sessions[index - 1];
        return Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: SessionCard(
            key: ValueKey('session-${session.id}'),
            session: session,
            onTap: () => context.push('/sessions/${session.id}'),
          ),
        );
      },
    );
  }
}

class _DayChip extends StatelessWidget {
  const _DayChip({
    super.key,
    required this.date,
    required this.selected,
    required this.isToday,
    required this.hasSessions,
    required this.onTap,
  });

  final DateTime date;
  final bool selected;
  final bool isToday;
  final bool hasSessions;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final foreground = selected ? Colors.white : appInk;
    return Material(
      color: selected ? appTeal : Colors.white,
      borderRadius: BorderRadius.circular(16),
      child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: onTap,
        child: Container(
          width: 54,
          padding: const EdgeInsets.symmetric(vertical: 8),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(16),
            border: Border.all(
              color: selected
                  ? appTeal
                  : isToday
                  ? appMint
                  : const Color(0xFFE2ECE9),
            ),
          ),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Text(
                weekdayZh(date.weekday).replaceFirst('周', ''),
                style: TextStyle(
                  fontSize: 13,
                  color: selected ? Colors.white70 : appMuted,
                ),
              ),
              const SizedBox(height: 2),
              Text(
                '${date.day}',
                style: TextStyle(
                  fontSize: 18,
                  fontWeight: FontWeight.w700,
                  color: foreground,
                ),
              ),
              const SizedBox(height: 3),
              Container(
                width: 5,
                height: 5,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: hasSessions
                      ? (selected ? Colors.white : appTeal)
                      : Colors.transparent,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
