import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/network/api_exception.dart';
import '../../core/providers.dart';
import '../auth/auth_controller.dart';
import '../auth/me.dart';
import '../schedule/schedule_controller.dart';

const contactVisibilityOptions = <String, String>{
  'private': '仅本人',
  'course_teachers': '同课程教师',
  'school_teachers': '全校教师',
};

class ProfileEditPage extends ConsumerStatefulWidget {
  const ProfileEditPage({super.key});

  @override
  ConsumerState<ProfileEditPage> createState() => _ProfileEditPageState();
}

class _ProfileEditPageState extends ConsumerState<ProfileEditPage> {
  final _formKey = GlobalKey<FormState>();
  late final TextEditingController _displayName;
  late final TextEditingController _department;
  late final TextEditingController _phone;
  late final TextEditingController _wechat;
  late String _phoneVisibility;
  late String _wechatVisibility;
  bool _saving = false;
  String? _error;

  TeacherProfile? get _teacher => ref.read(authControllerProvider).me?.teacher;

  @override
  void initState() {
    super.initState();
    final teacher = _teacher;
    _displayName = TextEditingController(text: teacher?.displayName ?? '');
    _department = TextEditingController(text: teacher?.department ?? '');
    _phone = TextEditingController(text: teacher?.phone ?? '');
    _wechat = TextEditingController(text: teacher?.wechat ?? '');
    _phoneVisibility = teacher?.phoneVisibility ?? 'private';
    _wechatVisibility = teacher?.wechatVisibility ?? 'private';
  }

  @override
  void dispose() {
    _displayName.dispose();
    _department.dispose();
    _phone.dispose();
    _wechat.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (_saving || !(_formKey.currentState?.validate() ?? false)) {
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      final me = await ref
          .read(authRepositoryProvider)
          .updateProfile(
            displayName: _displayName.text.trim(),
            department: _department.text.trim(),
            phone: _phone.text.trim(),
            phoneVisibility: _phoneVisibility,
            wechat: _wechat.text.trim(),
            wechatVisibility: _wechatVisibility,
          );
      ref.read(authControllerProvider.notifier).replaceMe(me);
      // The schedule cards show the teacher name, so drop cached weeks and let
      // the existing pages reload them.
      ref.read(scheduleControllerProvider.notifier).invalidateWeekCache();
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('资料已保存。')));
        Navigator.of(context).pop(true);
      }
    } on ApiException catch (error) {
      if (mounted) {
        setState(() => _error = error.message);
      }
    } catch (error) {
      if (mounted) {
        setState(() => _error = ApiException.fromUnknown(error).message);
      }
    } finally {
      if (mounted) {
        setState(() => _saving = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final me = ref.watch(authControllerProvider).me;
    return Scaffold(
      appBar: AppBar(title: const Text('编辑资料')),
      body: Form(
        key: _formKey,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(18, 12, 18, 28),
          children: [
            Card(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(18, 18, 18, 6),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const Text(
                      '基本信息',
                      style: TextStyle(fontSize: 15, color: appMuted),
                    ),
                    const SizedBox(height: 12),
                    _ReadOnlyField(
                      label: '工号',
                      value: _teacher?.teacherNo ?? '—',
                    ),
                    _ReadOnlyField(
                      label: '正式姓名',
                      value: _teacher?.formalName ?? '—',
                    ),
                    _ReadOnlyField(label: '邮箱', value: me?.email ?? '—'),
                    const SizedBox(height: 10),
                    TextFormField(
                      key: const Key('displayNameField'),
                      controller: _displayName,
                      maxLength: 100,
                      textInputAction: TextInputAction.next,
                      decoration: const InputDecoration(labelText: '显示名'),
                    ),
                    const SizedBox(height: 6),
                    TextFormField(
                      key: const Key('departmentField'),
                      controller: _department,
                      maxLength: 100,
                      textInputAction: TextInputAction.next,
                      decoration: const InputDecoration(labelText: '院系'),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 14),
            Card(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(18, 18, 18, 14),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const Text(
                      '联系方式',
                      style: TextStyle(fontSize: 15, color: appMuted),
                    ),
                    const SizedBox(height: 12),
                    TextFormField(
                      key: const Key('phoneField'),
                      controller: _phone,
                      maxLength: 32,
                      keyboardType: TextInputType.phone,
                      textInputAction: TextInputAction.next,
                      decoration: const InputDecoration(labelText: '电话'),
                    ),
                    const SizedBox(height: 4),
                    _VisibilityField(
                      fieldKey: const Key('phoneVisibilityField'),
                      label: '电话可见范围',
                      value: _phoneVisibility,
                      onChanged: (value) =>
                          setState(() => _phoneVisibility = value),
                    ),
                    const SizedBox(height: 14),
                    TextFormField(
                      key: const Key('wechatField'),
                      controller: _wechat,
                      maxLength: 64,
                      textInputAction: TextInputAction.done,
                      decoration: const InputDecoration(labelText: '微信'),
                    ),
                    const SizedBox(height: 4),
                    _VisibilityField(
                      fieldKey: const Key('wechatVisibilityField'),
                      label: '微信可见范围',
                      value: _wechatVisibility,
                      onChanged: (value) =>
                          setState(() => _wechatVisibility = value),
                    ),
                  ],
                ),
              ),
            ),
            if (_error != null) ...[
              const SizedBox(height: 14),
              Text(
                _error!,
                key: const Key('profileEditError'),
                style: const TextStyle(fontSize: 14, color: Color(0xFF9A3B30)),
              ),
            ],
            const SizedBox(height: 20),
            FilledButton(
              key: const Key('saveProfileButton'),
              onPressed: _saving ? null : _save,
              child: _saving
                  ? const SizedBox(
                      width: 22,
                      height: 22,
                      child: CircularProgressIndicator(
                        strokeWidth: 2.5,
                        color: Colors.white,
                      ),
                    )
                  : const Text('保存'),
            ),
          ],
        ),
      ),
    );
  }
}

class _ReadOnlyField extends StatelessWidget {
  const _ReadOnlyField({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 76,
            child: Text(
              label,
              style: const TextStyle(fontSize: 14.5, color: appMuted),
            ),
          ),
          Expanded(
            child: SelectableText(
              value,
              style: const TextStyle(fontSize: 15, height: 1.4),
            ),
          ),
        ],
      ),
    );
  }
}

class _VisibilityField extends StatelessWidget {
  const _VisibilityField({
    required this.fieldKey,
    required this.label,
    required this.value,
    required this.onChanged,
  });

  final Key fieldKey;
  final String label;
  final String value;
  final ValueChanged<String> onChanged;

  @override
  Widget build(BuildContext context) {
    return DropdownButtonFormField<String>(
      key: fieldKey,
      initialValue: value,
      decoration: InputDecoration(labelText: label),
      items: [
        for (final entry in contactVisibilityOptions.entries)
          DropdownMenuItem(value: entry.key, child: Text(entry.value)),
      ],
      onChanged: (next) {
        if (next != null) {
          onChanged(next);
        }
      },
    );
  }
}
