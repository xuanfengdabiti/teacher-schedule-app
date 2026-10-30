import '../../core/json.dart';
import '../../core/network/api_client.dart';
import '../schedule/models.dart';
import 'reminder_models.dart';

/// Reads/writes the existing account preference endpoint and the teacher's
/// own class sessions. No separate reminder backend is introduced.
class ReminderRepository {
  ReminderRepository(this._api);

  final ApiClient _api;

  Future<ReminderPreferences> fetchPreferences() async {
    final response = await _api.get('me/preferences/');
    return ReminderPreferences.fromJson(jsonMap(response.data));
  }

  /// PATCHes only the provided reminder fields; font size, push and other
  /// preferences are left untouched by the partial update.
  Future<ReminderPreferences> updatePreferences(
    Map<String, dynamic> fields, {
    required ReminderPreferences fallback,
  }) async {
    final response = await _api.patch('me/preferences/', data: fields);
    return ReminderPreferences.fromJson(
      jsonMap(response.data),
      fallback: fallback,
    );
  }

  Future<List<Session>> fetchUpcomingSessions({
    required DateTime from,
    required DateTime to,
  }) async {
    final response = await _api.get(
      'sessions/',
      query: {'from': isoDate(from), 'to': isoDate(to), 'scope': 'mine'},
    );
    final results = jsonMap(response.data)['results'];
    if (results is! List) {
      return const [];
    }
    return results
        .whereType<Map>()
        .map((item) => Session.fromJson(jsonMap(item)))
        .toList(growable: false);
  }
}
