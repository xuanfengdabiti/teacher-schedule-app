import 'dart:async';
import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:teacher_schedule/app.dart';
import 'package:teacher_schedule/core/config/api_config.dart';
import 'package:teacher_schedule/core/json.dart';
import 'package:teacher_schedule/core/providers.dart';
import 'package:teacher_schedule/core/storage/token_store.dart';
import 'package:teacher_schedule/features/auth/auth_controller.dart';

import 'support/mock_http_adapter.dart';

class _ContractServer {
  _ContractServer() {
    adapter = MockHttpAdapter(_respond);
  }

  final DateTime today = DateTime.now();
  late final MockHttpAdapter adapter;
  int sessionListCalls = 0;
  int meCalls = 0;
  final List<Map<String, dynamic>> savedDraftBodies = [];
  String previousNotePreview = '';
  int notesReadCalls = 0;
  int bootstrapCalls = 0;
  bool failBootstrap = false;
  String currentUserId = 'user-1';

  /// When true the bootstrap response contains another teacher's session too.
  bool includeForeignSession = false;

  /// When true the bootstrap response also contains a cancelled own session.
  bool includeCancelledOwnSession = false;

  String get baseUrl => 'http://example.test/api/v1/';

  Map<String, dynamic> get _term => {
    'id': 'term-1',
    'code': '2026-1',
    'name': '2025-2026学年第二学期',
    'start_date': '2020-01-01',
    'end_date': '2030-12-31',
  };

  Map<String, dynamic> get _me => {
    'id': currentUserId,
    'email': 'teacher@example.com',
    'email_verified': true,
    'teacher_status': 'active',
    'assignment_scope_version': 4,
    'current_term': _term,
    'teacher': {
      'teacher_no': 'T001',
      'formal_name': '李老师',
      'display_name': '李老师',
      'department': '信息学院',
      'phone': '',
      'phone_visibility': 'private',
      'wechat': '',
      'wechat_visibility': 'private',
    },
    'claim': null,
    'avatar_url': null,
  };

  Map<String, dynamic> get _sessionSummary {
    final date = isoDate(today);
    return {
      'id': 'session-1',
      'offering_id': 'offering-1',
      'offering_code': 'OFF-001',
      'course_code': 'MATH101',
      'course_name': '高等数学',
      'teaching_class': {
        'id': 'class-1',
        'class_code': 'CS2026-1',
        'name': '计算机一班',
      },
      'date': date,
      'weekday': today.weekday,
      'teaching_week': 8,
      'start_period': 1,
      'end_period': 2,
      'start_at': '${date}T09:00:00+08:00',
      'end_at': '${date}T10:35:00+08:00',
      'classroom': {
        'id': 'room-1',
        'classroom_code': 'A301',
        'campus': '主校区',
        'building': '教学楼A',
        'room_no': '301',
      },
      'teachers': [
        {'id': 'teacher-1', 'name': '王老师'},
      ],
      'status': 'scheduled',
      'status_reason': '',
      'is_mine': true,
      'is_moved': false,
      'origin_teaching_date': null,
      'version': 3,
      'previous_note_preview': previousNotePreview,
    };
  }

  Map<String, dynamic> get _sessionDetail => {
    ..._sessionSummary,
    'planned_hours': '2.0',
    'actual_hours': null,
    'completed_at': null,
    'enrolled_count': 45,
    'offering': {
      'target_hours': '32.0',
      'lead_teacher': {'id': 'teacher-1', 'name': '王老师'},
    },
    'term': {'id': 'term-1', 'code': '2026-1', 'schedule_version': 1},
  };

  Map<String, dynamic> get _calendar {
    final monday = startOfWeek(today);
    return {
      ..._term,
      'first_week_monday': isoDate(monday),
      'teaching_week_count': 20,
      'timezone': 'Asia/Shanghai',
      'schedule_version': 1,
      'is_current': true,
      'periods': [
        {
          'period_number': 1,
          'start_time': '08:00',
          'end_time': '08:45',
          'block_code': 'morning',
        },
        {
          'period_number': 2,
          'start_time': '08:55',
          'end_time': '09:40',
          'block_code': 'morning',
        },
      ],
      'exceptions': <Object>[],
      'weeks': [
        {
          'week': 8,
          'monday': isoDate(monday),
          'sunday': isoDate(monday.add(const Duration(days: 6))),
        },
      ],
    };
  }

