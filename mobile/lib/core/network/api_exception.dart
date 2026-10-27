import 'package:dio/dio.dart';

class ApiException implements Exception {
  const ApiException({
    required this.message,
    this.statusCode,
    this.code,
    this.fieldErrors = const {},
    this.current,
    this.isUnauthorized = false,
  });

  final String message;
  final int? statusCode;
  final String? code;
  final Map<String, List<String>> fieldErrors;

  /// Server's current object for 409 conflicts.
  final Map<String, dynamic>? current;
  final bool isUnauthorized;

  factory ApiException.fromDio(DioException error) {
    final status = error.response?.statusCode;
    final data = error.response?.data;
    if (data is Map) {
      final body = Map<String, dynamic>.from(data);
      final fields = <String, List<String>>{};
      for (final entry in body.entries) {
        if (entry.value is List) {
          final messages = (entry.value as List)
              .map((item) => item.toString())
              .where((item) => item.isNotEmpty)
              .toList();
          if (messages.isNotEmpty) {
            fields[entry.key.toString()] = messages;
          }
        }
      }
      final fieldMessage = fields.values
          .expand((messages) => messages)
          .where((message) => message.trim().isNotEmpty)
          .firstOrNull;
      final message =
          _firstString(body['detail']) ??
          _firstString(body['non_field_errors']) ??
          fieldMessage ??
          _statusMessage(status);
      final current = body['current'];
      return ApiException(
        message: message,
        statusCode: status,
        code: _firstString(body['code']),
        fieldErrors: fields,
        current: current is Map ? Map<String, dynamic>.from(current) : null,
        isUnauthorized: status == 401,
      );
    }
    return ApiException(
      message: _statusMessage(status),
      statusCode: status,
      isUnauthorized: status == 401,
    );
  }

  factory ApiException.fromUnknown(Object error) {
    if (error is ApiException) {
      return error;
    }
    if (error is DioException) {
      return ApiException.fromDio(error);
    }
    final text = error.toString().replaceFirst('Exception: ', '');
    return ApiException(message: text.isEmpty ? '请求失败，请稍后重试。' : text);
  }

  static String _statusMessage(int? status) {
    if (status == null) {
      return '网络请求失败，请检查服务地址和网络。';
    }
    if (status >= 500) {
      return '服务器暂时无法处理请求（HTTP $status）。';
    }
    return switch (status) {
      400 => '提交内容有误，请检查后重试。',
      401 => '登录状态已失效，请重新登录。',
      403 => '当前账号无权执行此操作。',
      404 => '没有找到对应数据。',
      409 => '数据已在别处更新，请刷新后再试。',
      _ => '请求失败（HTTP $status）。',
    };
  }

  static String? _firstString(Object? value) {
    if (value is String && value.trim().isNotEmpty) {
      return value;
    }
    if (value is List) {
      for (final item in value) {
        final text = _firstString(item);
        if (text != null) {
          return text;
        }
      }
    }
    return null;
  }

  @override
  String toString() => message;
}
