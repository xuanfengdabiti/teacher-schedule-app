import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:teacher_schedule/app/theme.dart';
import 'package:teacher_schedule/core/network/api_client.dart';
import 'package:teacher_schedule/core/storage/token_store.dart';
import 'package:teacher_schedule/features/collaboration/inquiry_compose_page.dart';
import 'package:teacher_schedule/features/collaboration/notification_list_page.dart';
import 'package:teacher_schedule/features/collaboration/reschedule_page.dart';

typedef GetHandler = Future<Response<dynamic>> Function(
  String path,
  Map<String, dynamic>? query,
);
typedef PostHandler = Future<Response<dynamic>> Function(
  String path,
  Object? data,
);

class _FakeApiClient extends ApiClient {
  _FakeApiClient({required this.onGet, required this.onPost})
    : super(
        baseUrl: 'http://test.local/api/v1/',
        tokenStore: MemoryTokenStore(),
      );

  final GetHandler onGet;
  final PostHandler onPost;
  final List<({String path, Map<String, dynamic>? query})> getCalls = [];
  final List<({String path, Object? data})> postCalls = [];

  @override
  Future<Response<dynamic>> get(String path, {Map<String, dynamic>? query}) {
    getCalls.add((path: path, query: query));
    return onGet(path, query);
  }

  @override
  Future<Response<dynamic>> post(String path, {Object? data}) {
    postCalls.add((path: path, data: data));
    return onPost(path, data);
  }
}

Response<dynamic> _response(Object? data) => Response<dynamic>(
  requestOptions: RequestOptions(path: '/'),
  data: data,
  statusCode: 200,
);

Widget _app(Widget child) => MaterialApp(theme: buildAppTheme(), home: child);

