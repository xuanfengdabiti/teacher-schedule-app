import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:teacher_schedule/features/schedule/models.dart';
import 'package:teacher_schedule/features/schedule/week_overview.dart';

final _monday = DateTime(2026, 3, 2);

List<Period> _periods() {
  return List<Period>.generate(
    8,
    (index) => Period(
      number: index + 1,
      startTime: '${(8 + index).toString().padLeft(2, '0')}:00',
      endTime: '${(8 + index).toString().padLeft(2, '0')}:45',
      blockCode: index < 4 ? 'morning' : 'afternoon',
    ),
  );
}

Session _session({
  required String id,
  required DateTime date,
  int startPeriod = 1,
  int endPeriod = 2,
  String courseName = '高等数学',
  String building = '教学楼A',
  String roomNo = '301',
  String teacherName = '王老师',
  String note = '',
}) {
  final startAt = DateTime(date.year, date.month, date.day, 8);
  return Session(
    id: id,
    offeringId: 'offering-$id',
    offeringCode: 'OFF-$id',
    courseCode: 'COURSE-$id',
    courseName: courseName,
    teachingClass: const TeachingClass(
      id: 'class-1',
      classCode: 'CS2026-1',
      name: '计算机一班',
    ),
    date: date,
    weekday: date.weekday,
    teachingWeek: 1,
    startPeriod: startPeriod,
    endPeriod: endPeriod,
    startAt: startAt,
    endAt: startAt.add(const Duration(minutes: 95)),
    classroom: Classroom(
      id: 'room-$roomNo',
      classroomCode: roomNo,
      campus: '主校区',
      building: building,
      roomNo: roomNo,
    ),
    teachers: [SessionTeacher(id: 'teacher-1', name: teacherName)],
    status: 'scheduled',
    statusReason: '',
    isMine: true,
    isMoved: false,
    version: 1,
    previousNotePreview: note,
  );
}

Future<void> _pumpOverview(
  WidgetTester tester, {
  required Size size,
  required List<Session> sessions,
  double textScale = 1,
  ValueChanged<Session>? onOpenSession,
}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);

  await tester.pumpWidget(
    MaterialApp(
      debugShowCheckedModeBanner: false,
      home: Builder(
        builder: (context) => MediaQuery(
          data: MediaQuery.of(context)
              .copyWith(textScaler: TextScaler.linear(textScale)),
          child: Scaffold(
            body: WeekOverview(
              monday: _monday,
              sessions: sessions,
              periods: _periods(),
              onOpenSession: onOpenSession ?? (_) {},
            ),
          ),
        ),
      ),
    ),
  );
  await tester.pump();
}

TransformationController _controller(WidgetTester tester) {
  return tester
      .widget<InteractiveViewer>(find.byKey(const Key('weekOverviewViewport')))
      .transformationController!;
}

