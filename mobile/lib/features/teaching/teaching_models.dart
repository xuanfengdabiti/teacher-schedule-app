import '../../core/json.dart';

String decimalText(Object? value, {String empty = '—'}) {
  final text = jsonString(value)?.trim();
  return text == null || text.isEmpty ? empty : text;
}

String planStatusText(String status) => switch (status) {
  'not_set' => '未建立计划',
  'on_track' => '进度正常',
  'behind' => '进度落后',
  _ => status,
};

String sessionStatusText(String status) => switch (status) {
  'scheduled' => '待上课',
  'completed' => '已完成',
  'cancelled' => '已取消',
  _ => status,
};

String handoffStatusText(String status) => switch (status) {
  'pending' => '待确认',
  'acknowledged' => '已确认',
  'withdrawn' => '已撤回',
  _ => status,
};

String completionActionText(String action) => switch (action) {
  'complete' => '完成授课',
  'correct' => '更正完成',
  'revert' => '撤销完成',
  _ => action,
};

class TeacherBrief {
  const TeacherBrief({required this.id, required this.name});

  final String id;
  final String name;

  factory TeacherBrief.fromJson(Object? value) {
    final map = jsonMap(value);
    return TeacherBrief(
      id: jsonString(map['id']) ?? '',
      name: jsonString(map['name']) ?? '未命名教师',
    );
  }
}

class PlanItem {
  const PlanItem({
    required this.id,
    required this.position,
    required this.title,
    required this.plannedHours,
    this.dueDate,
    this.dueSessionId,
    required this.archived,
  });

  final String id;
  final int position;
  final String title;
  final String plannedHours;
  final DateTime? dueDate;
  final String? dueSessionId;
  final bool archived;

  factory PlanItem.fromJson(Object? value) {
    final map = jsonMap(value);
    return PlanItem(
      id: jsonString(map['id']) ?? '',
      position: jsonInt(map['position']) ?? 0,
      title: jsonString(map['title']) ?? '',
      plannedHours: decimalText(map['planned_hours'], empty: '0.0'),
      dueDate: jsonDate(map['due_date']),
      dueSessionId: jsonString(map['due_session_id']),
      archived: map['archived'] == true,
    );
  }
}

class TeachingPlan {
  const TeachingPlan({
    required this.id,
    required this.version,
    required this.owner,
    required this.status,
    required this.items,
  });

  final String id;
  final int version;
  final TeacherBrief owner;
  final String status;
  final List<PlanItem> items;

  factory TeachingPlan.fromJson(Object? value) {
    final map = jsonMap(value);
    return TeachingPlan(
      id: jsonString(map['id']) ?? '',
      version: jsonInt(map['version']) ?? 0,
      owner: TeacherBrief.fromJson(map['owner']),
      status: jsonString(map['status']) ?? 'active',
      items: (map['items'] is List)
          ? (map['items'] as List).map(PlanItem.fromJson).toList()
          : const [],
    );
  }
}

class ProgressItem {
  const ProgressItem({
    required this.planItemId,
    required this.title,
    required this.plannedHours,
    required this.actualHours,
    this.due,
    required this.isDue,
    required this.shortfall,
    required this.archived,
  });

  final String planItemId;
  final String title;
  final String plannedHours;
  final String actualHours;
  final DateTime? due;
  final bool isDue;
  final String shortfall;
  final bool archived;

  factory ProgressItem.fromJson(Object? value) {
    final map = jsonMap(value);
    return ProgressItem(
      planItemId: jsonString(map['plan_item_id']) ?? '',
      title: jsonString(map['title']) ?? '',
      plannedHours: decimalText(map['planned_hours'], empty: '0.0'),
      actualHours: decimalText(map['actual_hours'], empty: '0.0'),
      due: jsonDate(map['due']),
      isDue: map['is_due'] == true,
      shortfall: decimalText(map['shortfall'], empty: '0.0'),
      archived: map['archived'] == true,
    );
  }
}

class OfferingProgress {
  const OfferingProgress({
    required this.targetHours,
    required this.completedHours,
    required this.remainingHours,
    required this.overHours,
    required this.remainingSessions,
    required this.pastPendingSessions,
    required this.futureSessions,
    required this.unallocatedHours,
    required this.planStatus,
    required this.behindHours,
    required this.behindThresholdHours,
    required this.items,
  });

