import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../../core/json.dart';
import 'models.dart';

class WeekOverview extends StatefulWidget {
  const WeekOverview({
    super.key,
    required this.monday,
    required this.sessions,
    required this.periods,
    required this.onOpenSession,
  });

  final DateTime monday;
  final List<Session> sessions;
  final List<Period> periods;

  /// Receives the session whose block was tapped.
  final void Function(Session session) onOpenSession;

  @override
  State<WeekOverview> createState() => _WeekOverviewState();
}

class _WeekOverviewState extends State<WeekOverview> {
  static const _rowHeight = 58.0;
  static const _timeWidth = 34.0;
  static const _canvasPaddingX = 12.0;
  static const _canvasPaddingTop = 6.0;
  static const _canvasPaddingBottom = 14.0;
  static const _dayHeaderHeight = 46.0;
  static const _headerGap = 4.0;

  final TransformationController _transformationController =
      TransformationController();

  @override
  void didUpdateWidget(covariant WeekOverview oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!sameDay(oldWidget.monday, widget.monday) ||
        !identical(oldWidget.sessions, widget.sessions)) {
      _resetView();
    }
  }

  @override
  void dispose() {
    _transformationController.dispose();
    super.dispose();
  }

  void _resetView() {
    _transformationController.value = Matrix4.identity();
  }

  @override
  Widget build(BuildContext context) {
    final visiblePeriods = _visiblePeriods();
    if (visiblePeriods.isEmpty) {
      return const Center(child: Text('本周暂无课程'));
    }
    final dates = List<DateTime>.generate(
      7,
      (index) => widget.monday.add(Duration(days: index)),
    );
    final sessionDays = {
      for (final date in dates)
        if (widget.sessions.any((session) => sameDay(session.date, date)))
          date.weekday,
    };

    return Column(
      children: [
        _OverviewToolbar(
          controller: _transformationController,
          hasSessions: widget.sessions.isNotEmpty,
          sessionDayCount: sessionDays.length,
          onReset: _resetView,
        ),
        const SizedBox(height: 6),
        Expanded(
          child: Container(
            clipBehavior: Clip.antiAlias,
            decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: appLine),
            ),
            child: LayoutBuilder(
              builder: (context, constraints) {
                final viewportWidth = constraints.maxWidth.isFinite
                    ? constraints.maxWidth
                    : 360.0;
                final contentWidth = math.max(
                  0.0,
                  viewportWidth - _canvasPaddingX * 2,
                );
                final gridWidth = math.max(0.0, contentWidth - _timeWidth);
                final dayWidth = gridWidth / 7;
                final gridHeight = _rowHeight * visiblePeriods.length;
                final contentHeight =
                    _canvasPaddingTop +
                    _dayHeaderHeight +
                    _headerGap +
                    gridHeight +
                    _canvasPaddingBottom;
                final viewportHeight = constraints.maxHeight.isFinite
                    ? constraints.maxHeight
                    : contentHeight;
                final childHeight = math.max(viewportHeight, contentHeight);

                return InteractiveViewer(
                  key: const Key('weekOverviewViewport'),
                  transformationController: _transformationController,
                  minScale: 1,
                  maxScale: 3,
                  panEnabled: true,
                  scaleEnabled: true,
                  boundaryMargin: EdgeInsets.zero,
                  constrained: false,
                  clipBehavior: Clip.hardEdge,
                  child: SizedBox(
                    width: viewportWidth,
                    height: childHeight,
                    child: ColoredBox(
                      color: Colors.white,
                      child: Align(
                        alignment: Alignment.topCenter,
                        child: Padding(
                          padding: const EdgeInsets.fromLTRB(
                            _canvasPaddingX,
                            _canvasPaddingTop,
                            _canvasPaddingX,
                            _canvasPaddingBottom,
                          ),
                          child: SizedBox(
                            width: contentWidth,
                            child: Column(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                _WeekHeader(
                                  dates: dates,
                                  dayWidth: dayWidth,
                                  sessionDays: sessionDays,
                                ),
                                const SizedBox(height: _headerGap),
                                _WeekGrid(
                                  dates: dates,
                                  periods: visiblePeriods,
                                  sessions: widget.sessions,
                                  dayWidth: dayWidth,
                                  onOpenSession: widget.onOpenSession,
                                ),
                              ],
                            ),
                          ),
                        ),
                      ),
                    ),
                  ),
                );
              },
            ),
          ),
        ),
      ],
    );
  }

  List<Period> _visiblePeriods() {
    if (widget.periods.isEmpty) {
      return List<Period>.generate(
        8,
        (index) => Period(
          number: index + 1,
          startTime: '',
          endTime: '',
          blockCode: '',
        ),
      );
    }
    return widget.periods
        .where((period) => period.number <= 8)
        .toList(growable: false)
      ..sort((a, b) => a.number.compareTo(b.number));
  }
}