Future<void> _pinch(
  WidgetTester tester,
  Offset center, {
  double start = 24,
  double end = 64,
}) async {
  final first = await tester.createGesture(pointer: 11);
  final second = await tester.createGesture(pointer: 12);
  await first.down(center - Offset(start, 0));
  await second.down(center + Offset(start, 0));
  await tester.pump();

  for (var step = 1; step <= 4; step++) {
    final distance = start + (end - start) * step / 4;
    await first.moveTo(center - Offset(distance, 0));
    await second.moveTo(center + Offset(distance, 0));
    await tester.pump(const Duration(milliseconds: 16));
  }

  await first.up();
  await second.up();
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('真实双指缩放后可平移，复位恢复 identity', (tester) async {
    await _pumpOverview(
      tester,
      size: const Size(400, 800),
      sessions: [
        _session(id: 'session-1', date: _monday),
        _session(
          id: 'session-2',
          date: _monday.add(const Duration(days: 3)),
          startPeriod: 3,
          endPeriod: 4,
          courseName: '线性代数',
        ),
      ],
    );

    final viewer = find.byKey(const Key('weekOverviewViewport'));
    final controller = _controller(tester);
    expect(controller.value.getMaxScaleOnAxis(), closeTo(1, 0.000001));
    expect(find.text('100%'), findsOneWidget);

    await _pinch(tester, tester.getCenter(viewer));

    final zoomedScale = controller.value.getMaxScaleOnAxis();
    expect(zoomedScale, greaterThan(1.2));
    expect(find.text('100%'), findsNothing);

    final beforePan = controller.value.getTranslation();
    await tester.drag(viewer, const Offset(-48, -32));
    await tester.pumpAndSettle();
    final afterPan = controller.value.getTranslation();
    expect(
      (afterPan.x - beforePan.x).abs() + (afterPan.y - beforePan.y).abs(),
      greaterThan(1),
      reason: '放大后单指拖动应平移课表',
    );
    expect(
      controller.value.getMaxScaleOnAxis(),
      closeTo(zoomedScale, 0.001),
      reason: '平移不应改变缩放比例',
    );

    await tester.tap(find.byKey(const Key('weekOverviewReset')));
    await tester.pumpAndSettle();

    expect(controller.value.getMaxScaleOnAxis(), closeTo(1, 0.000001));
    expect(
      controller.value.storage,
      orderedEquals(Matrix4.identity().storage),
      reason: '复位应同时恢复比例和位置',
    );
    expect(find.text('100%'), findsOneWidget);
  });

  testWidgets('从课程块中心双指放大不误开详情，抬指后点击仍对课次', (tester) async {
    Session? opened;
    await _pumpOverview(
      tester,
      size: const Size(400, 800),
      sessions: [
        _session(id: 'session-1', date: _monday),
        _session(
          id: 'session-2',
          date: _monday.add(const Duration(days: 1)),
          startPeriod: 3,
          endPeriod: 4,
          courseName: '大学物理',
        ),
      ],
      onOpenSession: (session) => opened = session,
    );

    final block = find.byKey(const ValueKey('overview-session-session-2'));
    await _pinch(tester, tester.getCenter(block), start: 18, end: 48);

    final controller = _controller(tester);
    expect(controller.value.getMaxScaleOnAxis(), greaterThan(1.2));
    expect(opened, isNull, reason: '双指缩放不应触发课程详情');

    await tester.tap(block);
    await tester.pumpAndSettle();

    expect(opened, isNotNull);
    expect(opened!.id, 'session-2');
    expect(opened!.courseName, '大学物理');
  });

  testWidgets('不再显示手势说明和加减按钮', (tester) async {
    await _pumpOverview(
      tester,
      size: const Size(400, 800),
      sessions: [_session(id: 'session-1', date: _monday)],
    );

    expect(find.textContaining('双指缩放'), findsNothing);
    expect(find.textContaining('天有课'), findsNothing);
    expect(find.textContaining('拖动'), findsNothing);
    expect(find.byIcon(Icons.pinch_outlined), findsNothing);
    expect(find.byKey(const Key('weekOverviewZoomIn')), findsNothing);
    expect(find.byKey(const Key('weekOverviewZoomOut')), findsNothing);
    expect(find.byIcon(Icons.add), findsNothing);
    expect(find.byIcon(Icons.remove), findsNothing);
    expect(find.byKey(const Key('weekOverviewReset')), findsOneWidget);
  });

  testWidgets('先单指轻拖再加第二指仍可双指放大', (tester) async {
    await _pumpOverview(
      tester,
      size: const Size(400, 800),
      sessions: [_session(id: 'session-1', date: _monday)],
    );

    final viewer = find.byKey(const Key('weekOverviewViewport'));
    final controller = _controller(tester);
    final center = tester.getCenter(viewer);
    final first = await tester.createGesture(pointer: 21);
    final second = await tester.createGesture(pointer: 22);

    await first.down(center - const Offset(30, 0));
    await first.moveTo(center - const Offset(50, 0));
    await tester.pump();

    await second.down(center + const Offset(20, 0));
    await tester.pump();
    for (var step = 1; step <= 4; step++) {
      await first.moveTo(center - Offset(50 + step * 10, 0));
      await second.moveTo(center + Offset(20 + step * 10, 0));
      await tester.pump(const Duration(milliseconds: 16));
    }
    await first.up();
    await second.up();
    await tester.pumpAndSettle();

    expect(controller.value.getMaxScaleOnAxis(), greaterThan(1.1));
  });

  testWidgets('双指缩小回1倍并保持平移边界，复位归零', (tester) async {
    await _pumpOverview(
      tester,
      size: const Size(400, 430),
      sessions: [
        _session(
          id: 'session-1',
          date: _monday.add(const Duration(days: 4)),
          startPeriod: 7,
          endPeriod: 8,
          courseName: '晚课专题',
        ),
      ],
    );

    final viewer = find.byKey(const Key('weekOverviewViewport'));
    final canvas = find.byKey(const Key('weekOverviewCanvas'));
    final controller = _controller(tester);
    final viewportSize = tester.getSize(viewer);
    final childSize = tester.getSize(canvas);

    await _pinch(tester, tester.getCenter(viewer), start: 20, end: 70);
    final zoomedScale = controller.value.getMaxScaleOnAxis();
    expect(zoomedScale, greaterThan(1.2));

    await tester.drag(viewer, const Offset(-1000, -1000));
    await tester.pumpAndSettle();

    final panScale = controller.value.getMaxScaleOnAxis();
    final panTranslation = controller.value.getTranslation();
    expect(
      panTranslation.x,
      closeTo(viewportSize.width - childSize.width * panScale, 0.5),
    );
    expect(
      panTranslation.y,
      closeTo(viewportSize.height - childSize.height * panScale, 0.5),
    );

    await _pinch(tester, tester.getCenter(viewer), start: 80, end: 20);

    final scale = controller.value.getMaxScaleOnAxis();
    final translation = controller.value.getTranslation();
    final minY = viewportSize.height - childSize.height * scale;
    expect(scale, closeTo(1, 0.01));
    expect(translation.x, closeTo(0, 0.5));
    expect(translation.y, lessThanOrEqualTo(0));
    expect(translation.y, greaterThanOrEqualTo(minY - 0.5));

    await tester.tap(find.byKey(const Key('weekOverviewReset')));
    await tester.pumpAndSettle();
    expect(controller.value.storage, orderedEquals(Matrix4.identity().storage));
  });

  testWidgets('父级重建传入新的课程列表不重置当前缩放', (tester) async {
    await _pumpOverview(
      tester,
      size: const Size(400, 800),
      sessions: [_session(id: 'session-1', date: _monday)],
    );

    final controller = _controller(tester);
    final viewer = find.byKey(const Key('weekOverviewViewport'));
    await _pinch(tester, tester.getCenter(viewer), start: 20, end: 60);
    final zoomedScale = controller.value.getMaxScaleOnAxis();
    expect(zoomedScale, greaterThan(1.2));

    await _pumpOverview(
      tester,
      size: const Size(400, 800),
      sessions: [_session(id: 'session-1', date: _monday)],
    );

    expect(identical(controller, _controller(tester)), isTrue);
    expect(
      _controller(tester).value.getMaxScaleOnAxis(),
      closeTo(zoomedScale, 0.001),
      reason: '普通父级重建不应把用户刚放大的课表复位',
    );
  });

  testWidgets('两节课程有空间时直接显示备注摘要', (tester) async {
    const note = '下次讲极限的ε-δ定义与例题';
    await _pumpOverview(
      tester,
      size: const Size(400, 800),
      sessions: [
        _session(
          id: 'session-1',
          date: _monday,
          startPeriod: 1,
          endPeriod: 2,
          note: note,
        ),
      ],
    );

    final noteText = find.byKey(
      const ValueKey('overview-note-preview-session-1'),
    );
    expect(noteText, findsOneWidget);
    expect(find.text('备注：$note'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('overview-note-session-1')),
      findsNothing,
      reason: '备注正文已显示时不再只放图标',
    );
    expect(tester.takeException(), isNull);
  });

  testWidgets('单节空间不足时保留备注图标', (tester) async {
    const note = '下次讲极限的ε-δ定义与例题';
    await _pumpOverview(
      tester,
      size: const Size(400, 800),
      sessions: [
        _session(
          id: 'session-1',
          date: _monday,
          startPeriod: 1,
          endPeriod: 1,
          note: note,
        ),
      ],
    );

    expect(find.text('备注：$note'), findsNothing);
    expect(
      find.byKey(const ValueKey('overview-note-session-1')),
      findsOneWidget,
    );
    expect(tester.takeException(), isNull);
  });

  testWidgets('320 宽和两倍字号下长课名省略且不溢出', (tester) async {
    const longName = '高级数学分析与概率论专题研讨课程（双语教学实验班）';
    await _pumpOverview(
      tester,
      size: const Size(320, 700),
      textScale: 2,
      sessions: [
        _session(
          id: 'session-long',
          date: _monday,
          startPeriod: 1,
          endPeriod: 2,
          courseName: longName,
          building: '综合实验教学楼',
          roomNo: 'A301-2',
          teacherName: '王老师、李老师',
          note: '上次课留下了一个需要继续讨论的开放问题',
        ),
      ],
    );

    expect(tester.takeException(), isNull);
    for (var weekday = 1; weekday <= 7; weekday++) {
      expect(
        find.byKey(ValueKey('overview-day-$weekday')),
        findsOneWidget,
        reason: '总览应显示完整七天',
      );
    }

    final courseText = tester.widget<Text>(find.text(longName));
    expect(courseText.overflow, TextOverflow.ellipsis);
    expect(courseText.maxLines, lessThanOrEqualTo(3));

    final blockRect = tester.getRect(
      find.byKey(const ValueKey('overview-session-session-long')),
    );
    expect(blockRect.left, greaterThanOrEqualTo(0));
    expect(blockRect.right, lessThanOrEqualTo(320));
    expect(tester.takeException(), isNull);
  });

  testWidgets('一倍比例下仍可单指竖向浏览整张课表', (tester) async {
    await _pumpOverview(
      tester,
      size: const Size(400, 430),
      sessions: [
        _session(
          id: 'session-1',
          date: _monday.add(const Duration(days: 4)),
          startPeriod: 7,
          endPeriod: 8,
          courseName: '晚课专题',
        ),
      ],
    );

    final viewer = find.byKey(const Key('weekOverviewViewport'));
    final controller = _controller(tester);
    expect(controller.value.getMaxScaleOnAxis(), closeTo(1, 0.000001));

    await tester.drag(viewer, const Offset(0, -100));
    await tester.pumpAndSettle();

    expect(
      controller.value.getMaxScaleOnAxis(),
      closeTo(1, 0.000001),
      reason: '单指浏览不应改变比例',
    );
    expect(
      controller.value.getTranslation().y,
      lessThan(-20),
      reason: '未缩放时单指仍应能向上浏览节次',
    );
  });
}
