import '../../core/json.dart';

Map<String, dynamic> mapOf(Object? value) => jsonMap(value);

List<Map<String, dynamic>> mapListOf(Object? value) {
  if (value is! List) {
    return const [];
  }
  return value.map(jsonMap).toList(growable: false);
}

List<String> stringListOf(Object? value) {
  if (value is! List) {
    return const [];
  }
  return value
      .map((item) => item?.toString() ?? '')
      .where((item) => item.isNotEmpty)
      .toList(growable: false);
}

bool boolOf(Object? value) =>
    value == true || value == 1 || value?.toString() == 'true';

String shortId(String value) =>
    value.length <= 8 ? value : value.substring(0, 8);

String periodLabel(int start, int end) =>
    start == end ? '第 $start 节' : '第 $start–$end 节';

String dateLabel(DateTime? value) {
  if (value == null) {
    return '日期未知';
  }
  final month = value.month.toString().padLeft(2, '0');
  final day = value.day.toString().padLeft(2, '0');
  return '${value.year}-$month-$day';
}

String dateTimeLabel(DateTime? value) {
  if (value == null) {
    return '时间未知';
  }
  final month = value.month.toString().padLeft(2, '0');
  final day = value.day.toString().padLeft(2, '0');
  final hour = value.hour.toString().padLeft(2, '0');
  final minute = value.minute.toString().padLeft(2, '0');
  return '$month-$day $hour:$minute';
}

String fullDateTimeLabel(DateTime? value) {
  if (value == null) {
    return '时间未知';
  }
  final hour = value.hour.toString().padLeft(2, '0');
  final minute = value.minute.toString().padLeft(2, '0');
  return '${dateLabel(value)} $hour:$minute';
}

String rangeLabel(DateTime? start, DateTime? end) {
  if (start == null && end == null) {
    return '时间未知';
  }
  return '${fullDateTimeLabel(start)} — ${fullDateTimeLabel(end)}';
}

String isoDateTimeWithOffset(DateTime value) {
  final local = value.isUtc ? value.toLocal() : value;
  final offset = local.timeZoneOffset;
  final sign = offset.inMinutes < 0 ? '-' : '+';
  final absolute = offset.abs();
  final offsetHour = absolute.inHours.toString().padLeft(2, '0');
  final offsetMinute = (absolute.inMinutes % 60).toString().padLeft(2, '0');
  final month = local.month.toString().padLeft(2, '0');
  final day = local.day.toString().padLeft(2, '0');
  final hour = local.hour.toString().padLeft(2, '0');
  final minute = local.minute.toString().padLeft(2, '0');
  final second = local.second.toString().padLeft(2, '0');
  return '${local.year}-$month-${day}T$hour:$minute:$second$sign$offsetHour:$offsetMinute';
}

String rescheduleStatusLabel(String status) => switch (status) {
  'draft' => '草稿',
  'submitted' => '待审批',
  'approved' => '已批准',
  'rejected' => '已驳回',
  'withdrawn' => '已撤回',
  _ => '未知状态',
};

String inquiryStatusLabel(String status) => switch (status) {
  'open' => '待回复',
  'replied' => '已回复',
  'closed' => '已结案',
  _ => '未知状态',
};

String replyResultLabel(String result) => switch (result) {
  'not_seen' => '未见到',
  'in_custody' => '已保管',
  'other' => '其他说明',
  _ => '其他说明',
};

String usageKindLabel(String kind) => switch (kind) {
  'session' => '课次',
  'cancelled_session' => '停课课次',
  'moved_out' => '已调出',
  'room_block' => '教室占用',
  _ => '其他安排',
};

String contactStatusLabel(String status) => switch (status) {
  'visible' => '可查看',
  'not_provided' => '未填写',
  'hidden' => '暂不可查看',
  _ => '未知状态',
};

String coverageLabel(String value) => switch (value) {
  'calendar' => '校历与节次',
  'teacher_assignments' => '任课安排',
  'teacher_schedule' => '教师课表',
  'student_enrollments' => '学生选课名单',
  'student_schedule' => '学生课表',
  'classroom_schedule' => '教室排课',
  'room_blocks' => '教室其他占用',
  'person_blocks' => '教师时间安排',
  _ => '其他资料',
};

