import '../../core/json.dart';
import '../../core/network/api_client.dart';
import 'models.dart';

class ScheduleRepository {
  ScheduleRepository(this._api);

  final ApiClient _api;

  Future<WeekSchedule> fetchWeek(DateTime monday) async {
    final from = startOfWeek(monday);
    final to = from.add(const Duration(days: 7));
    final response = await _api.get(
      'sessions/',
      query: {'from': isoDate(from), 'to': isoDate(to), 'scope': 'mine'},
    );
    final body = jsonMap(response.data);
    final results = body['results'];
    final sessions = results is List
        ? results
              .whereType<Map>()
              .map((item) => Session.fromJson(jsonMap(item)))
              .toList(growable: false)
        : <Session>[];
    final term = body['term'];
    return WeekSchedule(
      from: from,
      sessions: sessions,
      termId: term is Map ? jsonString(jsonMap(term)['id']) : null,
      termCode: term is Map ? jsonString(jsonMap(term)['code']) : null,
    );
  }

  Future<Session> fetchSession(String sessionId) async {
    final response = await _api.get('sessions/$sessionId/');
    return Session.fromJson(jsonMap(response.data));
  }

  Future<TermCalendar> fetchCalendar(String termId) async {
    final response = await _api.get('terms/$termId/calendar/');
    return TermCalendar.fromJson(jsonMap(response.data));
  }

  Future<List<Offering>> fetchOfferings() async {
    final response = await _api.get('offerings/');
    final body = jsonMap(response.data);
    final results = body['results'];
    if (results is! List) {
      return const [];
    }
    return results
        .whereType<Map>()
        .map((item) => Offering.fromJson(jsonMap(item)))
        .toList(growable: false);
  }
}
