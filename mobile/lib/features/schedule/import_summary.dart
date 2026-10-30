import '../../core/json.dart';

/// Small, non-persisted summary of the demo bootstrap payload.
class ImportSummary {
  const ImportSummary({
    required this.termName,
    required this.sessionCount,
    required this.courseCount,
  });

  final String termName;
  final int sessionCount;
  final int courseCount;

  bool get isEmpty => sessionCount == 0;

  factory ImportSummary.fromBootstrap(Map<String, dynamic> json) {
    final term = json['term'];
    final sessions = json['sessions'];
    final ownSessions = sessions is List
        ? sessions.whereType<Map>().where((item) {
            return item['is_mine'] == true && item['status'] != 'cancelled';
          })
        : const <Map>[];
    final courses = <String>{
      for (final item in ownSessions)
        (jsonString(item['offering_id']) ?? '') +
            (jsonString(item['course_code']) ?? ''),
    }..remove('');
    return ImportSummary(
      termName: term is Map ? jsonString(jsonMap(term)['name']) ?? '' : '',
      sessionCount: ownSessions.length,
      courseCount: courses.length,
    );
  }
}
