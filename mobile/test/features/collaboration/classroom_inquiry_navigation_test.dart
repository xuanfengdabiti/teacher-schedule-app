import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:teacher_schedule/app/theme.dart';
import 'package:teacher_schedule/core/network/api_client.dart';
import 'package:teacher_schedule/core/storage/token_store.dart';
import 'package:teacher_schedule/features/collaboration/classroom_inquiry_page.dart';
import 'package:teacher_schedule/features/collaboration/classroom_usage_page.dart';

class _FakeApiClient extends ApiClient {
  _FakeApiClient({required this.onGet, this.onPost})
    : super(
        baseUrl: 'http://test.local/api/v1/',
        tokenStore: MemoryTokenStore(),
      );

  final Future<Response<dynamic>> Function(String, Map<String, dynamic>?) onGet;
  final Future<Response<dynamic>> Function(String, Object?)? onPost;

  @override
  Future<Response<dynamic>> get(String path, {Map<String, dynamic>? query}) {
    return onGet(path, query);
  }

  @override
  Future<Response<dynamic>> post(String path, {Object? data}) {
    final handler = onPost;
    if (handler == null) {
      throw StateError('unexpected POST $path');
    }
    return handler(path, data);
  }
}

Response<dynamic> _response(Object? data) => Response<dynamic>(
  requestOptions: RequestOptions(path: '/'),
  data: data,
  statusCode: 200,
);

Map<String, dynamic> _classroomPayload() => {
  'id': 'room-1',
  'classroom_code': 'A101',
  'campus': '主校区',
  'building': '一教',
  'room_no': '101',
  'capacity': 60,
  'is_active': true,
};

Map<String, dynamic> _inquiryPayload(String id, String description) => {
  'id': id,
  'classroom': _classroomPayload(),
  'from_at': '2026-10-27T08:10:00+08:00',
  'to_at': '2026-10-27T17:10:00+08:00',
  'item_description': description,
  'status': 'open',
  'initiator': {'id': 't2', 'name': '当前教师', 'department': ''},
  'recipients': [],
  'replies': [],
  'closed_at': null,
  'close_note': '',
  'notice': '排定课程记录，不代表实际到场或物品去向。',
  'created_at': '2026-10-27T09:00:00+08:00',
  'is_initiator': true,
};

Map<String, dynamic> _pagedInquiries(List<Map<String, dynamic>> items) => {
  'count': items.length,
  'next': null,
  'previous': null,
  'results': items,
};

