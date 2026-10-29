import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:teacher_schedule/core/network/api_client.dart';
import 'package:teacher_schedule/core/network/api_exception.dart';
import 'package:teacher_schedule/core/storage/token_store.dart';
import 'package:teacher_schedule/features/profile/avatar_repository.dart';

import '../../support/mock_http_adapter.dart';

void main() {
  test('头像读取使用鉴权 GET 的 bytes，并带入 access token', () async {
    final tokens = MemoryTokenStore();
    await tokens.save(access: 'old-access', refresh: 'avatar-refresh');
    final adapter = MockHttpAdapter((options) {
      if (options.uri.path.endsWith('/auth/token/refresh/')) {
        return jsonResponse({'access': 'new-access', 'refresh': 'new-refresh'});
      }
      expect(options.headers['Authorization'], 'Bearer old-access');
      expect(options.responseType, ResponseType.bytes);
      return ResponseBody.fromBytes(
        [1, 2, 3],
        200,
        headers: {
          Headers.contentTypeHeader: ['image/png'],
        },
      );
    });
    final client = ApiClient(
      baseUrl: 'http://example.test/api/v1/',
      tokenStore: tokens,
      dio: mockDio(adapter),
    );

    final bytes = await AvatarRepository(client).fetchAvatar();
    expect(bytes, Uint8List.fromList([1, 2, 3]));
  });

  test('头像上传使用 multipart avatar 字段，删除走 DELETE', () async {
    final adapter = MockHttpAdapter((options) {
      if (options.method == 'POST' &&
          options.uri.path.endsWith('/me/avatar/')) {
        final data = options.data as FormData;
        final file = data.files.single;
        expect(file.key, 'avatar');
        expect(file.value.length, 3);
        return jsonResponse({
          'avatar_url': 'http://example.test/api/v1/me/avatar/?v=avatar.png',
        });
      }
      if (options.method == 'DELETE' &&
          options.uri.path.endsWith('/me/avatar/')) {
        return ResponseBody.fromString('', 204);
      }
      return jsonResponse({'detail': 'unexpected'}, 404);
    });
    final client = ApiClient(
      baseUrl: 'http://example.test/api/v1/',
      tokenStore: MemoryTokenStore(),
      dio: mockDio(adapter),
    );
    final repository = AvatarRepository(client);

    await repository.uploadAvatar(
      bytes: Uint8List.fromList([1, 2, 3]),
      filename: 'avatar.png',
    );
    await repository.removeAvatar();

    expect(adapter.requests, hasLength(2));
    expect(adapter.requests[0].method, 'POST');
    expect(adapter.requests[1].method, 'DELETE');
  });

  test('头像读取失败按 ApiException 处理', () async {
    final adapter = MockHttpAdapter(
      (options) => jsonResponse({'detail': '尚未设置头像。'}, 404),
    );
    final client = ApiClient(
      baseUrl: 'http://example.test/api/v1/',
      tokenStore: MemoryTokenStore(),
      dio: mockDio(adapter),
    );

    await expectLater(
      AvatarRepository(client).fetchAvatar(),
      throwsA(
        isA<ApiException>().having(
          (error) => error.statusCode,
          'statusCode',
          404,
        ),
      ),
    );
  });
}
