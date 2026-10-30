import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/theme.dart';
import '../../core/network/api_exception.dart';
import '../../core/providers.dart';
import 'account_access_notice.dart';
import 'account_access_repository.dart';
import 'account_entry_pages.dart';

class ForgotPasswordPage extends ConsumerStatefulWidget {
  const ForgotPasswordPage({super.key});

  @override
  ConsumerState<ForgotPasswordPage> createState() => _ForgotPasswordPageState();
}

class _ForgotPasswordPageState extends ConsumerState<ForgotPasswordPage> {
  final _formKey = GlobalKey<FormState>();
  final _email = TextEditingController();
  late final TextEditingController _server;
  bool _submitting = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _server = TextEditingController(
      text: ref.read(settingsRepositoryProvider).apiBaseUrl,
    );
  }

  @override
  void dispose() {
    _email.dispose();
    _server.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_submitting || !(_formKey.currentState?.validate() ?? false)) {
      return;
    }
    setState(() {
      _submitting = true;
      _error = null;
    });
    try {
      await saveAccountBaseUrl(ref, _server.text);
      await ref
          .read(accountAccessRepositoryProvider)
          .requestPasswordReset(_email.text);
      if (mounted) {
        context.push('/reset-password', extra: {'email': _email.text.trim()});
      }
    } on ApiException catch (error) {
      if (mounted) {
        setState(() => _error = error.message);
      }
    } on FormatException catch (error) {
      if (mounted) {
        setState(() => _error = error.message);
      }
    } finally {
      if (mounted) {
        setState(() => _submitting = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('忘记密码')),
      body: Form(
        key: _formKey,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(18, 12, 18, 28),
          children: [
            Card(
              child: Padding(
                padding: const EdgeInsets.all(18),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const Text(
                      '填写注册邮箱，我们会请求发送重置邮件。下一步粘贴邮件里的链接。',
                      style: TextStyle(
                        fontSize: 14,
                        height: 1.5,
                        color: appMuted,
                      ),
                    ),
                    const SizedBox(height: 14),
                    TextFormField(
                      key: const Key('resetEmailField'),
                      controller: _email,
                      keyboardType: TextInputType.emailAddress,
                      autocorrect: false,
                      decoration: const InputDecoration(labelText: '邮箱'),
                      validator: (value) {
                        final text = value?.trim() ?? '';
                        if (text.isEmpty || !text.contains('@')) {
                          return '请输入邮箱。';
                        }
                        return null;
                      },
                    ),
                    const SizedBox(height: 16),
                    FilledButton(
                      key: const Key('sendResetEmailButton'),
                      onPressed: _submitting ? null : _submit,
                      child: _submitting
                          ? const SizedBox(
                              width: 22,
                              height: 22,
                              child: CircularProgressIndicator(
                                strokeWidth: 2.5,
                                color: Colors.white,
                              ),
                            )
                          : const Text('发送重置邮件'),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 8),
            Card(
              child: AccountServerField(
                controller: _server,
                fieldKey: const Key('resetServerField'),
              ),
            ),
            if (_error != null) ...[
              const SizedBox(height: 12),
              Text(
                _error!,
                key: const Key('resetRequestError'),
                style: const TextStyle(fontSize: 14, color: Color(0xFF9A3B30)),
              ),
            ],
            const SizedBox(height: 12),
            TextButton(
              onPressed: () => context.go('/login'),
              child: const Text('返回登录'),
            ),
          ],
        ),
      ),
    );
  }
}

class ResetPasswordConfirmPage extends ConsumerStatefulWidget {
  const ResetPasswordConfirmPage({super.key, this.email});

  final String? email;

  @override
  ConsumerState<ResetPasswordConfirmPage> createState() =>
      _ResetPasswordConfirmPageState();
}

