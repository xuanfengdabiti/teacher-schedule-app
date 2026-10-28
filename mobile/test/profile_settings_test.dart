import 'dart:async';
import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:teacher_schedule/app.dart';
import 'package:teacher_schedule/core/providers.dart';
import 'package:teacher_schedule/core/storage/token_store.dart';
import 'package:teacher_schedule/features/auth/auth_controller.dart';
import 'package:teacher_schedule/features/auth/me.dart';
import 'package:teacher_schedule/features/profile/font_size_controller.dart';

import 'support/mock_http_adapter.dart';

class _Account {
  _Account({
    required this.id,
    required this.email,
    required this.name,
    required this.teacherNo,
    required this.fontSize,
  });

  final String id;
  final String email;
  String name;
  final String teacherNo;
  String fontSize;
  String phoneVisibility = 'course_teachers';
}

/// Fake backend for the three profile/account features.
class _ProfileServer {
  _ProfileServer() {
    adapter = MockHttpAdapter(_respond);
  }

  late final MockHttpAdapter adapter;
  var _activeId = 'user-2';

  Completer<void>? holdFontRead;
  Completer<void>? holdFontWrite;
  Completer<void>? holdPasswordChange;

  final wang = _Account(
    id: 'user-2',
    email: 'wang@example.invalid',
    name: '王老师',
    teacherNo: 'T001',
    fontSize: 'system',
  );
  final li = _Account(
    id: 'user-3',
    email: 'li@example.invalid',
    name: '李老师',
    teacherNo: 'T002',
    fontSize: 'extra_large',
  );

  int profileSaves = 0;
  int passwordChanges = 0;
  int preferenceReads = 0;
  int preferenceWrites = 0;
  bool failProfileSave = false;
  bool failPasswordChange = false;
  bool failFontWrite = false;
  String lastFontWritePayloadKeys = '';

  _Account get active => _activeId == 'user-3' ? li : wang;

  void activate(String accountId) {
    _activeId = accountId;
  }

  String get baseUrl => 'http://profile.test/api/v1/';

  Map<String, dynamic> _meFor(_Account account) => {
    'id': account.id,
    'email': account.email,
    'email_verified': true,
    'teacher_status': 'active',
    'assignment_scope_version': 4,
    'current_term': {
      'id': 'term-1',
      'code': '2026-1',
      'name': '2025-2026学年第二学期',
      'start_date': '2020-01-01',
      'end_date': '2030-12-31',
    },
    'teacher': {
      'teacher_no': account.teacherNo,
      'formal_name': account.name,
      'display_name': account.name,
      'department': '信息学院',
      'phone': '13800000000',
      'phone_visibility': account.phoneVisibility,
      'wechat': 'demo-wechat',
      'wechat_visibility': 'private',
    },
    'claim': null,
    'avatar_url': null,
  };

  FutureOr<ResponseBody> _respond(RequestOptions options) async {
    final path = options.uri.path;
    if (options.method == 'POST' && path.endsWith('/auth/login/')) {
      final body = Map<String, dynamic>.from(options.data as Map);
      final account = body['email'] == li.email ? li : wang;
      _activeId = account.id;
      return jsonResponse({
        'access': 'access-${account.id}',
        'refresh': 'refresh-${account.id}',
      });
    }
    if (path.endsWith('/me/preferences/')) {
      if (options.method == 'GET') {
        preferenceReads++;
        final pending = holdFontRead;
        if (pending != null) {
          await pending.future;
        }
        return jsonResponse({'font_size': active.fontSize});
      }
      preferenceWrites++;
      final body = Map<String, dynamic>.from(options.data as Map);
      lastFontWritePayloadKeys = body.keys.join(',');
      final pending = holdFontWrite;
      if (pending != null) {
        await pending.future;
      }
      if (failFontWrite) {
        return jsonResponse({'detail': '设置保存失败。'}, 500);
      }
      active.fontSize = body['font_size'] as String;
      return jsonResponse({'font_size': active.fontSize});
    }
    if (path.endsWith('/me/')) {
      if (options.method == 'PATCH') {
        profileSaves++;
        if (failProfileSave) {
          return jsonResponse({'detail': '保存失败，请稍后重试。'}, 500);
        }
        final body = Map<String, dynamic>.from(options.data as Map);
        active.name = body['display_name'] as String;
        active.phoneVisibility = body['phone_visibility'] as String;
        return jsonResponse(_meFor(active));
      }
      return jsonResponse(_meFor(active));
    }
    if (options.method == 'POST' && path.endsWith('/auth/password/change/')) {
      passwordChanges++;
      final pending = holdPasswordChange;
      if (pending != null) {
        await pending.future;
      }
      if (failPasswordChange) {
        return jsonResponse({
          'old_password': ['当前密码不正确。'],
        }, 400);
      }
      return jsonResponse({
        'detail': '密码已修改，请重新登录。',
        'reauthentication_required': true,
      });
    }
    if (options.method == 'POST' && path.endsWith('/auth/logout/')) {
      return jsonResponse({'detail': '已退出。'});
    }
    if (options.method == 'GET' && path.endsWith('/sessions/')) {
      return jsonResponse({
        'from': options.uri.queryParameters['from'],
        'to': options.uri.queryParameters['to'],
        'term': {'id': 'term-1', 'code': '2026-1', 'schedule_version': 1},
        'results': <Object>[],
      });
    }
    return jsonResponse({
      'code': 'UNEXPECTED',
      'detail': '${options.method} $path',
    }, 404);
  }
}