  FutureOr<ResponseBody> _respond(RequestOptions options) {
    final path = options.uri.path;
    if (options.method == 'POST' && path.endsWith('/auth/login/')) {
      return jsonResponse({
        'access': 'widget-access',
        'refresh': 'widget-refresh',
      });
    }
    if (options.method == 'GET' && path.endsWith('/me/')) {
      meCalls++;
      return jsonResponse(_me);
    }
    if (options.method == 'GET' && path.endsWith('/terms/term-1/calendar/')) {
      return jsonResponse(_calendar);
    }
    if (options.method == 'GET' && path.endsWith('/sync/bootstrap/')) {
      bootstrapCalls++;
      if (failBootstrap) {
        return jsonResponse({'detail': '示例课表暂时不可用。'}, 503);
      }
      return jsonResponse({
        'cursor': '7',
        'assignment_scope_version': 4,
        'term': {..._term, 'name': '2025-2026学年第二学期'},
        'offerings': <Object>[],
        'sessions': [
          _sessionSummary,
          if (includeForeignSession)
            {..._sessionSummary, 'id': 'session-2', 'is_mine': false},
          if (includeCancelledOwnSession)
            {..._sessionSummary, 'id': 'session-3', 'status': 'cancelled'},
        ],
        'notes': <Object>[],
        'generated_at': '2026-03-01T10:00:00+08:00',
      });
    }
    if (options.method == 'GET' && path.endsWith('/sessions/')) {
      sessionListCalls++;
      return jsonResponse({
        'from': options.uri.queryParameters['from'],
        'to': options.uri.queryParameters['to'],
        'term': {'id': 'term-1', 'code': '2026-1', 'schedule_version': 1},
        'results': [_sessionSummary],
      });
    }
    if (path.contains('/sessions/session-1')) {
      if (path.endsWith('/notes/')) {
        notesReadCalls++;
        return jsonResponse({
          'mine': null,
          'others': <Object>[],
          'previous': null,
        });
      }
      if (path.endsWith('/notes/mine/') && options.method == 'PUT') {
        final body = Map<String, dynamic>.from(options.data as Map);
        savedDraftBodies.add(body);
        return jsonResponse({
          'id': 'note-1',
          'version': 1,
          'draft': {
            'covered': body['covered'],
            'unfinished': body['unfinished'],
            'next_plan': body['next_plan'],
          },
          'published': null,
          'published_at': null,
          'published_version': null,
          'updated_at': '2026-03-01T10:00:00+08:00',
        });
      }
      if (path.endsWith('/sessions/session-1/') && options.method == 'GET') {
        return jsonResponse(_sessionDetail);
      }
    }
    return jsonResponse({
      'code': 'UNEXPECTED',
      'detail': '${options.method} $path',
    }, 404);
  }
}

Future<void> _pumpUntil(
  WidgetTester tester,
  Finder finder, {
  String? reason,
  int attempts = 60,
}) async {
  for (var attempt = 0; attempt < attempts; attempt++) {
    await tester.pump(const Duration(milliseconds: 100));
    if (finder.evaluate().isNotEmpty) {
      return;
    }
  }
  fail(reason ?? '等待界面超时：$finder');
}

Future<({_ContractServer server, MemoryTokenStore tokens})> _pumpApp(
  WidgetTester tester, {
  bool signedIn = false,
  bool importDone = true,
}) async {
  final server = _ContractServer();
  final tokens = MemoryTokenStore();
  if (signedIn) {
    await tokens.save(access: 'restored-access', refresh: 'restored-refresh');
  }
  SharedPreferences.setMockInitialValues({
    'api_base_url': server.baseUrl,
    if (signedIn && importDone) _importDoneKey(server.baseUrl): true,
  });
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
  return (server: server, tokens: tokens);
}

