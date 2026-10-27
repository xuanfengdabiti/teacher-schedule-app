import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/network/api_exception.dart';
import 'auth_controller.dart';

class AccountStatusPage extends ConsumerStatefulWidget {
  const AccountStatusPage({super.key});

  @override
  ConsumerState<AccountStatusPage> createState() => _AccountStatusPageState();
}

class _AccountStatusPageState extends ConsumerState<AccountStatusPage> {
  bool _checking = false;

  Future<void> _checkAgain() async {
    setState(() => _checking = true);
    try {
      await ref.read(authControllerProvider.notifier).refreshMe();
    } on ApiException catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.message)));
      }
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('检查失败：$error')));
      }
    } finally {
      if (mounted) {
        setState(() => _checking = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final session = ref.watch(authControllerProvider);
    final me = session.me;
    return Scaffold(
      appBar: AppBar(
        title: const Text('账号状态'),
        automaticallyImplyLeading: false,
      ),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 18, 20, 30),
          children: [
            Container(
              width: 66,
              height: 66,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: const Color(0xFFFFF4E5),
                borderRadius: BorderRadius.circular(22),
              ),
              child: const Icon(
                Icons.info_outline_rounded,
                color: Color(0xFFAD6A12),
                size: 34,
              ),
            ),
            const SizedBox(height: 18),
            Text('暂时不能查看课表', style: Theme.of(context).textTheme.headlineSmall),
            const SizedBox(height: 8),
            Text(
              me == null
                  ? '未读取到账号资料，请重新检查。'
                  : '${me.displayName} · ${me.email}',
              style: Theme.of(context).textTheme.bodyLarge,
            ),
            const SizedBox(height: 18),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(18),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      '需要处理',
                      style: TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    const SizedBox(height: 12),
                    for (final message in session.restrictions)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 10),
                        child: Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Padding(
                              padding: EdgeInsets.only(top: 3),
                              child: Icon(
                                Icons.chevron_right,
                                size: 20,
                                color: appTeal,
                              ),
                            ),
                            const SizedBox(width: 4),
                            Expanded(
                              child: Text(
                                message,
                                style: Theme.of(context).textTheme.bodyLarge,
                              ),
                            ),
                          ],
                        ),
                      ),
                  ],
                ),
              ),
            ),
            if (me?.teacherStatus == 'pending') ...[
              const SizedBox(height: 14),
              const Text(
                '审核由学校管理员完成；通过后回到这里点“重新检查”即可。',
                style: TextStyle(fontSize: 14, color: appMuted, height: 1.5),
              ),
            ],
            const SizedBox(height: 22),
            FilledButton.icon(
              onPressed: _checking ? null : _checkAgain,
              icon: _checking
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(
                        strokeWidth: 2,
                        color: Colors.white,
                      ),
                    )
                  : const Icon(Icons.refresh),
              label: const Text('重新检查'),
            ),
            const SizedBox(height: 10),
            OutlinedButton(
              onPressed: () =>
                  ref.read(authControllerProvider.notifier).logout(),
              child: const Text('退出登录'),
            ),
            const SizedBox(height: 18),
            const Text(
              '如有账号绑定问题，请联系学校教务。',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 13, color: appMuted),
            ),
          ],
        ),
      ),
    );
  }
}
