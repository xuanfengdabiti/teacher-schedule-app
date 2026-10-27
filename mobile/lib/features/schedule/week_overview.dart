import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../../core/json.dart';
import 'models.dart';

class WeekOverview extends StatelessWidget {
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
  Widget build(BuildContext context) {
    final visiblePeriods = _visiblePeriods();
    if (visiblePeriods.isEmpty) {
      return const Center(child: Text('本周暂无课程'));
    }
    final dates = List<DateTime>.generate(
      7,
      (index) => monday.add(Duration(days: index)),
    );
    return LayoutBuilder(
      builder: (context, constraints) {
        const timeWidth = 42.0;
        const horizontalPadding = 20.0;
        final dayWidth =
            (constraints.maxWidth - timeWidth - horizontalPadding) / 7;
        return SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(10, 0, 10, 16),
          child: Column(
            children: [
              Row(
                children: [
                  const SizedBox(width: timeWidth),
                  for (final date in dates)
                    SizedBox(
                      width: dayWidth,
                      child: _DayHeading(
                        date: date,
                        isToday: sameDay(date, DateTime.now()),
                      ),
                    ),
                ],
              ),
              const SizedBox(height: 4),
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SizedBox(
                    width: timeWidth,
                    height: _rowHeight * visiblePeriods.length,
                    child: Column(
                      children: [
                        for (final period in visiblePeriods)
                          SizedBox(
                            height: _rowHeight,
                            child: _PeriodLabel(period: period),
                          ),
                      ],
                    ),
                  ),
                  for (final date in dates)
                    SizedBox(
                      width: dayWidth,
                      height: _rowHeight * visiblePeriods.length,
                      child: _DayColumn(
                        date: date,
                        periods: visiblePeriods,
                        sessions: sessions,
                        onOpenSession: onOpenSession,
                      ),
                    ),
                ],
              ),
            ],
          ),
        );
      },
    );
  }

  List<Period> _visiblePeriods() {
    if (periods.isEmpty) {
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
    return periods.where((period) => period.number <= 8).toList(growable: false)
      ..sort((a, b) => a.number.compareTo(b.number));
  }
}

const double _rowHeight = 58;

class _DayHeading extends StatelessWidget {
  const _DayHeading({required this.date, required this.isToday});

  final DateTime date;
  final bool isToday;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 5),
      decoration: BoxDecoration(
        color: isToday ? const Color(0xFFE2F1ED) : Colors.transparent,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        children: [
          Text(
            weekdayZh(date.weekday).replaceFirst('周', ''),
            style: TextStyle(
              fontSize: 12,
              color: isToday ? appTeal : appMuted,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 1),
          Text(
            '${date.day}',
            style: TextStyle(
              fontSize: 14,
              fontWeight: FontWeight.w700,
              color: isToday ? appTeal : appInk,
            ),
          ),
        ],
      ),
    );
  }
}

class _PeriodLabel extends StatelessWidget {
  const _PeriodLabel({required this.period});

  final Period period;

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        Text(
          '${period.number}',
          style: const TextStyle(
            fontSize: 13,
            fontWeight: FontWeight.w700,
            color: appTeal,
          ),
        ),
        if (period.startTime.isNotEmpty)
          Text(
            period.startTime,
            style: const TextStyle(fontSize: 10, color: appMuted),
          ),
      ],
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
    return Stack(
      children: [
        Positioned.fill(
          child: Column(
            children: [
              for (var index = 0; index < periods.length; index++)
                Expanded(
                  child: Container(
                    margin: const EdgeInsets.all(1.5),
                    decoration: BoxDecoration(
                      color: const Color(0xFFF6FAF8),
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(color: const Color(0xFFEDF4F1)),
                    ),
                  ),
                ),
            ],
          ),
        ),
        for (final session in daySessions)
          ..._positionedBlocks(session, positionByPeriod),
      ],
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
        top: startIndex * _rowHeight + 1.5,
        height: (endIndex - startIndex + 1) * _rowHeight - 3,
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
    final classroom = _classroomLabel(session);
    return Material(
      color: cancelled ? const Color(0xFFF0EFED) : const Color(0xFFDDEFEA),
      borderRadius: BorderRadius.circular(6),
      child: InkWell(
        key: ValueKey('overview-session-${session.id}'),
        borderRadius: BorderRadius.circular(6),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 3, vertical: 3),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Flexible(
                child: Text(
                  session.courseName,
                  maxLines: 3,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: 10.5,
                    height: 1.15,
                    fontWeight: FontWeight.w700,
                    color: cancelled ? appMuted : appInk,
                    decoration: cancelled ? TextDecoration.lineThrough : null,
                  ),
                ),
              ),
              if (classroom.isNotEmpty || session.hasPreviousNote) ...[
                const Spacer(),
                if (classroom.isNotEmpty)
                  Text(
                    classroom,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      fontSize: 9.5,
                      height: 1.1,
                      color: appMuted,
                    ),
                  ),
                if (session.hasPreviousNote)
                  Padding(
                    padding: const EdgeInsets.only(top: 1),
                    child: Row(
                      key: ValueKey('overview-note-${session.id}'),
                      children: [
                        const Icon(
                          Icons.sticky_note_2_outlined,
                          size: 9,
                          color: appTeal,
                        ),
                        const SizedBox(width: 1),
                        Expanded(
                          child: Text(
                            session.previousNotePreview,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              fontSize: 8.5,
                              height: 1.05,
                              color: appTeal,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
              ],
            ],
          ),
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