String conflictCategoryLabel(String category) => switch (category) {
  'teacher' => '教师时间冲突',
  'student' => '学生冲突',
  'classroom' => '教室冲突',
  _ => '其他冲突',
};

class RescheduleConflict {
  const RescheduleConflict({
    required this.category,
    required this.sessionId,
    required this.courseName,
    required this.reason,
    required this.studentCount,
  });

  final String category;
  final String? sessionId;
  final String? courseName;
  final String? reason;
  final int? studentCount;

  factory RescheduleConflict.fromJson(Map<String, dynamic> json) {
    return RescheduleConflict(
      category: jsonString(json['category']) ?? '',
      sessionId: jsonString(json['session_id']),
      courseName: jsonString(json['course_name']),
      reason: jsonString(json['reason']),
      studentCount: jsonInt(json['student_count']),
    );
  }

  String get label {
    final name = courseName ?? '其他课程';
    final count = studentCount == null ? '' : '（$studentCount 人）';
    final detail = reason?.trim();
    if (detail != null && detail.isNotEmpty) {
      return '${conflictCategoryLabel(category)}$count：$detail';
    }
    return '${conflictCategoryLabel(category)}$count${name.isEmpty ? '' : '：$name'}';
  }
}

class RescheduleOption {
  const RescheduleOption({
    required this.startAt,
    required this.endAt,
    required this.startPeriod,
    required this.endPeriod,
    required this.classroomId,
    required this.classroomCode,
    required this.eligibility,
    required this.studentConflictCount,
    required this.conflicts,
    required this.missingData,
    required this.reasons,
  });

  final DateTime? startAt;
  final DateTime? endAt;
  final int startPeriod;
  final int endPeriod;
  final String classroomId;
  final String classroomCode;
  final String eligibility;
  final int studentConflictCount;
  final List<RescheduleConflict> conflicts;
  final List<String> missingData;
  final List<String> reasons;

  bool get isConflict => eligibility == 'conflict';
  bool get isUnverified => eligibility == 'unverified';
  bool get canChoose => !isConflict && startAt != null;
  bool get isAvailable => eligibility == 'available';

  String get label => switch (eligibility) {
    'available' => '可用',
    'unverified' => '需确认',
    'conflict' => '有冲突',
    _ => '需确认',
  };

  factory RescheduleOption.fromJson(Map<String, dynamic> json) {
    return RescheduleOption(
      startAt: jsonDateTime(json['start_at']),
      endAt: jsonDateTime(json['end_at']),
      startPeriod: jsonInt(json['start_period']) ?? 0,
      endPeriod: jsonInt(json['end_period']) ?? 0,
      classroomId: jsonString(json['classroom_id']) ?? '',
      classroomCode: jsonString(json['classroom_code']) ?? '教室未知',
      eligibility: jsonString(json['eligibility']) ?? 'unverified',
      studentConflictCount: jsonInt(json['student_conflict_count']) ?? 0,
      conflicts: mapListOf(json['conflicts'])
          .map(RescheduleConflict.fromJson)
          .toList(growable: false),
      missingData: stringListOf(json['missing_data']),
      reasons: stringListOf(json['reasons']),
    );
  }
}

class RescheduleOptionsData {
  const RescheduleOptionsData({
    required this.sessionId,
    required this.scheduleVersion,
    required this.checkedAt,
    required this.dataCompleteness,
    required this.options,
  });

  final String sessionId;
  final int scheduleVersion;
  final DateTime? checkedAt;
  final String dataCompleteness;
  final List<RescheduleOption> options;

  bool get complete => dataCompleteness == 'complete';

  factory RescheduleOptionsData.fromJson(Map<String, dynamic> json) {
    return RescheduleOptionsData(
      sessionId: jsonString(json['session_id']) ?? '',
      scheduleVersion: jsonInt(json['schedule_version']) ?? 0,
      checkedAt: jsonDateTime(json['checked_at']),
      dataCompleteness: jsonString(json['data_completeness']) ?? 'partial',
      options: mapListOf(json['options'])
          .map(RescheduleOption.fromJson)
          .toList(growable: false),
    );
  }
}

