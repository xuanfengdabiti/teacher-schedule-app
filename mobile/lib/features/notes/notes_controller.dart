import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/network/api_exception.dart';
import '../../core/providers.dart';
import '../auth/auth_controller.dart';
import 'note_models.dart';
import 'notes_repository.dart';

class NoteConflict {
  const NoteConflict({required this.message, this.current});

  final String message;
  final MineNote? current;
}

class NotesState {
  const NotesState({
    this.accountId = '',
    this.bundle,
    this.initialText,
    this.draftBaseVersion,
    this.isLoading = true,
    this.loadError,
    this.offline = false,
    this.conflict,
    this.backup,
    this.recoveredLocalDraft = false,
  });

  final String accountId;
  final NotesBundle? bundle;
  final NoteText? initialText;

  /// The version the local draft was written against.  It must not be replaced
  /// by a newer server version during recovery, otherwise an offline v1 draft
  /// would silently overwrite a v2 edit made elsewhere.
  final int? draftBaseVersion;
  final bool isLoading;
  final String? loadError;
  final bool offline;
  final NoteConflict? conflict;
  final LocalDraft? backup;
  final bool recoveredLocalDraft;

  MineNote? get mine => bundle?.mine;

  int get effectiveBaseVersion => draftBaseVersion ?? mine?.version ?? 0;

  NotesState copyWith({
    String? accountId,
    NotesBundle? bundle,
    NoteText? initialText,
    int? draftBaseVersion,
    bool clearDraftBaseVersion = false,
    bool? isLoading,
    String? loadError,
    bool clearLoadError = false,
    bool? offline,
    NoteConflict? conflict,
    bool clearConflict = false,
    LocalDraft? backup,
    bool clearBackup = false,
    bool? recoveredLocalDraft,
  }) {
    return NotesState(
      accountId: accountId ?? this.accountId,
      bundle: bundle ?? this.bundle,
      initialText: initialText ?? this.initialText,
      draftBaseVersion: clearDraftBaseVersion
          ? null
          : (draftBaseVersion ?? this.draftBaseVersion),
      isLoading: isLoading ?? this.isLoading,
      loadError: clearLoadError ? null : (loadError ?? this.loadError),
      offline: offline ?? this.offline,
      conflict: clearConflict ? null : (conflict ?? this.conflict),
      backup: clearBackup ? null : (backup ?? this.backup),
      recoveredLocalDraft: recoveredLocalDraft ?? this.recoveredLocalDraft,
    );
  }
}

class NoteActionResult {
  const NoteActionResult._({
    required this.ok,
    required this.message,
    this.conflict = false,
  });

  factory NoteActionResult.success(String message) =>
      NoteActionResult._(ok: true, message: message);

  factory NoteActionResult.failure(String message, {bool conflict = false}) =>
      NoteActionResult._(ok: false, message: message, conflict: conflict);

  final bool ok;
  final String message;
  final bool conflict;
}

class NotesController extends Notifier<NotesState> {
  NotesController(this.sessionId);

  final String sessionId;
  String _accountId = '';
  bool _disposed = false;
  bool _writeStarted = false;
  String? _draftSignature;
  String? _draftMutationId;
  String? _publishSignature;
  String? _publishMutationId;

  NotesRepository get _repository => ref.read(notesRepositoryProvider);
  DraftStore get _drafts => ref.read(draftStoreProvider);

  @override
  NotesState build() {
    _disposed = false;
    _writeStarted = false;
    _accountId = ref.watch(currentTeacherIdProvider) ?? '';
    ref.onDispose(() => _disposed = true);
    Future<void>.microtask(_load);
    return NotesState(accountId: _accountId);
  }