class _OverviewToolbar extends StatelessWidget {
  const _OverviewToolbar({
    required this.controller,
    required this.hasSessions,
    required this.sessionDayCount,
    required this.onReset,
  });

  final TransformationController controller;
  final bool hasSessions;
  final int sessionDayCount;
  final VoidCallback onReset;

  @override
  Widget build(BuildContext context) {
    final hint = !hasSessions
        ? '本周暂无课程 · 可双指缩放'
        : '双指缩放 · 放大后拖动 · $sessionDayCount天有课';
    return Row(
      children: [
        const Icon(Icons.pinch_outlined, size: 14, color: appMuted),
        const SizedBox(width: 4),
        Expanded(
          child: Text(
            hint,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(fontSize: 11.5, color: appMuted),
          ),
        ),
        const SizedBox(width: 8),
        _ZoomResetButton(controller: controller, onReset: onReset),
      ],
    );
  }
}

class _ZoomResetButton extends StatelessWidget {
  const _ZoomResetButton({required this.controller, required this.onReset});

  final TransformationController controller;
  final VoidCallback onReset;

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<Matrix4>(
      valueListenable: controller,
      builder: (context, matrix, _) {
        final scale = matrix.getMaxScaleOnAxis();
        final percent = (scale * 100).round().clamp(100, 300);
        return Tooltip(
          message: '复位课表缩放',
          child: Semantics(
            button: true,
            label: '复位课表缩放，当前$percent%',
            child: Material(
              color: percent == 100 ? Colors.white : const Color(0xFFEAF3FF),
              borderRadius: BorderRadius.circular(8),
              child: InkWell(
                key: const Key('weekOverviewReset'),
                onTap: onReset,
                borderRadius: BorderRadius.circular(8),
                child: Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 8,
                    vertical: 5,
                  ),
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: appLine),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(
                        Icons.center_focus_strong_outlined,
                        size: 13,
                        color: appTeal,
                      ),
                      const SizedBox(width: 3),
                      Text(
                        '$percent%',
                        style: const TextStyle(
                          fontSize: 11,
                          fontWeight: FontWeight.w600,
                          color: appTeal,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        );
      },
    );
  }
}

class _WeekHeader extends StatelessWidget {
  const _WeekHeader({
    required this.dates,
    required this.dayWidth,
    required this.sessionDays,
  });

  final List<DateTime> dates;
  final double dayWidth;
  final Set<int> sessionDays;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: _WeekOverviewState._dayHeaderHeight,
      child: Row(
        children: [
          const SizedBox(
            width: _WeekOverviewState._timeWidth,
            child: Center(
              child: Text(
                '节',
                style: TextStyle(
                  fontSize: 9.5,
                  fontWeight: FontWeight.w600,
                  color: appMuted,
                ),
              ),
            ),
          ),
          for (final date in dates)
            SizedBox(
              width: dayWidth,
              child: _DayHeading(
                key: ValueKey('overview-day-${date.weekday}'),
                date: date,
                isToday: sameDay(date, DateTime.now()),
                hasSessions: sessionDays.contains(date.weekday),
              ),
            ),
        ],
      ),
    );
  }
}

class _DayHeading extends StatelessWidget {
  const _DayHeading({
    super.key,
    required this.date,
    required this.isToday,
    required this.hasSessions,
  });

