import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/network/api_exception.dart';
import '../../core/providers.dart';
import '../auth/auth_controller.dart';

class PasswordChangePage extends ConsumerStatefulWidget {
  const PasswordChangePage({super.key});

  @override
  ConsumerState<PasswordChangePage> createState() => _PasswordChangePageState();
}

class _PasswordChangePageState extends ConsumerState<PasswordChangePage> {
  final _formKey = GlobalKey<FormState>();
  final _oldPassword = TextEditingController();
  final _newPassword = TextEditingController();
  final _confirmPassword = TextEditingController();
  bool _obscure = true;
  bool _submitting = false;
  String? _error;
  String? _mismatch;

  @override
  void dispose() {
    _oldPassword.dispose();
    _newPassword.dispose();
    _confirmPassword.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_submitting) {
      return;
    }
    setState(() {
      _error = null;
      _mismatch = null;
    });
    if (!(_formKey.currentState?.validate() ?? false)) {
      return;
    }
    if (_newPassword.text != _confirmPassword.text) {
      setState(() => _mismatch = '两次输入的新密码不一致。');
      return;
    }
    setState(() => _submitting = true);
    // Captured before the request: the page may be popped while it is in
    // flight, which disposes this ConsumerState's ref.
    final auth = ref.read(authControllerProvider.notifier);
    final repository = ref.read(authRepositoryProvider);
    try {
      await repository.changePassword(
        oldPassword: _oldPassword.text,
        newPassword: _newPassword.text,
        confirmPassword: _confirmPassword.text,
      );
      // Server revoked every refresh token. Clear local login material through
      // the auth controller without depending on this page staying mounted.
      await auth.signOutAfterPasswordChange();
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
        setState(() => _submitting = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('修改密码')),
      body: Form(
        key: _formKey,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(18, 12, 18, 28),
          children: [
            Card(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(18, 18, 18, 18),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    _PasswordField(
                      fieldKey: const Key('oldPasswordField'),
                      controller: _oldPassword,
                      label: '当前密码',
                      obscure: _obscure,
                      onToggle: () => setState(() => _obscure = !_obscure),
                      validator: (value) =>
                          (value == null || value.isEmpty) ? '请输入当前密码。' : null,
                    ),
                    const SizedBox(height: 14),
                    _PasswordField(
                      fieldKey: const Key('newPasswordField'),
                      controller: _newPassword,
                      label: '新密码',
                      obscure: _obscure,
                      onToggle: () => setState(() => _obscure = !_obscure),
                      validator: (value) =>
                          (value == null || value.isEmpty) ? '请输入新密码。' : null,
                    ),
                    const SizedBox(height: 14),
                    _PasswordField(
                      fieldKey: const Key('confirmPasswordField'),
                      controller: _confirmPassword,
                      label: '确认新密码',
                      obscure: _obscure,
                      onToggle: () => setState(() => _obscure = !_obscure),
                      validator: (value) =>
                          (value == null || value.isEmpty) ? '请再次输入新密码。' : null,
                    ),
                    if (_mismatch != null) ...[
                      const SizedBox(height: 12),
                      Text(
                        _mismatch!,
                        key: const Key('passwordMismatch'),
                        style: const TextStyle(
                          fontSize: 14,
                          color: Color(0xFF9A3B30),
                        ),
                      ),
                    ],
                    if (_error != null) ...[
                      const SizedBox(height: 12),
                      Text(
                        _error!,
                        key: const Key('passwordChangeError'),
                        style: const TextStyle(
                          fontSize: 14,
                          color: Color(0xFF9A3B30),
                        ),
                      ),
                    ],
                    const SizedBox(height: 20),
                    FilledButton(
                      key: const Key('changePasswordButton'),
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
                          : const Text('修改密码'),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 12),
            const Text(
              '修改成功后需要重新登录。',
              style: TextStyle(fontSize: 13, color: Color(0xFF60706E)),
            ),
          ],
        ),
      ),
    );
  }
}

class _PasswordField extends StatelessWidget {
  const _PasswordField({
    required this.fieldKey,
    required this.controller,
    required this.label,
    required this.obscure,
    required this.onToggle,
    required this.validator,
  });

  final Key fieldKey;
  final TextEditingController controller;
  final String label;
  final bool obscure;
  final VoidCallback onToggle;
  final String? Function(String?) validator;

  @override
  Widget build(BuildContext context) {
    return TextFormField(
      key: fieldKey,
      controller: controller,
      obscureText: obscure,
      validator: validator,
      decoration: InputDecoration(
        labelText: label,
        prefixIcon: const Icon(Icons.lock_outline),
        suffixIcon: IconButton(
          tooltip: obscure ? '显示密码' : '隐藏密码',
          onPressed: onToggle,
          icon: Icon(
            obscure ? Icons.visibility_outlined : Icons.visibility_off_outlined,
          ),
        ),
      ),
    );
  }
}
