import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:image_picker/image_picker.dart';

import '../../app/theme.dart';
import '../../core/network/api_exception.dart';
import '../../core/providers.dart';
import '../../shared/status_views.dart';
import '../auth/auth_controller.dart';
import '../reminders/reminder_settings_page.dart';
import 'avatar_repository.dart';
import 'avatar_view.dart';
import 'font_size_page.dart';
import 'profile_edit_page.dart';

class ProfilePage extends ConsumerStatefulWidget {
  const ProfilePage({super.key});

  @override
  ConsumerState<ProfilePage> createState() => _ProfilePageState();
}

class _ProfilePageState extends ConsumerState<ProfilePage> {
  bool _refreshing = false;
  final ImagePicker _picker = ImagePicker();
  final GlobalKey<ProfileAvatarState> _avatarKey =
      GlobalKey<ProfileAvatarState>();

  @override
  void initState() {
    super.initState();
    _recoverLostAvatar();
  }

  Future<void> _recoverLostAvatar() async {
    try {
      final response = await _picker.retrieveLostData();
      if (response.isEmpty) {
        return;
      }
      final files = response.files;
      final file = files != null && files.isNotEmpty
          ? files.first
          : response.file;
      if (file == null) {
        return;
      }
      WidgetsBinding.instance.addPostFrameCallback((_) {
        _avatarKey.currentState?.handleRecoveredFile(file);
      });
    } catch (_) {
      // Recovery is best-effort; a failed read must not block the page.
    }
  }

