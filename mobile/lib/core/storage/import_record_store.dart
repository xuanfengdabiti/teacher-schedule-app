import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import '../config/api_config.dart';

/// Small local marker for the demo first-import presentation.
///
/// The record is scoped by normalized API address, account id and term id.
/// It intentionally survives logout; it is not an offline schedule cache.
class ImportRecordStore {
  ImportRecordStore(this._prefs);

  static const _keyPrefix = 'demo_import_done_v1_';

  final SharedPreferences _prefs;

  String _recordKey(String baseUrl, String accountId, String termId) {
    final normalized = ApiConfig.normalizeBaseUrl(baseUrl);
    final raw = '$normalized|$accountId|$termId';
    final encoded = base64Url.encode(utf8.encode(raw)).replaceAll('=', '');
    return '$_keyPrefix$encoded';
  }

  bool isDone({
    required String baseUrl,
    required String accountId,
    required String termId,
  }) {
    return _prefs.getBool(_recordKey(baseUrl, accountId, termId)) ?? false;
  }

  Future<bool> markDone({
    required String baseUrl,
    required String accountId,
    required String termId,
  }) {
    return _prefs.setBool(_recordKey(baseUrl, accountId, termId), true);
  }

  Future<void> clear({
    required String baseUrl,
    required String accountId,
    required String termId,
  }) {
    return _prefs.remove(_recordKey(baseUrl, accountId, termId));
  }
}
