import '../../core/json.dart';
import '../../core/network/api_client.dart';
import 'teaching_models.dart';

abstract class TeachingApi {
  Future<PlanBundle> fetchPlan(String offeringId);

  Future<PlanBundle> savePlan({
    required String offeringId,
    required int baseVersion,
    required List<PlanItemInput> items,
  });

  Future<CompletionDetail> fetchCompletion(String sessionId);

  Future<CompletionDetail> submitCompletion(
    String sessionId,
    CompletionRequest request,
  );

  Future<HandoffPage> fetchHandoffs({required String box});

  Future<Handoff> fetchHandoff(String handoffId);

  Future<Handoff> markHandoffRead(String handoffId);

  Future<Handoff> acknowledgeHandoff(String handoffId);

  Future<Handoff> withdrawHandoff(String handoffId);

  Future<HandoffPrefill> fetchHandoffPrefill(String sourceSessionId);

  Future<String> fetchSessionOfferingId(String sessionId);

  Future<List<Handoff>> createHandoffs({
    required String offeringId,
    required List<String> recipientIds,
    required String content,
    required String sourceSessionId,
    String? targetSessionId,
    String? noteId,
  });
}

class TeachingRepository implements TeachingApi {
  TeachingRepository(this._api);

  final ApiClient _api;

  @override
  Future<PlanBundle> fetchPlan(String offeringId) async {
    final response = await _api.get('offerings/$offeringId/plan/');
    return PlanBundle.fromJson(response.data);
  }

  @override
  Future<PlanBundle> savePlan({
    required String offeringId,
    required int baseVersion,
    required List<PlanItemInput> items,
  }) async {
    final response = await _api.put(
      'offerings/$offeringId/plan/',
      data: {
        'base_version': baseVersion,
        'items': items.map((item) => item.toJson()).toList(),
      },
    );
    return PlanBundle.fromJson(response.data);
  }

  @override
  Future<CompletionDetail> fetchCompletion(String sessionId) async {
    final response = await _api.get('sessions/$sessionId/completion/');
    return CompletionDetail.fromJson(response.data);
  }

  @override
  Future<CompletionDetail> submitCompletion(
    String sessionId,
    CompletionRequest request,
  ) async {
    final response = await _api.put(
      'sessions/$sessionId/completion/',
      data: request.toJson(),
    );
    return CompletionDetail.fromJson(response.data);
  }

  @override
  Future<HandoffPage> fetchHandoffs({required String box}) async {
    final response = await _api.get('handoffs/', query: {'box': box});
    return HandoffPage.fromJson(response.data);
  }

  @override
  Future<Handoff> fetchHandoff(String handoffId) async {
    final response = await _api.get('handoffs/$handoffId/');
    return Handoff.fromJson(response.data);
  }

  @override
  Future<Handoff> markHandoffRead(String handoffId) async {
    final response = await _api.post('handoffs/$handoffId/read/');
    return Handoff.fromJson(response.data);
  }

  @override
  Future<Handoff> acknowledgeHandoff(String handoffId) async {
    final response = await _api.post('handoffs/$handoffId/acknowledge/');
    return Handoff.fromJson(response.data);
  }

  @override
  Future<Handoff> withdrawHandoff(String handoffId) async {
    final response = await _api.post('handoffs/$handoffId/withdraw/');
    return Handoff.fromJson(response.data);
  }

  @override
  Future<HandoffPrefill> fetchHandoffPrefill(String sourceSessionId) async {
    final response = await _api.get(
      'handoffs/prefill/',
      query: {'source_session_id': sourceSessionId},
    );
    return HandoffPrefill.fromJson(response.data);
  }

  @override
  Future<String> fetchSessionOfferingId(String sessionId) async {
    final response = await _api.get('sessions/$sessionId/');
    return jsonString(jsonMap(response.data)['offering_id']) ?? '';
  }

  @override
  Future<List<Handoff>> createHandoffs({
    required String offeringId,
    required List<String> recipientIds,
    required String content,
    required String sourceSessionId,
    String? targetSessionId,
    String? noteId,
  }) async {
    final response = await _api.post(
      'handoffs/',
      data: {
        'offering_id': offeringId,
        'recipient_ids': recipientIds,
        'content': content.trim(),
        'source_session_id': sourceSessionId,
        if (targetSessionId != null && targetSessionId.isNotEmpty)
          'target_session_id': targetSessionId,
        if (noteId != null && noteId.isNotEmpty) 'note_id': noteId,
      },
    );
    final data = response.data;
    if (data is! List) {
      return const [];
    }
    return data.map(Handoff.fromJson).toList();
  }
}
