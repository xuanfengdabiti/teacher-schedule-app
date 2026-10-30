import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/theme.dart';
import '../../core/json.dart';
import '../../core/network/api_client.dart';
import '../../core/providers.dart';
import '../../shared/status_views.dart';
import '../collaboration/reschedule_page.dart';
import '../preparation/preparation_page.dart';
import '../preparation/preparation_repository.dart';
import '../teaching/completion_page.dart';
import '../teaching/handoff_compose_page.dart';
import '../teaching/teaching_plan_page.dart';
import 'models.dart';
import 'schedule_controller.dart';

class SessionDetailPage extends ConsumerWidget {
  const SessionDetailPage({super.key, required this.sessionId});

  final String sessionId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final detail = ref.watch(sessionDetailProvider(sessionId));
    return Scaffold(
      appBar: AppBar(
        title: const Text('课程详情'),
        actions: [
          IconButton(
            tooltip: '刷新',
            onPressed: () => ref.invalidate(sessionDetailProvider(sessionId)),
            icon: const Icon(Icons.refresh_rounded, size: 21),
          ),
        ],
      ),
      body: detail.when(
        loading: () => const LoadingView(message: '正在加载课程详情…'),
        error: (error, stackTrace) => ErrorView(
          message: '$error',
          onRetry: () => ref.invalidate(sessionDetailProvider(sessionId)),
        ),
        data: (session) =>
            _DetailBody(session: session, api: ref.read(apiClientProvider)),
      ),
    );
  }
}

class _DetailBody extends StatelessWidget {
  const _DetailBody({required this.session, required this.api});

  final Session session;
  final ApiClient api;

