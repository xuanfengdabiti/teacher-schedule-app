import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import '../config/api_config.dart';

/// Local memory for the account's font-size choice.
///
/// The server preference remains the source of truth; this only lets the app
/// apply a known value immediately on the next sign-in. Scoped by normalized
/// API address and account id, so switching accounts never reuses a value.
class PreferencesStore {
  PreferencesStore(this._prefs);

  static const _keyPrefix = 'font_size_v1_';

  final SharedPreferences _prefs;

  String _key(String baseUrl, String accountId) {
    final raw = '${ApiConfig.normalizeBaseUrl(baseUrl)}|$accountId';
    final encoded = base64Url.encode(utf8.encode(raw)).replaceAll('=', '');
    return '$_keyPrefix$encoded';
  }

  String? readFontSize({required String baseUrl, required String accountId}) {
    return _prefs.getString(_key(baseUrl, accountId));
  }

  Future<void> writeFontSize({
    required String baseUrl,
    required String accountId,
    required String value,
  }) {
    return _prefs.setString(_key(baseUrl, accountId), value);
  }
}
