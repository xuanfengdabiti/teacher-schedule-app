import 'dart:async';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:teacher_schedule/app.dart';
import 'package:teacher_schedule/core/providers.dart';
import 'package:teacher_schedule/core/storage/token_store.dart';

import 'support/mock_http_adapter.dart';

class _AccountServer {
  _AccountServer() {
    adapter = MockHttpAdapter(_respond);
  }

  late final MockHttpAdapter adapter;
  bool emailVerified = false;
  String teacherStatus = 'active';
  bool registerSucceeds = true;
  int resendCalls = 0;

  String get baseUrl => 'http://account.test/api/v1/';

  Map<String, dynamic> get _me => {
    'id': 'user-9',
    'email': 'new.teacher@example.invalid',
    'email_verified': emailVerified,
    'teacher_status': teacherStatus,
    'assignment_scope_version': 1,
    'current_term': null,
    'teacher': null,
    'claim': null,
    'avatar_url': null,
  };

  FutureOr<ResponseBody> _respond(RequestOptions options) {
    final path = options.uri.path;
    if (path.endsWith('/auth/registration/')) {
      if (!registerSucceeds) {
        return jsonResponse({
          'email': ['该邮箱已注册，请直接登录或找回密码。'],
        }, 400);
      }
      return jsonResponse({'detail': 'Verification e-mail sent.'}, 201);
    }
    if (path.endsWith('/auth/registration/verify-email/')) {
      emailVerified = true;
      return jsonResponse({'detail': 'ok'});
    }
    if (path.endsWith('/auth/registration/resend-email/')) {
      resendCalls++;
      return jsonResponse({'detail': 'ok'});
    }
    if (path.endsWith('/auth/password/reset/')) {
      return jsonResponse({'detail': 'Password reset e-mail sent.'});
    }
    if (path.endsWith('/auth/password/reset/confirm/')) {
      return jsonResponse({'detail': 'Password reset successful.'});
    }
    if (path.endsWith('/me/')) {
      return jsonResponse(_me);
    }
    return jsonResponse({'code': 'UNEXPECTED', 'detail': path}, 404);
  }
}

Future<
  ({_AccountServer server, MemoryTokenStore tokens, SharedPreferences prefs})
>
_pumpApp(WidgetTester tester, {bool signedIn = false}) async {
  final server = _AccountServer();
  final tokens = MemoryTokenStore();
  if (signedIn) {
    await tokens.save(access: 'restricted-access', refresh: 'restricted');
  }
  SharedPreferences.setMockInitialValues({'api_base_url': server.baseUrl});
  final prefs = await SharedPreferences.getInstance();
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        sharedPreferencesProvider.overrideWithValue(prefs),
        tokenStoreProvider.overrideWithValue(tokens),
        httpClientProvider.overrideWithValue(mockDio(server.adapter)),
      ],
      child: const TeacherScheduleApp(),
    ),
  );
  return (server: server, tokens: tokens, prefs: prefs);
}

Future<void> _pumpUntil(
  WidgetTester tester,
  Finder finder, {
  String? reason,
}) async {
  for (var attempt = 0; attempt < 60; attempt++) {
    await tester.pump(const Duration(milliseconds: 100));
    if (finder.evaluate().isNotEmpty) {
      return;
    }
  }
  fail(reason ?? '等待界面超时：$finder');
}

