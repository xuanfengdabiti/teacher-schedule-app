import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:teacher_schedule/core/config/api_config.dart';
import 'package:teacher_schedule/core/storage/settings_repository.dart';

void main() {
  test('编译配置地址被标准化后作为默认值', () {
    expect(
      ApiConfig.resolveDefaultBaseUrl(
        configured: '38.76.172.48:18000',
        platform: TargetPlatform.android,
      ),
      'http://38.76.172.48:18000/api/v1/',
    );
    expect(
      ApiConfig.resolveDefaultBaseUrl(
        configured: ' http://38.76.172.48:18000/api/v1/ ',
        platform: TargetPlatform.iOS,
      ),
      'http://38.76.172.48:18000/api/v1/',
    );
  });

  test('未配置编译地址时保留平台默认', () {
    expect(
      ApiConfig.resolveDefaultBaseUrl(
        configured: '  ',
        platform: TargetPlatform.android,
      ),
      ApiConfig.androidEmulatorBaseUrl,
    );
    expect(
      ApiConfig.resolveDefaultBaseUrl(
        configured: '',
        platform: TargetPlatform.iOS,
      ),
      ApiConfig.localhostBaseUrl,
    );
  });

  test('已保存的手动地址优先于编译配置', () async {
    SharedPreferences.setMockInitialValues({
      'api_base_url': 'http://manual.example:18000/api/v1/',
    });
    final prefs = await SharedPreferences.getInstance();
    final settings = SettingsRepository(prefs);
    expect(settings.apiBaseUrl, 'http://manual.example:18000/api/v1/');

    SharedPreferences.setMockInitialValues({});
    final emptyPrefs = await SharedPreferences.getInstance();
    expect(
      SettingsRepository(emptyPrefs).apiBaseUrl,
      ApiConfig.defaultBaseUrl(),
    );
  });
}
