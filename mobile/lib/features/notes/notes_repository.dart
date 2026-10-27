import '../../core/json.dart';
import '../../core/network/api_client.dart';
import '../../core/network/api_exception.dart';
import 'note_models.dart';

class NoteConflictException implements Exception {
  const NoteConflictException({required this.message, this.current});

  final String message;
  final MineNote? current;

  @override
  String toString() => message;
}

class NotesRepository {
  NotesRepository(this._api);

  final ApiClient _api;

  Future<NotesBundle> fetchNotes(String sessionId) async {
    final response = await _api.get('sessions/$sessionId/notes/');
    return NotesBundle.fromJson(jsonMap(response.data));
  }

  Future<MineNote> saveDraft({
    required String sessionId,
    required NoteText text,
    required int baseVersion,
    required String mutationId,
  }) async {
    try {
      final response = await _api.put(
        'sessions/$sessionId/notes/mine/',
        data: {
          'covered': text.covered,
          'unfinished': text.unfinished,
          'next_plan': text.nextPlan,
          'base_version': baseVersion,
          'client_mutation_id': mutationId,
        },
      );
      return MineNote.fromJson(jsonMap(response.data));
    } on ApiException catch (error) {
      if (error.statusCode == 409) {
        throw NoteConflictException(
          message: error.message,
          current: error.current == null
              ? null
              : MineNote.fromJson(error.current!),
        );
      }
      rethrow;
    }
  }

  Future<MineNote> publish({
    required String sessionId,
    required int baseVersion,
    required String mutationId,
  }) async {
    try {
      final response = await _api.post(
        'sessions/$sessionId/notes/mine/publish/',
        data: {'base_version': baseVersion, 'client_mutation_id': mutationId},
      );
      return MineNote.fromJson(jsonMap(response.data));
    } on ApiException catch (error) {
      if (error.statusCode == 409) {
        throw NoteConflictException(
          message: error.message,
          current: error.current == null
              ? null
              : MineNote.fromJson(error.current!),
        );
      }
      rethrow;
    }
  }
}