Future<void> _tap(WidgetTester tester, Key key) async {
  final finder = find.byKey(key);
  if (finder.evaluate().isEmpty) {
    await tester.scrollUntilVisible(
      finder,
      160,
      scrollable: find.byType(Scrollable).first,
    );
  }
  await tester.ensureVisible(finder);
  await tester.pump(const Duration(milliseconds: 100));
  await tester.tap(finder, warnIfMissed: false);
  await tester.pump(const Duration(milliseconds: 250));
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('注册两次密码不一致：页内提示、不发请求、保留输入', (tester) async {
    final app = await _pumpApp(tester);
    await _pumpUntil(tester, find.byKey(const Key('loginButton')));
    await _tap(tester, const Key('openRegisterButton'));
    await _pumpUntil(tester, find.byKey(const Key('registerSubmitButton')));

    await tester.enterText(find.byKey(const Key('registerNameField')), '新老师');
    await tester.enterText(
      find.byKey(const Key('registerTeacherNoField')),
      '0099',
    );
    await tester.enterText(
      find.byKey(const Key('registerEmailField')),
      'new.teacher@example.invalid',
    );
    await tester.enterText(
      find.byKey(const Key('registerPasswordField')),
      'pass-1',
    );
    await tester.enterText(
      find.byKey(const Key('registerPassword2Field')),
      'pass-2',
    );
    await _tap(tester, const Key('registerSubmitButton'));

    expect(find.text('两次输入的密码不一致。'), findsOneWidget);
    expect(
      app.server.adapter.requests.where(
        (request) => request.path.endsWith('/auth/registration/'),
      ),
      isEmpty,
    );
    expect(
      tester
          .widget<TextFormField>(find.byKey(const Key('registerNameField')))
          .controller!
          .text,
      '新老师',
    );
    expect(find.byKey(const Key('registerSubmitButton')), findsOneWidget);
  });

  testWidgets('注册成功进入邮箱验证页并带出邮箱', (tester) async {
    final app = await _pumpApp(tester);
    await _pumpUntil(tester, find.byKey(const Key('loginButton')));
    await _tap(tester, const Key('openRegisterButton'));
    await _pumpUntil(tester, find.byKey(const Key('registerSubmitButton')));

    await tester.enterText(find.byKey(const Key('registerNameField')), '新老师');
    await tester.enterText(
      find.byKey(const Key('registerTeacherNoField')),
      '0099',
    );
    await tester.enterText(
      find.byKey(const Key('registerEmailField')),
      'new.teacher@example.invalid',
    );
    await tester.enterText(
      find.byKey(const Key('registerPasswordField')),
      'DemoPass2026!',
    );
    await tester.enterText(
      find.byKey(const Key('registerPassword2Field')),
      'DemoPass2026!',
    );
    await _tap(tester, const Key('registerSubmitButton'));
    await _pumpUntil(tester, find.byKey(const Key('verifySubmitButton')));

    final sent = app.server.adapter.requests.firstWhere(
      (request) => request.path.endsWith('/auth/registration/'),
    );
    expect(sent.method, 'POST');
    expect(sent.bodyAsMap['formal_name'], '新老师');
    expect(sent.bodyAsMap['teacher_no'], '0099');
    expect(sent.bodyAsMap['password1'], 'DemoPass2026!');
    expect(
      tester
          .widget<TextField>(find.byKey(const Key('verifyEmailField')))
          .controller!
          .text,
      'new.teacher@example.invalid',
    );
  });

  testWidgets('未登录可进入注册与忘记密码且不被弹回登录', (tester) async {
    await _pumpApp(tester);
    await _pumpUntil(tester, find.byKey(const Key('loginButton')));

    await _tap(tester, const Key('openRegisterButton'));
    await _pumpUntil(tester, find.byKey(const Key('registerSubmitButton')));
    expect(find.text('注册账号'), findsWidgets);
    // 停留若干帧确认没有被 signedOut 重定向弹回登录页。
    await tester.pump(const Duration(milliseconds: 400));
    expect(find.byKey(const Key('registerSubmitButton')), findsOneWidget);
    expect(find.byKey(const Key('loginButton')), findsNothing);

    await _tap(tester, const Key('registerBackToLogin'));
    await _pumpUntil(tester, find.byKey(const Key('loginButton')));

    await _tap(tester, const Key('openForgotPasswordButton'));
    await _pumpUntil(tester, find.byKey(const Key('sendResetEmailButton')));
    expect(find.text('忘记密码'), findsWidgets);
    expect(find.byKey(const Key('loginButton')), findsNothing);
  });

  testWidgets('登录页改服务地址后点注册：先保存再进入，旧地址不保留', (tester) async {
    final app = await _pumpApp(tester);
    await _pumpUntil(tester, find.byKey(const Key('loginButton')));

    await _tap(tester, const Key('serverSettingsTile'));
    await tester.enterText(
      find.byKey(const Key('serverUrlField')),
      'http://38.76.172.48:18000/api/v1/',
    );
    await _tap(tester, const Key('openRegisterButton'));
    await _pumpUntil(tester, find.byKey(const Key('registerSubmitButton')));

    expect(
      app.prefs.getString('api_base_url'),
      'http://38.76.172.48:18000/api/v1/',
    );
    expect(
      find.text('http://38.76.172.48:18000/api/v1/'),
      findsWidgets,
      reason: '注册页连接设置显示已保存的新地址',
    );
  });

  testWidgets('登录页地址非法时留在页内提示，不进入注册', (tester) async {
    await _pumpApp(tester);
    await _pumpUntil(tester, find.byKey(const Key('loginButton')));
    await _tap(tester, const Key('serverSettingsTile'));
    await tester.enterText(find.byKey(const Key('serverUrlField')), 'http://');
    await _tap(tester, const Key('openRegisterButton'));

    expect(find.textContaining('服务地址格式无效'), findsOneWidget);
    expect(find.byKey(const Key('registerSubmitButton')), findsNothing);
    expect(find.byKey(const Key('loginButton')), findsOneWidget);
  });

  testWidgets('忘记密码：发信请求后重置链接填入即可提交真实合同', (tester) async {
    final app = await _pumpApp(tester);
    await _pumpUntil(tester, find.byKey(const Key('loginButton')));
    await _tap(tester, const Key('openForgotPasswordButton'));
    await _pumpUntil(tester, find.byKey(const Key('sendResetEmailButton')));

    await tester.enterText(
      find.byKey(const Key('resetEmailField')),
      'new.teacher@example.invalid',
    );
    await _tap(tester, const Key('sendResetEmailButton'));
    await _pumpUntil(
      tester,
      find.byKey(const Key('resetPasswordSubmitButton')),
    );

    final request = app.server.adapter.requests.firstWhere(
      (item) => item.path.endsWith('/auth/password/reset/'),
    );
    expect(request.bodyAsMap['email'], 'new.teacher@example.invalid');

    await tester.enterText(
      find.byKey(const Key('resetLinkField')),
      'teacherschedule://reset-password?uid=MQ&token=token-7',
    );
    await _tap(tester, const Key('applyResetLinkButton'));
    await tester.enterText(
      find.byKey(const Key('resetNewPasswordField')),
      'NewPass2026!',
    );
    await tester.enterText(
      find.byKey(const Key('resetConfirmPasswordField')),
      'NewPass2026!',
    );
    // 只粘贴链接、不展开“手动填写”也应可提交。
    await _tap(tester, const Key('resetPasswordSubmitButton'));
    await _pumpUntil(tester, find.byKey(const Key('loginButton')));

    final confirm = app.server.adapter.requests.firstWhere(
      (item) => item.path.endsWith('/auth/password/reset/confirm/'),
    );
    expect(confirm.bodyAsMap['uid'], 'MQ');
    expect(confirm.bodyAsMap['token'], 'token-7');
    expect(confirm.bodyAsMap['new_password1'], 'NewPass2026!');
    expect(confirm.bodyAsMap['new_password2'], 'NewPass2026!');
    expect(find.byKey(const Key('loginButton')), findsOneWidget);
  });

  testWidgets('受限账号验证邮箱后 refreshMe 并按真实 active 进入今天', (tester) async {
    final app = await _pumpApp(tester, signedIn: true);
    await _pumpUntil(
      tester,
      find.byKey(const Key('openEmailVerificationButton')),
    );
    expect(find.text('暂时不能查看课表'), findsOneWidget);

    await _tap(tester, const Key('openEmailVerificationButton'));
    await _pumpUntil(tester, find.byKey(const Key('verifySubmitButton')));
    await tester.enterText(
      find.byKey(const Key('verifyLinkField')),
      'teacherschedule://verify-email?key=abc-123',
    );
    await _tap(tester, const Key('verifySubmitButton'));

    await _pumpUntil(tester, find.byKey(const Key('notificationsButton')));
    expect(
      app.server.adapter.requests
          .where((request) => request.path.endsWith('/me/'))
          .length,
      greaterThanOrEqualTo(2),
      reason: '验证后刷新真实 Me',
    );
    expect(find.text('暂时不能查看课表'), findsNothing);
  });

  testWidgets('受限账号验证后仍 pending：回账号状态显示审核提示', (tester) async {
    final app = await _pumpApp(tester, signedIn: true);
    app.server.teacherStatus = 'pending';
    await _pumpUntil(
      tester,
      find.byKey(const Key('openEmailVerificationButton')),
    );

    await _tap(tester, const Key('openEmailVerificationButton'));
    await _pumpUntil(tester, find.byKey(const Key('verifySubmitButton')));
    await tester.enterText(find.byKey(const Key('verifyLinkField')), 'abc-123');
    await _tap(tester, const Key('verifySubmitButton'));
    await _pumpUntil(tester, find.byKey(const Key('verifyBackToLogin')));
    expect(find.text('返回账号状态'), findsOneWidget);

    await _tap(tester, const Key('verifyBackToLogin'));
    await _pumpUntil(tester, find.text('教师身份待审核：管理员通过后即可查看课表。'));
    expect(find.text('暂时不能查看课表'), findsOneWidget);
    expect(find.text('重新检查'), findsOneWidget);
  });
}
