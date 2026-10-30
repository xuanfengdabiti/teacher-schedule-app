import 'package:flutter_test/flutter_test.dart';
import 'package:teacher_schedule/core/network/api_client.dart';
import 'package:teacher_schedule/core/network/api_exception.dart';
import 'package:teacher_schedule/core/storage/token_store.dart';
import 'package:teacher_schedule/features/preparation/preparation_repository.dart';

import '../../support/mock_http_adapter.dart';

Map<String, dynamic> _bundlePayload() => {
  'context': {
    'course_name': '高等数学',
    'date': '2026-10-27',
    'start_period': 1,
    'end_period': 2,
    'teaching_minutes': 90,
    'previous_note': '',
    'plan_items': [
      {'title': '极限与连续', 'planned_hours': '4.0'},
    ],
  },
  'draft': null,
  'ai_available': true,
};

Map<String, dynamic> _draftPayload(Map<String, dynamic> body) => {
  'id': 'draft-1',
  'topic': body['topic'],
  'outline': body['outline'],
  'case_study': body['case_study'],
  'discussion_questions': body['discussion_questions'],
  'preparation': body['preparation'],
  'version': (body['base_version'] as int) + 1,
  'generated_model': body['generated_model'],
  'generated_at': body['generated_at'],
  'updated_at': '2026-10-27T10:00:00+08:00',
};

void main() {
  test('生成请求使用局部 55 秒超时，普通 post 仍用 20 秒', () async {
    final adapter = MockHttpAdapter((options) {
      if (options.uri.path.endsWith('/preparation/generate/')) {
        return jsonResponse({
          'topic': '极限的定义',
          'outline': '提纲',
          'case_study': '案例',
          'discussion_questions': '问题',
          'preparation': '准备',
          'generated_model': 'demo-model',
          'generated_at': '2026-10-27T09:00:00+08:00',
        });
      }
      return jsonResponse(_bundlePayload());
    });
    final client = ApiClient(
      baseUrl: 'http://example.test/api/v1/',
      tokenStore: MemoryTokenStore(),
      dio: mockDio(adapter),
    );

    final repository = PreparationRepository(client);
    await repository.generate('session-1', '极限的定义');
    await client.post('auth/logout/');

    final generate = adapter.requests.firstWhere(
      (request) => request.path.endsWith('/preparation/generate/'),
    );
    final ordinary = adapter.requests.firstWhere(
      (request) => request.path.endsWith('/auth/logout/'),
    );
    expect(generate.receiveTimeout, const Duration(seconds: 55));
    expect(ordinary.receiveTimeout, const Duration(seconds: 20));
  });

  test('读取、生成、保存使用备课端点，不触碰备注和学时接口', () async {
    final adapter = MockHttpAdapter((options) {
      final path = options.uri.path;
      if (options.method == 'GET' && path.endsWith('/preparation/')) {
        return jsonResponse(_bundlePayload());
      }
      if (options.method == 'POST' && path.endsWith('/preparation/generate/')) {
        final topic = (options.data as Map)['topic'];
        return jsonResponse({
          'topic': topic,
          'outline': '生成的提纲',
          'case_study': '生成的案例',
          'discussion_questions': '生成的问题',
          'preparation': '生成的准备',
          'generated_model': 'demo-model',
          'generated_at': '2026-10-27T09:00:00+08:00',
        });
      }
      if (options.method == 'PUT' && path.endsWith('/preparation/')) {
        return jsonResponse(
          _draftPayload(Map<String, dynamic>.from(options.data as Map)),
        );
      }
      return jsonResponse({'detail': 'unexpected'}, 404);
    });
    final repository = PreparationRepository(
      ApiClient(
        baseUrl: 'http://example.test/api/v1/',
        tokenStore: MemoryTokenStore(),
        dio: mockDio(adapter),
      ),
    );

    final bundle = await repository.fetch('session-1');
    expect(bundle.context.teachingMinutes, 90);
    expect(bundle.aiAvailable, isTrue);
    expect(bundle.draft, isNull);

    final generated = await repository.generate('session-1', '极限的定义');
    expect(generated.outline, '生成的提纲');
    expect(generated.generatedModel, 'demo-model');

    final saved = await repository.save(
      sessionId: 'session-1',
      baseVersion: 0,
      topic: '极限的定义',
      outline: '教师修改的提纲',
      caseStudy: generated.caseStudy,
      discussionQuestions: generated.discussionQuestions,
      preparation: generated.preparation,
      generatedModel: generated.generatedModel,
      generatedAt: generated.generatedAt,
    );
    expect(saved.version, 1);
    expect(saved.outline, '教师修改的提纲');

    final paths = adapter.requests.map((request) => request.path).toList();
    expect(paths, [
      '/api/v1/sessions/session-1/preparation/',
      '/api/v1/sessions/session-1/preparation/generate/',
      '/api/v1/sessions/session-1/preparation/',
    ]);
    expect(paths.where((path) => path.contains('/notes')), isEmpty);
    expect(paths.where((path) => path.contains('/completion')), isEmpty);
  });

  test('保存 409 保留服务端可读说明并原样抛出，不返回伪造草稿', () async {
    final adapter = MockHttpAdapter(
      (options) => jsonResponse({
        'code': 'PREPARATION_VERSION_CONFLICT',
        'detail': '备课草稿已在其他设备更新，请重新读取后再保存。',
        'field_errors': <String, Object>{},
        'current': {
          'id': 'draft-1',
          'topic': '服务器主题',
          'outline': '',
          'case_study': '',
          'discussion_questions': '',
          'preparation': '',
          'version': 4,
          'generated_model': '',
          'generated_at': null,
          'updated_at': '2026-10-27T10:00:00+08:00',
        },
      }, 409),
    );
    final repository = PreparationRepository(
      ApiClient(
        baseUrl: 'http://example.test/api/v1/',
        tokenStore: MemoryTokenStore(),
        dio: mockDio(adapter),
      ),
    );

    await expectLater(
      repository.save(
        sessionId: 'session-1',
        baseVersion: 1,
        topic: '本机主题',
        outline: '',
        caseStudy: '',
        discussionQuestions: '',
        preparation: '',
      ),
      throwsA(
        isA<ApiException>()
            .having((error) => error.statusCode, 'statusCode', 409)
            .having(
              (error) => error.code,
              'code',
              'PREPARATION_VERSION_CONFLICT',
            )
            .having(
              (error) => error.message,
              'message',
              '备课草稿已在其他设备更新，请重新读取后再保存。',
            ),
      ),
    );
  });

  test('AI 不可用时读取仍返回已有草稿', () async {
    final adapter = MockHttpAdapter(
      (options) => jsonResponse({
        ..._bundlePayload(),
        'ai_available': false,
        'draft': {
          'id': 'draft-1',
          'topic': '已有主题',
          'outline': '已有提纲',
          'case_study': '',
          'discussion_questions': '',
          'preparation': '',
          'version': 2,
          'generated_model': '',
          'generated_at': null,
          'updated_at': '2026-10-26T10:00:00+08:00',
        },
      }),
    );
    final repository = PreparationRepository(
      ApiClient(
        baseUrl: 'http://example.test/api/v1/',
        tokenStore: MemoryTokenStore(),
        dio: mockDio(adapter),
      ),
    );

    final bundle = await repository.fetch('session-1');
    expect(bundle.aiAvailable, isFalse);
    expect(bundle.draft?.topic, '已有主题');
    expect(bundle.draft?.version, 2);
  });
}