  final DateTime date;
  final bool isToday;
  final bool hasSessions;

  @override
  Widget build(BuildContext context) {
    final color = isToday ? appTeal : appInk;
    return Column(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        SizedBox(
          height: 14,
          child: FittedBox(
            fit: BoxFit.scaleDown,
            child: Text(
              weekdayZh(date.weekday).replaceFirst('周', ''),
              style: TextStyle(
                fontSize: 10.5,
                height: 1,
                fontWeight: FontWeight.w600,
                color: isToday ? appTeal : appMuted,
              ),
            ),
          ),
        ),
        const SizedBox(height: 2),
        Container(
          width: 21,
          height: 21,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: isToday ? appTeal : Colors.transparent,
            shape: BoxShape.circle,
          ),
          child: FittedBox(
            fit: BoxFit.scaleDown,
            child: Text(
              '${date.day}',
              style: TextStyle(
                fontSize: 12.5,
                height: 1,
                fontWeight: FontWeight.w700,
                color: isToday ? Colors.white : color,
              ),
            ),
          ),
        ),
        const SizedBox(height: 2),
        Container(
          width: 4,
          height: 4,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: hasSessions ? appTeal : Colors.transparent,
          ),
        ),
      ],
    );
  }
}

class _WeekGrid extends StatelessWidget {
  const _WeekGrid({
    required this.dates,
    required this.periods,
    required this.sessions,
    required this.dayWidth,
    required this.onOpenSession,
  });

  final List<DateTime> dates;
  final List<Period> periods;
  final List<Session> sessions;
  final double dayWidth;
  final void Function(Session session) onOpenSession;