Future<void> _pumpPage(WidgetTester tester, Widget page) async {
  tester.view.physicalSize = const Size(800, 1600);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(_app(page));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('已知冲突时段不能选中提交', (tester) async {
    final api = _FakeApiClient(
      onGet: (_, _) async => _response(const {}),
      onPost: (path, _) async {
        expect(path, 'sessions/s1/reschedule-options/');
        return _response({
          'session_id': 's1',
          'schedule_version': 3,
          'checked_at': '2026-10-27T09:00:00+08:00',
          'data_completeness': 'complete',
          'options': [
            {
              'start_at': '2026-10-28T10:00:00+08:00',
              'end_at': '2026-10-28T11:40:00+08:00',
              'start_period': 3,
              'end_period': 4,
              'classroom_id': 'room-free',
              'classroom_code': 'A101',
              'eligibility': 'available',
              'student_conflict_count': 0,
              'conflicts': [],
              'missing_data': [],
              'reasons': [],
            },
            {
              'start_at': '2026-10-28T14:00:00+08:00',
              'end_at': '2026-10-28T15:40:00+08:00',
              'start_period': 5,
              'end_period': 6,
              'classroom_id': 'room-busy',
              'classroom_code': 'B202',
              'eligibility': 'conflict',
              'student_conflict_count': 2,
              'conflicts': [
                {
                  'category': 'student',
                  'session_id': 'other',
                  'course_name': '数据库',
                  'reason': null,
                  'student_count': 2,
                },
              ],
              'missing_data': [],
              'reasons': ['学生冲突 2 人：数据库'],
            },
          ],
        });
      },
    );

    await _pumpPage(tester, ReschedulePage(api: api, sessionId: 's1'));

    await tester.ensureVisible(find.byKey(const ValueKey('reschedule-reason')));
    await tester.enterText(
      find.byKey(const ValueKey('reschedule-reason')),
      '需要陪学生参加比赛',
    );

    final conflict = find.byKey(const ValueKey('reschedule-option-conflict-0'));
    await tester.ensureVisible(conflict);
    await tester.tap(conflict);
    await tester.pumpAndSettle();

    final submitFinder = find.byKey(const ValueKey('reschedule-submit'));
    await tester.ensureVisible(submitFinder);
    final disabled = tester.widget<FilledButton>(submitFinder);
    expect(disabled.onPressed, isNull);

    final free = find.byKey(const ValueKey('reschedule-option-available-0'));
    await tester.ensureVisible(free);
    await tester.tap(free);
    await tester.pumpAndSettle();
    final enabled = tester.widget<FilledButton>(submitFinder);
    expect(enabled.onPressed, isNotNull);
  });

  testWidgets('协查只提交勾选的收件人', (tester) async {
    Object? submitted;
    final api = _FakeApiClient(
      onGet: (path, _) async {
        expect(path, 'inquiries/candidates/');
        return _response({
          'classroom': {
            'id': 'room-1',
            'classroom_code': 'A101',
            'campus': '主校区',
            'building': '一教',
            'room_no': '101',
          },
          'from_at': '2026-10-28T08:00:00+08:00',
          'to_at': '2026-10-28T10:00:00+08:00',
          'candidates': [
            {
              'teacher': {'id': 't1', 'name': '张老师', 'department': '计算机学院'},
              'related_session_ids': ['s1'],
              'course_names': ['数据库'],
              'sessions': [
                {
                  'id': 's1',
                  'course_name': '数据库',
                  'start_at': '2026-10-28T08:00:00+08:00',
                  'end_at': '2026-10-28T09:40:00+08:00',
                  'start_period': 1,
                  'end_period': 2,
                },
              ],
            },
            {
              'teacher': {'id': 't2', 'name': '李老师', 'department': '数学学院'},
              'related_session_ids': ['s2'],
              'course_names': ['高等数学'],
              'sessions': [
                {
                  'id': 's2',
                  'course_name': '高等数学',
                  'start_at': '2026-10-28T08:00:00+08:00',
                  'end_at': '2026-10-28T09:40:00+08:00',
                  'start_period': 1,
                  'end_period': 2,
                },
              ],
            },
          ],
        });
      },
      onPost: (path, data) async {
        expect(path, 'inquiries/');
        submitted = data;
        return _response({'id': 'i1'});
      },
    );

    await _pumpPage(
      tester,
      InquiryComposePage(api: api, classroomId: 'room-1'),
    );

    await tester.enterText(
      find.byKey(const ValueKey('inquiry-description')),
      '黑色雨伞，伞柄有白色贴纸',
    );

    final first = find.byKey(const ValueKey('inquiry-candidate-t1'));
    await tester.ensureVisible(first);
    await tester.tap(
      find.descendant(of: first, matching: find.byType(Checkbox)),
    );
    await tester.pumpAndSettle();

    final submit = find.byKey(const ValueKey('inquiry-submit'));
    await tester.ensureVisible(submit);
    await tester.tap(submit);
    await tester.pumpAndSettle();

    expect(submitted, isA<Map<String, dynamic>>());
    final payload = submitted! as Map<String, dynamic>;
    expect(payload['recipient_ids'], ['t1']);
    expect(payload['item_description'], '黑色雨伞，伞柄有白色贴纸');

    await tester.tap(find.text('知道了'));
    await tester.pumpAndSettle();
  });

  testWidgets('消息加载更多不会重复请求同一游标', (tester) async {
    final api = _FakeApiClient(
      onGet: (path, query) async {
        expect(path, 'notifications/');
        if (query?['cursor'] == 'cursor-1') {
          return _response({
            'results': [
              {
                'id': 'n2',
                'type': 'inquiry',
                'object_type': 'inquiry',
                'object_id': 'i2',
                'title': '第二条消息',
                'body': '第二条正文',
                'created_at': '2026-10-27T09:00:00+08:00',
                'read_at': null,
                'is_read': false,
              },
            ],
            'next_cursor': null,
            'unread_count': 1,
          });
        }
        return _response({
          'results': [
            {
              'id': 'n1',
              'type': 'schedule',
              'object_type': 'session',
              'object_id': 's1',
              'title': '第一条消息',
              'body': '第一条正文',
              'created_at': '2026-10-27T10:00:00+08:00',
              'read_at': null,
              'is_read': false,
            },
          ],
          'next_cursor': 'cursor-1',
          'unread_count': 2,
        });
      },
      onPost: (_, _) async => _response(const {}),
    );

    await _pumpPage(tester, NotificationListPage(api: api));

    final loadMore = find.byKey(const ValueKey('notifications-load-more'));
    expect(loadMore, findsOneWidget);
    await tester.ensureVisible(loadMore);
    await tester.tap(loadMore);
    await tester.pumpAndSettle();

    expect(find.text('第一条消息'), findsOneWidget);
    expect(find.text('第二条消息'), findsOneWidget);
    expect(loadMore, findsNothing);
    expect(
      api.getCalls.where((call) => call.query?['cursor'] == 'cursor-1').length,
      1,
    );
  });
}