String _importDoneKey(String baseUrl) {
  final raw = '${ApiConfig.normalizeBaseUrl(baseUrl)}|user-1|term-1';
  final encoded = base64Url.encode(utf8.encode(raw)).replaceAll('=', '');
  return 'demo_import_done_v1_$encoded';
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('登录 -> 今天/本周缓存 -> 详情 -> 保存备注使用真实合同', (tester) async {
    final app = await _pumpApp(tester);
    final server = app.server;

    await _pumpUntil(
      tester,
      find.byKey(const Key('loginButton')),
      reason: '未进入登录页',
    );
    await tester.enterText(
      find.byKey(const Key('emailField')),
      'teacher@example.com',
    );
    await tester.enterText(
      find.byKey(const Key('passwordField')),
      'test-password',
    );
    final loginButton = find.byKey(const Key('loginButton'));
    await tester.ensureVisible(loginButton);
    await tester.pump(const Duration(milliseconds: 200));
    await tester.tap(loginButton);
    await tester.pump();

    // First login for this account/term goes through the demo import page.
    await _pumpUntil(
      tester,
      find.byKey(const Key('importDoneTitle')),
      reason: '首次登录未完成课表演示导入',
    );
    expect(server.bootstrapCalls, 1);
    expect(find.text('李老师'), findsWidgets);
    await tester.tap(find.byKey(const Key('importEnterButton')));
    await tester.pump(const Duration(milliseconds: 300));

    final sessionCard = find.byKey(const ValueKey('session-session-1'));
    await _pumpUntil(tester, sessionCard, reason: '登录后未显示今日课程');
    expect(server.sessionListCalls, 1);

    await tester.tap(find.text('课表'));
    await _pumpUntil(
      tester,
      find.byKey(ValueKey('dayTab-${isoDate(server.today)}')),
      reason: '未进入周课表',
    );
    expect(server.sessionListCalls, 1, reason: '同一周切页不应重复请求');

    await tester.tap(find.byIcon(Icons.today_outlined));
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.byKey(const ValueKey('previous-note-session-1')), findsNothing);
    expect(server.notesReadCalls, 0, reason: '课表卡片不应逐卡请求备注');

    await tester.tap(sessionCard);
    await _pumpUntil(
      tester,
      find.byKey(const Key('sessionActionSheet')),
      reason: '单击课程卡未打开操作菜单',
    );
    await tester.pumpAndSettle();
    expect(find.text('课程详情'), findsOneWidget);

    await tester.tap(find.byKey(const Key('sessionActionClose')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('sessionActionSheet')), findsNothing);
    expect(find.text('课程信息'), findsNothing, reason: '取消菜单不应进入详情');

    await tester.tap(sessionCard);
    await _pumpUntil(
      tester,
      find.byKey(const Key('sessionActionSheet')),
      reason: '再次单击未打开操作菜单',
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('sessionAction-detail')));
    await _pumpUntil(tester, find.text('课程信息'), reason: '未进入课程详情');

    await tester.tap(find.byKey(const Key('openNotesButton')));
    await _pumpUntil(
      tester,
      find.byKey(const Key('coveredField')),
      reason: '未进入教学备注',
    );

    await tester.enterText(
      find.byKey(const Key('coveredField')),
      '完成极限定义与例题讲解',
    );
    await tester.tap(find.byKey(const Key('saveDraftButton')));
    await _pumpUntil(
      tester,
      find.byKey(const Key('notesStatus')),
      reason: '保存后未显示成功状态',
    );

    expect(server.savedDraftBodies, hasLength(1));
    final body = server.savedDraftBodies.single;
    expect(body['covered'], '完成极限定义与例题讲解');
    expect(body['base_version'], 0);
    expect(body['client_mutation_id'], isA<String>());
    expect((body['client_mutation_id'] as String).length, 32);
    expect(find.text('草稿已保存。'), findsWidgets);

    // The server now supplies a summary for the next schedule response.
    server.previousNotePreview = '下次讲证据链审查，带一个案例，并请学生提前阅读补充材料后课堂上讨论。';
    await tester.tap(find.byType(BackButton));
    await tester.pump(const Duration(milliseconds: 300));
    await tester.tap(find.byType(BackButton));
    await _pumpUntil(
      tester,
      find.byKey(const ValueKey('previous-note-session-1')),
      reason: '保存返回后课表未显示备注',
    );

    final preview = find.textContaining('备注：');
    expect(preview, findsOneWidget);
    expect(tester.widget<Text>(preview).maxLines, 2, reason: '摘要最多两行');
    expect(server.sessionListCalls, 2, reason: '保存后应重取一次周信息');
    expect(server.notesReadCalls, 1, reason: '仅备注页请求一次，列表没有逐卡备注请求');

    final authHeader = server.adapter.requests
        .firstWhere((request) => request.path.endsWith('/sessions/'))
        .headers['Authorization'];
    expect(authHeader, 'Bearer widget-access');
  });

  testWidgets('320 宽和大字号下日签七天完整且不溢出', (tester) async {
    tester.view.physicalSize = const Size(320, 700);
    tester.view.devicePixelRatio = 1;
    tester.platformDispatcher.textScaleFactorTestValue = 1.3;
    addTearDown(tester.view.reset);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);

    final app = await _pumpApp(tester, signedIn: true);
    await _pumpUntil(
      tester,
      find.byKey(const ValueKey('session-session-1')),
      reason: '有令牌启动未恢复到课表',
    );

    await tester.tap(find.text('课表'));
    await _pumpUntil(
      tester,
      find.byKey(const Key('weekViewToggle')),
      reason: '未进入周课表',
    );

    final monday = startOfWeek(app.server.today);
    for (var index = 0; index < 7; index++) {
      expect(
        find.byKey(
          ValueKey('dayTab-${isoDate(monday.add(Duration(days: index)))}'),
        ),
        findsOneWidget,
        reason: '日签应完整显示七天',
      );
    }

    final weekSessionCard = find.byKey(const ValueKey('session-session-1'));
    await tester.ensureVisible(weekSessionCard);
    await tester.pumpAndSettle();
    await tester.tap(weekSessionCard);
    await _pumpUntil(
      tester,
      find.byKey(const Key('sessionActionSheet')),
      reason: '周课表按日列表单击未打开操作菜单',
    );
    await tester.pumpAndSettle();
    expect(find.text('课程详情'), findsOneWidget);
    await tester.tap(find.byKey(const Key('sessionActionClose')));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  });

  testWidgets('已有令牌启动时恢复 me 并直接进入课表', (tester) async {
    final app = await _pumpApp(tester, signedIn: true);
    await _pumpUntil(
      tester,
      find.byKey(const ValueKey('session-session-1')),
      reason: '有令牌启动未恢复到课表',
    );

    expect(find.byKey(const Key('loginButton')), findsNothing);
    expect(find.byKey(const Key('importDoneTitle')), findsNothing);
    expect(app.server.bootstrapCalls, 0, reason: '已有记录不应重复导入');
    expect(app.server.meCalls, greaterThanOrEqualTo(1));
    final meRequest = app.server.adapter.requests.firstWhere(
      (request) => request.path.endsWith('/me/'),
    );
    expect(meRequest.headers['Authorization'], 'Bearer restored-access');
  });

  testWidgets('首次导入失败可重试，失败不写成功记录', (tester) async {
    final app = await _pumpApp(tester, signedIn: true, importDone: false);
    final server = app.server;
    server.failBootstrap = true;

    await _pumpUntil(
      tester,
      find.byKey(const Key('importError')),
      reason: '失败时未停留在导入页',
    );
    expect(find.byKey(const Key('importDoneTitle')), findsNothing);
    expect(find.byKey(const Key('importRetryButton')), findsOneWidget);
    expect(server.bootstrapCalls, 1);
    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getBool(_importDoneKey(server.baseUrl)), isNull);

    server.failBootstrap = false;
    await tester.tap(find.byKey(const Key('importRetryButton')));
    await _pumpUntil(
      tester,
      find.byKey(const Key('importDoneTitle')),
      reason: '重试后未成功',
    );
    expect(server.bootstrapCalls, 2);
    expect(prefs.getBool(_importDoneKey(server.baseUrl)), isNull);

    await tester.tap(find.byKey(const Key('importEnterButton')));
    await _pumpUntil(
      tester,
      find.byKey(const ValueKey('session-session-1')),
      reason: '点击进入后未到 Today',
    );
    expect(prefs.getBool(_importDoneKey(server.baseUrl)), isTrue);
  });

  testWidgets('首次导入只统计本人未取消课次，进入后同账号再登录跳过', (tester) async {
    final app = await _pumpApp(tester, signedIn: true, importDone: false);
    final server = app.server;
    server.includeForeignSession = true;
    server.includeCancelledOwnSession = true;

    await _pumpUntil(
      tester,
      find.byKey(const Key('importDoneTitle')),
      reason: '未完成首次导入展示',
    );
    expect(server.bootstrapCalls, 1);
    expect(find.text('1 次'), findsOneWidget, reason: '不统计他人的课次');
    expect(find.text('1 门'), findsOneWidget);

    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getBool(_importDoneKey(server.baseUrl)), isNull);

    await tester.tap(find.byKey(const Key('importEnterButton')));
    await _pumpUntil(
      tester,
      find.byKey(const ValueKey('session-session-1')),
      reason: '未进入 Today',
    );
    expect(prefs.getBool(_importDoneKey(server.baseUrl)), isTrue);

    // Log out and in again: the saved marker skips the import presentation.
    final container = ProviderScope.containerOf(
      tester.element(find.byType(TeacherScheduleApp)),
    );
    // logout() performs a real network round-trip and a 4s timeout; run it
    // outside the fake-async zone so the test does not stall on its timers.
    await tester.runAsync(
      () => container.read(authControllerProvider.notifier).logout(),
    );
    await _pumpUntil(
      tester,
      find.byKey(const Key('loginButton')),
      reason: '退出后未回登录页',
    );
    await tester.enterText(
      find.byKey(const Key('emailField')),
      'teacher@example.com',
    );
    await tester.enterText(
      find.byKey(const Key('passwordField')),
      'test-password',
    );
    final loginButton = find.byKey(const Key('loginButton'));
    await tester.ensureVisible(loginButton);
    await tester.tap(loginButton);
    await _pumpUntil(
      tester,
      find.byKey(const ValueKey('session-session-1')),
      reason: '再次登录未直接进入课表',
    );
    expect(server.bootstrapCalls, 1, reason: '已有记录不应再次导入');
    expect(find.byKey(const Key('importDoneTitle')), findsNothing);
  });

  testWidgets('整周总览复用已取周数据，点课程进入详情', (tester) async {
    final app = await _pumpApp(tester, signedIn: true);
    final server = app.server;
    await _pumpUntil(
      tester,
      find.byKey(const ValueKey('session-session-1')),
      reason: '有令牌启动未恢复到课表',
    );
    expect(server.sessionListCalls, 1);

    await tester.tap(find.text('课表'));
    await _pumpUntil(
      tester,
      find.byKey(ValueKey('dayTab-${isoDate(server.today)}')),
      reason: '未进入周课表',
    );
    expect(server.sessionListCalls, 1);

    await tester.tap(find.text('整周总览'));
    await tester.pump(const Duration(milliseconds: 200));
    final overviewBlock = find.byKey(
      const ValueKey('overview-session-session-1'),
    );
    expect(overviewBlock, findsOneWidget);
    final blockSize = tester.getSize(overviewBlock);
    expect(blockSize.height, closeTo(58 * 2 - 3, 0.1), reason: '1–2 节课卡应跨两节高度');
    expect(server.sessionListCalls, 1, reason: '切换总览不应重复请求本周');

    // Tapping the lower half must open the same session detail.
    final bottomCenter =
        tester.getCenter(overviewBlock) + Offset(0, blockSize.height / 4);
    await tester.tapAt(bottomCenter);
    await _pumpUntil(tester, find.text('课程信息'), reason: '总览课程下半区未进入详情');
    expect(server.sessionListCalls, 1);
  });
}
