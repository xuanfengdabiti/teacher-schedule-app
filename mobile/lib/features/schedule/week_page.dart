import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/theme.dart';
import '../../core/json.dart';
import '../../core/providers.dart';
import '../../shared/status_views.dart';
import 'models.dart';
import 'schedule_controller.dart';
import 'session_action_sheet.dart';
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

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final selectedDate = ref.read(scheduleControllerProvider).selectedDate;
    if (ref.read(scheduleControllerProvider).weekFor(selectedDate) == null) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) {
          ref
              .read(scheduleControllerProvider.notifier)
              .ensureWeek(selectedDate);
        }
      });
    }
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

  void _openSessionActions(Session session) {
    unawaited(
      showSessionActionSheet(
        context: context,
        session: session,
        api: ref.read(apiClientProvider),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(scheduleControllerProvider);
    final selectedDate = state.selectedDate;
    final weekAsync = state.selectedWeek;
    if (weekAsync == null) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) {
          ref
              .read(scheduleControllerProvider.notifier)
              .ensureWeek(selectedDate);
        }
      });
    }
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
      backgroundColor: appCanvas,
      appBar: AppBar(
        backgroundColor: Colors.white,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        scrolledUnderElevation: 0,
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
          const SizedBox(width: 4),
        ],
        bottom: const PreferredSize(
          preferredSize: Size.fromHeight(1),
          child: Divider(height: 1, thickness: 1, color: appLine),
        ),
      ),
      body: SafeArea(
        top: false,
        child: Column(
          children: [
            Container(
              color: Colors.white,
              padding: const EdgeInsets.fromLTRB(12, 6, 12, 10),
              child: Column(
                children: [
                  _WeekNavigator(
                    teachingWeek: teachingWeek,
                    monday: monday,
                    onPrevious: () => ref
                        .read(scheduleControllerProvider.notifier)
                        .shiftWeek(-1),
                    onNext: () => ref
                        .read(scheduleControllerProvider.notifier)
                        .shiftWeek(1),
                    onToday: () =>
                        ref.read(scheduleControllerProvider.notifier).goToday(),
                  ),
                  const SizedBox(height: 8),
                  SizedBox(
                    width: double.infinity,
                    child: SegmentedButton<bool>(
                      key: const Key('weekViewToggle'),
                      segments: const [
                        ButtonSegment(
                          value: false,
                          label: Text(
                            '按日列表',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                        ButtonSegment(
                          value: true,
                          label: Text(
                            '整周总览',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                      ],
                      selected: {_overview},
                      showSelectedIcon: false,
                      expandedInsets: EdgeInsets.zero,
                      style: ButtonStyle(
                        visualDensity: VisualDensity.compact,
                        tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                        minimumSize: const WidgetStatePropertyAll(Size(0, 36)),
                        padding: const WidgetStatePropertyAll(
                          EdgeInsets.symmetric(horizontal: 8),
                        ),
                        backgroundColor: WidgetStateProperty.resolveWith(
                          (states) => states.contains(WidgetState.selected)
                              ? const Color(0xFFEAF3FF)
                              : Colors.white,
                        ),
                        foregroundColor: WidgetStateProperty.resolveWith(
                          (states) => states.contains(WidgetState.selected)
                              ? appTeal
                              : appMuted,
                        ),
                        side: const WidgetStatePropertyAll(
                          BorderSide(color: appLine),
                        ),
                        textStyle: const WidgetStatePropertyAll(
                          TextStyle(fontSize: 13, fontWeight: FontWeight.w600),
                        ),
                      ),
                      onSelectionChanged: (selection) {
                        setState(() => _overview = selection.first);
                      },
                    ),
                  ),
                ],
              ),
            ),
            if (!_overview) ...[
              const SizedBox(height: 10),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 12),
                child: IntrinsicHeight(
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      for (var index = 0; index < 7; index++) ...[
                        if (index > 0) const SizedBox(width: 4),
                        Expanded(
                          child: _DayChip(
                            key: ValueKey(
                              'dayTab-${isoDate(monday.add(Duration(days: index)))}',
                            ),
                            date: monday.add(Duration(days: index)),
                            selected: sameDay(
                              monday.add(Duration(days: index)),
                              selectedDate,
                            ),
                            isToday: sameDay(
                              monday.add(Duration(days: index)),
                              DateTime.now(),
                            ),
                            hasSessions:
                                week
                                    ?.sessionsOn(
                                      monday.add(Duration(days: index)),
                                    )
                                    .isNotEmpty ??
                                false,
                            onTap: () => ref
                                .read(scheduleControllerProvider.notifier)
                                .selectDate(monday.add(Duration(days: index))),
                          ),
                        ),
                      ],
                    ],
                  ),
                ),
              ),
            ],
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
                  ? Padding(
                      padding: const EdgeInsets.fromLTRB(12, 0, 12, 12),
                      child: WeekOverview(
                        key: ValueKey('weekOverview-${isoDate(monday)}'),
                        monday: monday,
                        sessions: week?.sessions ?? const [],
                        periods: calendar?.periods ?? const [],
                        onOpenSession: (session) =>
                            context.push('/sessions/${session.id}'),
                      ),
                    )
                  : _WeekContent(
                      date: selectedDate,
                      week: week,
                      sessions: sessions,
                      onOpenSession: _openSessionActions,
                    ),
            ),
          ],
        ),
      ),
    );
  }
}

class _WeekNavigator extends StatelessWidget {
  const _WeekNavigator({
    required this.teachingWeek,
    required this.monday,
    required this.onPrevious,
    required this.onNext,
    required this.onToday,
  });