String _importKey(String baseUrl, String accountId) {
  final raw = '${baseUrl.trim()}|$accountId|term-1';
  return 'demo_import_done_v1_${base64Url.encode(utf8.encode(raw)).replaceAll('=', '')}';
}

String _fontKey(String baseUrl, String accountId) {
  return 'font_size_v1_${base64Url.encode(utf8.encode('${baseUrl.trim()}|$accountId')).replaceAll('=', '')}';
}

Future<
  ({
    _ProfileServer server,
    MemoryTokenStore tokens,
    SharedPreferences prefs,
    ProviderContainer container,
  })
>
_pumpProfileApp(WidgetTester tester) async {
  // A tall surface keeps the whole form built, avoiding fragile scrolling in
  // these feature tests.
  tester.view.physicalSize = const Size(1000, 2200);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  final server = _ProfileServer();
  final store = MemoryTokenStore();
  await store.save(access: 'access-${server.wang.id}', refresh: 'refresh');
  SharedPreferences.setMockInitialValues({
    'api_base_url': server.baseUrl,
    _importKey(server.baseUrl, server.wang.id): true,
    _importKey(server.baseUrl, server.li.id): true,
  });
  final prefs = await SharedPreferences.getInstance();
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        sharedPreferencesProvider.overrideWithValue(prefs),
        tokenStoreProvider.overrideWithValue(store),
        httpClientProvider.overrideWithValue(mockDio(server.adapter)),
      ],
      child: const TeacherScheduleApp(),
    ),
  );
  final container = ProviderScope.containerOf(
    tester.element(find.byType(TeacherScheduleApp)),
  );
  return (server: server, tokens: store, prefs: prefs, container: container);
}

/// Container-only harness for deterministic async race tests.
Future<
  ({
    _ProfileServer server,
    SharedPreferences prefs,
    ProviderContainer container,
  })
>
_pumpContainer() async {
  final server = _ProfileServer();
  final store = MemoryTokenStore();
  await store.save(access: 'access-${server.wang.id}', refresh: 'refresh');
  SharedPreferences.setMockInitialValues({
    'api_base_url': server.baseUrl,
    _importKey(server.baseUrl, server.wang.id): true,
    _importKey(server.baseUrl, server.li.id): true,
  });
  final prefs = await SharedPreferences.getInstance();
  final container = ProviderContainer(
    overrides: [
      sharedPreferencesProvider.overrideWithValue(prefs),
      tokenStoreProvider.overrideWithValue(store),
      httpClientProvider.overrideWithValue(mockDio(server.adapter)),
    ],
  );
  addTearDown(container.dispose);
  container.read(authControllerProvider);
  for (var attempt = 0; attempt < 100; attempt++) {
    if (container.read(authControllerProvider).phase == SessionPhase.active) {
      break;
    }
    await Future<void>.delayed(const Duration(milliseconds: 10));
  }
  expect(
    container.read(authControllerProvider).phase,
    SessionPhase.active,
    reason: '容器未恢复到登录态',
  );
  return (server: server, prefs: prefs, container: container);
}

