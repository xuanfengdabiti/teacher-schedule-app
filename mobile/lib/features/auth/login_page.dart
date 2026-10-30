import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/theme.dart';
import '../../core/config/api_config.dart';
import '../../core/network/api_exception.dart';
import '../../core/providers.dart';
import 'account_access_notice.dart';
import 'auth_controller.dart';

class LoginPage extends ConsumerStatefulWidget {
  const LoginPage({super.key});

  @override
  ConsumerState<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends ConsumerState<LoginPage> {
  static const _demoPassword = 'TeacherDemo2026!';
  static const _demoLi = 'li@example.invalid';
  static const _demoZhang = 'zhang@example.invalid';
  static const _demoWang = 'wang@example.invalid';
  static const _demoTeacherA = 't0001@example.invalid';
  static const _demoTeacherB = 't0002@example.invalid';

  late final TextEditingController _serverController;
  final _emailController = TextEditingController();
  final _passwordController = TextEditingController();
  final _formKey = GlobalKey<FormState>();
  bool _obscurePassword = true;
  bool _submitting = false;
  bool _demoReplayImport = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _serverController = TextEditingController(
      text: ref.read(settingsRepositoryProvider).apiBaseUrl,
    );
  }

  @override
  void dispose() {
    _serverController.dispose();
    _emailController.dispose();
    _passwordController.dispose();
    super.dispose();
  }

  void _fillDemo(String email) {
    _emailController.text = email;
    _passwordController.text = _demoPassword;
    setState(() => _error = null);
  }

  Future<void> _submit() async {
    if (_submitting || !(_formKey.currentState?.validate() ?? false)) {
      return;
    }
    FocusScope.of(context).unfocus();
    setState(() {
      _submitting = true;
      _error = null;
    });
    try {
      ref.read(demoImportReplayProvider.notifier).set(_demoReplayImport);
      await ref
          .read(authControllerProvider.notifier)
          .login(
            baseUrl: _serverController.text,
            email: _emailController.text,
            password: _passwordController.text,
          );
    } on ApiException catch (error) {
      if (mounted) {
        setState(() => _error = error.message);
      }
    } on FormatException catch (error) {
      if (mounted) {
        setState(() => _error = error.message);
      }
    } catch (error) {
      if (mounted) {
        setState(() => _error = ApiException.fromUnknown(error).message);
      }
    } finally {
      if (mounted) {
        setState(() => _submitting = false);
      }
    }
  }

