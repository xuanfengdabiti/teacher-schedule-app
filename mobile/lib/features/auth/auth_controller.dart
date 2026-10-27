import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/config/api_config.dart';
import '../../core/network/api_exception.dart';
import '../../core/providers.dart';
import '../../core/storage/settings_repository.dart';
import '../../core/storage/token_store.dart';
import 'auth_repository.dart';
import 'me.dart';

enum SessionPhase { loading, signedOut, active, restricted, error }

class SessionState {
  const SessionState({required this.phase, this.me, this.errorMessage});

  const SessionState.loading() : this(phase: SessionPhase.loading);

  const SessionState.signedOut() : this(phase: SessionPhase.signedOut);

  const SessionState.error(String message)
    : this(phase: SessionPhase.error, errorMessage: message);

  final SessionPhase phase;
  final Me? me;
  final String? errorMessage;

  bool get canUseApp =>
      phase == SessionPhase.active && (me?.canUseTeacherFeatures ?? false);

  List<String> get restrictions {
    final account = me;
    if (account == null) {
      return const ['账号资料尚未加载。'];
    }
    return [
      if (!account.emailVerified) '邮箱尚未验证：请先打开验证邮件完成验证。',
      if (account.teacherStatus == 'pending') '教师身份待审核：管理员通过后即可查看课表。',
      if (account.teacherStatus == 'disabled') '教师档案已停用：请联系学校教务处理。',
      if (account.teacherStatus == 'unlinked')
        '账号尚未关联教师档案：请使用学校登记的邮箱，或联系教务完成关联。',
    ];
  }
}

class AuthController extends Notifier<SessionState> {
  bool _disposed = false;

  @override
  SessionState build() {
    _disposed = false;
    final bus = ref.watch(sessionExpiryProvider);
    void handleExpired() {
      if (!_disposed) {
        state = const SessionState.signedOut();
      }
    }

    bus.addListener(handleExpired);
    ref.onDispose(() {
      _disposed = true;
      bus.removeListener(handleExpired);
    });
    Future<void>.microtask(_restore);
    return const SessionState.loading();
  }

  SettingsRepository get _settings => ref.read(settingsRepositoryProvider);
  TokenStore get _tokens => ref.read(tokenStoreProvider);
  AuthRepository get _repository => ref.read(authRepositoryProvider);

  Future<void> _restore() async {
    final access = await _tokens.readAccess();
    final refresh = await _tokens.readRefresh();
    if ((access == null || access.isEmpty) &&
        (refresh == null || refresh.isEmpty)) {
      _set(const SessionState.signedOut());
      return;
    }
    try {
      ref.read(apiClientProvider).baseUrl = ApiConfig.normalizeBaseUrl(
        _settings.apiBaseUrl,
      );
      final me = await _repository.me();
      _apply(me);
    } on ApiException catch (error) {
      if (error.isUnauthorized) {
        await _tokens.clear();
        _set(const SessionState.signedOut());
      } else {
        _set(SessionState.error(error.message));
      }
    } catch (error) {
      _set(SessionState.error(ApiException.fromUnknown(error).message));
    }
  }

  Future<void> retryRestore() async {
    _set(const SessionState.loading());
    await _restore();
  }

  Future<void> login({
    required String baseUrl,
    required String email,
    required String password,
  }) async {
    await _settings.setApiBaseUrl(baseUrl);
    final normalized = ApiConfig.normalizeBaseUrl(baseUrl);
    ref.read(apiClientProvider).baseUrl = normalized;
    await _tokens.clear();
    try {
      final tokens = await _repository.login(email: email, password: password);
      await _tokens.save(access: tokens.access, refresh: tokens.refresh);
      final me = await _repository.me();
      _apply(me);
    } catch (_) {
      await _tokens.clear();
      _set(const SessionState.signedOut());
      rethrow;
    }
  }

  Future<void> refreshMe() async {
    try {
      final me = await _repository.me();
      _apply(me);
    } on ApiException catch (error) {
      if (error.isUnauthorized) {
        await _tokens.clear();
        _set(const SessionState.signedOut());
        return;
      }
      rethrow;
    }
  }

  Future<void> logout() async {
    final refresh = await _tokens.readRefresh();
    _set(const SessionState.signedOut());
    try {
      await _repository.logout(refresh).timeout(const Duration(seconds: 4));
    } catch (_) {
      // Local sign-out must still succeed when the server is unreachable.
    } finally {
      await _tokens.clear();
    }
  }

  void _apply(Me me) {
    _set(
      SessionState(
        phase: me.canUseTeacherFeatures
            ? SessionPhase.active
            : SessionPhase.restricted,
        me: me,
      ),
    );
  }

  void _set(SessionState next) {
    if (!_disposed) {
      state = next;
    }
  }
}

final authControllerProvider = NotifierProvider<AuthController, SessionState>(
  AuthController.new,
);

final currentTeacherIdProvider = Provider<String?>(
  (ref) =>
      ref.watch(authControllerProvider.select((session) => session.me?.id)),
);