void main() {
  testWidgets('统一页切换两个标签，两个入口仍可用', (tester) async {
    final api = _FakeApiClient(
      onGet: (path, query) async {
        if (path == 'classrooms/') {
          return _response({
            'count': 1,
            'next': null,
            'previous': null,
            'results': [_classroomPayload()],
          });
        }
        if (path == 'inquiries/') {
          expect(query?['box'], 'received');
          return _response(_pagedInquiries(const []));
        }
        throw StateError('unexpected path: $path');
      },
    );

    tester.view.physicalSize = const Size(800, 1400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      MaterialApp(
        theme: buildAppTheme(),
        home: ClassroomInquiryPage(api: api),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('教室与失物'), findsOneWidget);
    expect(find.text('A101'), findsOneWidget);
    expect(find.byKey(const Key('hubStartInquiryButton')), findsOneWidget);

    await tester.tap(find.text('失物消息'));
    await tester.pumpAndSettle();
    expect(find.text('收到的'), findsOneWidget);
    expect(find.text('暂无失物消息'), findsOneWidget);
    expect(find.byKey(const Key('hubStartInquiryButton')), findsOneWidget);

    await tester.tap(find.text('教室记录'));
    await tester.pumpAndSettle();
    expect(find.text('A101'), findsOneWidget);
  });

  testWidgets('从失物页直接发起：选教室→查教师→发送后回到我发起的', (tester) async {
    var created = false;
    var sentLoads = 0;
    var candidateRequests = 0;
    final api = _FakeApiClient(
      onGet: (path, query) async {
        if (path == 'classrooms/') {
          return _response({
            'count': 2,
            'next': null,
            'previous': null,
            'results': [
              _classroomPayload(),
              {
                'id': 'room-2',
                'classroom_code': 'B201',
                'campus': '主校区',
                'building': '二教',
                'room_no': '201',
                'capacity': 40,
                'is_active': true,
              },
            ],
          });
        }
        if (path == 'inquiries/candidates/') {
          candidateRequests++;
          expect(query?['classroom_id'], 'room-1');
          return _response({
            'classroom': _classroomPayload(),
            'from_at': '2026-10-27T08:10:00+08:00',
            'to_at': '2026-10-27T17:10:00+08:00',
            'candidates': [
              {
                'teacher': {'id': 't1', 'name': '李老师', 'department': ''},
                'related_session_ids': ['s1'],
                'course_names': ['示例课程'],
                'sessions': [],
              },
            ],
          });
        }
        if (path == 'inquiries/') {
          if (query?['box'] == 'received') {
            return _response(_pagedInquiries(const []));
          }
          expect(query?['box'], 'sent');
          sentLoads++;
          return _response(
            _pagedInquiries(
              created ? [_inquiryPayload('inquiry-new', '蓝色水杯')] : const [],
            ),
          );
        }
        throw StateError('unexpected path: $path');
      },
      onPost: (path, data) async {
        if (path == 'inquiries/') {
          final body = data! as Map;
          expect(body['classroom_id'], 'room-1');
          expect(body['item_description'], '蓝色水杯');
          created = true;
          return _response(_inquiryPayload('inquiry-new', '蓝色水杯'));
        }
        throw StateError('unexpected POST $path');
      },
    );

    tester.view.physicalSize = const Size(800, 1400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      MaterialApp(
        theme: buildAppTheme(),
        home: ClassroomInquiryPage(api: api),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.text('失物消息'));
    await tester.pumpAndSettle();
    expect(find.text('暂无失物消息'), findsOneWidget);

    await tester.tap(find.byKey(const Key('hubStartInquiryButton')));
    await tester.pumpAndSettle();

    expect(find.text('发起失物询问'), findsOneWidget);
    expect(find.text('请先选择教室'), findsOneWidget);
    expect(candidateRequests, 0, reason: '未选教室不应查询教师');

    await tester.tap(find.byKey(const ValueKey('inquiry-classroom-pick')));
    await tester.pumpAndSettle();
    expect(find.text('选择教室'), findsOneWidget);

    final room = find.byKey(const ValueKey('classroom-room-1'));
    await tester.ensureVisible(room);
    await tester.tap(room);
    await tester.pumpAndSettle();

    expect(candidateRequests, 1);
    expect(find.text('主校区 · 一教 · 101'), findsOneWidget);

    await tester.enterText(
      find.byKey(const ValueKey('inquiry-description')),
      '蓝色水杯',
    );
    await tester.tap(
      find.descendant(
        of: find.byKey(const ValueKey('inquiry-candidate-t1')),
        matching: find.byType(Checkbox),
      ),
    );
    await tester.pumpAndSettle();

    final submit = find.byKey(const ValueKey('inquiry-submit'));
    await tester.ensureVisible(submit);
    await tester.tap(submit);
    await tester.pumpAndSettle();

    await tester.tap(find.text('知道了'));
    await tester.pumpAndSettle();
    await tester.pump(const Duration(milliseconds: 400));
    await tester.pumpAndSettle();

    expect(find.text('教室与失物'), findsOneWidget);
    expect(find.text('我发起的'), findsOneWidget);
    expect(find.text('蓝色水杯'), findsOneWidget);
    expect(sentLoads, greaterThanOrEqualTo(1));
    expect(find.byKey(const Key('hubStartInquiryButton')), findsOneWidget);
  });

  testWidgets('历史日期进入教室记录后，发起询问沿用该日期', (tester) async {
    Map<String, dynamic>? candidatesQuery;
    final api = _FakeApiClient(
      onGet: (path, query) async {
        if (path == 'classrooms/room-1/usage/') {
          expect(query?['date'], '2026-10-20');
          return _response({
            'classroom': _classroomPayload(),
            'date': '2026-10-20',
            'notice': '排定课程记录，不代表实际到场或物品去向。',
            'data_completeness': 'complete',
            'entries': [],
          });
        }
        if (path == 'inquiries/candidates/') {
          candidatesQuery = query;
          return _response({
            'classroom': _classroomPayload(),
            'from_at': '2026-10-20T08:10:00+08:00',
            'to_at': '2026-10-20T17:10:00+08:00',
            'candidates': [],
          });
        }
        throw StateError('unexpected path: $path');
      },
    );

    tester.view.physicalSize = const Size(800, 1400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      MaterialApp(
        theme: buildAppTheme(),
        home: ClassroomUsagePage(
          api: api,
          classroomId: 'room-1',
          initialDate: DateTime(2026, 10, 20),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.text('发起失物询问'));
    await tester.pumpAndSettle();

    expect(candidatesQuery?['from_at'], contains('2026-10-20T08:10'));
    expect(candidatesQuery?['to_at'], contains('2026-10-20T17:10'));
    expect(find.text('2026-10-20 08:10'), findsOneWidget);
  });

  testWidgets('发送成功后回到失物消息的“我发起的”并刷新', (tester) async {
    var created = false;
    var sentLoads = 0;
    final api = _FakeApiClient(
      onGet: (path, query) async {
        if (path == 'classrooms/') {
          return _response({
            'count': 1,
            'next': null,
            'previous': null,
            'results': [_classroomPayload()],
          });
        }
        if (path == 'classrooms/room-1/usage/') {
          return _response({
            'classroom': _classroomPayload(),
            'date': '2026-10-27',
            'notice': '排定课程记录，不代表实际到场或物品去向。',
            'data_completeness': 'complete',
            'entries': [],
          });
        }
        if (path == 'inquiries/candidates/') {
          return _response({
            'classroom': _classroomPayload(),
            'from_at': '2026-10-27T08:10:00+08:00',
            'to_at': '2026-10-27T17:10:00+08:00',
            'candidates': [
              {
                'teacher': {'id': 't1', 'name': '李老师', 'department': ''},
                'related_session_ids': ['s1'],
                'course_names': ['示例课程'],
                'sessions': [],
              },
            ],
          });
        }
        if (path == 'inquiries/') {
          if (query?['box'] == 'received') {
            return _response(_pagedInquiries(const []));
          }
          expect(query?['box'], 'sent');
          sentLoads++;
          return _response(
            _pagedInquiries(
              created ? [_inquiryPayload('inquiry-new', '黑色雨伞')] : const [],
            ),
          );
        }
        throw StateError('unexpected path: $path');
      },
      onPost: (path, data) async {
        if (path == 'inquiries/') {
          created = true;
          return _response(_inquiryPayload('inquiry-new', '黑色雨伞'));
        }
        throw StateError('unexpected POST $path');
      },
    );

    tester.view.physicalSize = const Size(800, 1400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      MaterialApp(
        theme: buildAppTheme(),
        home: ClassroomInquiryPage(api: api),
      ),
    );
    await tester.pumpAndSettle();

    // Load the received tab first so the list exists and stays cached.
    await tester.tap(find.text('失物消息'));
    await tester.pumpAndSettle();
    expect(find.text('暂无失物消息'), findsOneWidget);

    await tester.tap(find.text('教室记录'));
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.text('A101'));
    await tester.tap(find.text('A101'));
    await tester.pumpAndSettle();

    await tester.tap(find.text('发起失物询问'));
    await tester.pumpAndSettle();

    await tester.enterText(
      find.byKey(const ValueKey('inquiry-description')),
      '黑色雨伞',
    );
    await tester.tap(
      find.descendant(
        of: find.byKey(const ValueKey('inquiry-candidate-t1')),
        matching: find.byType(Checkbox),
      ),
    );
    await tester.pumpAndSettle();

    final submit = find.byKey(const ValueKey('inquiry-submit'));
    await tester.ensureVisible(submit);
    await tester.tap(submit);
    await tester.pumpAndSettle();

    await tester.tap(find.text('知道了'));
    await tester.pumpAndSettle();
    await tester.pump(const Duration(milliseconds: 400));
    await tester.pumpAndSettle();

    expect(find.text('教室与失物'), findsOneWidget);
    expect(find.text('我发起的'), findsOneWidget);
    expect(find.text('黑色雨伞'), findsOneWidget);
    expect(sentLoads, greaterThanOrEqualTo(1));
  });
}