  @override
  Widget build(BuildContext context) {
    final height = _WeekOverviewState._rowHeight * periods.length;
    return ClipRRect(
      borderRadius: BorderRadius.circular(8),
      child: Container(
        foregroundDecoration: BoxDecoration(
          border: Border.all(color: appLine),
          borderRadius: BorderRadius.circular(8),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(
              width: _WeekOverviewState._timeWidth,
              height: height,
              child: Column(
                children: [
                  for (final period in periods)
                    SizedBox(
                      height: _WeekOverviewState._rowHeight,
                      child: _PeriodLabel(period: period),
                    ),
                ],
              ),
            ),
            for (final date in dates)
              SizedBox(
                width: dayWidth,
                height: height,
                child: _DayColumn(
                  date: date,
                  periods: periods,
                  sessions: sessions,
                  onOpenSession: onOpenSession,
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _PeriodLabel extends StatelessWidget {
  const _PeriodLabel({required this.period});

  final Period period;

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: const BoxDecoration(
        border: Border(
          bottom: BorderSide(color: Color(0xFFEDF1F7), width: 0.8),
        ),
      ),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Text(
            '${period.number}',
            maxLines: 1,
            style: const TextStyle(
              fontSize: 12.5,
              height: 1.1,
              fontWeight: FontWeight.w700,
              color: appTeal,
            ),
          ),
          if (period.startTime.isNotEmpty) ...[
            const SizedBox(height: 1),
            SizedBox(
              height: 12,
              child: FittedBox(
                fit: BoxFit.scaleDown,
                child: Text(
                  period.startTime,
                  maxLines: 1,
                  style: const TextStyle(
                    fontSize: 8.5,
                    height: 1,
                    color: appMuted,
                  ),
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _DayColumn extends StatelessWidget {
  const _DayColumn({
    required this.date,
    required this.periods,
    required this.sessions,
    required this.onOpenSession,
  });

  final DateTime date;
  final List<Period> periods;
  final List<Session> sessions;
  final void Function(Session session) onOpenSession;

  @override
  Widget build(BuildContext context) {
    final positionByPeriod = <int, int>{
      for (var index = 0; index < periods.length; index++)
        periods[index].number: index,
    };
    final daySessions = sessions
        .where((session) => sameDay(session.date, date))
        .toList(growable: false);
    return Container(
      decoration: const BoxDecoration(
        border: Border(left: BorderSide(color: appLine, width: 0.8)),
      ),
      child: Stack(
        children: [
          Positioned.fill(
            child: Column(
              children: [
                for (var index = 0; index < periods.length; index++)
                  SizedBox(
                    height: _WeekOverviewState._rowHeight,
                    child: const DecoratedBox(
                      decoration: BoxDecoration(
                        border: Border(
                          bottom: BorderSide(
                            color: Color(0xFFEDF1F7),
                            width: 0.8,
                          ),
                        ),
                      ),
                    ),
                  ),
              ],
            ),
          ),
          for (final session in daySessions)
            ..._positionedBlocks(session, positionByPeriod),
        ],
      ),
    );
  }

  List<Widget> _positionedBlocks(
    Session session,
    Map<int, int> positionByPeriod,
  ) {
    final startIndex = positionByPeriod[session.startPeriod];
    if (startIndex == null || startIndex >= positionByPeriod.length) {
      return const [];
    }
    var endIndex = positionByPeriod[session.endPeriod] ?? startIndex;
    if (endIndex < startIndex) {
      endIndex = startIndex;
    }
    if (endIndex >= positionByPeriod.length) {
      endIndex = positionByPeriod.length - 1;
    }
    return [
      Positioned(
        key: ValueKey('overview-position-${session.id}'),
        top: startIndex * _WeekOverviewState._rowHeight + 1.5,
        height: (endIndex - startIndex + 1) * _WeekOverviewState._rowHeight - 3,
        left: 1.5,
        right: 1.5,
        child: _SessionBlock(
          session: session,
          onTap: () => onOpenSession(session),
        ),
      ),
    ];
  }
}

class _SessionBlock extends StatelessWidget {
  const _SessionBlock({required this.session, required this.onTap});

  final Session session;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final cancelled = session.status == 'cancelled';
    final palette = cancelled
        ? const _CoursePalette(
            background: Color(0xFFF2F4F7),
            border: Color(0xFFE0E4EA),
          )
        : _paletteFor(session);
    final location = _classroomLabel(session);
    final teacher = session.teacherNames;
    final hasNote = session.hasPreviousNote;
    final textScaler = MediaQuery.textScalerOf(context);

    return Material(
      color: palette.background,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(7),
        side: BorderSide(color: palette.border),
      ),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        key: ValueKey('overview-session-${session.id}'),
        onTap: onTap,
        child: LayoutBuilder(
          builder: (context, constraints) {
            const verticalPadding = 3.0;
            const horizontalPadding = 3.5;
            const noteInset = 10.0;
            const gap = 1.5;
            final availableHeight = math.max(
              0.0,
              constraints.maxHeight - verticalPadding * 2,
            );
            final baseAvailableWidth = math.max(
              0.0,
              constraints.maxWidth - horizontalPadding * 2,
            );
            final courseLine = textScaler.scale(10.5) * 1.2;
            final detailLine = textScaler.scale(8.8) * 1.2;
            final noteLine = session.hasPreviousNote
                ? '备注：${session.previousNotePreview.trim()}'
                : null;
            var maxDetailCount = 0;
            final availableDetails = [
              if (location.isNotEmpty) location,
              if (teacher.isNotEmpty) teacher,
              ?noteLine,
            ];
            for (
              var candidate = availableDetails.length;
              candidate >= 1;
              candidate--
            ) {
              final required =
                  courseLine * 2 + candidate * detailLine + candidate * gap;
              if (availableHeight >= required) {
                maxDetailCount = candidate;
                break;
              }
            }
            if (maxDetailCount == 0 &&
                availableDetails.isNotEmpty &&
                availableHeight >= courseLine + detailLine + gap) {
              maxDetailCount = 1;
            }
            if (baseAvailableWidth < 28) {
              maxDetailCount = math.min(maxDetailCount, 1);
            }
            final details = _detailLines(
              room: location,
              teacher: teacher,
              note: noteLine,
              maxCount: maxDetailCount,
            );
            final noteIndex = noteLine == null ? -1 : details.indexOf(noteLine);
            final showNoteIcon = hasNote && noteIndex == -1;
            final detailHeight =
                details.length * detailLine + details.length * gap;
            final maxCourseLines = math
                .max(1, (availableHeight - detailHeight) ~/ courseLine)
                .clamp(1, 3)
                .toInt();
            const detailStyle = TextStyle(
              fontSize: 8.8,
              height: 1.15,
              color: appMuted,
            );

            return Padding(
              padding: EdgeInsets.fromLTRB(
                horizontalPadding,
                verticalPadding,
                showNoteIcon ? noteInset : horizontalPadding,
                verticalPadding,
              ),
              child: Stack(
                children: [
                  Positioned.fill(
                    child: ClipRect(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Flexible(
                            child: Text(
                              session.courseName,
                              maxLines: maxCourseLines,
                              overflow: TextOverflow.ellipsis,
                              style: TextStyle(
                                fontSize: 10.5,
                                height: 1.15,
                                fontWeight: FontWeight.w700,
                                color: cancelled ? appMuted : appInk,
                                decoration: cancelled
                                    ? TextDecoration.lineThrough
                                    : null,
                              ),
                            ),
                          ),
                          for (
                            var index = 0;
                            index < details.length;
                            index++
                          ) ...[
                            const SizedBox(height: gap),
                            Text(
                              details[index],
                              key: index == noteIndex
                                  ? ValueKey(
                                      'overview-note-preview-${session.id}',
                                    )
                                  : null,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: index == noteIndex
                                  ? detailStyle.copyWith(color: appTeal)
                                  : detailStyle,
                            ),
                          ],
                        ],
                      ),
                    ),
                  ),
                  if (showNoteIcon)
                    Positioned(
                      top: 0,
                      right: 0,
                      child: Icon(
                        Icons.sticky_note_2_outlined,
                        key: ValueKey('overview-note-${session.id}'),
                        size: 8.5,
                        color: appTeal,
                      ),
                    ),
                ],
              ),
            );
          },
        ),
      ),
    );
  }

  String _classroomLabel(Session session) {
    final room = session.classroom;
    final location = [
      if (room.building.isNotEmpty) room.building,
      if (room.roomNo.isNotEmpty) room.roomNo,
    ].join(' ');
    if (location.isNotEmpty) {
      return location;
    }
    return room.roomNo;
  }
}

class _CoursePalette {
  const _CoursePalette({required this.background, required this.border});

  final Color background;
  final Color border;
}

List<String> _detailLines({
  required String room,
  required String teacher,
  required String? note,
  required int maxCount,
}) {
  if (maxCount <= 0) {
    return const [];
  }
  if (maxCount >= 3) {
    return [if (room.isNotEmpty) room, if (teacher.isNotEmpty) teacher, ?note];
  }
  if (maxCount == 2) {
    final lines = <String>[];
    if (room.isNotEmpty) {
      lines.add(room);
    }
    if (note != null && lines.length < 2) {
      lines.add(note);
    }
    if (lines.length < 2 && teacher.isNotEmpty) {
      lines.add(teacher);
    }
    return lines;
  }
  if (room.isNotEmpty) {
    return [room];
  }
  if (note != null) {
    return [note];
  }
  if (teacher.isNotEmpty) {
    return [teacher];
  }
  return const [];
}

const _coursePalettes = [
  _CoursePalette(background: Color(0xFFEAF3FF), border: Color(0xFFC9DEFA)),
  _CoursePalette(background: Color(0xFFF1EEFF), border: Color(0xFFDCD3FF)),
  _CoursePalette(background: Color(0xFFE8F8F1), border: Color(0xFFC8ECDD)),
  _CoursePalette(background: Color(0xFFFFF3E6), border: Color(0xFFFFDDBA)),
];

_CoursePalette _paletteFor(Session session) {
  final key = session.offeringId.isNotEmpty
      ? session.offeringId
      : session.courseCode.isNotEmpty
      ? session.courseCode
      : session.id;
  var hash = 0;
  for (final codeUnit in key.codeUnits) {
    hash = (hash * 31 + codeUnit) & 0x7fffffff;
  }
  return _coursePalettes[hash % _coursePalettes.length];
}
