import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/theme.dart';
import '../../core/config/api_config.dart';
import '../../core/network/api_exception.dart';
import '../../core/providers.dart';
import 'account_access_notice.dart';
import 'account_access_repository.dart';
import 'auth_controller.dart';

/// Applies the address typed on the account page and points ApiClient at it.
Future<String> saveAccountBaseUrl(WidgetRef ref, String raw) async {
  final normalized = ApiConfig.normalizeBaseUrl(raw);
  await ref.read(settingsRepositoryProvider).setApiBaseUrl(normalized);
  ref.read(apiClientProvider).baseUrl = normalized;
  return normalized;
}

/// Shared server-address field used by the account-entry pages.
class AccountServerField extends StatelessWidget {
  const AccountServerField({
    super.key,
    required this.controller,
    required this.fieldKey,
  });

  final TextEditingController controller;
  final Key fieldKey;

  @override
  Widget build(BuildContext context) {
    return ExpansionTile(
      shape: const Border(),
      collapsedShape: const Border(),
      leading: const Icon(Icons.dns_outlined, color: appTeal),
      title: const Text('连接设置', style: TextStyle(fontSize: 15)),
      subtitle: Text(
        controller.text,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: const TextStyle(fontSize: 13),
      ),
      childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 14),
      children: [
        TextFormField(
          key: fieldKey,
          controller: controller,
          keyboardType: TextInputType.url,
          autocorrect: false,
          decoration: const InputDecoration(
            labelText: '服务地址',
            hintText: 'https://服务地址/api/v1/',
          ),
          validator: (value) =>
              (value?.trim() ?? '').isEmpty ? '请填写服务地址。' : null,
        ),
      ],
    );
  }
}

class RegisterPage extends ConsumerStatefulWidget {
  const RegisterPage({super.key});

  @override
  ConsumerState<RegisterPage> createState() => _RegisterPageState();
}

