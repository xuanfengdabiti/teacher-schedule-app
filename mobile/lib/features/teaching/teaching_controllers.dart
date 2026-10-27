import 'package:flutter/foundation.dart';

import '../../core/network/api_exception.dart';
import 'teaching_models.dart';
import 'teaching_repository.dart';

class PlanSaveResult {
  const PlanSaveResult({
    required this.ok,
    required this.message,
    this.conflict = false,
  });

  final bool ok;
  final String message;
  final bool conflict;
}

class TeachingPlanController extends ChangeNotifier {
  TeachingPlanController({required this.api, required this.offeringId});

  final TeachingApi api;
  final String offeringId;

  PlanBundle? bundle;
  bool loading = false;
  bool saving = false;
  String? error;
  String? notice;
  bool _disposed = false;

  Future<void> load() async {
    if (loading || saving) {
      return;
    }
    loading = true;
    error = null;
    _notify();
    try {
      bundle = await api.fetchPlan(offeringId);
    } on Object catch (value) {
      error = ApiException.fromUnknown(value).message;
    } finally {
      loading = false;
      _notify();
    }
  }

  Future<PlanSaveResult> save({
    required int baseVersion,
    required List<PlanItemInput> items,
  }) async {
    if (saving) {
      return const PlanSaveResult(ok: false, message: '正在保存，请稍候。');
    }
    saving = true;
    error = null;
    notice = null;
    _notify();
    try {
      bundle = await api.savePlan(
        offeringId: offeringId,
        baseVersion: baseVersion,
        items: items,
      );
      notice = '教学计划已保存，进度以服务端返回为准。';
      return const PlanSaveResult(ok: true, message: '教学计划已保存。');
    } on ApiException catch (value) {
      if (value.statusCode == 409) {
        return PlanSaveResult(
          ok: false,
          conflict: true,
          message: '计划已在其他设备更新。本地修改仍保留，请刷新后再合并。',
        );
      }
      error = value.message;
      return PlanSaveResult(ok: false, message: value.message);
    } on Object catch (value) {
      final message = ApiException.fromUnknown(value).message;
      error = message;
      return PlanSaveResult(ok: false, message: message);
    } finally {
      saving = false;
      _notify();
    }
  }

  void _notify() {
    if (!_disposed) {
      notifyListeners();
    }
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}

class CompletionSubmitResult {
  const CompletionSubmitResult({
    required this.ok,
    required this.message,
    this.conflict = false,
  });

  final bool ok;
  final String message;
  final bool conflict;
}

class CompletionController extends ChangeNotifier {
  CompletionController({required this.api, required this.sessionId});

  final TeachingApi api;
  final String sessionId;

  CompletionDetail? detail;
  bool loading = false;
  bool submitting = false;
  String? error;
  String? notice;
  bool _disposed = false;

  Future<void> load() async {
    if (loading || submitting) {
      return;
    }
    loading = true;
    error = null;
    _notify();
    try {
      detail = await api.fetchCompletion(sessionId);
    } on Object catch (value) {
      error = ApiException.fromUnknown(value).message;
    } finally {
      loading = false;
      _notify();
    }
  }

  Future<CompletionSubmitResult> submit(CompletionRequest request) async {
    final localError = request.validate();
    if (localError != null) {
      return CompletionSubmitResult(ok: false, message: localError);
    }
    if (submitting) {
      return const CompletionSubmitResult(ok: false, message: '正在提交，请稍候。');
    }
    submitting = true;
    error = null;
    notice = null;
    _notify();
    try {
      detail = await api.submitCompletion(sessionId, request);
      notice = '授课状态已更新，服务端进度已刷新。';
      return CompletionSubmitResult(
        ok: true,
        message: request.action == CompletionAction.revert
            ? '已撤销完成，课次回到待上课。'
            : '授课完成信息已保存。',
      );
    } on ApiException catch (value) {
      if (value.statusCode == 409) {
        return CompletionSubmitResult(
          ok: false,
          conflict: true,
          message: '课次已在其他设备更新。当前输入仍保留，请刷新服务端状态后再提交。',
        );
      }
      return CompletionSubmitResult(ok: false, message: value.message);
    } on Object catch (value) {
      return CompletionSubmitResult(
        ok: false,
        message: ApiException.fromUnknown(value).message,
      );
    } finally {
      submitting = false;
      _notify();
    }
  }

  void _notify() {
    if (!_disposed) {
      notifyListeners();
    }
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}

class HandoffListController extends ChangeNotifier {
  HandoffListController({required this.api});

  final TeachingApi api;

  String box = 'inbox';
  List<Handoff> items = const [];
  int total = 0;
  bool loading = false;
  String? error;
  bool _disposed = false;

  Future<void> load({String? nextBox}) async {
    if (nextBox != null && nextBox != box) {
      box = nextBox;
      items = const [];
      total = 0;
    }
    if (loading) {
      return;
    }
    loading = true;
    error = null;
    _notify();
    try {
      final page = await api.fetchHandoffs(box: box);
      items = page.results;
      total = page.count;
    } on Object catch (value) {
      error = ApiException.fromUnknown(value).message;
    } finally {
      loading = false;
      _notify();
    }
  }

  void replace(Handoff handoff) {
    final index = items.indexWhere((item) => item.id == handoff.id);
    if (index < 0) {
      return;
    }
    final next = [...items];
    next[index] = handoff;
    items = next;
    _notify();
  }

  void _notify() {
    if (!_disposed) {
      notifyListeners();
    }
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}

class HandoffAction {
  const HandoffAction({required this.ok, required this.message, this.detail});