  Future<void> _load() async {
    final local = _drafts.read(_accountId, sessionId);
    final backup = _drafts.readBackup(_accountId, sessionId);

    // A local draft is usable immediately.  Waiting for a dead network must
    // never be a prerequisite for opening text the teacher already has.
    if (local != null) {
      _set(
        NotesState(
          accountId: _accountId,
          initialText: local.text,
          draftBaseVersion: local.baseVersion,
          isLoading: false,
          backup: backup,
          recoveredLocalDraft: true,
        ),
      );
    }

    try {
      final bundle = await _repository.fetchNotes(sessionId);
      if (_writeStarted) {
        return;
      }
      final serverText = bundle.mine?.draft ?? const NoteText();
      if (local != null) {
        if (local.text.sameAs(serverText)) {
          await _drafts.clear(_accountId, sessionId);
          _set(
            NotesState(
              accountId: _accountId,
              bundle: bundle,
              initialText: serverText,
              isLoading: false,
              backup: backup,
            ),
          );
        } else {
          _set(
            NotesState(
              accountId: _accountId,
              bundle: bundle,
              initialText: local.text,
              draftBaseVersion: local.baseVersion,
              isLoading: false,
              backup: backup,
              recoveredLocalDraft: true,
            ),
          );
        }
        return;
      }
      _set(
        NotesState(
          accountId: _accountId,
          bundle: bundle,
          initialText: serverText,
          isLoading: false,
          backup: backup,
        ),
      );
    } catch (error) {
      if (local != null) {
        _set(
          state.copyWith(
            loadError: _message(error),
            offline: true,
            isLoading: false,
          ),
        );
        return;
      }
      _set(
        NotesState(
          accountId: _accountId,
          isLoading: false,
          loadError: _message(error),
        ),
      );
    }
  }

  Future<void> retryLoad() async {
    _writeStarted = false;
    _set(NotesState(accountId: _accountId));
    await _load();
  }

  Future<void> saveLocal(NoteText text) async {
    await _drafts.save(
      _accountId,
      sessionId,
      text,
      baseVersion: state.effectiveBaseVersion,
    );
  }

  Future<NoteActionResult> saveDraft(NoteText text, {int? baseVersion}) async {
    _writeStarted = true;
    final base = baseVersion ?? state.effectiveBaseVersion;
    final mutationId = _mutationIdForDraft(text, base);
    try {
      final mine = await _repository.saveDraft(
        sessionId: sessionId,
        text: text,
        baseVersion: base,
        mutationId: mutationId,
      );
      await _drafts.clearIfMatches(_accountId, sessionId, text);
      await _drafts.clearBackup(_accountId, sessionId);
      _set(
        state.copyWith(
          bundle:
              state.bundle?.withMine(mine) ??
              NotesBundle(mine: mine, others: const [], previous: null),
          draftBaseVersion: mine.version,
          clearConflict: true,
          clearBackup: true,
          clearLoadError: true,
          offline: false,
          isLoading: false,
          recoveredLocalDraft: false,
        ),
      );
      return NoteActionResult.success('草稿已保存。');
    } on NoteConflictException catch (error) {
      await _preserveLocal(text, base);
      _set(
        state.copyWith(
          conflict: NoteConflict(
            message: error.message,
            current: error.current,
          ),
          isLoading: false,
        ),
      );
      return NoteActionResult.failure(error.message, conflict: true);
    } catch (error) {
      await _preserveLocal(text, base);
      return NoteActionResult.failure(_message(error));
    }
  }

  Future<NoteActionResult> retryConflict(NoteText text) async {
    final serverVersion = state.conflict?.current?.version;
    return saveDraft(text, baseVersion: serverVersion ?? 0);
  }

