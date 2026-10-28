import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:teacher_schedule/app/theme.dart';
import 'package:teacher_schedule/core/network/api_client.dart';
import 'package:teacher_schedule/core/storage/token_store.dart';
import 'package:teacher_schedule/features/teaching/handoff_compose_page.dart';

class _FakeApiClient extends ApiClient {
  _FakeApiClient({required this.onGet})
    : super(
        baseUrl: 'http://test.local/api/v1/',
        tokenStore: MemoryTokenStore(),
      );

  final Future<Response<dynamic>> Function(String, Map<String, dynamic>?) onGet;

  @override
  Future<Response<dynamic>> get(String path, {Map<String, dynamic>? query}) {
    return onGet(path, query);
  }
}

Response<dynamic> _response(Object? data) => Response<dynamic>(
  requestOptions: RequestOptions(path: '/'),
  data: data,
  statusCode: 200,
);

Map<String, dynamic> _prefill({
  required List<Map<String, dynamic>> targetTeachers,
  bool hasNextSession = true,
}) => {
  'recipients': [
    {'id': 't1', 'name': '示例教师甲', 'is_suggested': true},
  ],
  'suggested_target_session': hasNextSession
      ? {
          'id': 'next-session',
          'date': '2026-11-02',
          'start_period': 1,
          'end_period': 2,
        }
      : null,
  'suggested_target_teachers': targetTeachers,
  'content': '上次未完成内容',
  'note_id': null,
  'note_published_version': null,
};

Future<void> _pumpCompose(
  WidgetTester tester,
  Map<String, dynamic> prefill,
) async {
  tester.view.physicalSize = const Size(800, 1600);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);

  final api = _FakeApiClient(
    onGet: (path, query) async {
      if (path == 'sessions/s1/') {
        return _response({'offering_id': 'offering-1'});
      }
      if (path == 'handoffs/prefill/') {
        return _response(prefill);
      }
      throw StateError('unexpected path: $path');
    },
  );
  await tester.pumpWidget(
    MaterialApp(
      theme: buildAppTheme(),
      home: HandoffComposePage(api: api, sourceSessionId: 's1'),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('下节课独立显示完整授课名单（含本人），接收人标签准确', (tester) async {
    await _pumpCompose(
      tester,
      _prefill(
        targetTeachers: [
          {'id': 't2', 'name': '示例教师乙'},
          {'id': 't1', 'name': '示例教师甲'},
        ],
      ),
    );

    expect(find.text('下节授课：示例教师乙、示例教师甲'), findsOneWidget);
    expect(find.byKey(const Key('handoffNextSessionWhen')), findsOneWidget);
    expect(find.text('11月2日 周一 · 第 1-2 节'), findsWidgets);

    // The sender (t2) is the actual next-session teacher even though the
    // recipient list excludes the sender.
    expect(find.byKey(const ValueKey('recipient-t2')), findsNothing);
    final recipient = find.byKey(const ValueKey('recipient-t1'));
    expect(recipient, findsOneWidget);
    expect(
      find.descendant(of: recipient, matching: find.text('下节授课')),
      findsOneWidget,
    );
    // The label is shown once, not repeated elsewhere.
    expect(find.text('下节授课'), findsOneWidget);
  });

  testWidgets('下一课无授课人时显示暂未安排，不编造名字', (tester) async {
    await _pumpCompose(tester, _prefill(targetTeachers: const []));

    expect(find.text('下节授课：暂未安排授课老师'), findsOneWidget);
    expect(find.text('下节授课'), findsNothing);
    expect(find.byKey(const ValueKey('recipient-t1')), findsOneWidget);
  });

  testWidgets('没有下一课时不显示下节授课行', (tester) async {
    await _pumpCompose(
      tester,
      _prefill(targetTeachers: const [], hasNextSession: false),
    );

    expect(find.byKey(const Key('handoffNextSessionCard')), findsNothing);
    expect(find.textContaining('下节授课'), findsNothing);
  });
}
