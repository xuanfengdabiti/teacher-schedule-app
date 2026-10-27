import '../../core/json.dart';

class CurrentTerm {
  const CurrentTerm({
    required this.id,
    required this.code,
    required this.name,
    required this.startDate,
    required this.endDate,
  });

  final String id;
  final String code;
  final String name;
  final DateTime startDate;
  final DateTime endDate;

  factory CurrentTerm.fromJson(Map<String, dynamic> json) {
    return CurrentTerm(
      id: jsonString(json['id']) ?? '',
      code: jsonString(json['code']) ?? '',
      name: jsonString(json['name']) ?? '',
      startDate: jsonDate(json['start_date']) ?? DateTime(1970),
      endDate: jsonDate(json['end_date']) ?? DateTime(1970),
    );
  }
}

class TeacherProfile {
  const TeacherProfile({
    required this.teacherNo,
    required this.formalName,
    required this.displayName,
    required this.department,
    required this.phone,
    required this.wechat,
  });

  final String teacherNo;
  final String formalName;
  final String displayName;
  final String department;
  final String phone;
  final String wechat;

  String get name => displayName.isNotEmpty ? displayName : formalName;

  factory TeacherProfile.fromJson(Map<String, dynamic> json) {
    return TeacherProfile(
      teacherNo: jsonString(json['teacher_no']) ?? '',
      formalName: jsonString(json['formal_name']) ?? '',
      displayName: jsonString(json['display_name']) ?? '',
      department: jsonString(json['department']) ?? '',
      phone: jsonString(json['phone']) ?? '',
      wechat: jsonString(json['wechat']) ?? '',
    );
  }
}

class Me {
  const Me({
    required this.id,
    required this.email,
    required this.emailVerified,
    required this.teacherStatus,
    required this.assignmentScopeVersion,
    required this.currentTerm,
    required this.teacher,
    required this.avatarUrl,
  });

  final String id;
  final String email;
  final bool emailVerified;
  final String teacherStatus;
  final int? assignmentScopeVersion;
  final CurrentTerm? currentTerm;
  final TeacherProfile? teacher;
  final String? avatarUrl;

  bool get canUseTeacherFeatures => emailVerified && teacherStatus == 'active';

  String get displayName {
    final teacherName = teacher?.name;
    if (teacherName != null && teacherName.isNotEmpty) {
      return teacherName;
    }
    return email;
  }

  factory Me.fromJson(Map<String, dynamic> json) {
    final currentTermJson = json['current_term'];
    final teacherJson = json['teacher'];
    return Me(
      id: jsonString(json['id']) ?? '',
      email: jsonString(json['email']) ?? '',
      emailVerified: json['email_verified'] == true,
      teacherStatus: jsonString(json['teacher_status']) ?? 'unlinked',
      assignmentScopeVersion: jsonInt(json['assignment_scope_version']),
      currentTerm: currentTermJson is Map
          ? CurrentTerm.fromJson(jsonMap(currentTermJson))
          : null,
      teacher: teacherJson is Map
          ? TeacherProfile.fromJson(jsonMap(teacherJson))
          : null,
      avatarUrl: jsonString(json['avatar_url']),
    );
  }
}
