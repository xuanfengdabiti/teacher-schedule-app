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
      final code = _firstString(body['code']);
      final message = _friendlyMessage(
        code: code,
        serverMessage:
            _firstString(body['detail']) ??
            _firstString(body['non_field_errors']) ??
            fieldMessage,
        status: status,
        fieldMessage: fieldMessage,
      );
      final current = body['current'];
      return ApiException(
        message: message,
        statusCode: status,
        code: code,
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
    return const ApiException(message: '操作没有成功，请稍后重试。');
  }

  static String _friendlyMessage({
    required String? code,
    required String? serverMessage,
    required int? status,
    required String? fieldMessage,
  }) {
    final shortMessage = _shortCodeMessage(code);
    if (shortMessage != null) {
      return shortMessage;
    }

    final readable = (serverMessage ?? '').trim();
    if (code == 'INVALID_NOTE' && _looksTechnical(readable)) {
      return _codeFallbackMessage(code)!;
    }
    final cleaned = _plainWording(readable);
    if (cleaned.isNotEmpty && !_looksTechnical(cleaned)) {
      return cleaned;
    }
    if (fieldMessage != null) {
      final cleanedField = _plainWording(fieldMessage);
      if (cleanedField.isNotEmpty && !_looksTechnical(cleanedField)) {
        return cleanedField;
      }
    }
    final mapped = _codeFallbackMessage(code);
    if (mapped != null) {
      return mapped;
    }
    return _statusMessage(status);
  }

  static String? _shortCodeMessage(String? code) {
    return switch (code) {
      'NOTE_VERSION_CONFLICT' => '备注已在其他设备更新，请核对后再保存。',
      'MUTATION_KEY_REUSED' => '这次保存重复提交了，请稍后重试。',
      'NOTHING_TO_PUBLISH' => '请填写备注',
      _ => null,
    };
  }

  /// Translate a few fixed backend phrases without touching names or values.
  static String _plainWording(String text) {
    if (text.isEmpty) {
      return text;
    }
    return text
        .replaceFirst('课程开设不存在或不在本人可见范围。', '没有找到对应课程。')
        .replaceFirst('对象不存在或不在本人可见范围。', '没有找到对应内容。')
        .replaceFirst('items 必须是数组。', '内容格式有误，请检查后重试。');
  }

  static String? _codeFallbackMessage(String? code) {
    return switch (code) {
      'INVALID_NOTE' => '这条备注暂时无法引用，请刷新后重试。',
      'INVALID_PLAN' => '教学计划内容有误，请检查后重试。',
      'INVALID_COMPLETION' => '学时登记内容有误，请检查后重试。',
      'NOT_OFFERING_TEACHER' => '当前账号不能操作这门课程。',
      'NOT_SESSION_TEACHER' => '只有这节课的授课教师可以操作。',
      'NOT_PLAN_OWNER' => '只有课程负责人可以保存教学计划。',
      'PLAN_VERSION_CONFLICT' ||
      'SESSION_VERSION_CONFLICT' => '内容已在别处更新，请刷新后再试。',
      _ => null,
    };
  }

  static bool _looksTechnical(String text) {
    final lower = text.toLowerCase();
    return text.contains(RegExp(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-')) ||
        lower.contains('exception') ||
        lower.contains('dioexception') ||
        lower.contains('socketexception') ||
        lower.contains('formatexception') ||
        lower.contains('client_mutation') ||
        lower.contains('base_version') ||
        lower.contains('due_date') ||
        lower.contains('发布版本') ||
        lower.contains('items 必须') ||
        lower.contains('items=') ||
        lower.contains('items:');
  }

  static String _statusMessage(int? status) {
    if (status == null) {
      return '连接没有成功，请检查连接设置和网络。';
    }
    if (status >= 500) {
      return '服务暂时不可用，请稍后重试。';
    }
    return switch (status) {
      400 => '提交内容有误，请检查后重试。',
      401 => '登录状态已失效，请重新登录。',
      403 => '当前账号不能执行此操作。',
      404 => '没有找到对应内容。',
      409 => '内容已在别处更新，请刷新后再试。',
      _ => '操作没有成功，请稍后重试。',
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
