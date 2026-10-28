import 'package:flutter/foundation.dart';

import '../../core/network/api_exception.dart';
import 'preparation_models.dart';
import 'preparation_repository.dart';

class PreparationSaveResult {
  const PreparationSaveResult({
    required this.ok,
    required this.message,
    this.conflict = false,
  });

  final bool ok;
  final String message;
  final bool conflict;
}

class PreparationController extends ChangeNotifier {
  PreparationController({required this.api, required this.sessionId});

  final PreparationApi api;
  final String sessionId;

  PreparationBundle? bundle;
  GeneratedPreparation? generated;
  bool loading = false;
  bool generating = false;
  bool saving = false;
  String? error;
  String? actionError;
  String? notice;
  bool _disposed = false;

  int get baseVersion => bundle?.draft?.version ?? 0;

  Future<void> load() async {
    if (loading) {
      return;
    }
    loading = true;
    error = null;
    _notify();
    try {
      bundle = await api.fetch(sessionId);
    } on Object catch (value) {
      error = _message(value);
    } finally {
      loading = false;
      _notify();
    }
  }

  /// Generates four editable sections. Nothing is saved and the existing draft
  /// version stays unchanged, so a later save still uses the loaded version.
  Future<bool> generate(String topic) async {
    if (generating || saving) {
      return false;
    }
    generating = true;
    actionError = null;
    notice = null;
    _notify();
    try {
      generated = await api.generate(sessionId, topic);
      notice = '备课内容已生成，可继续修改后保存。';
      return true;
    } on Object catch (value) {
      actionError = _message(value);
      return false;
    } finally {
      generating = false;
      _notify();
    }
  }

  Future<PreparationSaveResult> save({
    required String topic,
    required String outline,
    required String caseStudy,
    required String discussionQuestions,
    required String preparation,
  }) async {
    if (saving || generating) {
      return const PreparationSaveResult(ok: false, message: '正在处理，请稍候。');
    }
    final current = bundle;
    if (current == null) {
      return const PreparationSaveResult(ok: false, message: '备课信息尚未加载，请稍后重试。');
    }
    saving = true;
    actionError = null;
    notice = null;
    _notify();
    try {
      final saved = await api.save(
        sessionId: sessionId,
        baseVersion: baseVersion,
        topic: topic,
        outline: outline,
        caseStudy: caseStudy,
        discussionQuestions: discussionQuestions,
        preparation: preparation,
        generatedModel: generated?.generatedModel.isNotEmpty == true
            ? generated!.generatedModel
            : (current.draft?.generatedModel ?? ''),
        generatedAt: generated?.generatedAt ?? current.draft?.generatedAt,
      );
      bundle = current.withDraft(saved);
      generated = null;
      notice = '备课已保存。';
      return const PreparationSaveResult(ok: true, message: '备课已保存。');
    } on ApiException catch (value) {
      // A version conflict keeps the teacher's input and the old base version;
      // never retry automatically with the server's current version.
      actionError = value.message;
      return PreparationSaveResult(
        ok: false,
        message: value.message,
        conflict: value.statusCode == 409,
      );
    } on Object catch (value) {
      final message = _message(value);
      actionError = message;
      return PreparationSaveResult(ok: false, message: message);
    } finally {
      saving = false;
      _notify();
    }
  }

  String _message(Object error) {
    final message = ApiException.fromUnknown(error).message;
    if (message.trim().isEmpty) {
      return '暂时无法连接，请稍后重试。';
    }
    return message;
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