  Future<XFile?> _pickAvatar() async {
    try {
      return await _picker.pickImage(
        source: ImageSource.gallery,
        maxWidth: 1024,
        maxHeight: 1024,
        imageQuality: 82,
      );
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('无法打开相册，请稍后重试。')));
      }
      return null;
    }
  }

  Future<void> _refresh() async {
    setState(() => _refreshing = true);
    try {
      await ref.read(authControllerProvider.notifier).refreshMe();
    } on ApiException catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.message)));
      }
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(ApiException.fromUnknown(error).message)),
        );
      }
    } finally {
      if (mounted) {
        setState(() => _refreshing = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final session = ref.watch(authControllerProvider);
    final me = session.me;
    final teacher = me?.teacher;
    final serverUrl = ref
        .watch(settingsRepositoryProvider)
        .apiBaseUrl
        .replaceFirst(RegExp(r'/$'), '');
    if (me == null) {
      return const Scaffold(
        body: ErrorView(message: '账号资料不可用。', onRetry: _noop),
      );
    }
    return Scaffold(
      appBar: AppBar(
        title: const Text('我的'),
        actions: [
          IconButton(
            tooltip: '刷新账号',
            onPressed: _refreshing ? null : _refresh,
            icon: _refreshing
                ? const SizedBox(
                    width: 20,
                    height: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.refresh_rounded),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(18, 6, 18, 30),
        children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.all(20),
              child: Row(
                children: [
                  ProfileAvatar(
                    key: _avatarKey,
                    accountId: me.id,
                    displayName: me.displayName,
                    avatarUrl: me.avatarUrl,
                    repository: ref.watch(avatarRepositoryProvider),
                    pickImage: _pickAvatar,
                    refreshMe: () =>
                        ref.read(authControllerProvider.notifier).refreshMe(),
                  ),
                  const SizedBox(width: 16),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          me.displayName,
                          style: Theme.of(context).textTheme.titleLarge,
                        ),
                        const SizedBox(height: 4),
                        Text(
                          me.email,
                          style: const TextStyle(
                            fontSize: 14.5,
                            color: appMuted,
                          ),
                        ),
                        const SizedBox(height: 8),
                        Wrap(
                          spacing: 8,
                          runSpacing: 6,
                          children: [
                            InfoPill(
                              label: me.emailVerified ? '邮箱已验证' : '邮箱未验证',
                              color: me.emailVerified
                                  ? appTeal
                                  : const Color(0xFFAD6A12),
                            ),
                            InfoPill(
                              label: _teacherStatusLabel(me.teacherStatus),
                              color: me.teacherStatus == 'active'
                                  ? const Color(0xFF3D8B68)
                                  : const Color(0xFFAD6A12),
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 14),
          Card(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(18, 18, 18, 4),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('教师资料', style: Theme.of(context).textTheme.titleMedium),
                  const SizedBox(height: 8),
                  _ProfileRow(label: '工号', value: teacher?.teacherNo ?? '—'),
                  _ProfileRow(label: '正式姓名', value: teacher?.formalName ?? '—'),
                  _ProfileRow(label: '显示名', value: teacher?.displayName ?? '—'),
                  _ProfileRow(label: '院系', value: teacher?.department ?? '—'),
                  _ProfileRow(
                    label: '电话',
                    value: _contactValue(
                      teacher?.phone,
                      teacher?.phoneVisibility,
                    ),
                  ),
                  _ProfileRow(
                    label: '微信',
                    value: _contactValue(
                      teacher?.wechat,
                      teacher?.wechatVisibility,
                    ),
                  ),
                  _ProfileRow(
                    label: '当前学期',
                    value: me.currentTerm?.name ?? '未设置',
                    last: true,
                  ),
                  const SizedBox(height: 6),
                  Align(
                    alignment: Alignment.centerLeft,
                    child: TextButton.icon(
                      key: const Key('editProfileButton'),
                      onPressed: () => Navigator.of(context).push<void>(
                        MaterialPageRoute(
                          builder: (context) => const ProfileEditPage(),
                        ),
                      ),
                      icon: const Icon(Icons.edit_outlined, size: 20),
                      label: const Text('编辑资料'),
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 14),
          Card(
            child: Column(
              children: [
                ListTile(
                  key: const Key('fontSizeEntry'),
                  leading: const Icon(
                    Icons.format_size_rounded,
                    color: appTeal,
                  ),
                  title: const Text('字号设置'),
                  subtitle: const Text('调整全 App 的阅读字号'),
                  trailing: const Icon(Icons.chevron_right, color: appMuted),
                  onTap: () => Navigator.of(context).push<void>(
                    MaterialPageRoute(
                      builder: (context) => const FontSizePage(),
                    ),
                  ),
                ),
                const Divider(height: 1),
                ListTile(
                  key: const Key('reminderSettingsEntry'),
                  leading: const Icon(
                    Icons.notifications_active_outlined,
                    color: appTeal,
                  ),
                  title: const Text('上课提醒'),
                  trailing: const Icon(Icons.chevron_right, color: appMuted),
                  onTap: () => Navigator.of(context).push<void>(
                    MaterialPageRoute(
                      builder: (context) => const ReminderSettingsPage(),
                    ),
                  ),
                ),
                const Divider(height: 1),
                ListTile(
                  key: const Key('passwordChangeEntry'),
                  leading: const Icon(Icons.lock_outline, color: appTeal),
                  title: const Text('修改密码'),
                  trailing: const Icon(Icons.chevron_right, color: appMuted),
                  onTap: () => context.push('/password'),
                ),
              ],
            ),
          ),
          const SizedBox(height: 14),
          Card(
            child: ExpansionTile(
              shape: const Border(),
              collapsedShape: const Border(),
              leading: const Icon(Icons.link_outlined, color: appTeal),
              title: const Text('连接设置'),
              subtitle: const Text('仅在需要更改连接地址时展开'),
              childrenPadding: const EdgeInsets.fromLTRB(18, 0, 18, 18),
              children: [
                _ProfileRow(label: '连接地址', value: serverUrl, last: true),
              ],
            ),
          ),
          const SizedBox(height: 22),
          OutlinedButton.icon(
            key: const Key('logoutButton'),
            onPressed: () async {
              await ref.read(authControllerProvider.notifier).logout();
            },
            icon: const Icon(Icons.logout),
            label: const Text('退出登录'),
          ),
          const SizedBox(height: 12),
          const Text(
            '退出后需要重新登录，本机未提交的草稿会按账号保留。',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 13, color: appMuted),
          ),
        ],
      ),
    );
  }

  static String _contactValue(String? value, String? visibility) {
    if (value == null || value.isEmpty) {
      return '未填写';
    }
    final label = contactVisibilityOptions[visibility] ?? '仅本人';
    return '$value（$label）';
  }

  static String _teacherStatusLabel(String status) => switch (status) {
    'active' => '教师身份已确认',
    'pending' => '待审核',
    'disabled' => '已停用',
    'unlinked' => '待确认教师身份',
    _ => '身份待确认',
  };
}

class _ProfileRow extends StatelessWidget {
  const _ProfileRow({
    required this.label,
    required this.value,
    this.last = false,
  });

  final String label;
  final String value;
  final bool last;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 12),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SizedBox(
                width: 100,
                child: Text(
                  label,
                  style: const TextStyle(fontSize: 15, color: appMuted),
                ),
              ),
              Expanded(
                child: SelectableText(
                  value,
                  style: const TextStyle(fontSize: 15.5, height: 1.45),
                ),
              ),
            ],
          ),
        ),
        if (!last) const Divider(height: 1),
      ],
    );
  }
}

void _noop() {}
