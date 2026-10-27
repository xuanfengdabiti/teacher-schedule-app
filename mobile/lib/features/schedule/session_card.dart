import 'package:flutter/material.dart';

import '../../app/theme.dart';
import 'models.dart';

class SessionCard extends StatelessWidget {
  const SessionCard({super.key, required this.session, this.onTap});

  final Session session;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final cancelled = session.status == 'cancelled';
    return Card(
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: IntrinsicHeight(
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Container(
                width: 88,
                padding: const EdgeInsets.symmetric(
                  horizontal: 8,
                  vertical: 16,
                ),
                color: cancelled
                    ? const Color(0xFFF5F5F4)
                    : const Color(0xFFEAF5F2),
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Text(
                      session.periodsLabel,
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.w700,
                        color: appTeal,
                      ),
                    ),
                    const SizedBox(height: 6),
                    Text(
                      session.timeRange,
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                        fontSize: 12.5,
                        height: 1.35,
                        color: appMuted,
                      ),
                    ),
                  ],
                ),
              ),
              Expanded(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(15, 14, 12, 14),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Expanded(
                            child: Text(
                              session.courseName,
                              style: TextStyle(
                                fontSize: 18,
                                height: 1.3,
                                fontWeight: FontWeight.w700,
                                color: cancelled ? appMuted : appInk,
                                decoration: cancelled
                                    ? TextDecoration.lineThrough
                                    : null,
                              ),
                            ),
                          ),
                          if (cancelled || session.isMoved) ...[
                            const SizedBox(width: 8),
                            _StatusTag(
                              label: cancelled ? '停课' : '调课',
                              color: const Color(0xFFAD6A12),
                            ),
                          ],
                        ],
                      ),
                      const SizedBox(height: 7),
                      Text(
                        session.teacherNames.isEmpty
                            ? '教师未安排'
                            : session.teacherNames,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          fontSize: 14.5,
                          height: 1.35,
                          color: appMuted,
                        ),
                      ),
                      const SizedBox(height: 9),
                      Row(
                        children: [
                          const Icon(
                            Icons.location_on_outlined,
                            size: 18,
                            color: appTeal,
                          ),
                          const SizedBox(width: 4),
                          Expanded(
                            child: Text(
                              session.classroom.displayName.isEmpty
                                  ? '教室未安排'
                                  : session.classroom.displayName,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(
                                fontSize: 14.5,
                                color: appInk,
                              ),
                            ),
                          ),
                          const Icon(
                            Icons.chevron_right,
                            color: appMuted,
                            size: 22,
                          ),
                        ],
                      ),
                      if (session.hasPreviousNote) ...[
                        const SizedBox(height: 8),
                        Container(
                          key: ValueKey('previous-note-${session.id}'),
                          width: double.infinity,
                          padding: const EdgeInsets.fromLTRB(8, 6, 8, 7),
                          decoration: BoxDecoration(
                            color: const Color(0xFFF2F7F5),
                            borderRadius: BorderRadius.circular(8),
                          ),
                          child: Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const Padding(
                                padding: EdgeInsets.only(top: 1),
                                child: Icon(
                                  Icons.sticky_note_2_outlined,
                                  size: 15,
                                  color: appTeal,
                                ),
                              ),
                              const SizedBox(width: 5),
                              Expanded(
                                child: Text(
                                  '上次备注：${session.previousNotePreview}',
                                  maxLines: 2,
                                  overflow: TextOverflow.ellipsis,
                                  style: const TextStyle(
                                    fontSize: 13.5,
                                    height: 1.35,
                                    color: appMuted,
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
            ],
          ),
        ),
      ),
    );
  }
}

class _StatusTag extends StatelessWidget {
  const _StatusTag({required this.label, required this.color});

  final String label;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(7),
      ),
      child: Text(
        label,
        style: TextStyle(
          fontSize: 12,
          fontWeight: FontWeight.w600,
          color: color,
        ),
      ),
    );
  }
}
