import 'package:dio/dio.dart';

import '../storage/token_store.dart';
import 'api_exception.dart';

class ApiClient {
  ApiClient({
    required String baseUrl,
    required this.tokenStore,
    Dio? dio,
    this.onSessionExpired,
  }) : _dio = dio ?? Dio() {
    this.baseUrl = baseUrl;
    _dio.options
      ..connectTimeout = const Duration(seconds: 12)
      ..receiveTimeout = const Duration(seconds: 20)
      ..sendTimeout = const Duration(seconds: 20)
      ..headers['Accept'] = 'application/json';
    _dio.interceptors.add(
      InterceptorsWrapper(
        onRequest: _attachToken,
        onError: _handleUnauthorized,
      ),
    );
  }

  final Dio _dio;
  final TokenStore tokenStore;
  final void Function()? onSessionExpired;
  Future<_RefreshResult>? _refreshing;
  String _baseUrl = '';

  String get baseUrl => _baseUrl;

  set baseUrl(String value) {
    if (value == _baseUrl) {
      return;
    }
    _baseUrl = value;
    _dio.options.baseUrl = value;
  }

  Future<Response<dynamic>> get(
    String path, {
    Map<String, dynamic>? query,
  }) async {
    try {
      return await _dio.get<dynamic>(path, queryParameters: query);
    } on DioException catch (error) {
      throw ApiException.fromDio(error);
    }
  }

  Future<Response<dynamic>> post(String path, {Object? data}) async {
    try {
      return await _dio.post<dynamic>(path, data: data);
    } on DioException catch (error) {
      throw ApiException.fromDio(error);
    }
  }

  /// Same contract as [post] but with a per-request receive timeout for slow
  /// operations. The client-wide default (20s) is intentionally untouched.
  Future<Response<dynamic>> postWithTimeout(
    String path, {
    Object? data,
    required Duration receiveTimeout,
  }) async {
    try {
      return await _dio.post<dynamic>(
        path,
        data: data,
        options: Options(receiveTimeout: receiveTimeout),
      );
    } on DioException catch (error) {
      throw ApiException.fromDio(error);
    }
  }

  Future<Response<dynamic>> put(String path, {Object? data}) async {
    try {
      return await _dio.put<dynamic>(path, data: data);
    } on DioException catch (error) {
      throw ApiException.fromDio(error);
    }
  }

  Future<Response<dynamic>> patch(String path, {Object? data}) async {
    try {
      return await _dio.patch<dynamic>(path, data: data);
    } on DioException catch (error) {
      throw ApiException.fromDio(error);
    }
  }

  Future<void> _attachToken(
    RequestOptions options,
    RequestInterceptorHandler handler,
  ) async {
    if (options.extra['skipAuth'] != true) {
      final access = await tokenStore.readAccess();
      if (access != null && access.isNotEmpty) {
        options.headers['Authorization'] = 'Bearer $access';
      }
    }
    handler.next(options);
  }

  Future<void> _handleUnauthorized(
    DioException error,
    ErrorInterceptorHandler handler,
  ) async {
    final options = error.requestOptions;
    final canRetry =
        error.response?.statusCode == 401 &&
        options.extra['skipAuth'] != true &&
        options.extra['retried'] != true;
    if (!canRetry) {
      handler.next(error);
      return;
    }

    final refreshResult = await _refreshAccessToken();
    if (refreshResult == _RefreshResult.invalid) {
      await tokenStore.clear();
      onSessionExpired?.call();
      handler.next(error);
      return;
    }
    if (refreshResult == _RefreshResult.unavailable) {
      handler.reject(
        DioException(
          requestOptions: options,
          response: Response<dynamic>(
            requestOptions: options,
            statusCode: 503,
            data: {'detail': '网络暂时不可用，登录状态已保留，请稍后重试。'},
          ),
          type: DioExceptionType.badResponse,
          error: 'refresh unavailable',
        ),
      );
      return;
    }

    final access = await tokenStore.readAccess();
    options.headers['Authorization'] = 'Bearer $access';
    options.extra['retried'] = true;
    try {
      final response = await _dio.fetch<dynamic>(options);
      handler.resolve(response);
    } on DioException catch (retryError) {
      handler.next(retryError);
    }
  }

  Future<_RefreshResult> _refreshAccessToken() {
    final existing = _refreshing;
    if (existing != null) {
      return existing;
    }
    final future = _performRefresh();
    _refreshing = future;
    return future.whenComplete(() => _refreshing = null);
  }

  Future<_RefreshResult> _performRefresh() async {
    final refresh = await tokenStore.readRefresh();
    if (refresh == null || refresh.isEmpty) {
      return _RefreshResult.invalid;
    }
    try {
      final response = await _dio.post<dynamic>(
        'auth/token/refresh/',
        data: {'refresh': refresh},
        options: Options(extra: {'skipAuth': true}),
      );
      final body = response.data;
      if (body is! Map) {
        return _RefreshResult.unavailable;
      }
      final access = body['access']?.toString();
      final rotated = body['refresh']?.toString();
      if (access == null || access.isEmpty) {
        return _RefreshResult.unavailable;
      }
      await tokenStore.save(
        access: access,
        refresh: (rotated == null || rotated.isEmpty) ? refresh : rotated,
      );
      return _RefreshResult.success;
    } on DioException catch (error) {
      final status = error.response?.statusCode;
      if (status == 400 || status == 401) {
        return _RefreshResult.invalid;
      }
      return _RefreshResult.unavailable;
    } catch (_) {
      return _RefreshResult.unavailable;
    }
  }
}

enum _RefreshResult { success, invalid, unavailable }
