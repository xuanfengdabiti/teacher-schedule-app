import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image_picker/image_picker.dart';
import 'package:teacher_schedule/app/theme.dart';
import 'package:teacher_schedule/core/network/api_exception.dart';
import 'package:teacher_schedule/features/profile/avatar_repository.dart';
import 'package:teacher_schedule/features/profile/avatar_view.dart';

final Uint8List _photo1 = base64Decode(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
);
final Uint8List _photo2 = base64Decode(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
);

class _FakeAvatarApi implements AvatarApi {
  Uint8List? image;
  Object? fetchError;
  Object? uploadError;
  Object? removeError;
  int uploads = 0;
  int removes = 0;
  int fetches = 0;
  Uint8List? lastUploaded;
  Completer<Uint8List>? pendingFetch;

  @override
  Future<Uint8List> fetchAvatar() async {
    fetches++;
    if (fetchError != null) {
      throw fetchError!;
    }
    if (pendingFetch != null) {
      return pendingFetch!.future;
    }
    return image ?? Uint8List(0);
  }

  @override
  Future<void> uploadAvatar({
    required Uint8List bytes,
    required String filename,
  }) async {
    uploads++;
    if (uploadError != null) {
      throw uploadError!;
    }
    lastUploaded = bytes;
    image = bytes;
  }

  @override
  Future<void> removeAvatar() async {
    removes++;
    if (removeError != null) {
      throw removeError!;
    }
    image = null;
  }
}

Uint8List _photo() => _photo1;