Me _meFor(_ProfileServer server, String accountId) {
  final account = accountId == server.li.id ? server.li : server.wang;
  return Me(
    id: account.id,
    email: account.email,
    emailVerified: true,
    teacherStatus: 'active',
    assignmentScopeVersion: 4,
    currentTerm: null,
    teacher: null,
    avatarUrl: null,
  );
}

Future<void> _openProfile(WidgetTester tester) async {
  await _pumpUntil(tester, find.byType(NavigationBar), reason: '未进入主界面');
  await tester.tap(find.text('我的'));
  await _pumpUntil(tester, find.text('教师资料'), reason: '未进入我的页');
}

Future<void> _pumpUntil(
  WidgetTester tester,
  Finder finder, {
  String? reason,
}) async {
  for (var attempt = 0; attempt < 80; attempt++) {
    await tester.pump(const Duration(milliseconds: 100));
    if (finder.evaluate().isNotEmpty) {
      return;
    }
  }
  fail(reason ?? '等待界面超时：$finder');
}

Future<void> _tapVisible(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.pump(const Duration(milliseconds: 200));
  await tester.tap(finder);
  await tester.pump(const Duration(milliseconds: 300));
}

/// Signs in through the real login form.
Future<void> _signInAs(
  WidgetTester tester,
  _ProfileServer server,
  _Account account,
) async {
  await tester.enterText(find.byKey(const Key('emailField')), account.email);
  await tester.enterText(find.byKey(const Key('passwordField')), 'pw');
  await _tapVisible(tester, find.byKey(const Key('loginButton')));
  await _pumpUntil(tester, find.byType(NavigationBar), reason: '未登录到主界面');
  expect(server.active.id, account.id);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('编辑资料保存 PATCH、失败保留输入并回显新称呼', (tester) async {
    final app = await _pumpProfileApp(tester);
    final server = app.server;
    await _openProfile(tester);
    await _pumpUntil(tester, find.byKey(const Key('editProfileButton')));

    await _tapVisible(tester, find.byKey(const Key('editProfileButton')));
    await _pumpUntil(tester, find.byKey(const Key('displayNameField')));

    // Visibility comes from the server, not from a default.
    expect(find.text('同课程教师'), findsWidgets);

    await tester.enterText(find.byKey(const Key('displayNameField')), '王小明');
    await tester.enterText(find.byKey(const Key('phoneField')), '13900000000');

    server.failProfileSave = true;
    await _tapVisible(tester, find.byKey(const Key('saveProfileButton')));
    await _pumpUntil(tester, find.byKey(const Key('profileEditError')));
    expect(server.profileSaves, 1);
    expect(
      tester
          .widget<TextFormField>(find.byKey(const Key('displayNameField')))
          .controller!
          .text,
      '王小明',
      reason: '失败后输入保留',
    );

    server.failProfileSave = false;
    await _tapVisible(tester, find.byKey(const Key('saveProfileButton')));
    await tester.pump(const Duration(milliseconds: 400));

    expect(server.profileSaves, 2);
    expect(server.wang.name, '王小明');
    expect(server.wang.phoneVisibility, 'course_teachers');
    expect(find.text('王小明'), findsWidgets, reason: '返回后立即显示新称呼');
  });

  testWidgets('密码不一致不发请求，失败保留登录，成功清登录回登录页', (tester) async {
    final app = await _pumpProfileApp(tester);
    final server = app.server;
    await _openProfile(tester);
    await _pumpUntil(tester, find.byKey(const Key('passwordChangeEntry')));

    await _tapVisible(tester, find.byKey(const Key('passwordChangeEntry')));
    await _pumpUntil(tester, find.byKey(const Key('oldPasswordField')));

    await tester.enterText(
      find.byKey(const Key('oldPasswordField')),
      'TeacherDemo2026!',
    );
    await tester.enterText(
      find.byKey(const Key('newPasswordField')),
      'new-pass-1',
    );
    await tester.enterText(
      find.byKey(const Key('confirmPasswordField')),
      'new-pass-2',
    );
    await _tapVisible(tester, find.byKey(const Key('changePasswordButton')));
    expect(find.byKey(const Key('passwordMismatch')), findsOneWidget);
    expect(server.passwordChanges, 0, reason: '不一致不请求服务端');

    await tester.enterText(
      find.byKey(const Key('confirmPasswordField')),
      'new-pass-1',
    );
    server.failPasswordChange = true;
    await _tapVisible(tester, find.byKey(const Key('changePasswordButton')));
    await _pumpUntil(tester, find.byKey(const Key('passwordChangeError')));
    expect(server.passwordChanges, 1);
    expect(find.byKey(const Key('loginButton')), findsNothing, reason: '失败不退出');
    expect(
      tester
          .widget<TextFormField>(find.byKey(const Key('newPasswordField')))
          .controller!
          .text,
      'new-pass-1',
      reason: '失败保留输入',
    );

    server.failPasswordChange = false;
    await _tapVisible(tester, find.byKey(const Key('changePasswordButton')));
    await _pumpUntil(
      tester,
      find.byKey(const Key('loginButton')),
      reason: '成功后应回登录页',
    );
    expect(server.passwordChanges, 2);
    expect(await app.tokens.readAccess(), isNull, reason: '登录材料已清');
    expect(
      find.byKey(const Key('oldPasswordField')),
      findsNothing,
      reason: '旧表单已卸载',
    );
    expect(find.text('密码已修改，请重新登录。'), findsWidgets);
  });

  testWidgets('字号保存即时生效，真实换账号取各自值', (tester) async {
    final app = await _pumpProfileApp(tester);
    final server = app.server;
    await _openProfile(tester);
    await _pumpUntil(tester, find.byKey(const Key('fontSizeEntry')));

    await _tapVisible(tester, find.byKey(const Key('fontSizeEntry')));
    await _pumpUntil(tester, find.byKey(const Key('fontSize-large')));
    await _tapVisible(tester, find.byKey(const Key('fontSize-large')));
    await tester.pump(const Duration(milliseconds: 400));

    expect(server.lastFontWritePayloadKeys, 'font_size', reason: '只提交字号');
    expect(server.wang.fontSize, 'large');
    expect(app.container.read(fontSizeControllerProvider), 'large');
    expect(app.prefs.getString(_fontKey(server.baseUrl, 'user-2')), 'large');

    // Really switch accounts. 李老师 has extra_large stored on the server.
    await tester.runAsync(
      () => app.container.read(authControllerProvider.notifier).logout(),
    );
    await _pumpUntil(tester, find.byKey(const Key('loginButton')));
    await _signInAs(tester, server, server.li);

    expect(app.container.read(fontSizeControllerProvider), 'extra_large');
    final scaler = tester.widget<MediaQuery>(
      find
          .descendant(
            of: find.byType(MaterialApp),
            matching: find.byType(MediaQuery),
          )
          .first,
    );
    expect(
      scaler.data.textScaler.scale(10),
      closeTo(13, 0.01),
      reason: '新账号更大字号立即生效',
    );
    expect(
      app.prefs.getString(_fontKey(server.baseUrl, 'user-2')),
      'large',
      reason: '前一个账号的缓存未被覆盖',
    );

    // Switching back restores that account's own choice.
    await tester.runAsync(
      () => app.container.read(authControllerProvider.notifier).logout(),
    );
    await _pumpUntil(tester, find.byKey(const Key('loginButton')));
    await _signInAs(tester, server, server.wang);
    expect(app.container.read(fontSizeControllerProvider), 'large');
  });

  test('旧字号读请求晚返回不覆盖新保存', () async {
    final app = await _pumpContainer();
    final server = app.server;
    server.wang.fontSize = 'system';
    server.holdFontRead = Completer<void>();

    app.container
        .read(authControllerProvider.notifier)
        .replaceMe(_meFor(server, server.wang.id));
    // Starts the preference read, held by the server.
    expect(app.container.read(fontSizeControllerProvider), 'system');
    await Future<void>.delayed(const Duration(milliseconds: 20));
    expect(server.preferenceReads, 1);

    // The save completes first, then the stale read returns 'system'.
    expect(
      await app.container
          .read(fontSizeControllerProvider.notifier)
          .save('extra_large'),
      isNull,
    );
    server.holdFontRead!.complete();
    await Future<void>.delayed(const Duration(milliseconds: 30));

    expect(
      app.container.read(fontSizeControllerProvider),
      'extra_large',
      reason: '旧读结果不得覆盖新保存',
    );
    expect(
      app.prefs.getString(_fontKey(server.baseUrl, 'user-2')),
      'extra_large',
      reason: '缓存写入的是本次保存值',
    );
  });

  test('切换账号时在途保存不写入新账号', () async {
    final app = await _pumpContainer();
    final server = app.server;
    server.holdFontWrite = Completer<void>();

    app.container
        .read(authControllerProvider.notifier)
        .replaceMe(_meFor(server, server.wang.id));
    expect(app.container.read(fontSizeControllerProvider), 'system');

    final saving = app.container
        .read(fontSizeControllerProvider.notifier)
        .save('large');
    await Future<void>.delayed(const Duration(milliseconds: 20));
    expect(server.preferenceWrites, 1);

    // Switch to 李老师 while the PATCH is still in flight.
    server.activate(server.li.id);
    app.container
        .read(authControllerProvider.notifier)
        .replaceMe(_meFor(server, server.li.id));
    for (var attempt = 0; attempt < 50; attempt++) {
      if (app.container.read(fontSizeControllerProvider) == 'extra_large') {
        break;
      }
      await Future<void>.delayed(const Duration(milliseconds: 10));
    }
    expect(app.container.read(fontSizeControllerProvider), 'extra_large');

    server.holdFontWrite!.complete();
    expect(await saving, isNull);
    await Future<void>.delayed(const Duration(milliseconds: 30));

    expect(
      app.container.read(fontSizeControllerProvider),
      'extra_large',
      reason: '旧账号在途保存不得改写当前账号字号',
    );
    expect(
      app.prefs.getString(_fontKey(server.baseUrl, 'user-3')),
      isNot('large'),
      reason: '不得给新账号写旧账号的选择',
    );
    expect(app.prefs.getString(_fontKey(server.baseUrl, 'user-2')), isNull);
  });

  test('字号保存失败可重试且不伪造已保存', () async {
    final app = await _pumpContainer();
    final server = app.server;
    app.container
        .read(authControllerProvider.notifier)
        .replaceMe(_meFor(server, server.wang.id));
    expect(app.container.read(fontSizeControllerProvider), 'system');

    server.failFontWrite = true;
    final failure = await app.container
        .read(fontSizeControllerProvider.notifier)
        .save('large');
    expect(failure, isNotNull, reason: '失败要返回可读错误');
    expect(app.container.read(fontSizeControllerProvider), 'system');
    expect(app.prefs.getString(_fontKey(server.baseUrl, 'user-2')), isNull);

    server.failFontWrite = false;
    expect(
      await app.container
          .read(fontSizeControllerProvider.notifier)
          .save('large'),
      isNull,
    );
    expect(app.container.read(fontSizeControllerProvider), 'large');
    expect(app.prefs.getString(_fontKey(server.baseUrl, 'user-2')), 'large');
  });

  testWidgets('改密请求在途返回上一页，成功后仍清登录并回登录页', (tester) async {
    final app = await _pumpProfileApp(tester);
    final server = app.server;
    await _openProfile(tester);
    await _pumpUntil(tester, find.byKey(const Key('passwordChangeEntry')));

    await _tapVisible(tester, find.byKey(const Key('passwordChangeEntry')));
    await _pumpUntil(tester, find.byKey(const Key('oldPasswordField')));
    await tester.enterText(
      find.byKey(const Key('oldPasswordField')),
      'old-pass',
    );
    await tester.enterText(
      find.byKey(const Key('newPasswordField')),
      'new-pass-1',
    );
    await tester.enterText(
      find.byKey(const Key('confirmPasswordField')),
      'new-pass-1',
    );

    server.holdPasswordChange = Completer<void>();
    await _tapVisible(tester, find.byKey(const Key('changePasswordButton')));
    expect(server.passwordChanges, 1);

    // Leave the form while the request is still pending.
    await tester.tap(find.byType(BackButton));
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.byKey(const Key('oldPasswordField')), findsNothing);

    server.holdPasswordChange!.complete();
    await _pumpUntil(
      tester,
      find.byKey(const Key('loginButton')),
      reason: '暂挂请求成功后应回登录页',
    );
    await tester.pump(const Duration(milliseconds: 400));
    expect(await app.tokens.readAccess(), isNull, reason: '本机登录材料已清');
    expect(find.text('密码已修改，请重新登录。'), findsWidgets);
  });
}