  final String targetHours;
  final String completedHours;
  final String remainingHours;
  final String overHours;
  final int remainingSessions;
  final int pastPendingSessions;
  final int futureSessions;
  final String unallocatedHours;
  final String planStatus;
  final String behindHours;
  final String behindThresholdHours;
  final List<ProgressItem> items;

  factory OfferingProgress.fromJson(Object? value) {
    final map = jsonMap(value);
    return OfferingProgress(
      targetHours: decimalText(map['target_hours'], empty: '0.0'),
      completedHours: decimalText(map['completed_hours'], empty: '0.0'),
      remainingHours: decimalText(map['remaining_hours'], empty: '0.0'),
      overHours: decimalText(map['over_hours'], empty: '0.0'),
      remainingSessions: jsonInt(map['remaining_sessions']) ?? 0,
      pastPendingSessions: jsonInt(map['past_pending_sessions']) ?? 0,
      futureSessions: jsonInt(map['future_sessions']) ?? 0,
      unallocatedHours: decimalText(map['unallocated_hours'], empty: '0.0'),
      planStatus: jsonString(map['plan_status']) ?? 'not_set',
      behindHours: decimalText(map['behind_hours'], empty: '0.0'),
      behindThresholdHours: decimalText(
        map['behind_threshold_hours'],
        empty: '0.0',
      ),
      items: (map['items'] is List)
          ? (map['items'] as List).map(ProgressItem.fromJson).toList()
          : const [],
    );
  }
}

class PlanBundle {
  const PlanBundle({
    required this.plan,
    required this.progress,
    required this.canEdit,
  });

  final TeachingPlan? plan;
  final OfferingProgress progress;
  final bool canEdit;

  factory PlanBundle.fromJson(Object? value) {
    final map = jsonMap(value);
    return PlanBundle(
      plan: map['plan'] == null ? null : TeachingPlan.fromJson(map['plan']),
      progress: OfferingProgress.fromJson(map['progress']),
      canEdit: map['can_edit'] == true,
    );
  }
}

class PlanItemInput {
  const PlanItemInput({
    this.id,
    required this.title,
    required this.plannedHours,
    this.dueDate,
    this.dueSessionId,
  });

  final String? id;
  final String title;
  final String plannedHours;
  final DateTime? dueDate;
  final String? dueSessionId;

  Map<String, dynamic> toJson() => {
    if (id != null && id!.isNotEmpty) 'id': id,
    'title': title.trim(),
    'planned_hours': plannedHours.trim(),
    if (dueDate != null) 'due_date': isoDate(dueDate!),
    if (dueDate == null && dueSessionId != null && dueSessionId!.isNotEmpty)
      'due_session_id': dueSessionId,
  };
}

class CompletionAllocation {
  const CompletionAllocation({
    required this.planItemId,
    required this.title,
    required this.actualHours,
  });

  final String planItemId;
  final String title;
  final String actualHours;

  factory CompletionAllocation.fromJson(Object? value) {
    final map = jsonMap(value);
    return CompletionAllocation(
      planItemId: jsonString(map['plan_item_id']) ?? '',
      title: jsonString(map['title']) ?? '',
      actualHours: decimalText(map['actual_hours'], empty: '0.0'),
    );
  }
}

class AvailablePlanItem {
  const AvailablePlanItem({
    required this.id,
    required this.title,
    required this.plannedHours,
  });

  final String id;
  final String title;
  final String plannedHours;

  factory AvailablePlanItem.fromJson(Object? value) {
    final map = jsonMap(value);
    return AvailablePlanItem(
      id: jsonString(map['id']) ?? '',
      title: jsonString(map['title']) ?? '',
      plannedHours: decimalText(map['planned_hours'], empty: '0.0'),
    );
  }
}

class CompletionHistory {
  const CompletionHistory({
    required this.id,
    required this.action,
    required this.actor,
    required this.actualHoursBefore,
    required this.actualHoursAfter,
    required this.allocations,
    required this.reason,
    this.createdAt,
  });

  final String id;
  final String action;
  final TeacherBrief actor;
  final String actualHoursBefore;
  final String actualHoursAfter;
  final List<CompletionAllocation> allocations;
  final String reason;
  final DateTime? createdAt;

