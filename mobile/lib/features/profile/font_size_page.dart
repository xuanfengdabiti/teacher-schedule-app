import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import 'font_size_controller.dart';

class FontSizePage extends ConsumerStatefulWidget {
  const FontSizePage({super.key});

  @override
  ConsumerState<FontSizePage> createState() => _FontSizePageState();
}

class _FontSizePageState extends ConsumerState<FontSizePage> {
  bool _saving = false;
  String? _error;

  Future<void> _choose(String value) async {
    if (_saving) {
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    final error = await ref
        .read(fontSizeControllerProvider.notifier)
        .save(value);
    if (!mounted) {
      return;
    }
    setState(() {
      _saving = false;
      _error = error;
    });
    if (error == null) {
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('字号已更新。')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final selected = ref.watch(fontSizeControllerProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('字号设置')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(18, 12, 18, 28),
        children: [
          Card(
            child: RadioGroup<String>(
              groupValue: selected,
              onChanged: _saving
                  ? (_) {}
                  : (value) {
                      if (value != null) {
                        _choose(value);
                      }
                    },
              child: Column(
                children: [
                  for (final entry in fontSizeOptions.entries)
                    RadioListTile<String>(
                      key: Key('fontSize-${entry.key}'),
                      value: entry.key,
                      title: Text(entry.value),
                    ),
                ],
              ),
            ),
          ),
          if (_error != null) ...[
            const SizedBox(height: 12),
            Text(
              _error!,
              key: const Key('fontSizeError'),
              style: const TextStyle(fontSize: 14, color: Color(0xFF9A3B30)),
            ),
          ],
          const SizedBox(height: 16),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(18),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    '预览',
                    style: TextStyle(fontSize: 14, color: appMuted),
                  ),
                  const SizedBox(height: 12),
                  Text(
                    '证据法 · 历警楼101',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  const SizedBox(height: 6),
                  const Text('第1-2节 08:10-09:40'),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