  Future<NoteActionResult> publish(NoteText text) async {
    _writeStarted = true;
    var mine = state.mine;
    try {
      if (mine == null || !mine.draft.sameAs(text)) {
        final base = state.effectiveBaseVersion;
        mine = await _repository.saveDraft(
          sessionId: sessionId,
          text: text,
          baseVersion: base,
          mutationId: _mutationIdForDraft(text, base),
        );
        _set(
          state.copyWith(
            bundle:
                state.bundle?.withMine(mine) ??
                NotesBundle(mine: mine, others: const [], previous: null),
            draftBaseVersion: mine.version,
            clearConflict: true,
          ),
        );
      }
      if (mine.draft.isBlank) {
        return NoteActionResult.failure('请填写备注');
      }
      final published = await _repository.publish(
        sessionId: sessionId,
        baseVersion: mine.version,
        mutationId: _mutationIdForPublish(mine.version),
      );
      await _drafts.clearIfMatches(_accountId, sessionId, text);
      await _drafts.clearBackup(_accountId, sessionId);
      _set(
        state.copyWith(
          bundle:
              state.bundle?.withMine(published) ??
              NotesBundle(mine: published, others: const [], previous: null),
          draftBaseVersion: published.version,
          clearConflict: true,
          clearBackup: true,
          clearLoadError: true,
          offline: false,
          isLoading: false,
          recoveredLocalDraft: false,
        ),
      );
      return NoteActionResult.success('已共享给同课老师。');
    } on NoteConflictException catch (error) {
      await _preserveLocal(text, mine?.version ?? state.effectiveBaseVersion);
      final savedMine = mine;
      _set(
        state.copyWith(
          bundle: savedMine == null
              ? null
              : (state.bundle?.withMine(savedMine) ??
                    NotesBundle(
                      mine: savedMine,
                      others: const [],
                      previous: null,
                    )),
          conflict: NoteConflict(
            message: error.message,
            current: error.current,
          ),
          isLoading: false,
        ),
      );
      return NoteActionResult.failure(error.message, conflict: true);
    } catch (error) {
      await _preserveLocal(text, mine?.version ?? state.effectiveBaseVersion);
      return NoteActionResult.failure(_message(error));
    }
  }

  Future<NoteText?> adoptServerVersion(NoteText localText) async {
    final current = state.conflict?.current;
    if (current == null) {
      return null;
    }
    await _drafts.saveBackup(_accountId, sessionId, localText, current.version);
    await _drafts.clear(_accountId, sessionId);
    final backup = _drafts.readBackup(_accountId, sessionId);
    _set(
      state.copyWith(
        bundle:
            state.bundle?.withMine(current) ??
            NotesBundle(mine: current, others: const [], previous: null),
        initialText: current.draft,
        draftBaseVersion: current.version,
        clearConflict: true,
        backup: backup,
        isLoading: false,
        recoveredLocalDraft: false,
      ),
    );
    return current.draft;
  }

  NoteText? restoreBackup() => state.backup?.text;

  void clearConflict() {
    _set(state.copyWith(clearConflict: true));
  }

  Future<void> _preserveLocal(NoteText text, int baseVersion) async {
    await _drafts.save(_accountId, sessionId, text, baseVersion: baseVersion);
    final backup = _drafts.readBackup(_accountId, sessionId);
    if (backup != null) {
      _set(state.copyWith(backup: backup));
    }
  }

  String _mutationIdForDraft(NoteText text, int baseVersion) {
    final signature =
        '$baseVersion|${text.covered}\u0000'
        '${text.unfinished}\u0000${text.nextPlan}';
    if (_draftSignature == signature && _draftMutationId != null) {
      return _draftMutationId!;
    }
    _draftSignature = signature;
    _draftMutationId = newMutationId();
    return _draftMutationId!;
  }

  String _mutationIdForPublish(int baseVersion) {
    final signature = 'publish|$baseVersion';
    if (_publishSignature == signature && _publishMutationId != null) {
      return _publishMutationId!;
    }
    _publishSignature = signature;
    _publishMutationId = newMutationId();
    return _publishMutationId!;
  }

  void _set(NotesState next) {
    if (!_disposed) {
      state = next;
    }
  }

  String _message(Object error) {
    final message = error is ApiException ? error.message : '';
    if (message.trim().isEmpty) {
      return '暂时无法连接，请稍后重试。';
    }
    return message;
  }
}

final notesControllerProvider = NotifierProvider.autoDispose
    .family<NotesController, NotesState, String>(NotesController.new);
