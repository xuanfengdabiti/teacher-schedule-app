import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import '../../app/theme.dart';
import '../../core/network/api_exception.dart';
import 'avatar_repository.dart';

const int avatarMaxBytes = 2 * 1024 * 1024;

class ProfileAvatar extends StatefulWidget {
  const ProfileAvatar({
    super.key,
    required this.accountId,
    required this.displayName,
    required this.avatarUrl,
    required this.repository,
    required this.pickImage,
    required this.refreshMe,
  });

  final String accountId;
  final String displayName;
  final String? avatarUrl;
  final AvatarApi repository;
  final Future<XFile?> Function() pickImage;
  final Future<void> Function() refreshMe;

  @override
  State<ProfileAvatar> createState() => ProfileAvatarState();
}

class ProfileAvatarState extends State<ProfileAvatar> {
  Uint8List? _bytes;
  Uint8List? _pendingBytes;
  XFile? _pendingFile;
  bool _loading = false;
  bool _loadFailed = false;
  bool _uploading = false;
  bool _removing = false;
  int _loadNo = 0;

  bool get _busy => _uploading || _removing;

  @override
  void initState() {
    super.initState();
    _loadAvatar();
  }

  @override
  void didUpdateWidget(covariant ProfileAvatar oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.accountId != widget.accountId) {
      _bytes = null;
      _pendingBytes = null;
      _pendingFile = null;
      _loadAvatar();
      return;
    }
    if (oldWidget.avatarUrl != widget.avatarUrl) {
      _loadAvatar();
    }
  }

  Future<void> _loadAvatar() async {
    final url = widget.avatarUrl;
    final requestNo = ++_loadNo;
    if (url == null || url.isEmpty) {
      if (mounted) {
        setState(() {
          _bytes = null;
          _loading = false;
          _loadFailed = false;
        });
      }
      return;
    }
    setState(() {
      _loading = true;
      _loadFailed = false;
    });
    try {
      final bytes = await widget.repository.fetchAvatar();
      if (!mounted || requestNo != _loadNo) {
        return;
      }
      setState(() {
        _bytes = bytes;
        _loading = false;
        _loadFailed = false;
      });
    } catch (_) {
      if (!mounted || requestNo != _loadNo) {
        return;
      }
      setState(() {
        _bytes = null;
        _loading = false;
        _loadFailed = true;
      });
      _showMessage('头像加载失败，可点击右下角重试。');
    }
  }

  /// Recovers a photo the system picker returned after the activity restart.
  Future<void> handleRecoveredFile(XFile file) async {
    if (_busy) {
      return;
    }
    await _prepare(file);
  }

  Future<void> _openActions() async {
    if (_busy) {
      return;
    }
    final action = await showModalBottomSheet<String>(
      context: context,
      builder: (context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              key: const Key('avatarPickButton'),
              leading: const Icon(Icons.photo_library_outlined),
              title: const Text('从相册选择'),
              onTap: () => Navigator.pop(context, 'pick'),
            ),
            if ((widget.avatarUrl ?? '').isNotEmpty || _bytes != null)
              ListTile(
                key: const Key('avatarRemoveButton'),
                leading: const Icon(Icons.delete_outline),
                title: const Text('移除头像'),
                onTap: () => Navigator.pop(context, 'remove'),
              ),
            ListTile(
              leading: const Icon(Icons.close),
              title: const Text('取消'),
              onTap: () => Navigator.pop(context),
            ),
          ],
        ),
      ),
    );
    if (!mounted) {
      return;
    }
    if (action == 'pick') {
      await _chooseImage();
    } else if (action == 'remove') {
      await _confirmRemove();
    }
  }

  Future<void> _chooseImage() async {
    final file = await widget.pickImage();
    if (file == null || !mounted) {
      return;
    }
    await _prepare(file);
  }

  Future<void> _prepare(XFile file) async {
    final Uint8List bytes;
    try {
      bytes = await file.readAsBytes();
    } catch (_) {
      _showMessage('无法读取这张照片，请重新选择。');
      return;
    }
    if (!mounted) {
      return;
    }
    if (bytes.length > avatarMaxBytes) {
      _showMessage('照片超过 2 MB，请重新选择。');
      return;
    }
    setState(() {
      _pendingFile = file;
      _pendingBytes = bytes;
    });
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('使用这张照片？'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ClipRRect(
              borderRadius: BorderRadius.circular(16),
              child: Image.memory(
                bytes,
                key: const Key('avatarPreviewImage'),
                width: 180,
                height: 180,
                fit: BoxFit.cover,
              ),
            ),
            const SizedBox(height: 10),
            const Text(
              '上传后会显示在“我的”页面。',
              style: TextStyle(fontSize: 14, color: appMuted),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('取消'),
          ),
          FilledButton(
            key: const Key('avatarUploadConfirm'),
            onPressed: () => Navigator.pop(context, true),
            child: const Text('上传头像'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) {
      if (mounted) {
        setState(() {
          _pendingBytes = null;
          _pendingFile = null;
        });
      }
      return;
    }
    await _upload();
  }

  Future<void> _upload() async {
    final file = _pendingFile;
    final bytes = _pendingBytes;
    if (file == null || bytes == null || _busy) {
      return;
    }
    setState(() => _uploading = true);
    try {
      await widget.repository.uploadAvatar(
        bytes: bytes,
        filename: file.name.isEmpty ? 'avatar.jpg' : file.name,
      );
      if (!mounted) {
        return;
      }
      setState(() {
        _bytes = bytes;
        _pendingBytes = null;
        _pendingFile = null;
      });
      _showMessage('头像已更新。');
      try {
        await widget.refreshMe();
      } catch (error) {
        if (mounted) {
          _showMessage(ApiException.fromUnknown(error).message);
        }
      }
    } catch (error) {
      if (mounted) {
        // A failed upload must leave the teacher's previous avatar visible.
        setState(() {
          _pendingBytes = null;
          _pendingFile = null;
        });
        _showMessage(ApiException.fromUnknown(error).message);
      }
    } finally {
      if (mounted) {
        setState(() => _uploading = false);
      }
    }
  }

  Future<void> _confirmRemove() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('移除头像？'),
        content: const Text('移除后会显示姓名首字。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('取消'),
          ),
          FilledButton(
            key: const Key('avatarRemoveConfirm'),
            onPressed: () => Navigator.pop(context, true),
            child: const Text('移除'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) {
      return;
    }
    setState(() => _removing = true);
    try {
      await widget.repository.removeAvatar();
      if (!mounted) {
        return;
      }
      setState(() {
        _bytes = null;
        _pendingBytes = null;
        _pendingFile = null;
      });
      _showMessage('头像已移除。');
      try {
        await widget.refreshMe();
      } catch (error) {
        if (mounted) {
          _showMessage(ApiException.fromUnknown(error).message);
        }
      }
    } catch (error) {
      if (mounted) {
        _showMessage(ApiException.fromUnknown(error).message);
      }
    } finally {
      if (mounted) {
        setState(() => _removing = false);
      }
    }
  }

  void _showMessage(String message) {
    if (!mounted) {
      return;
    }
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(message)));
  }

  String get _initial {
    final trimmed = widget.displayName.trim();
    return trimmed.isEmpty ? '师' : trimmed.substring(0, 1);
  }

  @override
  Widget build(BuildContext context) {
    final bytes = _pendingBytes ?? _bytes;
    return Semantics(
      button: true,
      label: _loadFailed ? '头像加载失败，点击重试' : '更换头像',
      child: GestureDetector(
        key: const Key('profileAvatarButton'),
        onTap: _openActions,
        child: Stack(
          clipBehavior: Clip.none,
          children: [
            CircleAvatar(
              radius: 30,
              backgroundColor: const Color(0xFFDDEFEA),
              child: _loading || _uploading || _removing
                  ? const SizedBox(
                      width: 22,
                      height: 22,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : bytes == null
                  ? Text(
                      _initial,
                      style: const TextStyle(
                        fontSize: 24,
                        fontWeight: FontWeight.w700,
                        color: appTeal,
                      ),
                    )
                  : ClipOval(
                      child: Image.memory(
                        bytes,
                        key: const Key('profileAvatarImage'),
                        width: 60,
                        height: 60,
                        fit: BoxFit.cover,
                      ),
                    ),
            ),
            Positioned(
              right: -2,
              bottom: -2,
              child: _loadFailed
                  ? Tooltip(
                      message: '头像加载失败，点击重试',
                      child: GestureDetector(
                        key: const Key('avatarRetryButton'),
                        onTap: _loadAvatar,
                        child: Container(
                          padding: const EdgeInsets.all(3),
                          decoration: BoxDecoration(
                            color: const Color(0xFFFFF4E5),
                            shape: BoxShape.circle,
                            border: Border.all(color: const Color(0xFFE2B268)),
                          ),
                          child: const Icon(
                            Icons.refresh,
                            size: 13,
                            color: Color(0xFF9A6A1F),
                          ),
                        ),
                      ),
                    )
                  : Container(
                      padding: const EdgeInsets.all(3),
                      decoration: BoxDecoration(
                        color: Colors.white,
                        shape: BoxShape.circle,
                        border: Border.all(color: appLine),
                      ),
                      child: const Icon(Icons.edit, size: 13, color: appTeal),
                    ),
            ),
          ],
        ),
      ),
    );
  }
}
