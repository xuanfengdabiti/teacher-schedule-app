import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/network/api_client.dart';
import '../../core/network/api_exception.dart';
import '../../core/providers.dart';

/// Thin client for the public account-entry endpoints.
///
/// The pages call these methods only; no extra networking layer is introduced.
class AccountAccessRepository {
  AccountAccessRepository(this._api);

  final ApiClient _api;

  Future<void> register({
    required String formalName,
    required String teacherNo,
    required String email,
    required String password1,
    required String password2,
  }) {
    return _post('auth/registration/', {
      'formal_name': formalName.trim(),
      'teacher_no': teacherNo.trim(),
      'email': email.trim(),
      'password1': password1,
      'password2': password2,
    });
  }

  Future<void> verifyEmail(String key) {
    return _post('auth/registration/verify-email/', {'key': key.trim()});
  }

  Future<void> resendVerification(String email) {
    return _post('auth/registration/resend-email/', {'email': email.trim()});
  }

  Future<void> requestPasswordReset(String email) {
    return _post('auth/password/reset/', {'email': email.trim()});
  }

  Future<void> confirmPasswordReset({
    required String uid,
    required String token,
    required String password1,
    required String password2,
  }) {
    return _post('auth/password/reset/confirm/', {
      'uid': uid.trim(),
      'token': token.trim(),
      'new_password1': password1,
      'new_password2': password2,
    });
  }

  Future<void> _post(String path, Map<String, dynamic> data) async {
    try {
      await _api.post(path, data: data);
    } on ApiException {
      rethrow;
    } catch (error) {
      throw ApiException.fromUnknown(error);
    }
  }
}

/// Extracted values from a pasted App link, or from bare `key` / `uid,token`.
///
/// Invalid percent escapes simply yield empty values; callers show one short
/// "copy the link again" message instead of throwing.
class AccountLinkValues {
  const AccountLinkValues({this.key, this.uid, this.token});

  final String? key;
  final String? uid;
  final String? token;
}

String? _decode(Object? value) {
  if (value == null) {
    return null;
  }
  try {
    return Uri.decodeComponent(value.toString());
  } on FormatException {
    return null;
  } on ArgumentError {
    return null;
  }
}

AccountLinkValues parseVerifyLink(String input) {
  final text = input.trim();
  if (text.isEmpty) {
    return const AccountLinkValues();
  }
  try {
    final uri = Uri.tryParse(text);
    if (uri != null && uri.scheme.startsWith('teacherschedule')) {
      return AccountLinkValues(key: _decode(uri.queryParameters['key']));
    }
    // Allow "key=..." as well as the bare key.
    if (text.contains('key=')) {
      final part = text.split('key=').last.split('&').first;
      return AccountLinkValues(key: _decode(part));
    }
    // A bare key still has to be decodable; broken escapes mean a broken paste.
    return AccountLinkValues(key: _decode(text));
  } on FormatException {
    return const AccountLinkValues();
  } on ArgumentError {
    return const AccountLinkValues();
  }
}

AccountLinkValues parseResetLink(String input) {
  final text = input.trim();
  if (text.isEmpty) {
    return const AccountLinkValues();
  }
  try {
    final uri = Uri.tryParse(text);
    if (uri != null && uri.scheme.startsWith('teacherschedule')) {
      return AccountLinkValues(
        uid: _decode(uri.queryParameters['uid']),
        token: _decode(uri.queryParameters['token']),
      );
    }
    final params = Uri.splitQueryString(text.replaceFirst('?', ''));
    if (params['uid'] != null || params['token'] != null) {
      return AccountLinkValues(
        uid: _decode(params['uid']),
        token: _decode(params['token']),
      );
    }
    return const AccountLinkValues();
  } on FormatException {
    return const AccountLinkValues();
  } on ArgumentError {
    return const AccountLinkValues();
  }
}

final accountAccessRepositoryProvider = Provider<AccountAccessRepository>(
  (ref) => AccountAccessRepository(ref.watch(apiClientProvider)),
);