  factory CompletionHistory.fromJson(Object? value) {
    final map = jsonMap(value);
    return CompletionHistory(
      id: jsonString(map['id']) ?? '',
      action: jsonString(map['action']) ?? '',
      actor: TeacherBrief.fromJson(map['actor']),
      actualHoursBefore: decimalText(map['actual_hours_before']),
      actualHoursAfter: decimalText(map['actual_hours_after']),
      allocations: (map['allocations'] is List)
          ? (map['allocations'] as List)
                .map(CompletionAllocation.fromJson)
                .toList()
          : const [],
      reason: jsonString(map['reason']) ?? '',
      createdAt: jsonDateTime(map['created_at']),
    );
  }
}

class CompletionDetail {
  const CompletionDetail({
    required this.sessionId,
    required this.status,
    required this.actualHours,
    this.completedBy,
    this.completedAt,
    required this.completionRef,
    required this.version,
    required this.allocations,
    required this.availableItems,
    required this.history,
  });

  final String sessionId;
  final String status;
  final String? actualHours;
  final TeacherBrief? completedBy;
  final DateTime? completedAt;
  final String completionRef;
  final int version;
  final List<CompletionAllocation> allocations;
  final List<AvailablePlanItem> availableItems;
  final List<CompletionHistory> history;

  bool get isCompleted => status == 'completed';

  factory CompletionDetail.fromJson(Object? value) {
    final map = jsonMap(value);
    return CompletionDetail(
      sessionId: jsonString(map['session_id']) ?? '',
      status: jsonString(map['status']) ?? 'scheduled',
      actualHours: jsonString(map['actual_hours']),
      completedBy: map['completed_by'] == null
          ? null
          : TeacherBrief.fromJson(map['completed_by']),
      completedAt: jsonDateTime(map['completed_at']),
      completionRef: jsonString(map['completion_ref']) ?? '',
      version: jsonInt(map['version']) ?? 0,
      allocations: (map['allocations'] is List)
          ? (map['allocations'] as List)
                .map(CompletionAllocation.fromJson)
                .toList()
          : const [],
      availableItems: (map['available_items'] is List)
          ? (map['available_items'] as List)
                .map(AvailablePlanItem.fromJson)
                .toList()
          : const [],
      history: (map['history'] is List)
          ? (map['history'] as List).map(CompletionHistory.fromJson).toList()
          : const [],
    );
  }
}

class HandoffSession {
  const HandoffSession({
    required this.id,
    this.date,
    required this.startPeriod,
    required this.endPeriod,
  });

  final String id;
  final DateTime? date;
  final int startPeriod;
  final int endPeriod;

  factory HandoffSession.fromJson(Object? value) {
    final map = jsonMap(value);
    return HandoffSession(
      id: jsonString(map['id']) ?? '',
      date: jsonDate(map['date']),
      startPeriod: jsonInt(map['start_period']) ?? 0,
      endPeriod: jsonInt(map['end_period']) ?? 0,
    );
  }
}

class Handoff {
  const Handoff({
    required this.id,
    required this.offeringId,
    required this.courseName,
    required this.teachingClassName,
    required this.sender,
    required this.recipient,
    this.sourceSession,
    this.targetSession,
    required this.contentSnapshot,
    this.notePublishedVersion,
    required this.status,
    this.readAt,
    this.acknowledgedAt,
    this.withdrawnAt,
    this.createdAt,
    required this.canAcknowledge,
    required this.canWithdraw,
  });

  final String id;
  final String offeringId;
  final String courseName;
  final String teachingClassName;
  final TeacherBrief sender;
  final TeacherBrief recipient;
  final HandoffSession? sourceSession;
  final HandoffSession? targetSession;
  final String contentSnapshot;
  final int? notePublishedVersion;
  final String status;
  final DateTime? readAt;
  final DateTime? acknowledgedAt;
  final DateTime? withdrawnAt;
  final DateTime? createdAt;
  final bool canAcknowledge;
  final bool canWithdraw;

  String get sessionLabel {
    final session = sourceSession;
    if (session == null) {
      return '未关联具体课次';
    }
    final date = session.date == null ? '' : monthDayZh(session.date!);
    return '$date 第 ${session.startPeriod}-${session.endPeriod} 节';
  }

