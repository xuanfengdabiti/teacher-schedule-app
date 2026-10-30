import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/network/api_exception.dart';
import '../../core/providers.dart';
import 'import_summary.dart';

enum ImportPhase { idle, running, success, failure }

class ImportState {
  const ImportState({
    required this.phase,
    this.step = 0,
    this.summary,
    this.errorMessage,
  });

  /// 0..2 while running; 3 once every step is finished.
  final int step;
  final ImportPhase phase;
  final ImportSummary? summary;
  final String? errorMessage;

  bool get isRunning => phase == ImportPhase.running;
  bool get isSuccess => phase == ImportPhase.success;
  bool get isFailure => phase == ImportPhase.failure;
}

/// Runs the demo import presentation against the existing bootstrap endpoint.
///
/// The page never talks to Dio directly; only a compact summary is kept in
/// memory for display. Nothing is written to disk here.
class ImportController extends Notifier<ImportState> {
  static const stepDuration = Duration(milliseconds: 550);

  int _runId = 0;
  bool _busy = false;
  bool _disposed = false;

  @override
  ImportState build() {
    _disposed = false;
    ref.onDispose(() => _disposed = true);
    return const ImportState(phase: ImportPhase.idle);
  }

  Future<void> start({String? teacherNo}) async {
    if (_busy) {
      return;
    }
    _busy = true;
    final runId = ++_runId;
    // Teacher 0001 gets a slightly slower demo import; everyone else keeps
    // the original pace/course data and bootstrap request.
    final localStepDuration = teacherNo?.trim() == '0001'
        ? const Duration(milliseconds: 883)
        : stepDuration;
    state = const ImportState(phase: ImportPhase.running);

    ImportSummary? summary;
    Object? failure;
    final request = () async {
      try {
        summary = await ref
            .read(scheduleRepositoryProvider)
            .fetchBootstrapSummary();
      } catch (error) {
        failure = error;
      }
    }();

    for (var step = 1; step < 3; step++) {
      await Future<void>.delayed(localStepDuration);
      if (_disposed || runId != _runId) {
        return;
      }
      state = ImportState(phase: ImportPhase.running, step: step);
    }
    await Future<void>.delayed(localStepDuration);
    await request;
    if (_disposed || runId != _runId) {
      return;
    }
    _busy = false;
    if (failure != null) {
      final apiError = failure is ApiException ? failure as ApiException : null;
      state = ImportState(
        phase: ImportPhase.failure,
        step: 2,
        errorMessage: apiError?.message ?? '课表获取失败，请稍后重试。',
      );
      return;
    }
    state = ImportState(phase: ImportPhase.success, step: 3, summary: summary);
  }
}

final importControllerProvider =
    NotifierProvider<ImportController, ImportState>(ImportController.new);
