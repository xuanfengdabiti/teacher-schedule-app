import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:teacher_schedule/core/config/api_config.dart';
import 'package:teacher_schedule/core/config/beijing_endpoint_migration.dart';
import 'package:teacher_schedule/core/storage/import_record_store.dart';
import 'package:teacher_schedule/core/storage/preferences_store.dart';
import 'package:teacher_schedule/core/storage/settings_repository.dart';
import 'package:teacher_schedule/core/storage/token_store.dart';

String _scopedKey(String prefix, String baseUrl, String suffix) {
  final raw = '$baseUrl|$suffix';
  return '$prefix${base64Url.encode(utf8.encode(raw)).replaceAll('=', '')}';
}

Future<SharedPreferences> _prefs(Map<String, Object> values) async {
  SharedPreferences.setMockInitialValues(values);
  return SharedPreferences.getInstance();
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('旧香港地址和URL缓存升级，草稿与登录材料保留', () async {
    final tokens = MemoryTokenStore();
    await tokens.save(access: 'access-1', refresh: 'refresh-1');
    final oldFont = _scopedKey('font_size_v1_', oldHongKongApiBaseUrl, 't1');
    final oldImport = _scopedKey(
      'demo_import_done_v1_',
      oldHongKongApiBaseUrl,
      't1|term-1',
    );
    final customFont = _scopedKey(
      'font_size_v1_',
      'http://192.168.1.20:18000/api/v1/',
      't2',
    );
    final draftKey = 'teaching_note_draft_t1_session-1';
    final backupKey = 'teaching_note_backup_t1_session-1';
    final prefs = await _prefs({
      'api_base_url': 'http://38.76.172.48:18000/api/v1',
      oldFont: 'large',
      oldImport: true,
      customFont: 'small',
      draftKey: '未提交的离线备注',
      backupKey: '离线备注备份',
    });

    await migrateBeijingEndpoint(prefs, newDefault: beijingApiBaseUrl);

    expect(SettingsRepository(prefs).apiBaseUrl, beijingApiBaseUrl);
    expect(
      PreferencesStore(prefs)
          .readFontSize(baseUrl: beijingApiBaseUrl, accountId: 't1'),
      'large',
    );
    expect(
      ImportRecordStore(
        prefs,
      ).isDone(baseUrl: beijingApiBaseUrl, accountId: 't1', termId: 'term-1'),
      isTrue,
    );
    // Old URL-scoped data is copied, not removed.
    expect(prefs.getString(oldFont), 'large');
    expect(prefs.getBool(oldImport), isTrue);
    // Unrelated custom-address caches and offline drafts are untouched.
    expect(prefs.getString(customFont), 'small');
    expect(prefs.getString(draftKey), '未提交的离线备注');
    expect(prefs.getString(backupKey), '离线备注备份');
    expect(
      prefs.getKeys().where((key) => key.startsWith('font_size_v1_')).length,
      3,
      reason: '旧香港、自定义地址和新增北京 key 都在，不删除旧 key',
    );
    expect(
      PreferencesStore(prefs)
          .readFontSize(baseUrl: beijingApiBaseUrl, accountId: 't2'),
      isNull,
      reason: '其他地址的缓存不能复制到北京',
    );
    expect(await tokens.readAccess(), 'access-1');
    expect(await tokens.readRefresh(), 'refresh-1');
  });

  test('已存在的北京缓存优先，旧香港缓存不覆盖', () async {
    final oldFont = _scopedKey('font_size_v1_', oldHongKongApiBaseUrl, 't1');
    final newFont = _scopedKey('font_size_v1_', beijingApiBaseUrl, 't1');
    final oldImport = _scopedKey(
      'demo_import_done_v1_',
      oldHongKongApiBaseUrl,
      't1|term-1',
    );
    final newImport = _scopedKey(
      'demo_import_done_v1_',
      beijingApiBaseUrl,
      't1|term-1',
    );
    final prefs = await _prefs({
      'api_base_url': oldHongKongApiBaseUrl,
      oldFont: 'large',
      newFont: 'small',
      oldImport: true,
      newImport: false,
    });

    await migrateBeijingEndpoint(prefs, newDefault: beijingApiBaseUrl);

    expect(prefs.getString(newFont), 'small');
    expect(prefs.getBool(newImport), isFalse);
    expect(prefs.getString(oldFont), 'large');
    expect(prefs.getBool(oldImport), isTrue);
    expect(
      PreferencesStore(prefs)
          .readFontSize(baseUrl: beijingApiBaseUrl, accountId: 't1'),
      'small',
    );
  });

  test('重复运行幂等，不重复写入', () async {
    final oldFont = _scopedKey('font_size_v1_', oldHongKongApiBaseUrl, 't1');
    final prefs = await _prefs({
      'api_base_url': oldHongKongApiBaseUrl,
      oldFont: 'large',
    });

    await migrateBeijingEndpoint(prefs, newDefault: beijingApiBaseUrl);
    final keysAfterFirst = prefs.getKeys().toSet();
    await migrateBeijingEndpoint(prefs, newDefault: beijingApiBaseUrl);

    expect(prefs.getKeys().toSet(), keysAfterFirst);
    expect(prefs.getString(oldFont), 'large');
    expect(SettingsRepository(prefs).apiBaseUrl, beijingApiBaseUrl);
  });

  test('自定义与localhost地址保持，不切换', () async {
    final custom = await _prefs({
      'api_base_url': 'http://192.168.1.20:18000/api/v1/',
    });
    await migrateBeijingEndpoint(custom, newDefault: beijingApiBaseUrl);
    expect(
      SettingsRepository(custom).apiBaseUrl,
      'http://192.168.1.20:18000/api/v1/',
    );

    final localhost = await _prefs({
      'api_base_url': 'http://127.0.0.1:18000/api/v1/',
    });
    await migrateBeijingEndpoint(localhost, newDefault: beijingApiBaseUrl);
    expect(
      SettingsRepository(localhost).apiBaseUrl,
      'http://127.0.0.1:18000/api/v1/',
    );
  });

  test('没有保存地址时不写入，依赖新的编译默认并迁移旧缓存', () async {
    final oldFont = _scopedKey('font_size_v1_', oldHongKongApiBaseUrl, 't1');
    final prefs = await _prefs({oldFont: 'large'});

    await migrateBeijingEndpoint(prefs, newDefault: beijingApiBaseUrl);

    expect(prefs.containsKey('api_base_url'), isFalse);
    expect(
      PreferencesStore(prefs)
          .readFontSize(baseUrl: beijingApiBaseUrl, accountId: 't1'),
      'large',
    );
  });

  test('编译目标不是北京时不迁移地址和缓存', () async {
    final oldFont = _scopedKey('font_size_v1_', oldHongKongApiBaseUrl, 't1');
    final prefs = await _prefs({
      'api_base_url': oldHongKongApiBaseUrl,
      oldFont: 'large',
    });

    await migrateBeijingEndpoint(prefs, newDefault: ApiConfig.localhostBaseUrl);
    await migrateBeijingEndpoint(
      prefs,
      newDefault: 'http://101.201.48.132:18000/api/v1/',
    );

    expect(SettingsRepository(prefs).apiBaseUrl, oldHongKongApiBaseUrl);
    expect(
      PreferencesStore(prefs)
          .readFontSize(baseUrl: beijingApiBaseUrl, accountId: 't1'),
      isNull,
    );
  });
}