  final bool ok;
  final String message;
  final Handoff? detail;
}

class HandoffDetailController extends ChangeNotifier {
  HandoffDetailController({
    required this.api,
    required this.handoffId,
    required this.readOnOpen,
  });

  final TeachingApi api;
  final String handoffId;
  final bool readOnOpen;

  Handoff? detail;
  bool loading = false;
  bool acting = false;
  String? error;
  String? readError;
  String? notice;
  bool _disposed = false;

  Future<void> load() async {
    if (loading) {
      return;
    }
    loading = true;
    error = null;
    readError = null;
    _notify();
    try {
      detail = await api.fetchHandoff(handoffId);
      _notify();
      if (readOnOpen && detail?.readAt == null) {
        try {
          detail = await api.markHandoffRead(handoffId);
        } on Object catch (value) {
          readError = '读取状态未能同步：${ApiException.fromUnknown(value).message}';
        }
      }
    } on Object catch (value) {
      error = ApiException.fromUnknown(value).message;
    } finally {
      loading = false;
      _notify();
    }
  }

  Future<HandoffAction> acknowledge() async {
    if (acting) {
      return const HandoffAction(ok: false, message: '正在处理，请稍候。');
    }
    if (detail?.canAcknowledge != true) {
      return const HandoffAction(ok: false, message: '当前交接不能确认。');
    }
    acting = true;
    error = null;
    _notify();
    try {
      final updated = await api.acknowledgeHandoff(handoffId);
      detail = updated;
      notice = '已确认收到交接。';
      return HandoffAction(ok: true, message: notice!, detail: updated);
    } on Object catch (value) {
      final message = ApiException.fromUnknown(value).message;
      error = message;
      return HandoffAction(ok: false, message: message);
    } finally {
      acting = false;
      _notify();
    }
  }

  Future<HandoffAction> withdraw() async {
    if (acting) {
      return const HandoffAction(ok: false, message: '正在处理，请稍候。');
    }
    if (detail?.canWithdraw != true) {
      return const HandoffAction(ok: false, message: '当前交接不能撤回。');
    }
    acting = true;
    error = null;
    _notify();
    try {
      final updated = await api.withdrawHandoff(handoffId);
      detail = updated;
      notice = '交接已撤回。';
      return HandoffAction(ok: true, message: notice!, detail: updated);
    } on Object catch (value) {
      final message = ApiException.fromUnknown(value).message;
      error = message;
      return HandoffAction(ok: false, message: message);
    } finally {
      acting = false;
      _notify();
    }
  }

  void _notify() {
    if (!_disposed) {
      notifyListeners();
    }
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}

class HandoffComposeController extends ChangeNotifier {
  HandoffComposeController({required this.api, required this.sourceSessionId});

  final TeachingApi api;
  final String sourceSessionId;

  String offeringId = '';
  HandoffPrefill? prefill;
  Set<String> selectedRecipientIds = <String>{};
  bool includeSuggestedTarget = true;
  bool loading = false;
  bool submitting = false;
  String? error;
  bool _disposed = false;

  Future<void> load() async {
    if (loading) {
      return;
    }
    loading = true;
    error = null;
    _notify();
    try {
      final offeringFuture = api.fetchSessionOfferingId(sourceSessionId);
      final prefillFuture = api.fetchHandoffPrefill(sourceSessionId);
      offeringId = await offeringFuture;
      prefill = await prefillFuture;
      final validIds = {
        for (final recipient
            in prefill?.recipients ?? const <HandoffRecipient>[])
          recipient.id,
      };
      selectedRecipientIds = selectedRecipientIds
          .where(validIds.contains)
          .toSet();
      includeSuggestedTarget = prefill?.suggestedTargetSession != null;
    } on Object catch (value) {
      error = ApiException.fromUnknown(value).message;
    } finally {
      loading = false;
      _notify();
    }
  }

  void toggleRecipient(String teacherId) {
    final next = {...selectedRecipientIds};
    if (!next.remove(teacherId)) {
      next.add(teacherId);
    }
    selectedRecipientIds = next;
    _notify();
  }

  void setIncludeTarget(bool value) {
    includeSuggestedTarget = value;
    _notify();
  }

  Future<HandoffAction> submit(String content) async {
    if (submitting) {
      return const HandoffAction(ok: false, message: '正在发送，请稍候。');
    }
    if (selectedRecipientIds.isEmpty) {
      return const HandoffAction(ok: false, message: '请至少选择一位接收人。');
    }
    if (content.trim().isEmpty) {
      return const HandoffAction(ok: false, message: '请填写交接内容。');
    }
    if (offeringId.isEmpty) {
      return const HandoffAction(ok: false, message: '未取得课程开设信息，请重新加载。');
    }
    submitting = true;
    error = null;
    _notify();
    try {
      final created = await api.createHandoffs(
        offeringId: offeringId,
        recipientIds: selectedRecipientIds.toList(),
        content: content,
        sourceSessionId: sourceSessionId,
        targetSessionId: includeSuggestedTarget
            ? prefill?.suggestedTargetSession?.id
            : null,
        noteId: prefill?.noteId,
      );
      return HandoffAction(
        ok: true,
        message: created.isEmpty ? '交接已发送。' : '已向 ${created.length} 位教师发送交接。',
        detail: created.isEmpty ? null : created.first,
      );
    } on Object catch (value) {
      final message = ApiException.fromUnknown(value).message;
      error = message;
      return HandoffAction(ok: false, message: message);
    } finally {
      submitting = false;
      _notify();
    }
  }

  void _notify() {
    if (!_disposed) {
      notifyListeners();
    }
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}