class RescheduleSessionSummary {
  const RescheduleSessionSummary({
    required this.id,
    required this.courseName,
    required this.date,
    required this.startPeriod,
    required this.endPeriod,
    required this.classroomCode,
  });

  final String id;
  final String courseName;
  final DateTime? date;
  final int startPeriod;
  final int endPeriod;
  final String classroomCode;

  factory RescheduleSessionSummary.fromJson(Map<String, dynamic> json) {
    return RescheduleSessionSummary(
      id: jsonString(json['id']) ?? '',
      courseName: jsonString(json['course_name']) ?? '课程未知',
      date: jsonDate(json['date']),
      startPeriod: jsonInt(json['start_period']) ?? 0,
      endPeriod: jsonInt(json['end_period']) ?? 0,
      classroomCode: jsonString(json['classroom_code']) ?? '教室未知',
    );
  }

  String get arrangement =>
      '${dateLabel(date)} ${periodLabel(startPeriod, endPeriod)} · $classroomCode';
}

class RescheduleTargetSummary {
  const RescheduleTargetSummary({
    required this.date,
    required this.startPeriod,
    required this.endPeriod,
    required this.classroomId,
    required this.classroomCode,
    required this.startAt,
    required this.endAt,
  });

  final DateTime? date;
  final int startPeriod;
  final int endPeriod;
  final String classroomId;
  final String classroomCode;
  final DateTime? startAt;
  final DateTime? endAt;

  factory RescheduleTargetSummary.fromJson(Map<String, dynamic> json) {
    return RescheduleTargetSummary(
      date: jsonDate(json['date']),
      startPeriod: jsonInt(json['start_period']) ?? 0,
      endPeriod: jsonInt(json['end_period']) ?? 0,
      classroomId: jsonString(json['classroom_id']) ?? '',
      classroomCode: jsonString(json['classroom_code']) ?? '教室未知',
      startAt: jsonDateTime(json['start_at']),
      endAt: jsonDateTime(json['end_at']),
    );
  }

  String get arrangement =>
      '${dateLabel(date)} ${periodLabel(startPeriod, endPeriod)} · $classroomCode';
}

class RescheduleRequestItem {
  const RescheduleRequestItem({
    required this.id,
    required this.session,
    required this.target,
    required this.reason,
    required this.status,
    required this.needsVerification,
    required this.decisionNote,
    required this.canWithdraw,
    required this.applicantName,
  });

  final String id;
  final RescheduleSessionSummary session;
  final RescheduleTargetSummary target;
  final String reason;
  final String status;
  final bool needsVerification;
  final String decisionNote;
  final bool canWithdraw;
  final String applicantName;

  bool get isDraft => status == 'draft';
  bool get isSubmitted => status == 'submitted';
  String get statusLabel => rescheduleStatusLabel(status);

  factory RescheduleRequestItem.fromJson(Map<String, dynamic> json) {
    final applicant = mapOf(json['applicant']);
    return RescheduleRequestItem(
      id: jsonString(json['id']) ?? '',
      session: RescheduleSessionSummary.fromJson(mapOf(json['session'])),
      target: RescheduleTargetSummary.fromJson(mapOf(json['target'])),
      reason: jsonString(json['reason']) ?? '',
      status: jsonString(json['status']) ?? 'draft',
      needsVerification: boolOf(json['needs_verification']),
      decisionNote: jsonString(json['decision_note']) ?? '',
      canWithdraw: boolOf(json['can_withdraw']),
      applicantName: jsonString(applicant['name']) ?? '',
    );
  }
}

class ClassroomItem {
  const ClassroomItem({
    required this.id,
    required this.classroomCode,
    required this.campus,
    required this.building,
    required this.roomNo,
    required this.capacity,
    required this.isActive,
  });

  final String id;
  final String classroomCode;
  final String campus;
  final String building;
  final String roomNo;
  final int capacity;
  final bool isActive;

