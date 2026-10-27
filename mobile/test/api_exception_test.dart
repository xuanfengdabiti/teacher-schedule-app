import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:teacher_schedule/core/network/api_exception.dart';
import 'package:teacher_schedule/features/collaboration/collaboration_models.dart';

void main() {
  test('未知异常不显示原始异常类型', () {
    final error = ApiException.fromUnknown(
      FormatException('Unexpected character at line 3'),
    );
    expect(error.message, isNot(contains('FormatException')));
    expect(error.message, isNot(contains('Unexpected character')));
    expect(error.message, '操作没有成功，请稍后重试。');
  });

  test('可读的业务校验说明保留，不被通用文案覆盖', () {
    final error = ApiException.fromDio(
      DioException(
        requestOptions: RequestOptions(path: '/'),
        response: Response<dynamic>(
          requestOptions: RequestOptions(path: '/'),
          statusCode: 400,
          data: {
            'code': 'VALIDATION_ERROR',
            'detail': '结束节不能早于起始节。',
            'field_errors': {},
          },
        ),
        type: DioExceptionType.badResponse,
      ),
    );
    expect(error.message, '结束节不能早于起始节。');
    expect(error.statusCode, 400);

    final planError = ApiException.fromDio(
      DioException(
        requestOptions: RequestOptions(path: '/'),
        response: Response<dynamic>(
          requestOptions: RequestOptions(path: '/'),
          statusCode: 400,
          data: {
            'code': 'INVALID_PLAN',
            'detail': '专题标题不能为空。',
            'field_errors': {},
          },
        ),
        type: DioExceptionType.badResponse,
      ),
    );
    expect(planError.message, '专题标题不能为空。');
  });

  test('备注冲突保留 code/current 并显示自然说明', () {
    final error = ApiException.fromDio(
      DioException(
        requestOptions: RequestOptions(path: '/'),
        response: Response<dynamic>(
          requestOptions: RequestOptions(path: '/'),
          statusCode: 409,
          data: {
            'code': 'NOTE_VERSION_CONFLICT',
            'detail': '备注已在其他设备更新。请保留本地文本，与服务器当前内容核对后再保存。',
            'field_errors': {},
            'current': {'version': 4},
          },
        ),
        type: DioExceptionType.badResponse,
      ),
    );
    expect(error.code, 'NOTE_VERSION_CONFLICT');
    expect(error.statusCode, 409);
    expect(error.current, {'version': 4});
    expect(error.message, '备注已在其他设备更新，请核对后再保存。');
  });

  test('HANDOFF_WITHDRAWN 保留后端中文说明', () {
    final error = ApiException.fromDio(
      DioException(
        requestOptions: RequestOptions(path: '/'),
        response: Response<dynamic>(
          requestOptions: RequestOptions(path: '/'),
          statusCode: 409,
          data: {
            'code': 'HANDOFF_WITHDRAWN',
            'detail': '该交接已撤回，不能确认。',
            'field_errors': {},
          },
        ),
        type: DioExceptionType.badResponse,
      ),
    );
    expect(error.code, 'HANDOFF_WITHDRAWN');
    expect(error.message, '该交接已撤回，不能确认。');
  });

  test('通知详情不显示英文类型，且只替换固定系统文案', () {
    final item = NotificationItem.fromJson({
      'id': 'n1',
      'type': 'inquiry_received',
      'object_type': 'inquiry',
      'object_id': 'i1',
      'title': '收到失物协查请求',
      'body': '李老师 发起了 10-27 08:10–10:00 在 A101 的失物协查。物品描述：黑色雨伞',
      'created_at': '2026-10-27T09:00:00+08:00',
      'read_at': null,
      'is_read': false,
    });
    expect(item.displayTitle, '收到失物询问');
    expect(item.displayBody, contains('失物询问'));
    expect(item.displayBody, isNot(contains('协查')));
    expect(item.displayBody, contains('黑色雨伞'));
  });
}
