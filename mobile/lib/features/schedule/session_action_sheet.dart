import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../app/theme.dart';
import '../../core/json.dart';
import '../../core/network/api_client.dart';
import '../collaboration/reschedule_page.dart';
import '../preparation/preparation_page.dart';
import '../preparation/preparation_repository.dart';
import '../teaching/completion_page.dart';
import '../teaching/handoff_compose_page.dart';
import '../teaching/teaching_plan_page.dart';
import 'models.dart';

enum _SessionAction {
  notes,
  handoff,
  plan,
  preparation,
  completion,
  reschedule,
  detail,
}

class _ActionSpec {
  const _ActionSpec({
    required this.action,
    required this.label,
    required this.icon,
    required this.color,
    required this.background,
    this.emphasized = false,
  });

  final _SessionAction action;
  final String label;
  final IconData icon;
  final Color color;
  final Color background;
  final bool emphasized;
}

Future<void> showSessionActionSheet({
  required BuildContext context,
  required Session session,
  required ApiClient api,
}) async {
  final action = await showModalBottomSheet<_SessionAction>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    builder: (context) => _SessionActionSheet(session: session),
  );
  if (action == null || !context.mounted) {
    return;
  }

  switch (action) {
    case _SessionAction.notes:
      await context.push<void>(
        '/sessions/${session.id}/notes',
        extra: {'title': session.courseName, 'editable': true},
      );
      break;
    case _SessionAction.handoff:
      await Navigator.of(context).push<void>(
        MaterialPageRoute(
          builder: (context) =>
              HandoffComposePage(api: api, sourceSessionId: session.id),
        ),
      );
      break;
    case _SessionAction.plan:
      if (session.offeringId.isEmpty) {
        return;
      }
      await Navigator.of(context).push<void>(
        MaterialPageRoute(
          builder: (context) =>
              TeachingPlanPage(api: api, offeringId: session.offeringId),
        ),
      );
      break;
    case _SessionAction.preparation:
      await Navigator.of(context).push<void>(
        MaterialPageRoute(
          builder: (context) => PreparationPage(
            api: PreparationRepository(api),
            sessionId: session.id,
          ),
        ),
      );
      break;
    case _SessionAction.completion:
      await Navigator.of(context).push<void>(
        MaterialPageRoute(
          builder: (context) => CompletionPage(api: api, sessionId: session.id),
        ),
      );
      break;
    case _SessionAction.reschedule:
      await Navigator.of(context).push<void>(
        MaterialPageRoute(
          builder: (context) => ReschedulePage(api: api, sessionId: session.id),
        ),
      );
      break;
    case _SessionAction.detail:
      await context.push<void>('/sessions/${session.id}');
      break;
  }
}

class _SessionActionSheet extends StatelessWidget {
  const _SessionActionSheet({required this.session});

  final Session session;

