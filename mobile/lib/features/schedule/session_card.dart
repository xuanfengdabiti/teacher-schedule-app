import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../../core/json.dart';
import 'models.dart';

class SessionCard extends StatelessWidget {
  const SessionCard({
    super.key,
    required this.session,
    this.onTap,
    this.onLongPress,
  });

  final Session session;
  final VoidCallback? onTap;
  final VoidCallback? onLongPress;

  @override
  Widget build(BuildContext context) {
    final cancelled = session.status == 'cancelled';
    final completed = session.status == 'completed';
    final moved = session.isMoved || session.status == 'moved';
    final status = cancelled
        ? const _CardStatus('停课', Color(0xFFB76A18))
        : completed
        ? const _CardStatus('已完成', appMint)
        : moved
        ? const _CardStatus('调课', Color(0xFFB76A18))
        : null;

    return Container(
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: appLine),
        boxShadow: const [
          BoxShadow(
            color: Color(0x0A1B2B45),
            blurRadius: 8,
            offset: Offset(0, 2),
          ),
        ],
      ),
      child: Material(
        color: Colors.transparent,
        borderRadius: BorderRadius.circular(12),
        clipBehavior: Clip.antiAlias,
        child: InkWell(
          onTap: onTap,
          onLongPress: onLongPress,
          child: IntrinsicHeight(
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Container(width: 3, color: cancelled ? appMuted : appTeal),
                SizedBox(
                  width: 66,
                  child: Padding(
                    padding: const EdgeInsets.fromLTRB(9, 10, 6, 10),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        FittedBox(
                          fit: BoxFit.scaleDown,
                          alignment: Alignment.centerLeft,
                          child: Text(
                            session.periodsLabel,
                            maxLines: 1,
                            style: TextStyle(
                              fontSize: 13,
                              height: 1.2,
                              fontWeight: FontWeight.w700,
                              color: cancelled ? appMuted : appTeal,
                            ),
                          ),
                        ),
                        const SizedBox(height: 5),
                        Text(
                          '${timeZh(session.startAt)}\n${timeZh(session.endAt)}',
                          style: const TextStyle(
                            fontSize: 11,
                            height: 1.35,
                            color: appMuted,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
                Expanded(
                  child: Padding(
                    padding: const EdgeInsets.fromLTRB(0, 10, 10, 10),
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
                                style: TextStyle(
                                  fontSize: 16,
                                  height: 1.3,
                                  fontWeight: FontWeight.w700,
                                  color: cancelled ? appMuted : appInk,
                                  decoration: cancelled
                                      ? TextDecoration.lineThrough
                                      : null,
                                ),
                              ),
                            ),
                            if (status != null) ...[
                              const SizedBox(width: 6),
                              _StatusTag(status: status),
                            ],
                          ],
                        ),
                        const SizedBox(height: 4),
                        Text(
                          session.teacherNames.isEmpty
                              ? '教师未安排'
                              : session.teacherNames,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            fontSize: 12.5,
                            height: 1.3,
                            color: appMuted,
                          ),
                        ),
                        const SizedBox(height: 4),
                        Row(
                          children: [
                            const Icon(
                              Icons.location_on_outlined,
                              size: 14,
                              color: appMuted,
                            ),
                            const SizedBox(width: 3),
                            Expanded(
                              child: Text(
                                session.classroom.displayName.isEmpty
                                    ? '教室未安排'
                                    : session.classroom.displayName,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(
                                  fontSize: 12.5,
                                  height: 1.3,
                                  color: appInk,
                                ),
                              ),
                            ),
                            const Icon(
                              Icons.chevron_right_rounded,
                              color: appMuted,
                              size: 17,
                            ),
                          ],
                        ),
                        if (session.hasPreviousNote) ...[
                          const SizedBox(height: 7),
                          Text(
                            '备注：${session.previousNotePreview}',
                            key: ValueKey('previous-note-${session.id}'),
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              fontSize: 12,
                              height: 1.35,
                              color: appMuted,
                            ),
                          ),
                        ],
                      ],
                    ),
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

class _CardStatus {
  const _CardStatus(this.label, this.color);

  final String label;
  final Color color;
}

class _StatusTag extends StatelessWidget {
  const _StatusTag({required this.status});

  final _CardStatus status;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: status.color.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Text(
        status.label,
        style: TextStyle(
          fontSize: 11,
          height: 1.2,
          fontWeight: FontWeight.w600,
          color: status.color,
        ),
      ),
    );
  }
}