  factory ClassroomItem.fromJson(Map<String, dynamic> json) {
    return ClassroomItem(
      id: jsonString(json['id']) ?? '',
      classroomCode: jsonString(json['classroom_code']) ?? '教室未知',
      campus: jsonString(json['campus']) ?? '',
      building: jsonString(json['building']) ?? '',
      roomNo: jsonString(json['room_no']) ?? '',
      capacity: jsonInt(json['capacity']) ?? 0,
      isActive: boolOf(json['is_active']),
    );
  }

  String get location =>
      [campus, building, roomNo].where((part) => part.isNotEmpty).join(' · ');
}

class ClassroomUsageTeacher {
  const ClassroomUsageTeacher({
    required this.id,
    required this.name,
    required this.hasVisibleContact,
  });

  final String id;
  final String name;
  final bool hasVisibleContact;

  factory ClassroomUsageTeacher.fromJson(Map<String, dynamic> json) {
    return ClassroomUsageTeacher(
      id: jsonString(json['id']) ?? '',
      name: jsonString(json['name']) ?? '教师',
      hasVisibleContact: boolOf(json['has_visible_contact']),
    );
  }
}

class ClassroomUsageEntry {
  const ClassroomUsageEntry({
    required this.kind,
    required this.sessionId,
    required this.roomBlockId,
    required this.startAt,
    required this.endAt,
    required this.startPeriod,
    required this.endPeriod,
    required this.courseName,
    required this.teachingClass,
    required this.status,
    required this.statusReason,
    required this.isMoved,
    required this.teachers,
    required this.currentStartAt,
    required this.currentEndAt,
    required this.currentStartPeriod,
    required this.currentEndPeriod,
    required this.currentClassroomId,
    required this.currentClassroomCode,
    required this.reason,
    required this.blockCode,
  });

  final String kind;
  final String? sessionId;
  final String? roomBlockId;
  final DateTime? startAt;
  final DateTime? endAt;
  final int? startPeriod;
  final int? endPeriod;
  final String? courseName;
  final String? teachingClass;
  final String? status;
  final String? statusReason;
  final bool isMoved;
  final List<ClassroomUsageTeacher> teachers;
  final DateTime? currentStartAt;
  final DateTime? currentEndAt;
  final int? currentStartPeriod;
  final int? currentEndPeriod;
  final String? currentClassroomId;
  final String? currentClassroomCode;
  final String? reason;
  final String? blockCode;

  String get kindLabel => usageKindLabel(kind);
  bool get isSession => kind == 'session' || kind == 'cancelled_session';

  String get periodLabelText {
    if (startPeriod == null || endPeriod == null) {
      return '';
    }
    return periodLabel(startPeriod!, endPeriod!);
  }

  factory ClassroomUsageEntry.fromJson(Map<String, dynamic> json) {
    return ClassroomUsageEntry(
      kind: jsonString(json['kind']) ?? '',
      sessionId: jsonString(json['session_id']),
      roomBlockId: jsonString(json['room_block_id']),
      startAt: jsonDateTime(json['start_at']),
      endAt: jsonDateTime(json['end_at']),
      startPeriod: jsonInt(json['start_period']),
      endPeriod: jsonInt(json['end_period']),
      courseName: jsonString(json['course_name']),
      teachingClass: jsonString(json['teaching_class']),
      status: jsonString(json['status']),
      statusReason: jsonString(json['status_reason']),
      isMoved: boolOf(json['is_moved']),
      teachers: mapListOf(json['teachers'])
          .map(ClassroomUsageTeacher.fromJson)
          .toList(growable: false),
      currentStartAt: jsonDateTime(json['current_start_at']),
      currentEndAt: jsonDateTime(json['current_end_at']),
      currentStartPeriod: jsonInt(json['current_start_period']),
      currentEndPeriod: jsonInt(json['current_end_period']),
      currentClassroomId: jsonString(json['current_classroom_id']),
      currentClassroomCode: jsonString(json['current_classroom_code']),
      reason: jsonString(json['reason']),
      blockCode: jsonString(json['block_code']),
    );
  }
}

