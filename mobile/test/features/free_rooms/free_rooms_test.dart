import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:teacher_schedule/app/theme.dart';
import 'package:teacher_schedule/core/network/api_client.dart';
import 'package:teacher_schedule/core/storage/token_store.dart';
import 'package:teacher_schedule/features/free_rooms/free_rooms_models.dart';
import 'package:teacher_schedule/features/free_rooms/free_rooms_page.dart';
import 'package:teacher_schedule/features/free_rooms/free_rooms_repository.dart';

typedef GetHandler = Future<Response<dynamic>> Function(
  String path,
  Map<String, dynamic>? query,
);

class _FakeApiClient extends ApiClient {
  _FakeApiClient({required this.onGet})
    : super(
        baseUrl: 'http://test.local/api/v1/',
        tokenStore: MemoryTokenStore(),
      );

  final GetHandler onGet;
  final List<({String path, Map<String, dynamic>? query})> calls = [];

  @override
  Future<Response<dynamic>> get(String path, {Map<String, dynamic>? query}) {
    calls.add((path: path, query: query));
    return onGet(path, query);
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

Map<String, dynamic> _pagePayload({required bool withUnverified}) {
  return {
    'date': '2026-11-02',
    'start_time': '08:00',
    'end_time': '09:40',
    'checked_at': '2026-11-02T08:00:00+08:00',
    'available_count': 1,
    'unverified_count': withUnverified ? 1 : 0,
    'count': withUnverified ? 2 : 1,
    'next': null,
    'previous': null,
    'results': [
      {
        'id': 'room-available',
        'classroom_code': 'A101',
        'campus': '主校区',
        'building': '一教',
        'room_no': '101',
        'capacity': 60,
        'is_active': true,
        'availability': 'available',
        'data_completeness': 'complete',
      },
      if (withUnverified)
        {
          'id': 'room-unverified',
          'classroom_code': 'B202',
          'campus': '主校区',
          'building': '二教',
          'room_no': '202',
          'capacity': 40,
          'is_active': true,
          'availability': 'unverified',
          'data_completeness': 'incomplete',
        },
    ],
  };
}

void main() {
  test('日期、时间、容量和分页正确传给 API', () async {
    final api = _FakeApiClient(
      onGet: (path, _) async {
        expect(path, 'classrooms/availability/');
        return _response(_pagePayload(withUnverified: false));
      },
    );
    final repository = FreeRoomsRepository(api);

    final result = await repository.search(
      FreeRoomQuery(
        date: DateTime(2026, 11, 2),
        startTime: '08:00',
        endTime: '09:40',
        q: 'B',
        campus: '主校区',
        building: '示例楼',
        minCapacity: 50,
      ),
      page: 2,
    );

    final query = api.calls.single.query!;
    expect(query['date'], '2026-11-02');
    expect(query['start_time'], '08:00');
    expect(query['end_time'], '09:40');
    expect(query['min_capacity'], 50);
    expect(query['q'], 'B');
    expect(query['campus'], '主校区');
    expect(query['building'], '示例楼');
    expect(query['page'], 2);
    expect(result.availableCount, 1);
    expect(result.results.single.classroomCode, 'A101');
  });

  testWidgets('空闲和需确认使用不同状态渲染', (tester) async {
    final api = _FakeApiClient(
      onGet: (path, _) async {
        expect(path, 'classrooms/availability/');
        return _response(_pagePayload(withUnverified: true));
      },
    );

    await _pumpPage(tester, FreeRoomsPage(api: api));

    expect(find.text('空闲 1'), findsOneWidget);
    expect(find.text('需确认 1'), findsOneWidget);
    expect(find.text('A101'), findsOneWidget);
    expect(find.text('B202'), findsOneWidget);
    expect(find.text('空闲'), findsOneWidget);
    expect(find.text('需确认'), findsOneWidget);
    expect(find.text('容量 40 · 部分安排待确认'), findsOneWidget);
  });

  testWidgets('点击结果按查询日期打开教室记录', (tester) async {
    final api = _FakeApiClient(
      onGet: (path, query) async {
        if (path == 'classrooms/availability/') {
          return _response(_pagePayload(withUnverified: false));
        }
        expect(path, 'classrooms/room-available/usage/');
        expect(query?['date'], '2026-11-02');
        return _response({
          'classroom': {
            'id': 'room-available',
            'classroom_code': 'A101',
            'campus': '主校区',
            'building': '一教',
            'room_no': '101',
            'capacity': 60,
            'is_active': true,
          },
          'date': '2026-11-02',
          'notice': '排定课程记录，不代表实际到场或物品去向。',
          'data_completeness': 'complete',
          'entries': [],
        });
      },
    );

    await _pumpPage(tester, FreeRoomsPage(api: api));

    final card = find.byKey(const ValueKey('free-room-room-available'));
    await tester.ensureVisible(card);
    await tester.tap(card);
    await tester.pumpAndSettle();

    expect(find.text('教室记录'), findsOneWidget);
    expect(
      api.calls.any(
        (call) =>
            call.path == 'classrooms/room-available/usage/' &&
            call.query?['date'] == '2026-11-02',
      ),
      isTrue,
    );
  });
}
