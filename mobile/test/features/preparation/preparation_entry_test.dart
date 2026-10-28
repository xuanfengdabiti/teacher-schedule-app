import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:teacher_schedule/app/theme.dart';
import 'package:teacher_schedule/core/providers.dart';
import 'package:teacher_schedule/core/storage/token_store.dart';
import 'package:teacher_schedule/features/schedule/models.dart';
import 'package:teacher_schedule/features/schedule/schedule_controller.dart';
import 'package:teacher_schedule/features/schedule/session_detail_page.dart';

import '../../support/mock_http_adapter.dart';

Session _session({required bool isMine}) => Session(
  id: 'session-1',
  offeringId: 'offering-1',
  offeringCode: 'OFF-001',
  courseCode: 'MATH101',
  courseName: '高等数学',
  teachingClass: const TeachingClass(
    id: 'class-1',
    classCode: 'CS2026-1',
    name: '计算机一班',
  ),
  date: DateTime(2026, 10, 27),
  weekday: 2,
  teachingWeek: 8,
  startPeriod: 1,
  endPeriod: 2,
  startAt: DateTime(2026, 10, 27, 9),
  endAt: DateTime(2026, 10, 27, 10, 35),
  classroom: const Classroom(
    id: 'room-1',
    classroomCode: 'A301',
    campus: '主校区',
    building: '教学楼A',
    roomNo: '301',
  ),
  teachers: const [SessionTeacher(id: 'teacher-1', name: '王老师')],
  status: 'scheduled',
  statusReason: '',
  isMine: isMine,
  isMoved: false,
  version: 3,
  plannedHours: '2.0',
);

Map<String, dynamic> _preparationPayload() => {
  'context': {
    'course_name': '高等数学',
    'date': '2026-10-27',
    'start_period': 1,
    'end_period': 2,
    'teaching_minutes': 90,
    'previous_note': '',
    'plan_items': <Object>[],
  },
  'draft': null,
  'ai_available': true,
};

Future<MockHttpAdapter> _pumpDetail(
  WidgetTester tester, {
  required bool isMine,
}) async {
  tester.view.physicalSize = const Size(900, 2400);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);

  final adapter = MockHttpAdapter((options) {
    if (options.method == 'GET' &&
        options.uri.path.endsWith('/sessions/session-1/preparation/')) {
      return jsonResponse(_preparationPayload());
    }
    return jsonResponse({'detail': 'unexpected'}, 404);
  });
  SharedPreferences.setMockInitialValues({});
  final prefs = await SharedPreferences.getInstance();
  final session = _session(isMine: isMine);

  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        sharedPreferencesProvider.overrideWithValue(prefs),
        tokenStoreProvider.overrideWithValue(MemoryTokenStore()),
        httpClientProvider.overrideWithValue(mockDio(adapter)),
        sessionDetailProvider('session-1').overrideWith((ref) async => session),
      ],
      child: MaterialApp(
        theme: buildAppTheme(),
        home: const SessionDetailPage(sessionId: 'session-1'),
      ),
    ),
  );
  await tester.pumpAndSettle();
  return adapter;
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('本人课程详情显示满宽 AI 备课入口，原有操作保持不变', (tester) async {
    await _pumpDetail(tester, isMine: true);

    expect(find.text('教学备注'), findsOneWidget);
    expect(find.text('教学计划'), findsOneWidget);
    expect(find.text('登记学时'), findsOneWidget);
    expect(find.text('进度交接'), findsOneWidget);
    expect(find.text('查询调课'), findsOneWidget);

    final entry = find.byKey(const Key('openPreparationButton'));
    expect(entry, findsOneWidget);
    expect(
      find.descendant(of: entry, matching: find.byIcon(Icons.auto_awesome)),
      findsOneWidget,
    );
    expect(
      find.descendant(of: entry, matching: find.text('AI备课')),
      findsOneWidget,
    );

    final width = tester.getSize(entry).width;
    expect(width, greaterThan(300), reason: '入口应为满宽按钮');
  });

  testWidgets('他人课程不显示 AI 备课入口', (tester) async {
    await _pumpDetail(tester, isMine: false);

    expect(find.byKey(const Key('openPreparationButton')), findsNothing);
    expect(find.text('教学备注'), findsNothing);
  });

  testWidgets('点击入口打开备课页面并读取上下文', (tester) async {
    final adapter = await _pumpDetail(tester, isMine: true);
    await tester.tap(find.byKey(const Key('openPreparationButton')));
    await tester.pumpAndSettle();

    expect(find.text('AI 备课'), findsOneWidget);
    expect(find.text('高等数学'), findsWidgets);
    expect(find.text('10月27日 周二 · 1-2节 · 本节 90 分钟'), findsOneWidget);
    expect(
      adapter.requests
          .where(
            (request) =>
                request.method == 'GET' &&
                request.path.endsWith('/sessions/session-1/preparation/'),
          )
          .length,
      1,
    );
  });
}