class ClassroomUsageData {
  const ClassroomUsageData({
    required this.classroom,
    required this.date,
    required this.notice,
    required this.dataCompleteness,
    required this.entries,
  });

  final ClassroomItem classroom;
  final DateTime? date;
  final String notice;
  final String dataCompleteness;
  final List<ClassroomUsageEntry> entries;

  bool get complete => dataCompleteness == 'complete';

  factory ClassroomUsageData.fromJson(Map<String, dynamic> json) {
    return ClassroomUsageData(
      classroom: ClassroomItem.fromJson(mapOf(json['classroom'])),
      date: jsonDate(json['date']),
      notice: jsonString(json['notice']) ?? '',
      dataCompleteness: jsonString(json['data_completeness']) ?? 'incomplete',
      entries: mapListOf(json['entries'])
          .map(ClassroomUsageEntry.fromJson)
          .toList(growable: false),
    );
  }
}

class TeacherContact {
  const TeacherContact({
    required this.id,
    required this.name,
    required this.department,
    required this.phone,
    required this.phoneStatus,
    required this.wechat,
    required this.wechatStatus,
  });

  final String id;
  final String name;
  final String department;
  final String? phone;
  final String phoneStatus;
  final String? wechat;
  final String wechatStatus;

  factory TeacherContact.fromJson(Map<String, dynamic> json) {
    final teacher = mapOf(json['teacher']);
    return TeacherContact(
      id: jsonString(teacher['id']) ?? '',
      name: jsonString(teacher['name']) ?? '教师',
      department: jsonString(teacher['department']) ?? '',
      phone: jsonString(json['phone']),
      phoneStatus: jsonString(json['phone_status']) ?? 'not_provided',
      wechat: jsonString(json['wechat']),
      wechatStatus: jsonString(json['wechat_status']) ?? 'not_provided',
    );
  }
}

class InquiryTeacher {
  const InquiryTeacher({
    required this.id,
    required this.name,
    required this.department,
  });

  final String id;
  final String name;
  final String department;

  factory InquiryTeacher.fromJson(Map<String, dynamic> json) {
    return InquiryTeacher(
      id: jsonString(json['id']) ?? '',
      name: jsonString(json['name']) ?? '教师',
      department: jsonString(json['department']) ?? '',
    );
  }
}

class InquiryClassroom {
  const InquiryClassroom({
    required this.id,
    required this.classroomCode,
    required this.campus,
    required this.building,
    required this.roomNo,
  });

  final String id;
  final String classroomCode;
  final String campus;
  final String building;
  final String roomNo;

  factory InquiryClassroom.fromJson(Map<String, dynamic> json) {
    return InquiryClassroom(
      id: jsonString(json['id']) ?? '',
      classroomCode: jsonString(json['classroom_code']) ?? '教室未知',
      campus: jsonString(json['campus']) ?? '',
      building: jsonString(json['building']) ?? '',
      roomNo: jsonString(json['room_no']) ?? '',
    );
  }

  String get location =>
      [campus, building, roomNo].where((part) => part.isNotEmpty).join(' · ');
}

class InquiryCandidateSession {
  const InquiryCandidateSession({
    required this.id,
    required this.courseName,
    required this.startAt,
    required this.endAt,
    required this.startPeriod,
    required this.endPeriod,
  });

  final String id;
  final String courseName;
  final DateTime? startAt;
  final DateTime? endAt;
  final int startPeriod;
  final int endPeriod;

  factory InquiryCandidateSession.fromJson(Map<String, dynamic> json) {
    return InquiryCandidateSession(
      id: jsonString(json['id']) ?? '',
      courseName: jsonString(json['course_name']) ?? '课程未知',
      startAt: jsonDateTime(json['start_at']),
      endAt: jsonDateTime(json['end_at']),
      startPeriod: jsonInt(json['start_period']) ?? 0,
      endPeriod: jsonInt(json['end_period']) ?? 0,
    );
  }
}

class InquiryCandidate {
  const InquiryCandidate({
    required this.teacher,
    required this.relatedSessionIds,
    required this.courseNames,
    required this.sessions,
  });