  @override
  Widget build(BuildContext context) {
    final cancelled = session.status == 'cancelled';
    final hasLeadTeacher = session.leadTeacherName?.isNotEmpty == true;
    return SafeArea(
      top: false,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(14, 6, 14, 24),
        children: [
          _Surface(
            padding: const EdgeInsets.fromLTRB(14, 12, 14, 13),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Wrap(
                  spacing: 12,
                  runSpacing: 5,
                  children: [
                    _StatusDot(
                      label: cancelled ? '已停课' : session.statusLabel,
                      color: cancelled ? const Color(0xFFB7791F) : appTeal,
                    ),
                    if (session.isMoved)
                      const _StatusDot(label: '已调课', color: Color(0xFFB7791F)),
                    if (session.teachingWeek != null)
                      _StatusDot(
                        label: '第${session.teachingWeek}周',
                        color: appMuted,
                      ),
                  ],
                ),
                const SizedBox(height: 8),
                Text(
                  session.courseName,
                  style: const TextStyle(
                    fontSize: 20,
                    height: 1.35,
                    fontWeight: FontWeight.w700,
                    color: appInk,
                  ),
                ),
                const SizedBox(height: 5),
                Text(
                  '${monthDayZh(session.date)} ${weekdayZh(session.weekday)} · '
                  '${session.periodsLabel} · ${session.timeRange}',
                  style: const TextStyle(
                    fontSize: 13.5,
                    color: appMuted,
                    height: 1.45,
                  ),
                ),
                if (session.statusReason.isNotEmpty) ...[
                  const SizedBox(height: 7),
                  Text(
                    session.statusReason,
                    style: const TextStyle(
                      fontSize: 12.5,
                      color: Color(0xFF8E5310),
                      height: 1.45,
                    ),
                  ),
                ],
              ],
            ),
          ),
          const SizedBox(height: 10),
          if (session.isMine)
            _SessionActions(session: session, api: api)
          else
            const _Surface(
              padding: EdgeInsets.fromLTRB(12, 9, 12, 9),
              child: Row(
                children: [
                  Icon(Icons.info_outline, size: 17, color: appMuted),
                  SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      '由这节课的授课教师填写备注和教学事项。',
                      style: TextStyle(
                        fontSize: 13,
                        height: 1.45,
                        color: appMuted,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          const SizedBox(height: 10),
          _Surface(
            padding: const EdgeInsets.fromLTRB(14, 12, 14, 4),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  '课程信息',
                  style: TextStyle(
                    fontSize: 15,
                    fontWeight: FontWeight.w700,
                    color: appInk,
                  ),
                ),
                const SizedBox(height: 4),
                _InfoRow(
                  icon: Icons.person_outline,
                  label: '教师',
                  value: session.teacherNames.isEmpty
                      ? '未安排'
                      : session.teacherNames,
                ),
                _InfoRow(
                  icon: Icons.location_on_outlined,
                  label: '教室',
                  value: session.classroom.displayName.isEmpty
                      ? '未安排'
                      : session.classroom.displayName,
                ),
                _InfoRow(
                  icon: Icons.schedule,
                  label: '时间',
                  value:
                      '${monthDayZh(session.date)} '
                      '${weekdayZh(session.weekday)}\n'
                      '${session.periodsLabel}  ${session.timeRange}',
                ),
                _InfoRow(
                  icon: Icons.menu_book_outlined,
                  label: '计划学时',
                  value: _hours(session.plannedHours),
                ),
                _InfoRow(
                  icon: Icons.timer_outlined,
                  label: '实际学时',
                  value: session.actualHours == null
                      ? '未完成'
                      : _hours(session.actualHours),
                ),
                _InfoRow(
                  icon: Icons.flag_outlined,
                  label: '课程目标',
                  value: session.offeringTargetHours == null
                      ? '—'
                      : _hours(session.offeringTargetHours),
                ),
                _InfoRow(
                  icon: Icons.people_alt_outlined,
                  label: '选课人数',
                  value: session.enrolledCount == null
                      ? '—'
                      : '${session.enrolledCount} 人',
                  last: !hasLeadTeacher,
                ),
                if (hasLeadTeacher)
                  _InfoRow(
                    icon: Icons.star_outline,
                    label: '负责人',
                    value: session.leadTeacherName!,
                    last: true,
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  static String _hours(String? value) {
    if (value == null || value.isEmpty) {
      return '—';
    }
    return '$value 学时';
  }
}

class _SessionActions extends StatelessWidget {
  const _SessionActions({required this.session, required this.api});

  final Session session;
  final ApiClient api;

  @override
  Widget build(BuildContext context) {
    final actionText = const TextStyle(
      fontSize: 13.5,
      fontWeight: FontWeight.w600,
    );
    return _Surface(
      padding: const EdgeInsets.fromLTRB(10, 10, 10, 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Padding(
            padding: EdgeInsets.fromLTRB(2, 0, 2, 7),
            child: Text(
              '常用操作',
              style: TextStyle(fontSize: 12.5, color: appMuted),
            ),
          ),
          FilledButton.icon(
            key: const Key('openPreparationButton'),
            onPressed: () => Navigator.of(context).push<void>(
              MaterialPageRoute(
                builder: (context) => PreparationPage(
                  api: PreparationRepository(api),
                  sessionId: session.id,
                ),
              ),
            ),
            style: FilledButton.styleFrom(
              minimumSize: const Size.fromHeight(42),
              textStyle: actionText,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(9),
              ),
            ),
            icon: const Icon(Icons.auto_awesome, size: 18),
            label: const Text('AI备课'),
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: _OutlineAction(
                  key: const Key('openNotesButton'),
                  icon: Icons.edit_note_outlined,
                  label: '教学备注',
                  onPressed: () => context.push(
                    '/sessions/${session.id}/notes',
                    extra: {'title': session.courseName, 'editable': true},
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: _OutlineAction(
                  key: const Key('openTeachingPlanButton'),
                  icon: Icons.checklist_rtl,
                  label: '教学计划',
                  onPressed: session.offeringId.isEmpty
                      ? null
                      : () => Navigator.of(context).push<void>(
                          MaterialPageRoute(
                            builder: (context) => TeachingPlanPage(
                              api: api,
                              offeringId: session.offeringId,
                            ),
                          ),
                        ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: _OutlineAction(
                  key: const Key('openCompletionButton'),
                  icon: Icons.timelapse,
                  label: '登记学时',
                  onPressed: () => Navigator.of(context).push<void>(
                    MaterialPageRoute(
                      builder: (context) =>
                          CompletionPage(api: api, sessionId: session.id),
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: _OutlineAction(
                  key: const Key('openHandoffButton'),
                  icon: Icons.swap_horiz_rounded,
                  label: '进度交接',
                  onPressed: () => Navigator.of(context).push<void>(
                    MaterialPageRoute(
                      builder: (context) => HandoffComposePage(
                        api: api,
                        sourceSessionId: session.id,
                      ),
                    ),
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          _OutlineAction(
            key: const Key('openRescheduleButton'),
            icon: Icons.published_with_changes,
            label: '查询调课',
            onPressed: () => Navigator.of(context).push<void>(
              MaterialPageRoute(
                builder: (context) =>
                    ReschedulePage(api: api, sessionId: session.id),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _OutlineAction extends StatelessWidget {
  const _OutlineAction({
    super.key,
    required this.icon,
    required this.label,
    required this.onPressed,
  });

  final IconData icon;
  final String label;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    return OutlinedButton.icon(
      onPressed: onPressed,
      style: OutlinedButton.styleFrom(
        minimumSize: const Size.fromHeight(42),
        padding: const EdgeInsets.symmetric(horizontal: 8),
        foregroundColor: appInk,
        side: const BorderSide(color: appLine),
        textStyle: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(9)),
      ),
      icon: Icon(icon, size: 17, color: appTeal),
      label: Text(label, maxLines: 1, overflow: TextOverflow.ellipsis),
    );
  }
}

class _Surface extends StatelessWidget {
  const _Surface({required this.child, required this.padding});

  final Widget child;
  final EdgeInsetsGeometry padding;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: padding,
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: appLine),
      ),
      child: child,
    );
  }
}

class _StatusDot extends StatelessWidget {
  const _StatusDot({required this.label, required this.color});

  final String label;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 5,
          height: 5,
          decoration: BoxDecoration(color: color, shape: BoxShape.circle),
        ),
        const SizedBox(width: 5),
        Text(
          label,
          style: TextStyle(
            fontSize: 12.5,
            fontWeight: FontWeight.w600,
            color: color,
          ),
        ),
      ],
    );
  }
}

class _InfoRow extends StatelessWidget {
  const _InfoRow({
    required this.icon,
    required this.label,
    required this.value,
    this.last = false,
  });

  final IconData icon;
  final String label;
  final String value;
  final bool last;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 8),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(icon, size: 17, color: appMuted),
              const SizedBox(width: 8),
              SizedBox(
                width: 64,
                child: Text(
                  label,
                  style: const TextStyle(
                    fontSize: 13,
                    height: 1.45,
                    color: appMuted,
                  ),
                ),
              ),
              Expanded(
                child: SelectableText(
                  value.isEmpty ? '—' : value,
                  style: const TextStyle(
                    fontSize: 14,
                    height: 1.45,
                    color: appInk,
                  ),
                ),
              ),
            ],
          ),
        ),
        if (!last) const Divider(height: 1),
      ],
    );
  }
}