Future<void> _pumpAvatar(
  WidgetTester tester, {
  required AvatarApi api,
  required Future<XFile?> Function() pickImage,
  String? avatarUrl,
  String accountId = 'user-1',
  Future<void> Function()? refreshMe,
}) async {
  await tester.pumpWidget(
    MaterialApp(
      theme: buildAppTheme(),
      home: Scaffold(
        body: Center(
          child: ProfileAvatar(
            accountId: accountId,
            displayName: '王老师',
            avatarUrl: avatarUrl,
            repository: api,
            pickImage: pickImage,
            refreshMe: refreshMe ?? () async {},
          ),
        ),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('取消选图不发请求，也不显示预览确认', (tester) async {
    final api = _FakeAvatarApi();
    await _pumpAvatar(tester, api: api, pickImage: () async => null);

    await tester.tap(find.byKey(const Key('profileAvatarButton')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('avatarPickButton')));
    await tester.pumpAndSettle();

    expect(find.text('使用这张照片？'), findsNothing);
    expect(api.uploads, 0);
    expect(find.text('王'), findsOneWidget);
  });

  testWidgets('选择照片先预览，确认后上传并回显，刷新账号', (tester) async {
    final api = _FakeAvatarApi();
    var refreshed = 0;
    final file = XFile.fromData(
      _photo(),
      name: 'avatar.jpg',
      mimeType: 'image/jpeg',
    );
    await _pumpAvatar(
      tester,
      api: api,
      pickImage: () async => file,
      refreshMe: () async => refreshed++,
    );

    await tester.tap(find.byKey(const Key('profileAvatarButton')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('avatarPickButton')));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('avatarPreviewImage')), findsOneWidget);
    expect(api.uploads, 0);

    await tester.tap(find.byKey(const Key('avatarUploadConfirm')));
    await tester.pumpAndSettle();

    expect(api.uploads, 1);
    expect(api.lastUploaded, _photo());
    expect(refreshed, 1);
    expect(find.byKey(const Key('profileAvatarImage')), findsOneWidget);
    expect(find.text('头像已更新。'), findsOneWidget);
  });

  testWidgets('超过 2 MiB 提示重选且不上传', (tester) async {
    final api = _FakeAvatarApi();
    final file = XFile.fromData(
      Uint8List(avatarMaxBytes + 1),
      name: 'big.jpg',
      mimeType: 'image/jpeg',
    );
    await _pumpAvatar(tester, api: api, pickImage: () async => file);

    await tester.tap(find.byKey(const Key('profileAvatarButton')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('avatarPickButton')));
    await tester.pumpAndSettle();

    expect(find.text('照片超过 2 MB，请重新选择。'), findsOneWidget);
    expect(find.text('使用这张照片？'), findsNothing);
    expect(api.uploads, 0);
  });

  testWidgets('上传失败保留旧头像并可重试', (tester) async {
    final api = _FakeAvatarApi();
    api.image = _photo2;
    api.uploadError = const ApiException(message: '头像上传失败，请重试。');
    final file = XFile.fromData(
      _photo(),
      name: 'avatar.jpg',
      mimeType: 'image/jpeg',
    );
    await _pumpAvatar(
      tester,
      api: api,
      avatarUrl: 'http://example.test/api/v1/me/avatar/?v=old',
      pickImage: () async => file,
    );

    expect(find.byKey(const Key('profileAvatarImage')), findsOneWidget);

    await tester.tap(find.byKey(const Key('profileAvatarButton')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('avatarPickButton')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('avatarUploadConfirm')));
    await tester.pumpAndSettle();

    expect(find.text('头像上传失败，请重试。'), findsOneWidget);
    expect(find.byKey(const Key('profileAvatarImage')), findsOneWidget);
    expect(api.image, _photo2);

    api.uploadError = null;
    await tester.tap(find.byKey(const Key('profileAvatarButton')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('avatarPickButton')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('avatarUploadConfirm')));
    await tester.pumpAndSettle();
    expect(api.uploads, 2);
    expect(find.text('头像已更新。'), findsOneWidget);
  });

  testWidgets('移除头像调用删除并在成功后回退姓名首字', (tester) async {
    final api = _FakeAvatarApi();
    api.image = _photo2;
    var refreshed = 0;
    await _pumpAvatar(
      tester,
      api: api,
      avatarUrl: 'http://example.test/api/v1/me/avatar/?v=old',
      pickImage: () async => null,
      refreshMe: () async => refreshed++,
    );

    await tester.tap(find.byKey(const Key('profileAvatarButton')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('avatarRemoveButton')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('avatarRemoveConfirm')));
    await tester.pumpAndSettle();

    expect(api.removes, 1);
    expect(refreshed, 1);
    expect(find.text('王'), findsOneWidget);
    expect(find.byKey(const Key('profileAvatarImage')), findsNothing);
  });

  testWidgets('账号切换时旧字节不会串到新账号', (tester) async {
    final api = _FakeAvatarApi();
    api.image = _photo1;
    await _pumpAvatar(
      tester,
      api: api,
      avatarUrl: 'http://example.test/api/v1/me/avatar/?v=old',
      pickImage: () async => null,
    );
    expect(find.byKey(const Key('profileAvatarImage')), findsOneWidget);

    api.image = _photo2;
    await tester.pumpWidget(
      MaterialApp(
        theme: buildAppTheme(),
        home: Scaffold(
          body: Center(
            child: ProfileAvatar(
              accountId: 'user-2',
              displayName: '李老师',
              avatarUrl: 'http://example.test/api/v1/me/avatar/?v=new',
              repository: api,
              pickImage: () async => null,
              refreshMe: () async {},
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    final image = tester.widget<Image>(
      find.byKey(const Key('profileAvatarImage')),
    );
    final bytes = (image.image as MemoryImage).bytes;
    expect(bytes, _photo2);
    expect(find.text('李'), findsNothing);
  });

  testWidgets('头像读取失败保留姓名首字，可点击重试', (tester) async {
    final api = _FakeAvatarApi();
    api.fetchError = const ApiException(message: '头像读取失败。');
    await _pumpAvatar(
      tester,
      api: api,
      avatarUrl: 'http://example.test/api/v1/me/avatar/?v=old',
      pickImage: () async => null,
    );

    expect(find.text('王'), findsOneWidget);
    expect(find.byKey(const Key('avatarRetryButton')), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsNothing);

    api.fetchError = null;
    api.image = _photo1;
    await tester.tap(find.byKey(const Key('avatarRetryButton')));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('profileAvatarImage')), findsOneWidget);
    expect(find.byKey(const Key('avatarRetryButton')), findsNothing);
  });

  testWidgets('换到无头像账号时清除在途加载，不一直转圈', (tester) async {
    final api = _FakeAvatarApi();
    api.pendingFetch = Completer<Uint8List>();
    await tester.pumpWidget(
      MaterialApp(
        theme: buildAppTheme(),
        home: Scaffold(
          body: Center(
            child: ProfileAvatar(
              accountId: 'user-1',
              displayName: '王老师',
              avatarUrl: 'http://example.test/api/v1/me/avatar/?v=old',
              repository: api,
              pickImage: () async => null,
              refreshMe: () async {},
            ),
          ),
        ),
      ),
    );
    await tester.pump();

    expect(find.byType(CircularProgressIndicator), findsOneWidget);

    api.pendingFetch = null;
    await tester.pumpWidget(
      MaterialApp(
        theme: buildAppTheme(),
        home: Scaffold(
          body: Center(
            child: ProfileAvatar(
              accountId: 'user-2',
              displayName: '王老师',
              avatarUrl: null,
              repository: api,
              pickImage: () async => null,
              refreshMe: () async {},
            ),
          ),
        ),
      ),
    );
    await tester.pump();

    expect(find.byType(CircularProgressIndicator), findsNothing);
    expect(find.text('王'), findsOneWidget);
  });
}
