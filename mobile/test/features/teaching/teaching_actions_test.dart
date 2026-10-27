import 'package:flutter_test/flutter_test.dart';
import 'package:teacher_schedule/features/teaching/teaching_controllers.dart';
import 'package:teacher_schedule/features/teaching/teaching_models.dart';
import 'package:teacher_schedule/features/teaching/teaching_repository.dart';

void main() {
  group('交接详情', () {
    test('打开收件箱详情只标记已读，不自动确认', () async {
      final api = _FakeTeachingApi(
        handoff: _handoff(status: 'pending', canAcknowledge: true),
      );
      final controller = HandoffDetailController(
        api: api,
        handoffId: 'handoff-1',
        readOnOpen: true,
      );

      await controller.load();

      expect(api.markReadCalls, 1);
      expect(api.acknowledgeCalls, 0);
      expect(controller.detail?.status, 'pending');
      expect(controller.detail?.readAt, isNotNull);

      await controller.acknowledge();

      expect(api.acknowledgeCalls, 1);
      expect(controller.detail?.status, 'acknowledged');
    });

    test('已发出的交接打开时不调用已读接口', () async {
      final api = _FakeTeachingApi(
        handoff: _handoff(status: 'pending', canWithdraw: true),
      );
      final controller = HandoffDetailController(
        api: api,
        handoffId: 'handoff-2',
        readOnOpen: false,
      );

      await controller.load();

      expect(api.markReadCalls, 0);
      expect(api.acknowledgeCalls, 0);
    });
  });

  group('授课完成', () {
    test('更正 payload 保留小数字符串并校验原因', () async {
      const request = CompletionRequest(
        action: CompletionAction.correct,
        baseVersion: 7,
        actualHours: '2.0',
        allocations: {'item-1': '1.5'},
        reason: '补齐实际完成学时',
      );

      final payload = request.toJson();

      expect(payload['action'], 'correct');
      expect(payload['base_version'], 7);
      expect(payload['actual_hours'], '2.0');
      expect(payload['allocations'], [
        {'plan_item_id': 'item-1', 'actual_hours': '1.5'},
      ]);
      expect(payload['reason'], '补齐实际完成学时');

      const missingReason = CompletionRequest(
        action: CompletionAction.correct,
        baseVersion: 7,
        actualHours: '2.0',
      );
      expect(missingReason.validate(), isNotNull);
    });

    test('更正原因为空时控制器不发请求', () async {
      final api = _FakeTeachingApi(
        completion: _completion(status: 'completed', version: 3),
      );
      final controller = CompletionController(api: api, sessionId: 'session-1');

      final result = await controller.submit(
        const CompletionRequest(
          action: CompletionAction.correct,
          baseVersion: 3,
          actualHours: '2.0',
          reason: ' ',
        ),
      );

      expect(result.ok, isFalse);
      expect(result.message, contains('原因'));
      expect(api.submitCompletionCalls, 0);
    });
  });
}

Handoff _handoff({
  required String status,
  bool canAcknowledge = false,
  bool canWithdraw = false,
  DateTime? readAt,
}) {
  return Handoff(
    id: 'handoff-1',
    offeringId: 'offering-1',
    courseName: '示例课程',
    teachingClassName: '示例教学班',
    sender: const TeacherBrief(id: 'teacher-1', name: '示例教师甲'),
    recipient: const TeacherBrief(id: 'teacher-2', name: '示例教师乙'),
    sourceSession: const HandoffSession(
      id: 'session-1',
      startPeriod: 1,
      endPeriod: 2,
    ),
    contentSnapshot: '下次课继续完成示例专题。',
    status: status,
    readAt: readAt,
    createdAt: DateTime(2026, 10, 27, 8),
    canAcknowledge: canAcknowledge,
    canWithdraw: canWithdraw,
  );
}

CompletionDetail _completion({required String status, required int version}) {
  return CompletionDetail(
    sessionId: 'session-1',
    status: status,
    actualHours: status == 'completed' ? '2.0' : null,
    completionRef: '',
    version: version,
    allocations: const [],
    availableItems: const [],
    history: const [],
  );
}

class _FakeTeachingApi implements TeachingApi {
  _FakeTeachingApi({this.handoff, this.completion});

  Handoff? handoff;
  CompletionDetail? completion;
  int markReadCalls = 0;
  int acknowledgeCalls = 0;
  int submitCompletionCalls = 0;

  @override
  Future<Handoff> fetchHandoff(String handoffId) async => handoff!;

  @override
  Future<Handoff> markHandoffRead(String handoffId) async {
    markReadCalls++;
    handoff = Handoff(
      id: handoff!.id,
      offeringId: handoff!.offeringId,
      courseName: handoff!.courseName,
      teachingClassName: handoff!.teachingClassName,
      sender: handoff!.sender,
      recipient: handoff!.recipient,
      sourceSession: handoff!.sourceSession,
      targetSession: handoff!.targetSession,
      contentSnapshot: handoff!.contentSnapshot,
      notePublishedVersion: handoff!.notePublishedVersion,
      status: handoff!.status,
      readAt: DateTime(2026, 10, 27, 9),
      acknowledgedAt: handoff!.acknowledgedAt,
      withdrawnAt: handoff!.withdrawnAt,
      createdAt: handoff!.createdAt,
      canAcknowledge: handoff!.canAcknowledge,
      canWithdraw: handoff!.canWithdraw,
    );
    return handoff!;
  }

  @override
  Future<Handoff> acknowledgeHandoff(String handoffId) async {
    acknowledgeCalls++;
    handoff = _handoff(status: 'acknowledged', readAt: handoff!.readAt);
    return handoff!;
  }

  @override
  Future<CompletionDetail> submitCompletion(
    String sessionId,
    CompletionRequest request,
  ) async {
    submitCompletionCalls++;
    completion = _completion(status: 'completed', version: 4);
    return completion!;
  }

  @override
  Future<CompletionDetail> fetchCompletion(String sessionId) async =>
      completion ?? _completion(status: 'scheduled', version: 1);

  @override
  Future<Handoff> withdrawHandoff(String handoffId) async {
    handoff = _handoff(status: 'withdrawn');
    return handoff!;
  }

  @override
  Future<HandoffPage> fetchHandoffs({required String box}) async =>
      const HandoffPage(count: 0, results: []);

  @override
  Future<HandoffPrefill> fetchHandoffPrefill(String sourceSessionId) async =>
      const HandoffPrefill(recipients: [], content: '');

  @override
  Future<String> fetchSessionOfferingId(String sessionId) async => 'offering-1';

  @override
  Future<List<Handoff>> createHandoffs({
    required String offeringId,
    required List<String> recipientIds,
    required String content,
    required String sourceSessionId,
    String? targetSessionId,
    String? noteId,
  }) async => const [];

  @override
  Future<PlanBundle> fetchPlan(String offeringId) async {
    throw UnimplementedError();
  }

  @override
  Future<PlanBundle> savePlan({
    required String offeringId,
    required int baseVersion,
    required List<PlanItemInput> items,
  }) async {
    throw UnimplementedError();
  }
}