  Future<void> _openAccountPage(String path) async {
    if (_submitting) {
      return;
    }
    FocusScope.of(context).unfocus();
    setState(() => _error = null);
    try {
      final normalized = ApiConfig.normalizeBaseUrl(_serverController.text);
      await ref.read(settingsRepositoryProvider).setApiBaseUrl(normalized);
      ref.read(apiClientProvider).baseUrl = normalized;
      if (mounted) {
        _serverController.text = normalized;
        context.push(path);
      }
    } on FormatException catch (error) {
      if (mounted) {
        setState(() => _error = error.message);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final accountNotice = ref.watch(accountAccessNoticeProvider);
    if (accountNotice != null) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (!mounted) {
          return;
        }
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(accountNotice)));
        ref.read(accountAccessNoticeProvider.notifier).clear();
      });
    }
    final passwordChanged = ref.watch(passwordChangedNoticeProvider);
    if (passwordChanged) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (!mounted) {
          return;
        }
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('密码已修改，请重新登录。')));
        ref.read(passwordChangedNoticeProvider.notifier).clear();
      });
    }
    return Scaffold(
      body: Container(
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [Color(0xFFE5F3F0), Color(0xFFF7FBFA)],
          ),
        ),
        child: SafeArea(
          child: Center(
            child: SingleChildScrollView(
              padding: const EdgeInsets.fromLTRB(22, 28, 22, 28),
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 460),
                child: Form(
                  key: _formKey,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Row(
                        children: [
                          Container(
                            width: 58,
                            height: 58,
                            decoration: BoxDecoration(
                              color: Colors.white,
                              borderRadius: BorderRadius.circular(18),
                              border: Border.all(color: appLine),
                            ),
                            child: const Icon(
                              Icons.calendar_month_rounded,
                              color: appTeal,
                              size: 32,
                            ),
                          ),
                          const SizedBox(width: 14),
                          const Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                '教师课表',
                                style: TextStyle(
                                  fontSize: 28,
                                  fontWeight: FontWeight.w800,
                                  color: appInk,
                                ),
                              ),
                              SizedBox(height: 2),
                              Text(
                                '今天要上的课，一眼看清',
                                style: TextStyle(fontSize: 15, color: appMuted),
                              ),
                            ],
                          ),
                        ],
                      ),
                      const SizedBox(height: 30),
                      Card(
                        child: Padding(
                          padding: const EdgeInsets.fromLTRB(20, 22, 20, 22),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                              Row(
                                children: [
                                  Text(
                                    '登录',
                                    style: Theme.of(context)
                                        .textTheme
                                        .titleLarge,
                                  ),
                                  const Spacer(),
                                  Container(
                                    padding: const EdgeInsets.symmetric(
                                      horizontal: 10,
                                      vertical: 5,
                                    ),
                                    decoration: BoxDecoration(
                                      color: const Color(0xFFEAF5F2),
                                      borderRadius: BorderRadius.circular(999),
                                    ),
                                    child: const Text(
                                      '演示账号 · 虚构数据',
                                      style: TextStyle(
                                        fontSize: 12,
                                        fontWeight: FontWeight.w600,
                                        color: appTeal,
                                      ),
                                    ),
                                  ),
                                ],
                              ),
                              const SizedBox(height: 12),
                              const Text(
                                '直接选择演示教师，密码统一为 TeacherDemo2026!',
                                style: TextStyle(
                                  fontSize: 13,
                                  height: 1.45,
                                  color: appMuted,
                                ),
                              ),
                              const SizedBox(height: 10),
                              Row(
                                children: [
                                  Expanded(
                                    child: OutlinedButton(
                                      key: const Key('demoLiButton'),
                                      onPressed: () => _fillDemo(_demoLi),
                                      child: const Text('李老师'),
                                    ),
                                  ),
                                  const SizedBox(width: 8),
                                  Expanded(
                                    child: OutlinedButton(
                                      key: const Key('demoZhangButton'),
                                      onPressed: () => _fillDemo(_demoZhang),
                                      child: const Text('张老师'),
                                    ),
                                  ),
                                  const SizedBox(width: 8),
                                  Expanded(
                                    child: OutlinedButton(
                                      key: const Key('demoWangButton'),
                                      onPressed: () => _fillDemo(_demoWang),
                                      child: const Text('王老师'),
                                    ),
                                  ),
                                ],
                              ),
                              const SizedBox(height: 4),
                              Theme(
                                data: Theme.of(context)
                                    .copyWith(dividerColor: Colors.transparent),
                                child: ExpansionTile(
                                  key: const Key('otherDemoAccountsTile'),
                                  tilePadding: EdgeInsets.zero,
                                  childrenPadding: const EdgeInsets.only(
                                    bottom: 8,
                                  ),
                                  shape: const Border(),
                                  collapsedShape: const Border(),
                                  title: const Text(
                                    '其他演示账号',
                                    style: TextStyle(
                                      fontSize: 14,
                                      color: appMuted,
                                    ),
                                  ),
                                  children: [
                                    Row(
                                      children: [
                                        Expanded(
                                          child: OutlinedButton(
                                            key: const Key(
                                              'demoTeacherAButton',
                                            ),
                                            onPressed: () =>
                                                _fillDemo(_demoTeacherA),
                                            child: const Text('教师甲'),
                                          ),
                                        ),
                                        const SizedBox(width: 8),
                                        Expanded(
                                          child: OutlinedButton(
                                            key: const Key(
                                              'demoTeacherBButton',
                                            ),
                                            onPressed: () =>
                                                _fillDemo(_demoTeacherB),
                                            child: const Text('教师乙'),
                                          ),
                                        ),
                                      ],
                                    ),
                                    CheckboxListTile(
                                      key: const Key('demoReplayImportSwitch'),
                                      value: _demoReplayImport,
                                      onChanged: (value) => setState(
                                        () =>
                                            _demoReplayImport = value ?? false,
                                      ),
                                      dense: true,
                                      contentPadding: EdgeInsets.zero,
                                      controlAffinity:
                                          ListTileControlAffinity.leading,
                                      title: const Text(
                                        '演示首次导入',
                                        style: TextStyle(fontSize: 14),
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                              const SizedBox(height: 8),
                              TextFormField(
                                key: const Key('emailField'),
                                controller: _emailController,
                                keyboardType: TextInputType.emailAddress,
                                textInputAction: TextInputAction.next,
                                autocorrect: false,
                                decoration: const InputDecoration(
                                  labelText: '邮箱',
                                  prefixIcon: Icon(Icons.mail_outline),
                                ),
                                validator: (value) {
                                  final text = value?.trim() ?? '';
                                  if (text.isEmpty || !text.contains('@')) {
                                    return '请输入学校邮箱。';
                                  }
                                  return null;
                                },
                              ),
                              const SizedBox(height: 14),
                              TextFormField(
                                key: const Key('passwordField'),
                                controller: _passwordController,
                                obscureText: _obscurePassword,
                                textInputAction: TextInputAction.done,
                                onFieldSubmitted: (_) => _submit(),
                                decoration: InputDecoration(
                                  labelText: '密码',
                                  prefixIcon: const Icon(Icons.lock_outline),
                                  suffixIcon: IconButton(
                                    tooltip: _obscurePassword ? '显示密码' : '隐藏密码',
                                    onPressed: () => setState(
                                      () =>
                                          _obscurePassword = !_obscurePassword,
                                    ),
                                    icon: Icon(
                                      _obscurePassword
                                          ? Icons.visibility_outlined
                                          : Icons.visibility_off_outlined,
                                    ),
                                  ),
                                ),
                                validator: (value) =>
                                    (value == null || value.isEmpty)
                                    ? '请输入密码。'
                                    : null,
                              ),
                              if (_error != null) ...[
                                const SizedBox(height: 14),
                                Container(
                                  padding: const EdgeInsets.all(12),
                                  decoration: BoxDecoration(
                                    color: const Color(0xFFFFF1EF),
                                    borderRadius: BorderRadius.circular(12),
                                    border: Border.all(
                                      color: const Color(0xFFF3C5BE),
                                    ),
                                  ),
                                  child: Text(
                                    _error!,
                                    key: const Key('loginError'),
                                    style: const TextStyle(
                                      fontSize: 15,
                                      color: Color(0xFF9A3B30),
                                    ),
                                  ),
                                ),
                              ],
                              const SizedBox(height: 18),
                              FilledButton(
                                key: const Key('loginButton'),
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
                                    : const Text('登录'),
                              ),
                              const SizedBox(height: 4),
                              Row(
                                children: [
                                  Expanded(
                                    child: TextButton(
                                      key: const Key('openRegisterButton'),
                                      onPressed: _submitting
                                          ? null
                                          : () => _openAccountPage('/register'),
                                      child: const Text('注册账号'),
                                    ),
                                  ),
                                  Expanded(
                                    child: TextButton(
                                      key: const Key(
                                        'openForgotPasswordButton',
                                      ),
                                      onPressed: _submitting
                                          ? null
                                          : () => _openAccountPage(
                                              '/forgot-password',
                                            ),
                                      child: const Text('忘记密码'),
                                    ),
                                  ),
                                ],
                              ),
                            ],
                          ),
                        ),
                      ),
                      const SizedBox(height: 14),
                      Card(
                        child: ExpansionTile(
                          key: const Key('serverSettingsTile'),
                          shape: const Border(),
                          collapsedShape: const Border(),
                          leading: const Icon(
                            Icons.dns_outlined,
                            color: appTeal,
                          ),
                          title: const Text(
                            '连接设置',
                            style: TextStyle(
                              fontSize: 16,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                          subtitle: Text(
                            _serverController.text,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(fontSize: 13),
                          ),
                          childrenPadding: const EdgeInsets.fromLTRB(
                            18,
                            0,
                            18,
                            18,
                          ),
                          children: [
                            TextFormField(
                              key: const Key('serverUrlField'),
                              controller: _serverController,
                              keyboardType: TextInputType.url,
                              autocorrect: false,
                              decoration: const InputDecoration(
                                labelText: '连接地址',
                                hintText: 'http://192.168.1.10:18000/api/v1',
                              ),
                              onChanged: (_) => setState(() {}),
                              validator: (value) {
                                if ((value?.trim() ?? '').isEmpty) {
                                  return '请填写连接地址。';
                                }
                                return null;
                              },
                            ),
                            const SizedBox(height: 10),
                            const Text(
                              '服务地址以 /api/v1/ 结尾。',
                              style: TextStyle(
                                fontSize: 13,
                                height: 1.45,
                                color: appMuted,
                              ),
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(height: 20),
                      const Text(
                        '请使用演示账号登录。',
                        textAlign: TextAlign.center,
                        style: TextStyle(fontSize: 13, color: appMuted),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
