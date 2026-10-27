import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../../core/network/api_client.dart';
import '../../shared/status_views.dart';
import 'teaching_controllers.dart';
import 'teaching_models.dart';
import 'teaching_repository.dart';
import 'teaching_widgets.dart';

class CompletionPage extends StatefulWidget {
  const CompletionPage({super.key, required this.api, required this.sessionId});

  final ApiClient api;
  final String sessionId;

  @override
  State<CompletionPage> createState() => _CompletionPageState();
}

class _CompletionPageState extends State<CompletionPage> {
  late final CompletionController _controller;
  late final TextEditingController _actualHours;
  late final TextEditingController _reason;
  final Map<String, TextEditingController> _allocationControllers = {};
  final Set<String> _selectedAllocations = {};
  bool _dirty = false;
  bool _applying = false;
  String? _inlineError;
  String? _inlineNotice;

  @override
  void initState() {
    super.initState();
    _controller = CompletionController(
      api: TeachingRepository(widget.api),
      sessionId: widget.sessionId,
    );
    _actualHours = TextEditingController()..addListener(_markDirty);
    _reason = TextEditingController()..addListener(_markDirty);
    _controller.addListener(_onChanged);
    _load();
  }

  Future<void> _load() async {
    await _controller.load();
    if (!mounted) {
      return;
    }
    final detail = _controller.detail;
    if (detail != null && _controller.error == null) {
      _applyDetail(detail);
      setState(() {});
    }
  }

  void _onChanged() {
    if (mounted) {
      setState(() {});
    }
  }

  void _markDirty() {
    if (!_applying && mounted) {
      setState(() => _dirty = true);
    }
  }

  void _applyDetail(CompletionDetail detail) {
    _applying = true;
    for (final controller in _allocationControllers.values) {
      controller.dispose();
    }
    _allocationControllers.clear();
    _selectedAllocations.clear();
    _actualHours.text = detail.actualHours ?? '';
    _reason.clear();
    for (final item in detail.availableItems) {
      final controller = TextEditingController()..addListener(_markDirty);
      _allocationControllers[item.id] = controller;
    }
    for (final allocation in detail.allocations) {
      _selectedAllocations.add(allocation.planItemId);
      final controller = _allocationControllers.putIfAbsent(
        allocation.planItemId,
        () => TextEditingController()..addListener(_markDirty),
      );
      controller.text = allocation.actualHours;
    }
    _applying = false;
    _dirty = false;
    _inlineError = null;
    _inlineNotice = null;
  }

  @override
  void dispose() {
    _controller
      ..removeListener(_onChanged)
      ..dispose();
    _actualHours.dispose();
    _reason.dispose();
    for (final controller in _allocationControllers.values) {
      controller.dispose();
    }
    super.dispose();
  }

  Map<String, String> _allocations() {
    final values = <String, String>{};
    for (final entry in _allocationControllers.entries) {
      if (!_selectedAllocations.contains(entry.key)) {
        continue;
      }
      final text = entry.value.text.trim();
      if (text.isNotEmpty) {
        values[entry.key] = text;
      }
    }
    return values;
  }