class _RegisterPageState extends ConsumerState<RegisterPage> {
  final _formKey = GlobalKey<FormState>();
  final _name = TextEditingController();
  final _teacherNo = TextEditingController();
  final _email = TextEditingController();
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
    _name.dispose();
    _teacherNo.dispose();
    _email.dispose();
    _password1.dispose();
    _password2.dispose();
    _server.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_submitting || !(_formKey.currentState?.validate() ?? false)) {
      return;
    }
    if (_password1.text != _password2.text) {
      setState(() => _error = '两次输入的密码不一致。');
      return;
    }
    FocusScope.of(context).unfocus();
    setState(() {
      _submitting = true;
      _error = null;
    });
    try {
      await saveAccountBaseUrl(ref, _server.text);
      await ref
          .read(accountAccessRepositoryProvider)
          .register(
            formalName: _name.text,
            teacherNo: _teacherNo.text,
            email: _email.text,
            password1: _password1.text,
            password2: _password2.text,
          );
      if (mounted) {
        context.push('/verify-email', extra: {'email': _email.text.trim()});
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
      appBar: AppBar(title: const Text('注册账号')),
      body: Form(
        key: _formKey,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(18, 12, 18, 28),
          children: [
            Card(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(18, 18, 18, 16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    TextFormField(
                      key: const Key('registerNameField'),
                      controller: _name,
                      maxLength: 100,
                      textInputAction: TextInputAction.next,
                      decoration: const InputDecoration(labelText: '正式姓名'),
                      validator: (value) =>
                          (value?.trim() ?? '').isEmpty ? '请填写正式姓名。' : null,
                    ),
                    TextFormField(
                      key: const Key('registerTeacherNoField'),
                      controller: _teacherNo,
                      maxLength: 64,
                      textInputAction: TextInputAction.next,
                      decoration: const InputDecoration(labelText: '工号'),
                      validator: (value) =>
                          (value?.trim() ?? '').isEmpty ? '请填写工号。' : null,
                    ),
                    const SizedBox(height: 6),
                    TextFormField(
                      key: const Key('registerEmailField'),
                      controller: _email,
                      keyboardType: TextInputType.emailAddress,
                      autocorrect: false,
                      textInputAction: TextInputAction.next,
                      decoration: const InputDecoration(labelText: '邮箱'),
                      validator: (value) {
                        final text = value?.trim() ?? '';
                        if (text.isEmpty || !text.contains('@')) {
                          return '请输入邮箱。';
                        }
                        return null;
                      },
                    ),
                    const SizedBox(height: 14),
                    TextFormField(
                      key: const Key('registerPasswordField'),
                      controller: _password1,
                      obscureText: _obscure,
                      textInputAction: TextInputAction.next,
                      decoration: InputDecoration(
                        labelText: '密码',
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
                          (value == null || value.isEmpty) ? '请输入密码。' : null,
                    ),
                    const SizedBox(height: 14),
                    TextFormField(
                      key: const Key('registerPassword2Field'),
                      controller: _password2,
                      obscureText: _obscure,
                      textInputAction: TextInputAction.done,
                      onFieldSubmitted: (_) => _submit(),
                      decoration: const InputDecoration(labelText: '确认密码'),
                      validator: (value) =>
                          (value == null || value.isEmpty) ? '请再次输入密码。' : null,
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 8),
            Card(
              child: AccountServerField(
                controller: _server,
                fieldKey: const Key('registerServerField'),
              ),
            ),
            if (_error != null) ...[
              const SizedBox(height: 12),
              Text(
                _error!,
                key: const Key('registerError'),
                style: const TextStyle(fontSize: 14, color: Color(0xFF9A3B30)),
              ),
            ],
            const SizedBox(height: 18),
            FilledButton(
              key: const Key('registerSubmitButton'),
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
                  : const Text('注册'),
            ),
            const SizedBox(height: 12),
            TextButton(
              key: const Key('registerBackToLogin'),
              onPressed: () => context.go('/login'),
              child: const Text('返回登录'),
            ),
          ],
        ),
      ),
    );
  }
}

class VerifyEmailPage extends ConsumerStatefulWidget {
  const VerifyEmailPage({super.key, this.email});

  final String? email;

  @override
  ConsumerState<VerifyEmailPage> createState() => _VerifyEmailPageState();
}

class _VerifyEmailPageState extends ConsumerState<VerifyEmailPage> {
  final _link = TextEditingController();
  late final TextEditingController _email;
  late final TextEditingController _server;
  bool _submitting = false;
  bool _resending = false;
  bool _verified = false;
  bool _hasSession = false;
  String? _error;
  String? _status;

  @override
  void initState() {
    super.initState();
    _email = TextEditingController(text: widget.email ?? '');
    _server = TextEditingController(
      text: ref.read(settingsRepositoryProvider).apiBaseUrl,
    );
    _hasSession = ref.read(authControllerProvider).me != null;
  }

  @override
  void dispose() {
    _link.dispose();
    _email.dispose();
    _server.dispose();
    super.dispose();
  }

  Future<void> _verify() async {
    if (_submitting) {
      return;
    }
    final values = parseVerifyLink(_link.text);
    if ((values.key ?? '').isEmpty) {
      setState(() => _error = '链接格式不正确，请重新复制邮件中的验证链接。');
      return;
    }
    setState(() {
      _submitting = true;
      _error = null;
      _status = null;
    });
    try {
      await saveAccountBaseUrl(ref, _server.text);
      await ref.read(accountAccessRepositoryProvider).verifyEmail(values.key!);
      if (!mounted) {
        return;
      }
      setState(() {
        _verified = true;
        _status = _hasSession ? '邮箱已验证，正在更新账号状态…' : '邮箱已验证，请返回登录。';
      });
      if (_hasSession) {
        // Refresh the real Me so a restricted session becomes active/pending as
        // the server reports, instead of only changing local UI state.
        await ref.read(authControllerProvider.notifier).refreshMe();
        if (mounted) {
          setState(() => _status = '邮箱已验证。');
        }
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

  Future<void> _resend() async {
    if (_resending) {
      return;
    }
    if (_email.text.trim().isEmpty) {
      setState(() => _error = '请先填写邮箱。');
      return;
    }
    setState(() {
      _resending = true;
      _error = null;
      _status = null;
    });
    try {
      await saveAccountBaseUrl(ref, _server.text);
      await ref
          .read(accountAccessRepositoryProvider)
          .resendVerification(_email.text);
      if (mounted) {
        setState(() => _status = '验证邮件已请求发送，请查收邮箱。');
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
        setState(() => _resending = false);
      }
    }
  }

  void _back() {
    if (_hasSession) {
      // The router picks account-status or the schedule from the refreshed
      // session; never fake a signed-in identity here.
      context.go('/account-status');
      return;
    }
    ref
        .read(accountAccessNoticeProvider.notifier)
        .set(_verified ? '邮箱已验证，请登录。' : null);
    context.go('/login');
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('验证邮箱')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(18, 12, 18, 28),
        children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.all(18),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Text(
                    '打开邮件中的验证链接，复制整段链接或验证码填入下面。',
                    style: TextStyle(
                      fontSize: 14,
                      height: 1.5,
                      color: appMuted,
                    ),
                  ),
                  const SizedBox(height: 14),
                  TextField(
                    key: const Key('verifyLinkField'),
                    controller: _link,
                    maxLines: 2,
                    decoration: const InputDecoration(labelText: '验证链接或验证码'),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    key: const Key('verifyEmailField'),
                    controller: _email,
                    keyboardType: TextInputType.emailAddress,
                    autocorrect: false,
                    decoration: const InputDecoration(labelText: '邮箱'),
                  ),
                  const SizedBox(height: 16),
                  FilledButton(
                    key: const Key('verifySubmitButton'),
                    onPressed: _submitting ? null : _verify,
                    child: _submitting
                        ? const SizedBox(
                            width: 22,
                            height: 22,
                            child: CircularProgressIndicator(
                              strokeWidth: 2.5,
                              color: Colors.white,
                            ),
                          )
                        : const Text('完成验证'),
                  ),
                  const SizedBox(height: 8),
                  OutlinedButton(
                    key: const Key('resendVerificationButton'),
                    onPressed: _resending ? null : _resend,
                    child: Text(_resending ? '发送中…' : '重新发送验证邮件'),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 8),
          Card(
            child: AccountServerField(
              controller: _server,
              fieldKey: const Key('verifyServerField'),
            ),
          ),
          if (_status != null) ...[
            const SizedBox(height: 12),
            Text(
              _status!,
              key: const Key('verifyStatus'),
              style: const TextStyle(fontSize: 14, color: appTeal),
            ),
          ],
          if (_error != null) ...[
            const SizedBox(height: 12),
            Text(
              _error!,
              key: const Key('verifyError'),
              style: const TextStyle(fontSize: 14, color: Color(0xFF9A3B30)),
            ),
          ],
          const SizedBox(height: 16),
          FilledButton.tonal(
            key: const Key('verifyBackToLogin'),
            onPressed: _back,
            child: Text(_hasSession ? '返回账号状态' : '返回登录'),
          ),
        ],
      ),
    );
  }
}