class _ResetPasswordConfirmPageState
    extends ConsumerState<ResetPasswordConfirmPage> {
  final _formKey = GlobalKey<FormState>();
  final _link = TextEditingController();
  final _uid = TextEditingController();
  final _token = TextEditingController();
  final _password1 = TextEditingController();
  final _password2 = TextEditingController();
  late final TextEditingController _server;
  bool _obscure = true;
  bool _submitting = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _server = TextEditingController(
      text: ref.read(settingsRepositoryProvider).apiBaseUrl,
    );
  }

  @override
  void dispose() {
    _link.dispose();
    _uid.dispose();
    _token.dispose();
    _password1.dispose();
    _password2.dispose();
    _server.dispose();
    super.dispose();
  }

  void _applyLink() {
    final values = parseResetLink(_link.text);
    if ((values.uid ?? '').isEmpty || (values.token ?? '').isEmpty) {
      setState(() => _error = '链接格式不正确，请重新复制邮件中的重置链接。');
      return;
    }
    setState(() {
      _uid.text = values.uid!;
      _token.text = values.token!;
      _error = null;
    });
  }

  Future<void> _submit() async {
    if (_submitting) {
      return;
    }
    if (_password1.text != _password2.text) {
      setState(() => _error = '两次输入的新密码不一致。');
      return;
    }
    if (_uid.text.trim().isEmpty || _token.text.trim().isEmpty) {
      // 只粘贴链接时也能继续：先尝试从链接填入，仍失败才提示。
      final values = parseResetLink(_link.text);
      if ((values.uid ?? '').isNotEmpty && (values.token ?? '').isNotEmpty) {
        _uid.text = values.uid!;
        _token.text = values.token!;
      }
    }
    if (_uid.text.trim().isEmpty || _token.text.trim().isEmpty) {
      setState(() => _error = '请粘贴重置链接，或展开“手动填写”补全信息。');
      return;
    }
    if (_password1.text.isEmpty || _password2.text.isEmpty) {
      setState(() => _error = '请填写新密码并确认。');
      return;
    }
    if (!(_formKey.currentState?.validate() ?? false)) {
      return;
    }
    setState(() {
      _submitting = true;
      _error = null;
    });
    try {
      await saveAccountBaseUrl(ref, _server.text);
      await ref
          .read(accountAccessRepositoryProvider)
          .confirmPasswordReset(
            uid: _uid.text,
            token: _token.text,
            password1: _password1.text,
            password2: _password2.text,
          );
      if (mounted) {
        ref.read(accountAccessNoticeProvider.notifier).set('密码已重置，请登录。');
        context.go('/login');
      }
    } on ApiException catch (error) {
      if (mounted) {
        setState(() => _error = error.message);
      }
    } on FormatException catch (error) {
      if (mounted) {
        setState(() => _error = error.message);
      }
    } finally {
      if (mounted) {
        setState(() => _submitting = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('重置密码')),
      body: Form(
        key: _formKey,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(18, 12, 18, 28),
          children: [
            Card(
              child: Padding(
                padding: const EdgeInsets.all(18),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const Text(
                      '粘贴邮件中的重置链接，填写新密码即可。',
                      style: TextStyle(
                        fontSize: 14,
                        height: 1.5,
                        color: appMuted,
                      ),
                    ),
                    const SizedBox(height: 14),
                    TextField(
                      key: const Key('resetLinkField'),
                      controller: _link,
                      maxLines: 2,
                      decoration: const InputDecoration(labelText: '重置链接'),
                    ),
                    Align(
                      alignment: Alignment.centerLeft,
                      child: TextButton(
                        key: const Key('applyResetLinkButton'),
                        onPressed: _applyLink,
                        child: const Text('从链接填入'),
                      ),
                    ),
                    ExpansionTile(
                      key: const Key('manualResetParamsTile'),
                      tilePadding: EdgeInsets.zero,
                      childrenPadding: EdgeInsets.zero,
                      shape: const Border(),
                      collapsedShape: const Border(),
                      title: const Text(
                        '手动填写',
                        style: TextStyle(fontSize: 14, color: appMuted),
                      ),
                      children: [
                        TextFormField(
                          key: const Key('resetUidField'),
                          controller: _uid,
                          decoration: const InputDecoration(labelText: '邮箱标识'),
                          validator: (value) =>
                              (value?.trim() ?? '').isEmpty ? '请填写邮箱标识。' : null,
                        ),
                        const SizedBox(height: 12),
                        TextFormField(
                          key: const Key('resetTokenField'),
                          controller: _token,
                          decoration: const InputDecoration(labelText: '重置验证码'),
                          validator: (value) => (value?.trim() ?? '').isEmpty
                              ? '请填写重置验证码。'
                              : null,
                        ),
                      ],
                    ),
                    const SizedBox(height: 14),
                    TextFormField(
                      key: const Key('resetNewPasswordField'),
                      controller: _password1,
                      obscureText: _obscure,
                      decoration: InputDecoration(
                        labelText: '新密码',
                        suffixIcon: IconButton(
                          tooltip: _obscure ? '显示密码' : '隐藏密码',
                          onPressed: () => setState(() => _obscure = !_obscure),
                          icon: Icon(
                            _obscure
                                ? Icons.visibility_outlined
                                : Icons.visibility_off_outlined,
                          ),
                        ),
                      ),
                      validator: (value) =>
                          (value == null || value.isEmpty) ? '请输入新密码。' : null,
                    ),
                    const SizedBox(height: 12),
                    TextFormField(
                      key: const Key('resetConfirmPasswordField'),
                      controller: _password2,
                      obscureText: _obscure,
                      decoration: const InputDecoration(labelText: '确认新密码'),
                      validator: (value) =>
                          (value == null || value.isEmpty) ? '请再次输入新密码。' : null,
                    ),
                    const SizedBox(height: 18),
                    FilledButton(
                      key: const Key('resetPasswordSubmitButton'),
                      onPressed: _submitting ? null : _submit,
                      child: _submitting
                          ? const SizedBox(
                              width: 22,
                              height: 22,
                              child: CircularProgressIndicator(
                                strokeWidth: 2.5,
                                color: Colors.white,
                              ),
                            )
                          : const Text('重置密码'),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 8),
            Card(
              child: AccountServerField(
                controller: _server,
                fieldKey: const Key('resetConfirmServerField'),
              ),
            ),
            if (_error != null) ...[
              const SizedBox(height: 12),
              Text(
                _error!,
                key: const Key('resetConfirmError'),
                style: const TextStyle(fontSize: 14, color: Color(0xFF9A3B30)),
              ),
            ],
            const SizedBox(height: 12),
            TextButton(
              onPressed: () => context.go('/login'),
              child: const Text('返回登录'),
            ),
          ],
        ),
      ),
    );
  }
}
