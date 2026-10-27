import 'package:shared_preferences/shared_preferences.dart';

import '../config/api_config.dart';

class SettingsRepository {
  SettingsRepository(this._prefs);

  static const _baseUrlKey = 'api_base_url';
  final SharedPreferences _prefs;

  String get apiBaseUrl =>
      _prefs.getString(_baseUrlKey) ?? ApiConfig.defaultBaseUrl();

  Future<void> setApiBaseUrl(String value) async {
    await _prefs.setString(_baseUrlKey, ApiConfig.normalizeBaseUrl(value));
  }
}
