import '../../core/json.dart';
import '../../core/network/api_client.dart';
import 'preparation_models.dart';

abstract class PreparationApi {
  Future<PreparationBundle> fetch(String sessionId);

  Future<GeneratedPreparation> generate(String sessionId, String topic);

  Future<PreparationDraft> save({
    required String sessionId,
    required int baseVersion,
    required String topic,
    required String outline,
    required String caseStudy,
    required String discussionQuestions,
    required String preparation,
    String generatedModel = '',
    String? generatedAt,
  });
}

class PreparationRepository implements PreparationApi {
  PreparationRepository(this._api);

  final ApiClient _api;

  @override
  Future<PreparationBundle> fetch(String sessionId) async {
    final response = await _api.get('sessions/$sessionId/preparation/');
    return PreparationBundle.fromJson(jsonMap(response.data));
  }

  @override
  Future<GeneratedPreparation> generate(String sessionId, String topic) async {
    // The model call is slower than ordinary reads; keep this local 55s
    // receive timeout instead of changing the client-wide 20s default.
    final response = await _api.postWithTimeout(
      'sessions/$sessionId/preparation/generate/',
      data: {'topic': topic},
      receiveTimeout: const Duration(seconds: 55),
    );
    return GeneratedPreparation.fromJson(jsonMap(response.data));
  }

  @override
  Future<PreparationDraft> save({
    required String sessionId,
    required int baseVersion,
    required String topic,
    required String outline,
    required String caseStudy,
    required String discussionQuestions,
    required String preparation,
    String generatedModel = '',
    String? generatedAt,
  }) async {
    final response = await _api.put(
      'sessions/$sessionId/preparation/',
      data: {
        'base_version': baseVersion,
        'topic': topic,
        'outline': outline,
        'case_study': caseStudy,
        'discussion_questions': discussionQuestions,
        'preparation': preparation,
        'generated_model': generatedModel,
        if (generatedAt != null && generatedAt.isNotEmpty)
          'generated_at': generatedAt,
      },
    );
    return PreparationDraft.fromJson(jsonMap(response.data));
  }
}
