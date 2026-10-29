import 'package:flutter/foundation.dart';

class ApiConfig {
  static const String androidEmulatorBaseUrl = 'http://10.0.2.2:18000/api/v1/';
  static const String localhostBaseUrl = 'http://127.0.0.1:18000/api/v1/';

  /// Set at build time, e.g.
  /// `flutter build apk --dart-define=API_BASE_URL=http://host:18000/api/v1/`.
  static const String compiledBaseUrl = String.fromEnvironment('API_BASE_URL');

  static String defaultBaseUrl() => resolveDefaultBaseUrl();

  /// Pure resolver so build-time configuration and platform fallback can be
  /// verified without changing the running platform.
  static String resolveDefaultBaseUrl({
    String configured = compiledBaseUrl,
    TargetPlatform? platform,
  }) {
    final raw = configured.trim();
    if (raw.isNotEmpty) {
      return normalizeBaseUrl(raw);
    }
    final target = platform ?? defaultTargetPlatform;
    if (target == TargetPlatform.android) {
      return androidEmulatorBaseUrl;
    }
    return localhostBaseUrl;
  }

  /// Accepts a host-only value or a full API address and returns a Dio-safe
  /// base URL ending in `/`.
  static String normalizeBaseUrl(String raw) {
    var value = raw.trim();
    if (value.isEmpty) {
      throw const FormatException('请输入服务地址。');
    }
    if (!RegExp(r'^https?://', caseSensitive: false).hasMatch(value)) {
      value = 'http://$value';
    }
    value = value.replaceAll(RegExp(r'/+$'), '');
    final uri = Uri.tryParse(value);
    if (uri == null || uri.host.isEmpty) {
      throw const FormatException(
        '服务地址格式无效，例如：http://192.168.1.10:18000/api/v1',
      );
    }
    if (uri.path.isEmpty || uri.path == '/') {
      value = '$value/api/v1';
    }
    return '$value/';
  }
}
