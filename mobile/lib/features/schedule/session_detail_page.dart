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
            icon: const Icon(Icons.refresh_rounded),
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
        padding: const EdgeInsets.fromLTRB(18, 4, 18, 28),
        children: [
          Container(
            padding: const EdgeInsets.all(20),
            decoration: BoxDecoration(
              gradient: const LinearGradient(
                colors: [Color(0xFFE5F3F0), Color(0xFFF5FAF9)],
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
              ),
              borderRadius: BorderRadius.circular(22),
              border: Border.all(color: const Color(0xFFD8EAE5)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Wrap(
                  spacing: 8,
                  runSpacing: 6,
                  children: [
                    InfoPill(
                      label: cancelled ? '已停课' : session.statusLabel,
                      color: cancelled ? const Color(0xFFAD6A12) : appTeal,
                    ),
                    if (session.isMoved)
                      const InfoPill(label: '已调课', color: Color(0xFFAD6A12)),
                    if (session.teachingWeek != null)
                      InfoPill(label: '第${session.teachingWeek}周'),
                  ],
                ),
                const SizedBox(height: 12),
                Text(
                  session.courseName,
                  style: const TextStyle(
                    fontSize: 23,
                    height: 1.35,
                    fontWeight: FontWeight.w800,
                    color: appInk,
                  ),
                ),
                const SizedBox(height: 8),
                Text(
                  '${monthDayZh(session.date)} ${weekdayZh(session.weekday)} · '
                  '${session.periodsLabel} · ${session.timeRange}',
                  style: const TextStyle(
                    fontSize: 16,
                    color: appMuted,
                    height: 1.45,
                  ),
                ),
                if (session.statusReason.isNotEmpty) ...[
                  const SizedBox(height: 10),
                  Text(
                    session.statusReason,
                    style: const TextStyle(
                      fontSize: 15,
                      color: Color(0xFF8E5310),
                      height: 1.45,
                    ),
                  ),
                ],
              ],
            ),
          ),
          const SizedBox(height: 14),
          if (session.isMine)
            _SessionActions(session: session, api: api)
          else
            Container(
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(
                color: const Color(0xFFF3F6F5),
                borderRadius: BorderRadius.circular(14),
              ),
              child: const Text(
                '由这节课的授课教师填写备注和教学事项。',
                style: TextStyle(fontSize: 15, color: appMuted),
              ),
            ),
          const SizedBox(height: 14),
          Card(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(18, 18, 18, 8),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('课程信息', style: Theme.of(context).textTheme.titleMedium),
                  const SizedBox(height: 10),
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
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        FilledButton.icon(
          key: const Key('openNotesButton'),
          onPressed: () => context.push(
            '/sessions/${session.id}/notes',
            extra: {'title': session.courseName, 'editable': true},
          ),
          icon: const Icon(Icons.edit_note_outlined),
          label: const Text('教学备注'),
        ),
        const SizedBox(height: 10),
        Row(
          children: [
            Expanded(
              child: OutlinedButton.icon(
                key: const Key('openTeachingPlanButton'),
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
                icon: const Icon(Icons.checklist_rtl),
                label: const Text('教学计划'),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: OutlinedButton.icon(
                key: const Key('openCompletionButton'),
                onPressed: () => Navigator.of(context).push<void>(
                  MaterialPageRoute(
                    builder: (context) =>
                        CompletionPage(api: api, sessionId: session.id),
                  ),
                ),
                icon: const Icon(Icons.timelapse),
                label: const Text('登记学时'),
              ),
            ),
          ],
        ),
        const SizedBox(height: 10),
        Row(
          children: [
            Expanded(
              child: OutlinedButton.icon(
                key: const Key('openHandoffButton'),
                onPressed: () => Navigator.of(context).push<void>(
                  MaterialPageRoute(
                    builder: (context) => HandoffComposePage(
                      api: api,
                      sourceSessionId: session.id,
                    ),
                  ),
                ),
                icon: const Icon(Icons.swap_horiz_rounded),
                label: const Text('进度交接'),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: OutlinedButton.icon(
                key: const Key('openRescheduleButton'),
                onPressed: () => Navigator.of(context).push<void>(
                  MaterialPageRoute(
                    builder: (context) =>
                        ReschedulePage(api: api, sessionId: session.id),
                  ),
                ),
                icon: const Icon(Icons.published_with_changes),
                label: const Text('查询调课'),
              ),
            ),
          ],
        ),
        const SizedBox(height: 10),
        OutlinedButton.icon(
          key: const Key('openPreparationButton'),
          onPressed: () => Navigator.of(context).push<void>(
            MaterialPageRoute(
              builder: (context) => PreparationPage(
                api: PreparationRepository(api),
                sessionId: session.id,
              ),
            ),
          ),
          icon: const Icon(Icons.auto_awesome),
          label: const Text('AI备课'),
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
    return Padding(
      padding: EdgeInsets.only(bottom: last ? 10 : 0),
      child: Column(
        children: [
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 11),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(icon, size: 20, color: appTeal),
                const SizedBox(width: 10),
                SizedBox(
                  width: 72,
                  child: Text(
                    label,
                    style: const TextStyle(fontSize: 15, color: appMuted),
                  ),
                ),
                Expanded(
                  child: SelectableText(
                    value.isEmpty ? '—' : value,
                    style: const TextStyle(
                      fontSize: 16,
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
      ),
    );
  }
}