  final int? teachingWeek;
  final DateTime monday;
  final VoidCallback onPrevious;
  final VoidCallback onNext;
  final VoidCallback onToday;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        _WeekArrowButton(
          tooltip: '上一周',
          icon: Icons.chevron_left,
          onPressed: onPrevious,
        ),
        Expanded(
          child: Column(
            children: [
              Text(
                teachingWeek == null ? '学期外' : '第$teachingWeek周',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(
                  fontSize: 18,
                  height: 1.15,
                  fontWeight: FontWeight.w700,
                  color: appInk,
                ),
              ),
              const SizedBox(height: 1),
              Text(
                '${monthDayZh(monday)} - ${monthDayZh(monday.add(const Duration(days: 6)))}',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 11.5, color: appMuted),
              ),
            ],
          ),
        ),
        _WeekArrowButton(
          tooltip: '下一周',
          icon: Icons.chevron_right,
          onPressed: onNext,
        ),
        const SizedBox(width: 4),
        TextButton(
          style: TextButton.styleFrom(
            foregroundColor: appTeal,
            minimumSize: const Size(0, 34),
            padding: const EdgeInsets.symmetric(horizontal: 8),
            tapTargetSize: MaterialTapTargetSize.shrinkWrap,
            visualDensity: VisualDensity.compact,
            textStyle: const TextStyle(
              fontSize: 12.5,
              fontWeight: FontWeight.w600,
            ),
          ),
          onPressed: onToday,
          child: const Text('回今天'),
        ),
      ],
    );
  }
}

class _WeekArrowButton extends StatelessWidget {
  const _WeekArrowButton({
    required this.tooltip,
    required this.icon,
    required this.onPressed,
  });

  final String tooltip;
  final IconData icon;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    return IconButton(
      tooltip: tooltip,
      onPressed: onPressed,
      icon: Icon(icon, size: 20),
      style: IconButton.styleFrom(
        foregroundColor: appTeal,
        backgroundColor: const Color(0xFFF2F7FF),
        fixedSize: const Size(34, 34),
        padding: EdgeInsets.zero,
        tapTargetSize: MaterialTapTargetSize.shrinkWrap,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
      ),
    );
  }
}

class _WeekContent extends StatelessWidget {
  const _WeekContent({
    required this.date,
    required this.week,
    required this.sessions,
    required this.onOpenSession,
  });

  final DateTime date;
  final WeekSchedule? week;
  final List<Session> sessions;
  final ValueChanged<Session> onOpenSession;

  @override
  Widget build(BuildContext context) {
    if (sessions.isEmpty) {
      final wholeWeekEmpty = week?.sessions.isEmpty ?? true;
      return EmptyView(
        title: wholeWeekEmpty ? '本周暂无课程' : '${monthDayZh(date)} 没有课',
        detail: wholeWeekEmpty
            ? (week?.termId == null
                  ? '这一周不在学期教学周内。'
                  : '如果学期已经开始却一直没有课程，请联系教务确认。')
            : '可以点上面的日期查看本周其他课程。',
        icon: wholeWeekEmpty
            ? Icons.event_busy_outlined
            : Icons.free_cancellation_outlined,
      );
    }
    return ListView.builder(
      key: ValueKey('day-list-${isoDate(date)}'),
      padding: const EdgeInsets.fromLTRB(14, 2, 14, 24),
      itemCount: sessions.length + 1,
      itemBuilder: (context, index) {
        if (index == 0) {
          return Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: Row(
              children: [
                Expanded(
                  child: Text(
                    '${monthDayZh(date)} ${weekdayZh(date.weekday)}',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      fontSize: 15,
                      fontWeight: FontWeight.w600,
                      color: appInk,
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 8,
                    vertical: 3,
                  ),
                  decoration: BoxDecoration(
                    color: const Color(0xFFEAF3FF),
                    borderRadius: BorderRadius.circular(7),
                  ),
                  child: Text(
                    '${sessions.length} 节',
                    style: const TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w600,
                      color: appTeal,
                    ),
                  ),
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
            onTap: () => onOpenSession(session),
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
    final borderColor = selected || isToday ? appTeal : appLine;
    return Semantics(
      button: true,
      selected: selected,
      label:
          '${monthDayZh(date)} ${weekdayZh(date.weekday)}${hasSessions ? '，有课' : ''}',
      child: Material(
        color: selected ? appTeal : Colors.white,
        borderRadius: BorderRadius.circular(10),
        child: InkWell(
          borderRadius: BorderRadius.circular(10),
          onTap: onTap,
          child: Container(
            constraints: const BoxConstraints(minHeight: 56),
            padding: const EdgeInsets.symmetric(horizontal: 2, vertical: 6),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(10),
              border: Border.all(
                color: borderColor,
                width: isToday && !selected ? 1.2 : 1,
              ),
            ),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Text(
                  weekdayZh(date.weekday).replaceFirst('周', ''),
                  maxLines: 1,
                  style: TextStyle(
                    fontSize: 11,
                    height: 1.15,
                    color: selected
                        ? Colors.white70
                        : isToday
                        ? appTeal
                        : appMuted,
                  ),
                ),
                const SizedBox(height: 3),
                FittedBox(
                  fit: BoxFit.scaleDown,
                  child: Text(
                    '${date.day}',
                    style: TextStyle(
                      fontSize: 16,
                      height: 1.1,
                      fontWeight: FontWeight.w700,
                      color: foreground,
                    ),
                  ),
                ),
                const SizedBox(height: 3),
                Container(
                  width: 4,
                  height: 4,
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
      ),
    );
  }
}
