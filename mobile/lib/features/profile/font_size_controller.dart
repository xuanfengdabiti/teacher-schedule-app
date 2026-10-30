import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/config/api_config.dart';
import '../../core/network/api_exception.dart';
import '../../core/providers.dart';
import '../auth/auth_controller.dart';
import '../auth/auth_repository.dart';

const fontSizeOptions = <String, String>{
  'system': '跟随系统',
  'large': '大',
  'extra_large': '更大',
};

/// Extra text scale applied on top of the system setting.
double textScaleForFontSize(String value) => switch (value) {
  'large' => 1.15,
  'extra_large' => 1.3,
  _ => 1.0,
};

typedef _Scope = ({String baseUrl, String accountId});

/// Current account font-size preference.
///
/// Reads and writes share one monotonically increasing sequence number and
/// capture the normalized API address plus account id when they start. A late
/// response therefore cannot overwrite a newer choice, and cannot apply to or
/// cache into a different account after a switch.
class FontSizeController extends Notifier<String> {
  int _seq = 0;
  int _lastWriteSeq = 0;
  int _lastReadSeq = 0;
  bool _disposed = false;

  AuthRepository get _repository => ref.read(authRepositoryProvider);

  _Scope? get _scope {
    final accountId = ref.read(authControllerProvider).me?.id;
    if (accountId == null || accountId.isEmpty) {
      return null;
    }
    return (
      baseUrl: ApiConfig.normalizeBaseUrl(
        ref.read(settingsRepositoryProvider).apiBaseUrl,
      ),
      accountId: accountId,
    );
  }

  bool _isCurrent(_Scope scope) {
    final current = _scope;
    return current != null &&
        current.baseUrl == scope.baseUrl &&
        current.accountId == scope.accountId;
  }

  @override
  String build() {
    _disposed = false;
    // Never reset the monotonic sequence. Bumping it here invalidates every
    // request started for the previous account (A→B→A cannot collide).
    final generation = ++_seq;
    _lastWriteSeq = generation;
    _lastReadSeq = generation;
    final accountId = ref.watch(currentTeacherIdProvider);
    ref.onDispose(() => _disposed = true);
    if (accountId == null || accountId.isEmpty) {
      return 'system';
    }
    final scope = _scope;
    if (scope == null || scope.accountId != accountId) {
      return 'system';
    }
    final local = ref
        .read(preferencesStoreProvider)
        .readFontSize(baseUrl: scope.baseUrl, accountId: accountId);
    _refreshFromServer(scope);
    return local ?? 'system';
  }

  /// Server read. Ignored when a newer write or read started, or the account
  /// changed while the request was in flight.
  Future<void> _refreshFromServer(_Scope scope) async {
    final id = ++_seq;
    _lastReadSeq = id;
    try {
      final value = await _repository.fetchFontSize();
      if (_disposed ||
          id < _lastWriteSeq ||
          id != _lastReadSeq ||
          !_isCurrent(scope)) {
        return;
      }
      state = value;
      await ref
          .read(preferencesStoreProvider)
          .writeFontSize(
            baseUrl: scope.baseUrl,
            accountId: scope.accountId,
            value: value,
          );
    } catch (_) {
      // Non-blocking: the schedule must remain usable without preferences.
    }
  }

  /// Returns an error message, or null once the choice was applied.
  Future<String?> save(String value) async {
    final scope = _scope;
    if (scope == null) {
      return null;
    }
    final id = ++_seq;
    _lastWriteSeq = id;
    // Any read started before this write is stale now.
    _lastReadSeq = id;
    try {
      await _repository.updateFontSize(value);
    } on ApiException catch (error) {
      return error.message;
    } catch (error) {
      return ApiException.fromUnknown(error).message;
    }
    // A newer write or an account switch wins; the stale response is dropped.
    if (_disposed || id != _lastWriteSeq || !_isCurrent(scope)) {
      return null;
    }
    state = value;
    await ref
        .read(preferencesStoreProvider)
        .writeFontSize(
          baseUrl: scope.baseUrl,
          accountId: scope.accountId,
          value: value,
        );
    return null;
  }
}

final fontSizeControllerProvider = NotifierProvider<FontSizeController, String>(
  FontSizeController.new,
);