  Future<void> _submit(CompletionAction action) async {
    final detail = _controller.detail;
    if (detail == null || _controller.submitting) {
      return;
    }
    if (action != CompletionAction.revert) {
      for (final itemId in _selectedAllocations) {
        final text = _allocationControllers[itemId]?.text.trim() ?? '';
        if (text.isEmpty) {
          setState(() => _inlineError = '已勾选专题需要填写分配学时，或取消勾选后再提交。');
          return;
        }
      }
    }
    final request = CompletionRequest(
      action: action,
      baseVersion: detail.version,
      actualHours: _actualHours.text,
      allocations: action == CompletionAction.revert
          ? const {}
          : _allocations(),
      reason: _reason.text,
    );
    final localError = request.validate();
    if (localError != null) {
      setState(() => _inlineError = localError);
      return;
    }
    setState(() {
      _inlineError = null;
      _inlineNotice = null;
    });
    final result = await _controller.submit(request);
    if (!mounted) {
      return;
    }
    if (result.conflict) {
      await _showConflict(result.message);
      return;
    }
    if (result.ok) {
      final updated = _controller.detail;
      if (updated != null) {
        _applyDetail(updated);
      }
      setState(() {
        _dirty = false;
        _inlineNotice = result.message;
      });
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(result.message)));
    } else {
      setState(() => _inlineError = result.message);
    }
  }

  Future<void> _showConflict(String message) async {
    final reload = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('课次版本已变化'),
        content: Text('$message\n\n当前输入的学时和专题分配仍保留，不会自动覆盖。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('保留输入'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('载入服务器状态'),
          ),
        ],
      ),
    );
    if (reload == true && mounted) {
      await _load();
    }
  }

  Future<void> _refresh() async {
    if (_dirty) {
      final confirmed =
          await showDialog<bool>(
            context: context,
            builder: (context) => AlertDialog(
              title: const Text('刷新会重置输入'),
              content: const Text('当前填写的学时、专题分配和原因将被服务端最新状态替换。'),
              actions: [
                TextButton(
                  onPressed: () => Navigator.pop(context, false),
                  child: const Text('保留输入'),
                ),
                FilledButton(
                  onPressed: () => Navigator.pop(context, true),
                  child: const Text('刷新'),
                ),
              ],
            ),
          ) ??
          false;
      if (!confirmed) {
        return;
      }
    }
    if (!mounted) {
      return;
    }
    await _load();
  }

  @override
  Widget build(BuildContext context) {
    final detail = _controller.detail;
    return Scaffold(
      appBar: AppBar(
        title: const Text('授课完成与学时'),
        actions: [
          IconButton(
            key: const Key('completionRefreshButton'),
            tooltip: '刷新',
            onPressed: _controller.loading ? null : _refresh,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: _buildBody(detail),
      bottomNavigationBar: detail == null ? null : _buildActions(detail),
    );
  }

  Widget _buildBody(CompletionDetail? detail) {
    if (detail == null && _controller.loading) {
      return const LoadingView(message: '正在读取课次完成信息…');
    }
    if (detail == null && _controller.error != null) {
      return ErrorView(message: _controller.error!, onRetry: _load);
    }
    if (detail == null) {
      return const SizedBox.shrink();
    }
    final completed = detail.isCompleted;
    return RefreshIndicator(
      onRefresh: _refresh,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 20),
        children: [
          if (_controller.loading) const LinearProgressIndicator(minHeight: 2),
          _StatusCard(detail: detail),
          if (_controller.error != null) ...[
            const SizedBox(height: 10),
            TeachingNotice(
              text: _controller.error!,
              tone: TeachingNoticeTone.error,
              icon: Icons.cloud_off_outlined,
            ),
          ],
          if (_inlineError != null) ...[
            const SizedBox(height: 10),
            TeachingNotice(
              text: _inlineError!,
              tone: TeachingNoticeTone.error,
              icon: Icons.error_outline,
            ),
          ],
          if (_inlineNotice != null) ...[
            const SizedBox(height: 10),
            TeachingNotice(
              text: _inlineNotice!,
              icon: Icons.check_circle_outline,
            ),
          ],
          const SizedBox(height: 12),
          const TeachingNotice(text: '保存备注不会自动确认授课完成。完成、更正和撤销都以本页提交的服务端结果为准。'),
          const SizedBox(height: 12),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text(
                    completed ? '完成信息' : '确认本次授课',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    key: const Key('completionActualHoursField'),
                    controller: _actualHours,
                    enabled: detail.status != 'cancelled',
                    keyboardType: const TextInputType.numberWithOptions(
                      decimal: true,
                    ),
                    decoration: const InputDecoration(
                      labelText: '实际学时',
                      hintText: '例如 2.0，最多一位小数',
                      helperText: '全部专题分配合计不得超过本课次实际学时。',
                    ),
                  ),
                  const SizedBox(height: 12),
                  Text('专题分配', style: Theme.of(context).textTheme.titleSmall),
                  const SizedBox(height: 4),
                  if (detail.availableItems.isEmpty)
                    const Text(
                      '该课程还没有可分配的专题。',
                      style: TextStyle(fontSize: 14, color: appMuted),
                    )
                  else
                    ListView.builder(
                      key: const Key('completionAllocationList'),
                      shrinkWrap: true,
                      physics: const NeverScrollableScrollPhysics(),
                      itemCount: detail.availableItems.length,
                      itemBuilder: (context, index) {
                        final item = detail.availableItems[index];
                        final controller = _allocationControllers[item.id];
                        final selected = _selectedAllocations.contains(item.id);
                        return Padding(
                          key: ValueKey('allocation-${item.id}'),
                          padding: const EdgeInsets.symmetric(vertical: 6),
                          child: Row(
                            children: [
                              Checkbox(
                                value: selected,
                                onChanged: detail.status == 'cancelled'
                                    ? null
                                    : (value) {
                                        setState(() {
                                          if (value == true) {
                                            _selectedAllocations.add(item.id);
                                            if (controller != null &&
                                                controller.text.isEmpty) {
                                              controller.text = '0.0';
                                            }
                                          } else {
                                            _selectedAllocations.remove(
                                              item.id,
                                            );
                                          }
                                          _dirty = true;
                                        });
                                      },
                              ),
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(
                                      item.title,
                                      style: const TextStyle(fontSize: 15),
                                    ),
                                    Text(
                                      '计划 ${item.plannedHours} 学时',
                                      style: const TextStyle(
                                        fontSize: 12.5,
                                        color: appMuted,
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                              SizedBox(
                                width: 96,
                                child: TextField(
                                  controller: controller,
                                  enabled:
                                      selected && detail.status != 'cancelled',
                                  keyboardType:
                                      const TextInputType.numberWithOptions(
                                        decimal: true,
                                      ),
                                  textAlign: TextAlign.end,
                                  decoration: const InputDecoration(
                                    isDense: true,
                                    hintText: '0.0',
                                    suffixText: '学时',
                                  ),
                                ),
                              ),
                            ],
                          ),
                        );
                      },
                    ),
                  const SizedBox(height: 12),
                  TextField(
                    key: const Key('completionReasonField'),
                    controller: _reason,
                    enabled: detail.status != 'cancelled',
                    minLines: 2,
                    maxLines: 4,
                    maxLength: 2000,
                    decoration: InputDecoration(
                      labelText: completed ? '更正/撤销原因' : '说明（可选）',
                      hintText: completed ? '更正或撤销完成必须说明原因' : '例如：按计划完成本次内容',
                      counterText: '',
                    ),
                  ),
                ],
              ),
            ),
          ),
          if (detail.allocations.isNotEmpty) ...[
            const SizedBox(height: 12),
            _CurrentAllocationCard(allocations: detail.allocations),
          ],
          if (detail.history.isNotEmpty) ...[
            const SizedBox(height: 12),
            _HistoryCard(history: detail.history),
          ],
        ],
      ),
    );
  }

  Widget _buildActions(CompletionDetail detail) {
    if (detail.status == 'cancelled') {
      return const SafeArea(
        top: false,
        child: Padding(
          padding: EdgeInsets.all(16),
          child: Text(
            '课次已取消，不能确认或更正授课完成。',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 14, color: appMuted),
          ),
        ),
      );
    }
    final submitting = _controller.submitting;
    return SafeArea(
      top: false,
      child: Container(
        padding: const EdgeInsets.fromLTRB(16, 10, 16, 10),
        decoration: const BoxDecoration(
          color: Colors.white,
          border: Border(top: BorderSide(color: appLine)),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (_dirty)
              const Align(
                alignment: Alignment.centerLeft,
                child: Padding(
                  padding: EdgeInsets.only(bottom: 8),
                  child: Text(
                    '输入尚未提交，失败时会保留。',
                    style: TextStyle(fontSize: 13, color: Color(0xFF8A5A0B)),
                  ),
                ),
              ),
            Row(
              children: [
                if (detail.isCompleted) ...[
                  Expanded(
                    child: OutlinedButton(
                      key: const Key('completionRevertButton'),
                      onPressed: submitting
                          ? null
                          : () => _submit(CompletionAction.revert),
                      style: OutlinedButton.styleFrom(
                        foregroundColor: const Color(0xFF9A3B30),
                        side: const BorderSide(color: Color(0xFFD6A39B)),
                      ),
                      child: const Text('撤销完成'),
                    ),
                  ),
                  const SizedBox(width: 10),
                ],
                Expanded(
                  child: FilledButton.icon(
                    key: Key(
                      detail.isCompleted
                          ? 'completionCorrectButton'
                          : 'completionCompleteButton',
                    ),
                    onPressed: submitting
                        ? null
                        : () => _submit(
                            detail.isCompleted
                                ? CompletionAction.correct
                                : CompletionAction.complete,
                          ),
                    icon: submitting
                        ? const SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(
                              strokeWidth: 2,
                              color: Colors.white,
                            ),
                          )
                        : Icon(
                            detail.isCompleted
                                ? Icons.edit_calendar_outlined
                                : Icons.task_alt,
                          ),
                    label: Text(detail.isCompleted ? '保存更正' : '确认完成'),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _StatusCard extends StatelessWidget {
  const _StatusCard({required this.detail});

  final CompletionDetail detail;

  @override
  Widget build(BuildContext context) {
    final completed = detail.isCompleted;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text('课次完成状态', style: Theme.of(context).textTheme.titleLarge),
                const Spacer(),
                TeachingStatusPill(
                  label: sessionStatusText(detail.status),
                  color: completed ? appTeal : appMuted,
                ),
              ],
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 10,
              runSpacing: 10,
              children: [
                TeachingMetric(
                  label: '实际学时',
                  value: detail.actualHours ?? '—',
                  caption: '已登记学时',
                  emphasis: true,
                ),
              ],
            ),
            if (completed) ...[
              const SizedBox(height: 10),
              Text(
                '完成人：${detail.completedBy?.name ?? '—'}',
                style: const TextStyle(fontSize: 14, color: appMuted),
              ),
              Text(
                '完成时间：${teachingDateTime(detail.completedAt)}',
                style: const TextStyle(fontSize: 14, color: appMuted),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _CurrentAllocationCard extends StatelessWidget {
  const _CurrentAllocationCard({required this.allocations});

  final List<CompletionAllocation> allocations;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('当前专题分配', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 6),
            for (final allocation in allocations)
              ListTile(
                key: ValueKey('current-allocation-${allocation.planItemId}'),
                dense: true,
                contentPadding: EdgeInsets.zero,
                title: Text(allocation.title),
                trailing: Text(
                  '${allocation.actualHours} 学时',
                  style: const TextStyle(fontWeight: FontWeight.w600),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _HistoryCard extends StatelessWidget {
  const _HistoryCard({required this.history});

  final List<CompletionHistory> history;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('完成操作历史', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            for (final record in history)
              ListTile(
                key: ValueKey('completion-history-${record.id}'),
                dense: true,
                contentPadding: EdgeInsets.zero,
                title: Text(
                  '${completionActionText(record.action)} · ${record.actor.name}',
                ),
                subtitle: Text(
                  '${teachingDateTime(record.createdAt)}'
                  '${record.reason.isEmpty ? '' : '\n原因：${record.reason}'}',
                  style: const TextStyle(fontSize: 13.5, height: 1.45),
                ),
                trailing: Text(
                  '${record.actualHoursAfter} 学时',
                  style: const TextStyle(fontWeight: FontWeight.w600),
                ),
              ),
          ],
        ),
      ),
    );
  }
}
