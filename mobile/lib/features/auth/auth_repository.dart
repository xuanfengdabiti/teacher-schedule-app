import '../../core/json.dart';
import '../../core/network/api_client.dart';
import 'me.dart';

class AuthRepository {
  AuthRepository(this._api);

  final ApiClient _api;

  Future<({String access, String refresh})> login({
    required String email,
    required String password,
  }) async {
    final response = await _api.post(
      'auth/login/',
      data: {'email': email.trim(), 'password': password},
    );
    final body = jsonMap(response.data);
    final access = jsonString(body['access']);
    final refresh = jsonString(body['refresh']);
    if (access == null || refresh == null) {
      throw StateError('登录响应缺少 access 或 refresh。');
    }
    return (access: access, refresh: refresh);
  }

  Future<Me> me() async {
    final response = await _api.get('me/');
    return Me.fromJson(jsonMap(response.data));
  }

  Future<void> logout(String? refresh) async {
    if (refresh == null || refresh.isEmpty) {
      return;
    }
    await _api.post('auth/logout/', data: {'refresh': refresh});
  }
}