  @override
  Widget build(BuildContext context) {
    final actions = <_ActionSpec>[
      if (session.isMine) ...[
        const _ActionSpec(
          action: _SessionAction.notes,
          label: '教学备注',
          icon: Icons.edit_note_outlined,
          color: appTeal,
          background: appBlueSoft,
        ),
        const _ActionSpec(
          action: _SessionAction.handoff,
          label: '进度交接',
          icon: Icons.swap_horiz_rounded,
          color: appMint,
          background: appSuccessSoft,
        ),
        if (session.offeringId.isNotEmpty)
          const _ActionSpec(
            action: _SessionAction.plan,
            label: '教学计划',
            icon: Icons.checklist_rtl,
            color: Color(0xFF7A5AF8),
            background: Color(0xFFF1EDFF),
          ),
        const _ActionSpec(
          action: _SessionAction.preparation,
          label: 'AI备课',
          icon: Icons.auto_awesome_outlined,
          color: Color(0xFF2E90FA),
          background: Color(0xFFECF6FF),
        ),
        const _ActionSpec(
          action: _SessionAction.completion,
          label: '登记学时',
          icon: Icons.timelapse,
          color: Color(0xFF168F6A),
          background: Color(0xFFE8F7F2),
        ),
        const _ActionSpec(
          action: _SessionAction.reschedule,
          label: '查询调课',
          icon: Icons.published_with_changes,
          color: appOrange,
          background: appOrangeSoft,
          emphasized: true,
        ),
      ],
      const _ActionSpec(
        action: _SessionAction.detail,
        label: '课程详情',
        icon: Icons.article_outlined,
        color: Color(0xFF5B6472),
        background: Color(0xFFF2F4F8),
      ),
    ];
    final place = [
      if (session.classroom.displayName.isNotEmpty)
        session.classroom.displayName
      else
        '教室未安排',
    ].join(' · ');

    return SafeArea(
      top: false,
      child: ConstrainedBox(
        constraints: BoxConstraints(
          maxHeight: MediaQuery.sizeOf(context).height * 0.84,
        ),
        child: SingleChildScrollView(
          key: const Key('sessionActionSheet'),
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 14),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Expanded(
                              child: Text(
                                session.courseName,
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(
                                  fontSize: 17,
                                  height: 1.3,
                                  fontWeight: FontWeight.w700,
                                  color: appInk,
                                ),
                              ),
                            ),
                            if (!session.isMine) ...[
                              const SizedBox(width: 8),
                              const _ViewOnlyTag(),
                            ],
                          ],
                        ),
                        const SizedBox(height: 5),
                        Text(
                          '${monthDayZh(session.date)} '
                          '${weekdayZh(session.weekday)} · '
                          '${session.periodsLabel} · $place',
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            fontSize: 12,
                            height: 1.35,
                            color: appMuted,
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(width: 4),
                  IconButton(
                    key: const Key('sessionActionClose'),
                    tooltip: '关闭',
                    onPressed: () => Navigator.of(context).pop(),
                    visualDensity: VisualDensity.compact,
                    icon: const Icon(Icons.close_rounded, size: 19),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              for (final action in actions)
                Padding(
                  padding: const EdgeInsets.only(bottom: 2),
                  child: _ActionRow(
                    spec: action,
                    onTap: () => Navigator.of(context).pop(action.action),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ActionRow extends StatelessWidget {
  const _ActionRow({required this.spec, required this.onTap});

  final _ActionSpec spec;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: spec.emphasized ? const Color(0xFFFFF8EF) : Colors.transparent,
      borderRadius: BorderRadius.circular(10),
      child: InkWell(
        key: ValueKey('sessionAction-${spec.action.name}'),
        borderRadius: BorderRadius.circular(10),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
          child: Row(
            children: [
              Container(
                width: 32,
                height: 32,
                decoration: BoxDecoration(
                  color: spec.background,
                  borderRadius: BorderRadius.circular(9),
                ),
                child: Icon(spec.icon, size: 18, color: spec.color),
              ),
              const SizedBox(width: 11),
              Expanded(
                child: Text(
                  spec.label,
                  style: TextStyle(
                    fontSize: 14,
                    height: 1.3,
                    fontWeight: FontWeight.w600,
                    color: spec.emphasized ? const Color(0xFFAD641C) : appInk,
                  ),
                ),
              ),
              const Icon(
                Icons.chevron_right_rounded,
                size: 18,
                color: appMuted,
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ViewOnlyTag extends StatelessWidget {
  const _ViewOnlyTag();

  @override
  Widget build(BuildContext context) {
    return Container(
      key: const Key('sessionActionViewOnly'),
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
      decoration: BoxDecoration(
        color: const Color(0xFFF2F4F8),
        borderRadius: BorderRadius.circular(6),
      ),
      child: const Text(
        '仅查看',
        style: TextStyle(
          fontSize: 11,
          height: 1.2,
          fontWeight: FontWeight.w600,
          color: appMuted,
        ),
      ),
    );
  }
}
