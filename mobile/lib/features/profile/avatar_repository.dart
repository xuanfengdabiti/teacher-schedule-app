import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/network/api_client.dart';
import '../../core/providers.dart';

abstract class AvatarApi {
  Future<Uint8List> fetchAvatar();

  Future<void> uploadAvatar({
    required Uint8List bytes,
    required String filename,
  });

  Future<void> removeAvatar();
}

class AvatarRepository implements AvatarApi {
  AvatarRepository(this._api);

  final ApiClient _api;

  @override
  Future<Uint8List> fetchAvatar() => _api.getBytes('me/avatar/');

  @override
  Future<void> uploadAvatar({
    required Uint8List bytes,
    required String filename,
  }) async {
    final data = FormData.fromMap({
      'avatar': MultipartFile.fromBytes(bytes, filename: filename),
    });
    await _api.post('me/avatar/', data: data);
  }

  @override
  Future<void> removeAvatar() => _api.delete('me/avatar/');
}

final avatarRepositoryProvider = Provider<AvatarApi>(
  (ref) => AvatarRepository(ref.watch(apiClientProvider)),
);