  factory Handoff.fromJson(Object? value) {
    final map = jsonMap(value);
    final offering = jsonMap(map['offering']);
    final teachingClass = jsonMap(offering['teaching_class']);
    return Handoff(
      id: jsonString(map['id']) ?? '',
      offeringId: jsonString(offering['id']) ?? '',
      courseName: jsonString(offering['course_name']) ?? '未命名课程',
      teachingClassName: jsonString(teachingClass['name']) ?? '未命名教学班',
      sender: TeacherBrief.fromJson(map['sender']),
      recipient: TeacherBrief.fromJson(map['recipient']),
      sourceSession: map['source_session'] == null
          ? null
          : HandoffSession.fromJson(map['source_session']),
      targetSession: map['target_session'] == null
          ? null
          : HandoffSession.fromJson(map['target_session']),
      contentSnapshot: jsonString(map['content_snapshot']) ?? '',
      notePublishedVersion: jsonInt(map['note_published_version']),
      status: jsonString(map['status']) ?? 'pending',
      readAt: jsonDateTime(map['read_at']),
      acknowledgedAt: jsonDateTime(map['acknowledged_at']),
      withdrawnAt: jsonDateTime(map['withdrawn_at']),
      createdAt: jsonDateTime(map['created_at']),
      canAcknowledge: map['can_acknowledge'] == true,
      canWithdraw: map['can_withdraw'] == true,
    );
  }
}

class HandoffRecipient {
  const HandoffRecipient({
    required this.id,
    required this.name,
    required this.isSuggested,
  });

  final String id;
  final String name;
  final bool isSuggested;

  factory HandoffRecipient.fromJson(Object? value) {
    final map = jsonMap(value);
    return HandoffRecipient(
      id: jsonString(map['id']) ?? '',
      name: jsonString(map['name']) ?? '未命名教师',
      isSuggested: map['is_suggested'] == true,
    );
  }
}

class HandoffPrefill {
  const HandoffPrefill({
    required this.recipients,
    this.suggestedTargetSession,
    required this.content,
    this.noteId,
    this.notePublishedVersion,
  });

  final List<HandoffRecipient> recipients;
  final HandoffSession? suggestedTargetSession;
  final String content;
  final String? noteId;
  final int? notePublishedVersion;

  factory HandoffPrefill.fromJson(Object? value) {
    final map = jsonMap(value);
    return HandoffPrefill(
      recipients: (map['recipients'] is List)
          ? (map['recipients'] as List).map(HandoffRecipient.fromJson).toList()
          : const [],
      suggestedTargetSession: map['suggested_target_session'] == null
          ? null
          : HandoffSession.fromJson(map['suggested_target_session']),
      content: jsonString(map['content']) ?? '',
      noteId: jsonString(map['note_id']),
      notePublishedVersion: jsonInt(map['note_published_version']),
    );
  }
}

class HandoffPage {
  const HandoffPage({required this.count, required this.results});

  final int count;
  final List<Handoff> results;

  factory HandoffPage.fromJson(Object? value) {
    final map = jsonMap(value);
    return HandoffPage(
      count: jsonInt(map['count']) ?? 0,
      results: (map['results'] is List)
          ? (map['results'] as List).map(Handoff.fromJson).toList()
          : const [],
    );
  }
}

enum CompletionAction {
  complete('complete', '完成授课'),
  correct('correct', '更正完成'),
  revert('revert', '撤销完成');

  const CompletionAction(this.apiValue, this.label);

  final String apiValue;
  final String label;
}

class CompletionRequest {
  const CompletionRequest({
    required this.action,
    required this.baseVersion,
    this.actualHours = '',
    this.allocations = const {},
    this.reason = '',
  });

  final CompletionAction action;
  final int baseVersion;
  final String actualHours;
  final Map<String, String> allocations;
  final String reason;

  bool get requiresReason =>
      action == CompletionAction.correct || action == CompletionAction.revert;

  String? validate() {
    if (action != CompletionAction.revert) {
      if (!_isDecimal(actualHours)) {
        return '实际学时请填写最多一位小数的非负数字。';
      }
      for (final entry in allocations.entries) {
        if (!_isDecimal(entry.value)) {
          return '专题分配学时请填写最多一位小数的非负数字。';
        }
      }
    }
    if (requiresReason && reason.trim().isEmpty) {
      return '更正或撤销完成必须填写原因。';
    }
    return null;
  }

  Map<String, dynamic> toJson() => {
    'action': action.apiValue,
    'base_version': baseVersion,
    'actual_hours': action == CompletionAction.revert
        ? null
        : actualHours.trim(),
    'allocations': action == CompletionAction.revert
        ? const <Map<String, String>>[]
        : [
            for (final entry in allocations.entries)
              {'plan_item_id': entry.key, 'actual_hours': entry.value.trim()},
          ],
    'reason': reason.trim(),
  };
}

bool _isDecimal(String value) =>
    RegExp(r'^\d{1,5}(\.\d)?$').hasMatch(value.trim());
