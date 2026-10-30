import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import 'api_config.dart';

/// Old and new production endpoints for the one-time Beijing move.
const oldHongKongApiBaseUrl = 'http://38.76.172.48:18000/api/v1/';
const beijingApiBaseUrl = 'http://101.201.48.132/api/v1/';

const _baseUrlKey = 'api_base_url';
const _scopedKeyPrefixes = ['font_size_v1_', 'demo_import_done_v1_'];

/// Moves a stored Hong Kong endpoint and its URL-scoped caches to Beijing.
///
/// Runs only when the compiled default really is the Beijing endpoint. Custom
/// or localhost addresses are never touched, old keys are kept, and existing
/// Beijing cache keys win over migrated ones.
Future<void> migrateBeijingEndpoint(
  SharedPreferences prefs, {
  String? newDefault,
}) async {
  final target = ApiConfig.normalizeBaseUrl(
    newDefault ?? ApiConfig.defaultBaseUrl(),
  );
  if (target != beijingApiBaseUrl) {
    return;
  }

  final stored = prefs.getString(_baseUrlKey);
  if (stored != null && stored.trim().isNotEmpty) {
    String? normalized;
    try {
      normalized = ApiConfig.normalizeBaseUrl(stored);
    } on FormatException {
      normalized = null;
    }
    if (normalized?.toLowerCase() == oldHongKongApiBaseUrl) {
      await prefs.setString(_baseUrlKey, target);
    }
  }

  for (final key in prefs.getKeys().toList(growable: false)) {
    final prefix = _scopedKeyPrefixes
        .where((candidate) => key.startsWith(candidate))
        .firstOrNull;
    if (prefix == null) {
      continue;
    }
    final decoded = _decode(key.substring(prefix.length));
    if (decoded == null) {
      continue;
    }
    final separator = decoded.indexOf('|');
    if (separator <= 0) {
      continue;
    }
    if (decoded.substring(0, separator) != oldHongKongApiBaseUrl) {
      continue;
    }
    final newKey =
        '$prefix${_encode('$target${decoded.substring(separator)}')}';
    if (prefs.containsKey(newKey)) {
      continue;
    }
    final value = prefs.get(key);
    if (value is String) {
      await prefs.setString(newKey, value);
    } else if (value is bool) {
      await prefs.setBool(newKey, value);
    }
  }
}

String _encode(String raw) =>
    base64Url.encode(utf8.encode(raw)).replaceAll('=', '');

String? _decode(String encoded) {
  try {
    final padded = encoded.padRight((encoded.length + 3) & ~3, '=');
    return utf8.decode(base64Url.decode(padded));
  } on FormatException {
    return null;
  }
}
