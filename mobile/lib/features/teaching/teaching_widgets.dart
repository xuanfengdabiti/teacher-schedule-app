import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../../core/json.dart';
import 'teaching_models.dart';

class TeachingStatusPill extends StatelessWidget {
  const TeachingStatusPill({
    super.key,
    required this.label,
    this.color = appTeal,
  });

  final String label;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        label,
        style: TextStyle(
          color: color,
          fontSize: 13,
          fontWeight: FontWeight.w700,
        ),
      ),
    );
  }
}

class TeachingMetric extends StatelessWidget {
  const TeachingMetric({
    super.key,
    required this.label,
    required this.value,
    this.caption,
    this.emphasis = false,
  });

  final String label;
  final String value;
  final String? caption;
  final bool emphasis;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 140,
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
      decoration: BoxDecoration(
        color: emphasis ? const Color(0xFFEAF5F2) : const Color(0xFFF7FAF9),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: emphasis ? const Color(0xFFBFDCD3) : appLine),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: const TextStyle(fontSize: 13, color: appMuted)),
          const SizedBox(height: 4),
          Text(
            value,
            style: TextStyle(
              fontSize: emphasis ? 24 : 21,
              height: 1.1,
              fontWeight: FontWeight.w700,
              color: emphasis ? appTeal : appInk,
            ),
          ),
          if (caption != null) ...[
            const SizedBox(height: 3),
            Text(
              caption!,
              style: const TextStyle(fontSize: 12.5, color: appMuted),
            ),
          ],
        ],
      ),
    );
  }
}

class TeachingNotice extends StatelessWidget {
  const TeachingNotice({
    super.key,
    required this.text,
    this.icon = Icons.info_outline,
    this.tone = TeachingNoticeTone.info,
  });

  final String text;
  final IconData icon;
  final TeachingNoticeTone tone;

  @override
  Widget build(BuildContext context) {
    final (background, foreground) = switch (tone) {
      TeachingNoticeTone.info => (const Color(0xFFEAF5F2), appTeal),
      TeachingNoticeTone.warning => (
        const Color(0xFFFFFAF1),
        const Color(0xFF8A5A0B),
      ),
      TeachingNoticeTone.error => (
        const Color(0xFFFFF1EF),
        const Color(0xFF9A3B30),
      ),
    };
    return Container(
      padding: const EdgeInsets.all(13),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(13),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 20, color: foreground),
          const SizedBox(width: 9),
          Expanded(
            child: Text(
              text,
              style: TextStyle(fontSize: 14, height: 1.45, color: foreground),
            ),
          ),
        ],
      ),
    );
  }
}

enum TeachingNoticeTone { info, warning, error }

String teachingDateTime(DateTime? value) {
  if (value == null) {
    return '—';
  }
  return '${monthDayZh(value)} ${timeZh(value)}';
}

String teachingSessionText(HandoffSession? session) {
  if (session == null) {
    return '未关联课次';
  }
  final date = session.date == null ? '' : monthDayZh(session.date!);
  return '$date 第 ${session.startPeriod}-${session.endPeriod} 节';
}
