import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:teacher_schedule/core/network/api_client.dart';
import 'package:teacher_schedule/core/network/api_exception.dart';
import 'package:teacher_schedule/core/storage/token_store.dart';

import 'support/mock_http_adapter.dart';

void main() {
  test('401 只刷新一次，保存旋转后的 refresh，并重放原请求', () async {
    final tokens = MemoryTokenStore();
    await tokens.save(access: 'old-access', refresh: 'old-refresh');
    var meCalls = 0;
    var refreshCalls = 0;

    final adapter = MockHttpAdapter((options) {
      if (options.uri.path.endsWith('/auth/token/refresh/')) {
        refreshCalls++;
        expect(options.data, {'refresh': 'old-refresh'});
        return jsonResponse({
          'access': 'new-access',
          'refresh': 'rotated-refresh',
        });
      }
      if (options.uri.path.endsWith('/me/')) {
        meCalls++;
        final authorization = options.headers['Authorization'];
        if (authorization == 'Bearer old-access') {
          return jsonResponse({'detail': '凭据已过期'}, 401);
        }
        expect(authorization, 'Bearer new-access');
        return jsonResponse({
          'id': 'u1',
          'email': 'teacher@example.com',
          'email_verified': true,
          'teacher_status': 'active',
          'assignment_scope_version': 3,
          'current_term': null,
          'teacher': null,
          'claim': null,
          'avatar_url': null,
        });
      }
      return jsonResponse({'detail': 'unexpected'}, 404);
    });
    final client = ApiClient(
      baseUrl: 'http://example.test/api/v1/',
      tokenStore: tokens,
      dio: mockDio(adapter),
    );

    final response = await client.get('me/');

    expect(response.statusCode, 200);
    expect(meCalls, 2);
    expect(refreshCalls, 1);
    expect(await tokens.readAccess(), 'new-access');
    expect(await tokens.readRefresh(), 'rotated-refresh');
    expect(
      adapter.requests
          .where((request) => request.path.endsWith('/auth/token/refresh/'))
          .length,
      1,
    );
  });

  test('refresh 网络失败保留令牌，不触发退出登录', () async {
    final tokens = MemoryTokenStore();
    await tokens.save(access: 'old-access', refresh: 'old-refresh');
    var expiredCalls = 0;
    var refreshCalls = 0;
    final adapter = MockHttpAdapter((options) {
      if (options.uri.path.endsWith('/auth/token/refresh/')) {
        refreshCalls++;
        return jsonResponse({'detail': '网关暂时不可用'}, 503);
      }
      return jsonResponse({'detail': '凭据已过期'}, 401);
    });
    final client = ApiClient(
      baseUrl: 'http://example.test/api/v1/',
      tokenStore: tokens,
      dio: mockDio(adapter),
      onSessionExpired: () => expiredCalls++,
    );

    await expectLater(
      client.get('me/'),
      throwsA(
        isA<ApiException>()
            .having((error) => error.statusCode, 'statusCode', 503)
            .having(
              (error) => error.message,
              'message',
              '网络暂时不可用，登录状态已保留，请稍后重试。',
            ),
      ),
    );

    expect(refreshCalls, 1);
    expect(expiredCalls, 0);
    expect(await tokens.readAccess(), 'old-access');
    expect(await tokens.readRefresh(), 'old-refresh');
  });

  test('refresh 明确 401 才清除令牌并通知退出', () async {
    final tokens = MemoryTokenStore();
    await tokens.save(access: 'old-access', refresh: 'old-refresh');
    var expiredCalls = 0;
    final adapter = MockHttpAdapter((options) {
      if (options.uri.path.endsWith('/auth/token/refresh/')) {
        return jsonResponse({'detail': '刷新令牌已失效'}, 401);
      }
      return jsonResponse({'detail': '凭据已过期'}, 401);
    });
    final client = ApiClient(
      baseUrl: 'http://example.test/api/v1/',
      tokenStore: tokens,
      dio: mockDio(adapter),
      onSessionExpired: () => expiredCalls++,
    );

    await expectLater(client.get('me/'), throwsA(isA<ApiException>()));
    expect(expiredCalls, 1);
    expect(await tokens.readAccess(), isNull);
    expect(await tokens.readRefresh(), isNull);
  });

  test('401 后 multipart 上传用 clone 重放并携带新 token', () async {
    final tokens = MemoryTokenStore();
    await tokens.save(access: 'old-access', refresh: 'old-refresh');
    var uploads = 0;
    var refreshCalls = 0;
    final adapter = MockHttpAdapter((options) {
      if (options.uri.path.endsWith('/auth/token/refresh/')) {
        refreshCalls++;
        return jsonResponse({'access': 'new-access', 'refresh': 'new-refresh'});
      }
      if (options.uri.path.endsWith('/me/avatar/')) {
        uploads++;
        if (uploads == 1) {
          expect(options.headers['Authorization'], 'Bearer old-access');
          return jsonResponse({'detail': '访问令牌已过期。'}, 401);
        }
        expect(options.headers['Authorization'], 'Bearer new-access');
        expect(options.data, isA<FormData>());
        return jsonResponse({
          'avatar_url': 'http://example.test/api/v1/me/avatar/?v=a.png',
        });
      }
      return jsonResponse({'detail': 'unexpected'}, 404);
    });
    final client = ApiClient(
      baseUrl: 'http://example.test/api/v1/',
      tokenStore: tokens,
      dio: mockDio(adapter),
    );

    final response = await client.post(
      'me/avatar/',
      data: FormData.fromMap({
        'avatar': MultipartFile.fromBytes([1, 2, 3, 4], filename: 'avatar.png'),
      }),
    );

    expect(response.statusCode, 200);
    expect(uploads, 2);
    expect(refreshCalls, 1);
    expect(await tokens.readAccess(), 'new-access');
  });

  test('非 401 响应保留服务端错误说明', () async {
    final adapter = MockHttpAdapter(
      (options) =>
          jsonResponse({'code': 'TEACHER_PENDING', 'detail': '教师身份待审核。'}, 403),
    );
    final client = ApiClient(
      baseUrl: 'http://example.test/api/v1/',
      tokenStore: MemoryTokenStore(),
      dio: mockDio(adapter),
    );

    await expectLater(
      client.get('sessions/'),
      throwsA(
        isA<ApiException>()
            .having((error) => error.statusCode, 'statusCode', 403)
            .having((error) => error.message, 'message', '教师身份待审核。'),
      ),
    );
  });
}
