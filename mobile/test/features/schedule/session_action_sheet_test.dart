import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:teacher_schedule/app/theme.dart';
import 'package:teacher_schedule/core/network/api_client.dart';
import 'package:teacher_schedule/core/storage/token_store.dart';
import 'package:teacher_schedule/features/schedule/models.dart';
import 'package:teacher_schedule/features/schedule/session_action_sheet.dart';
import 'package:teacher_schedule/features/schedule/session_card.dart';

class _FakeApiClient extends ApiClient {
  _FakeApiClient()
    : super(
        baseUrl: 'http://test.local/api/v1/',
        tokenStore: MemoryTokenStore(),
      );
}

Session _session({bool isMine = true}) {
  return Session(
    id: 'session-1',
    offeringId: 'offering-1',
    offeringCode: 'OFF-001',
    courseCode: 'INV101',
    courseName: '刑事侦查专题研究',
    teachingClass: const TeachingClass(
      id: 'class-1',
      classCode: 'INV2026-1',
      name: '侦查一班',
    ),
    date: DateTime(2026, 10, 26),
    weekday: 1,
    teachingWeek: 8,
    startPeriod: 1,
    endPeriod: 2,
    startAt: DateTime(2026, 10, 26, 8),
    endAt: DateTime(2026, 10, 26, 9, 40),
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
    version: 1,
    previousNotePreview: '下次讲证据链审查，并请学生提前阅读补充材料。',
  );
}

Widget _sheetHost(Session session) {
  return MaterialApp(
    theme: buildAppTheme(),
    home: Scaffold(
      body: Builder(
        builder: (context) => Center(
          child: FilledButton(
            onPressed: () {
              showSessionActionSheet(
                context: context,
                session: session,
                api: _FakeApiClient(),
              );
            },
            child: const Text('打开菜单'),
          ),
        ),
      ),
    ),
  );
}

void main() {
  testWidgets('课程卡长按触发快捷操作回调', (tester) async {
    var longPressed = false;
    await tester.pumpWidget(
      MaterialApp(
        theme: buildAppTheme(),
        home: Scaffold(
          body: Padding(
            padding: const EdgeInsets.all(16),
            child: SessionCard(
              session: _session(),
              onLongPress: () => longPressed = true,
            ),
          ),
        ),
      ),
    );

    await tester.longPress(find.byType(SessionCard));

    expect(longPressed, isTrue);
  });

  testWidgets('本人课程长按菜单提供全部现有操作', (tester) async {
    await tester.pumpWidget(_sheetHost(_session()));
    await tester.tap(find.text('打开菜单'));
    await tester.pumpAndSettle();

    expect(find.text('刑事侦查专题研究'), findsOneWidget);
    for (final label in [
      '教学备注',
      '进度交接',
      '教学计划',
      'AI备课',
      '登记学时',
      '查询调课',
      '课程详情',
    ]) {
      expect(find.text(label), findsOneWidget);
    }
    expect(find.byKey(const Key('sessionActionViewOnly')), findsNothing);
  });

  testWidgets('非本人课程菜单只保留查看详情', (tester) async {
    await tester.pumpWidget(_sheetHost(_session(isMine: false)));
    await tester.tap(find.text('打开菜单'));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('sessionActionViewOnly')), findsOneWidget);
    expect(find.text('课程详情'), findsOneWidget);
    expect(find.text('教学备注'), findsNothing);
    expect(find.text('查询调课'), findsNothing);
  });

  testWidgets('320 宽和较大字体下课程卡不溢出', (tester) async {
    tester.view.physicalSize = const Size(320, 640);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      MaterialApp(
        theme: buildAppTheme(),
        builder: (context, child) {
          final media = MediaQuery.of(context);
          return MediaQuery(
            data: media.copyWith(textScaler: const TextScaler.linear(1.35)),
            child: child ?? const SizedBox.shrink(),
          );
        },
        home: Scaffold(
          body: Padding(
            padding: const EdgeInsets.all(16),
            child: SessionCard(session: _session()),
          ),
        ),
      ),
    );

    expect(tester.takeException(), isNull);
    expect(find.text('刑事侦查专题研究'), findsOneWidget);
    expect(find.textContaining('备注：'), findsOneWidget);
  });
}