  final InquiryTeacher teacher;
  final List<String> relatedSessionIds;
  final List<String> courseNames;
  final List<InquiryCandidateSession> sessions;

  factory InquiryCandidate.fromJson(Map<String, dynamic> json) {
    return InquiryCandidate(
      teacher: InquiryTeacher.fromJson(mapOf(json['teacher'])),
      relatedSessionIds: stringListOf(json['related_session_ids']),
      courseNames: stringListOf(json['course_names']),
      sessions: mapListOf(json['sessions'])
          .map(InquiryCandidateSession.fromJson)
          .toList(growable: false),
    );
  }
}

class InquiryCandidatesData {
  const InquiryCandidatesData({
    required this.classroom,
    required this.fromAt,
    required this.toAt,
    required this.candidates,
  });

  final InquiryClassroom classroom;
  final DateTime? fromAt;
  final DateTime? toAt;
  final List<InquiryCandidate> candidates;

  factory InquiryCandidatesData.fromJson(Map<String, dynamic> json) {
    return InquiryCandidatesData(
      classroom: InquiryClassroom.fromJson(mapOf(json['classroom'])),
      fromAt: jsonDateTime(json['from_at']),
      toAt: jsonDateTime(json['to_at']),
      candidates: mapListOf(json['candidates'])
          .map(InquiryCandidate.fromJson)
          .toList(growable: false),
    );
  }
}

class InquiryRecipient {
  const InquiryRecipient({
    required this.teacher,
    required this.relatedSessionIds,
    required this.firstViewedAt,
    required this.hasReplied,
  });

  final InquiryTeacher teacher;
  final List<String> relatedSessionIds;
  final DateTime? firstViewedAt;
  final bool hasReplied;

  factory InquiryRecipient.fromJson(Map<String, dynamic> json) {
    return InquiryRecipient(
      teacher: InquiryTeacher.fromJson(mapOf(json['teacher'])),
      relatedSessionIds: stringListOf(json['related_session_ids']),
      firstViewedAt: jsonDateTime(json['first_viewed_at']),
      hasReplied: boolOf(json['has_replied']),
    );
  }
}

class InquiryReply {
  const InquiryReply({
    required this.id,
    required this.author,
    required this.result,
    required this.body,
    required this.createdAt,
  });

  final String id;
  final InquiryTeacher author;
  final String result;
  final String body;
  final DateTime? createdAt;

  factory InquiryReply.fromJson(Map<String, dynamic> json) {
    return InquiryReply(
      id: jsonString(json['id']) ?? '',
      author: InquiryTeacher.fromJson(mapOf(json['author'])),
      result: jsonString(json['result']) ?? 'other',
      body: jsonString(json['body']) ?? '',
      createdAt: jsonDateTime(json['created_at']),
    );
  }

  String get resultLabel => replyResultLabel(result);
}

class InquiryItem {
  const InquiryItem({
    required this.id,
    required this.classroom,
    required this.fromAt,
    required this.toAt,
    required this.itemDescription,
    required this.status,
    required this.initiator,
    required this.recipients,
    required this.replies,
    required this.closedAt,
    required this.closeNote,
    required this.notice,
    required this.createdAt,
    required this.isInitiator,
  });

  final String id;
  final InquiryClassroom classroom;
  final DateTime? fromAt;
  final DateTime? toAt;
  final String itemDescription;
  final String status;
  final InquiryTeacher initiator;
  final List<InquiryRecipient> recipients;
  final List<InquiryReply> replies;
  final DateTime? closedAt;
  final String closeNote;
  final String notice;
  final DateTime? createdAt;
  final bool isInitiator;

  bool get isClosed => status == 'closed';
  String get statusLabel => inquiryStatusLabel(status);

