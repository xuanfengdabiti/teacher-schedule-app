import '../../core/json.dart';

class SessionTeacher {
  const SessionTeacher({required this.id, required this.name});

  final String id;
  final String name;

  factory SessionTeacher.fromJson(Map<String, dynamic> json) {
    return SessionTeacher(
      id: jsonString(json['id']) ?? '',
      name: jsonString(json['name']) ?? '',
    );
  }
}

class TeachingClass {
  const TeachingClass({
    required this.id,
    required this.classCode,
    required this.name,
  });

  final String id;
  final String classCode;
  final String name;

  factory TeachingClass.fromJson(Map<String, dynamic> json) {
    return TeachingClass(
      id: jsonString(json['id']) ?? '',
      classCode: jsonString(json['class_code']) ?? '',
      name: jsonString(json['name']) ?? '',
    );
  }
}

class Classroom {
  const Classroom({
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

  String get displayName {
    final parts = <String>[
      if (campus.isNotEmpty) campus,
      if (building.isNotEmpty) building,
      if (roomNo.isNotEmpty) roomNo,
    ];
    if (parts.isNotEmpty) {
      return parts.join(' · ');
    }
    return classroomCode;
  }

  factory Classroom.fromJson(Map<String, dynamic> json) {
    return Classroom(
      id: jsonString(json['id']) ?? '',
      classroomCode: jsonString(json['classroom_code']) ?? '',
      campus: jsonString(json['campus']) ?? '',
      building: jsonString(json['building']) ?? '',
      roomNo: jsonString(json['room_no']) ?? '',
    );
  }
}

class Session {
  const Session({
    required this.id,
    required this.offeringId,
    required this.offeringCode,
    required this.courseCode,
    required this.courseName,
    required this.teachingClass,
    required this.date,
    required this.weekday,
    required this.teachingWeek,
    required this.startPeriod,
    required this.endPeriod,
    required this.startAt,
    required this.endAt,
    required this.classroom,
    required this.teachers,
    required this.status,
    required this.statusReason,
    required this.isMine,
    required this.isMoved,
    required this.version,
    this.plannedHours,
    this.actualHours,
    this.completedAt,
    this.enrolledCount,
    this.offeringTargetHours,
    this.leadTeacherName,
    this.termId,
    this.termCode,
    this.previousNotePreview = '',
  });

  final String id;
  final String offeringId;
  final String offeringCode;
  final String courseCode;
  final String courseName;
  final TeachingClass teachingClass;
  final DateTime date;
  final int weekday;
  final int? teachingWeek;
  final int startPeriod;
  final int endPeriod;
  final DateTime startAt;
  final DateTime endAt;
  final Classroom classroom;
  final List<SessionTeacher> teachers;
  final String status;
  final String statusReason;
  final bool isMine;
  final bool isMoved;
  final int version;
  final String? plannedHours;
  final String? actualHours;
  final DateTime? completedAt;
  final int? enrolledCount;
  final String? offeringTargetHours;
  final String? leadTeacherName;
  final String? termId;
  final String? termCode;

  /// Concise summary supplied with schedule reads. Empty when unavailable.
  final String previousNotePreview;

  bool get hasPreviousNote => previousNotePreview.trim().isNotEmpty;

  String get periodsLabel =>
      startPeriod == endPeriod ? '$startPeriod节' : '$startPeriod-$endPeriod节';

  String get timeRange => '${timeZh(startAt)}-${timeZh(endAt)}';

  String get teacherNames => teachers.map((teacher) => teacher.name).join('、');

  String get statusLabel => switch (status) {
    'scheduled' || 'normal' => '正常',
    'cancelled' => '已停课',
    'completed' => '已完成',
    'moved' => '已调课',
    _ => '其他状态',
  };

  factory Session.fromJson(Map<String, dynamic> json) {
    final classJson = json['teaching_class'];
    final classroomJson = json['classroom'];
    final offeringJson = json['offering'];
    final termJson = json['term'];
    final teachersJson = json['teachers'];
    final startAt =
        jsonDateTime(json['start_at']) ??
        jsonDateTime(json['start']) ??
        DateTime.now();
    final endAt =
        jsonDateTime(json['end_at']) ?? jsonDateTime(json['end']) ?? startAt;
    final date =
        jsonDate(json['date']) ??
        DateTime(startAt.year, startAt.month, startAt.day);
    return Session(
      id: jsonString(json['id']) ?? '',
      offeringId: jsonString(json['offering_id']) ?? '',
      offeringCode: jsonString(json['offering_code']) ?? '',
      courseCode: jsonString(json['course_code']) ?? '',
      courseName: jsonString(json['course_name']) ?? '未命名课程',
      teachingClass: classJson is Map
          ? TeachingClass.fromJson(jsonMap(classJson))
          : const TeachingClass(id: '', classCode: '', name: ''),
      date: date,
      weekday: jsonInt(json['weekday']) ?? date.weekday,
      teachingWeek: jsonInt(json['teaching_week']),
      startPeriod: jsonInt(json['start_period']) ?? 0,
      endPeriod: jsonInt(json['end_period']) ?? 0,
      startAt: startAt,
      endAt: endAt,
      classroom: classroomJson is Map
          ? Classroom.fromJson(jsonMap(classroomJson))
          : const Classroom(
              id: '',
              classroomCode: '',
              campus: '',
              building: '',
              roomNo: '',
            ),
      teachers: teachersJson is List
          ? teachersJson
                .whereType<Map>()
                .map((item) => SessionTeacher.fromJson(jsonMap(item)))
                .toList(growable: false)
          : const [],
      status: jsonString(json['status']) ?? 'scheduled',
      statusReason: jsonString(json['status_reason']) ?? '',
      isMine: json['is_mine'] == true,
      isMoved: json['is_moved'] == true,
      version: jsonInt(json['version']) ?? 0,
      plannedHours: jsonString(json['planned_hours']),
      actualHours: jsonString(json['actual_hours']),
      completedAt: jsonDateTime(json['completed_at']),
      enrolledCount: jsonInt(json['enrolled_count']),
      offeringTargetHours: offeringJson is Map
          ? jsonString(jsonMap(offeringJson)['target_hours'])
          : null,
      leadTeacherName: offeringJson is Map
          ? jsonString(jsonMap(jsonMap(offeringJson)['lead_teacher'])['name'])
          : null,
      termId: termJson is Map ? jsonString(jsonMap(termJson)['id']) : null,
      termCode: termJson is Map ? jsonString(jsonMap(termJson)['code']) : null,
      previousNotePreview:
          jsonString(json['previous_note_preview'])?.trim() ?? '',
    );
  }
}

class Term {
  const Term({
    required this.id,
    required this.code,
    required this.name,
    required this.startDate,
    required this.endDate,
    required this.firstWeekMonday,
    required this.teachingWeekCount,
    required this.timezone,
    required this.scheduleVersion,
    required this.isCurrent,
  });

  final String id;
  final String code;
  final String name;
  final DateTime startDate;
  final DateTime endDate;
  final DateTime firstWeekMonday;
  final int teachingWeekCount;
  final String timezone;
  final int scheduleVersion;
  final bool isCurrent;

  factory Term.fromJson(Map<String, dynamic> json) {
    return Term(
      id: jsonString(json['id']) ?? '',
      code: jsonString(json['code']) ?? '',
      name: jsonString(json['name']) ?? '',
      startDate: jsonDate(json['start_date']) ?? DateTime(1970),
      endDate: jsonDate(json['end_date']) ?? DateTime(1970),
      firstWeekMonday: jsonDate(json['first_week_monday']) ?? DateTime(1970),
      teachingWeekCount: jsonInt(json['teaching_week_count']) ?? 0,
      timezone: jsonString(json['timezone']) ?? '',
      scheduleVersion: jsonInt(json['schedule_version']) ?? 0,
      isCurrent: json['is_current'] == true,
    );
  }
}

class Period {
  const Period({
    required this.number,
    required this.startTime,
    required this.endTime,
    required this.blockCode,
  });

  final int number;
  final String startTime;
  final String endTime;
  final String blockCode;

  factory Period.fromJson(Map<String, dynamic> json) {
    return Period(
      number: jsonInt(json['period_number']) ?? 0,
      startTime: jsonString(json['start_time']) ?? '',
      endTime: jsonString(json['end_time']) ?? '',
      blockCode: jsonString(json['block_code']) ?? '',
    );
  }
}

class TeachingWeek {
  const TeachingWeek({
    required this.week,
    required this.monday,
    required this.sunday,
  });

  final int week;
  final DateTime monday;
  final DateTime sunday;

  factory TeachingWeek.fromJson(Map<String, dynamic> json) {
    return TeachingWeek(
      week: jsonInt(json['week']) ?? 0,
      monday: jsonDate(json['monday']) ?? DateTime(1970),
      sunday: jsonDate(json['sunday']) ?? DateTime(1970),
    );
  }
}

class TermCalendar {
  const TermCalendar({
    required this.term,
    required this.periods,
    required this.weeks,
  });

  final Term term;
  final List<Period> periods;
  final List<TeachingWeek> weeks;

  int? teachingWeekFor(DateTime date) {
    for (final week in weeks) {
      final end = week.sunday.add(const Duration(days: 1));
      if (!date.isBefore(week.monday) && date.isBefore(end)) {
        return week.week;
      }
    }
    return null;
  }

  factory TermCalendar.fromJson(Map<String, dynamic> json) {
    final weeksJson = json['weeks'];
    final periodsJson = json['periods'];
    return TermCalendar(
      term: Term.fromJson(json),
      periods: periodsJson is List
          ? periodsJson
                .whereType<Map>()
                .map((item) => Period.fromJson(jsonMap(item)))
                .toList(growable: false)
          : const [],
      weeks: weeksJson is List
          ? weeksJson
                .whereType<Map>()
                .map((item) => TeachingWeek.fromJson(jsonMap(item)))
                .toList(growable: false)
          : const [],
    );
  }
}

class WeekSchedule {
  const WeekSchedule({
    required this.from,
    required this.sessions,
    this.termCode,
    this.termId,
  });

  final DateTime from;
  final List<Session> sessions;
  final String? termCode;
  final String? termId;

  DateTime get to => from.add(const Duration(days: 7));

  List<Session> sessionsOn(DateTime date) => sessions
      .where((session) => sameDay(session.date, date))
      .toList(growable: false);
}

class OfferingTeacher {
  const OfferingTeacher({
    required this.id,
    required this.name,
    required this.role,
  });

  final String id;
  final String name;
  final String role;

  factory OfferingTeacher.fromJson(Map<String, dynamic> json) {
    return OfferingTeacher(
      id: jsonString(json['id']) ?? '',
      name: jsonString(json['name']) ?? '',
      role: jsonString(json['role']) ?? '',
    );
  }
}

class Offering {
  const Offering({
    required this.id,
    required this.offeringCode,
    required this.courseCode,
    required this.courseName,
    required this.teachingClass,
    required this.targetHours,
    required this.leadTeacherName,
    required this.teachers,
    required this.isAssigned,
  });

  final String id;
  final String offeringCode;
  final String courseCode;
  final String courseName;
  final TeachingClass teachingClass;
  final String targetHours;
  final String leadTeacherName;
  final List<OfferingTeacher> teachers;
  final bool isAssigned;

  factory Offering.fromJson(Map<String, dynamic> json) {
    final classJson = json['teaching_class'];
    final leadJson = json['lead_teacher'];
    final teachersJson = json['teachers'];
    return Offering(
      id: jsonString(json['id']) ?? '',
      offeringCode: jsonString(json['offering_code']) ?? '',
      courseCode: jsonString(json['course_code']) ?? '',
      courseName: jsonString(json['course_name']) ?? '未命名课程',
      teachingClass: classJson is Map
          ? TeachingClass.fromJson(jsonMap(classJson))
          : const TeachingClass(id: '', classCode: '', name: ''),
      targetHours: jsonString(json['target_hours']) ?? '',
      leadTeacherName: leadJson is Map
          ? jsonString(jsonMap(leadJson)['name']) ?? ''
          : '',
      teachers: teachersJson is List
          ? teachersJson
                .whereType<Map>()
                .map((item) => OfferingTeacher.fromJson(jsonMap(item)))
                .toList(growable: false)
          : const [],
      isAssigned: json['is_assigned'] == true,
    );
  }
}