  factory InquiryItem.fromJson(Map<String, dynamic> json) {
    return InquiryItem(
      id: jsonString(json['id']) ?? '',
      classroom: InquiryClassroom.fromJson(mapOf(json['classroom'])),
      fromAt: jsonDateTime(json['from_at']),
      toAt: jsonDateTime(json['to_at']),
      itemDescription: jsonString(json['item_description']) ?? '',
      status: jsonString(json['status']) ?? 'open',
      initiator: InquiryTeacher.fromJson(mapOf(json['initiator'])),
      recipients: mapListOf(json['recipients'])
          .map(InquiryRecipient.fromJson)
          .toList(growable: false),
      replies: mapListOf(json['replies'])
          .map(InquiryReply.fromJson)
          .toList(growable: false),
      closedAt: jsonDateTime(json['closed_at']),
      closeNote: jsonString(json['close_note']) ?? '',
      notice: jsonString(json['notice']) ?? '',
      createdAt: jsonDateTime(json['created_at']),
      isInitiator: boolOf(json['is_initiator']),
    );
  }
}

class NotificationItem {
  const NotificationItem({
    required this.id,
    required this.type,
    required this.objectType,
    required this.objectId,
    required this.title,
    required this.body,
    required this.createdAt,
    required this.readAt,
    required this.isRead,
  });

  final String id;
  final String type;
  final String objectType;
  final String? objectId;
  final String title;
  final String body;
  final DateTime? createdAt;
  final DateTime? readAt;
  final bool isRead;

  String get displayTitle => switch (type) {
    'inquiry_received' => '收到失物询问',
    'inquiry_replied' => '失物询问有回复',
    'handoff_received' => '收到进度交接',
    'handoff_acknowledged' => '进度交接已确认',
    'handoff_check' => '请核对进度交接',
    _ => title,
  };

  /// Old notifications may still use "协查/教学交接" in fixed system text.
  /// Only replace those fixed fragments; keep the quoted user content intact.
  String get displayBody {
    var text = body;
    if (type == 'inquiry_received') {
      text = text
          .replaceFirst('收到失物协查请求', '收到失物询问')
          .replaceFirst('失物协查。', '失物询问。')
          .replaceFirst(RegExp(r'的失物协查。'), '的失物询问。');
    } else if (type == 'inquiry_replied') {
      text = text
          .replaceFirst('协查收到回复', '失物询问有回复')
          .replaceFirst(RegExp(r'^协查'), '失物询问');
    } else if (type == 'handoff_received') {
      text = text
          .replaceFirst('收到教学交接', '收到进度交接')
          .replaceFirst('教学交接：', '进度交接：');
    } else if (type == 'handoff_acknowledged') {
      text = text
          .replaceFirst('教学交接已确认', '进度交接已确认')
          .replaceFirst('的教学交接。', '的进度交接。');
    } else if (type == 'handoff_check') {
      text = text.replaceFirst('交接《', '进度交接《');
    }
    return text;
  }

  factory NotificationItem.fromJson(Map<String, dynamic> json) {
    return NotificationItem(
      id: jsonString(json['id']) ?? '',
      type: jsonString(json['type']) ?? '',
      objectType: jsonString(json['object_type']) ?? '',
      objectId: jsonString(json['object_id']),
      title: jsonString(json['title']) ?? '通知',
      body: jsonString(json['body']) ?? '',
      createdAt: jsonDateTime(json['created_at']),
      readAt: jsonDateTime(json['read_at']),
      isRead: boolOf(json['is_read']),
    );
  }

  NotificationItem asRead(DateTime value) {
    return NotificationItem(
      id: id,
      type: type,
      objectType: objectType,
      objectId: objectId,
      title: title,
      body: body,
      createdAt: createdAt,
      readAt: value,
      isRead: true,
    );
  }
}

class NotificationPageData {
  const NotificationPageData({
    required this.results,
    required this.nextCursor,
    required this.unreadCount,
  });

  final List<NotificationItem> results;
  final String? nextCursor;
  final int unreadCount;

  factory NotificationPageData.fromJson(Map<String, dynamic> json) {
    return NotificationPageData(
      results: mapListOf(json['results'])
          .map(NotificationItem.fromJson)
          .toList(growable: false),
      nextCursor: jsonString(json['next_cursor']),
      unreadCount: jsonInt(json['unread_count']) ?? 0,
    );
  }
}
