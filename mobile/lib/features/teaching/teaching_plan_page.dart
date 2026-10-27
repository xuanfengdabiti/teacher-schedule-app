import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../../core/json.dart';
import '../../core/network/api_client.dart';
import '../../shared/status_views.dart';
import 'teaching_controllers.dart';
import 'teaching_models.dart';
import 'teaching_repository.dart';
import 'teaching_widgets.dart';

class TeachingPlanPage extends StatefulWidget {
  const TeachingPlanPage({
    super.key,
    required this.api,
    required this.offeringId,
  });

  final ApiClient api;
  final String offeringId;

  @override
  State<TeachingPlanPage> createState() => _TeachingPlanPageState();
}

class _PlanEdit {
  _PlanEdit({
    required this.localKey,
    this.id,
    required this.title,
    required this.plannedHours,
    this.dueDate,
    this.dueSessionId,
  });

  final String localKey;
  final String? id;
  String title;
  String plannedHours;
  DateTime? dueDate;
  String? dueSessionId;
}

class _TeachingPlanPageState extends State<TeachingPlanPage> {
  late final TeachingPlanController _controller;
  List<_PlanEdit> _edits = [];
  bool _dirty = false;
  bool _loadedOnce = false;
  int _newItemCounter = 0;

  @override
  void initState() {
    super.initState();
    _controller = TeachingPlanController(
      api: TeachingRepository(widget.api),
      offeringId: widget.offeringId,
    );
    _controller.addListener(_onChanged);
    _load();
  }

  Future<void> _load() async {
    await _controller.load();
    if (!mounted) {
      return;
    }
    final bundle = _controller.bundle;
    if (bundle != null && _controller.error == null) {
      _applyBundle(bundle);
      _loadedOnce = true;
    }
    setState(() {});
  }

  void _onChanged() {
    if (mounted) {
      setState(() {});
    }
  }

  void _applyBundle(PlanBundle bundle) {
    final plan = bundle.plan;
    _edits = [
      for (final item in plan?.items ?? const <PlanItem>[])
        if (!item.archived)
          _PlanEdit(
            localKey: item.id,
            id: item.id,
            title: item.title,
            plannedHours: item.plannedHours,
            dueDate: item.dueDate,
            dueSessionId: item.dueSessionId,
          ),
    ];
    _dirty = false;
  }

  @override
  void dispose() {
    _controller
      ..removeListener(_onChanged)
      ..dispose();
    super.dispose();
  }

  Future<bool> _confirmDiscard() async {
    if (!_dirty) {
      return true;
    }
    return await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
            title: const Text('放弃未保存修改？'),
            content: const Text('刷新会显示服务器上的计划，当前未保存的标题、学时和截止日期会丢失。'),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context, false),
                child: const Text('继续编辑'),
              ),
              FilledButton(
                onPressed: () => Navigator.pop(context, true),
                child: const Text('放弃并刷新'),
              ),
            ],
          ),
        ) ??
        false;
  }

  Future<void> _refresh() async {
    if (!await _confirmDiscard()) {
      return;
    }
    if (!mounted) {
      return;
    }
    await _load();
    if (mounted && _controller.error != null) {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(_controller.error!)));
    }
  }

  Future<void> _addItem() async {
    final result = await _editItem(
      _PlanEdit(
        localKey: 'new-${_newItemCounter++}',
        title: '',
        plannedHours: '1.0',
      ),
      isNew: true,
    );
    if (result == null || !mounted) {
      return;
    }
    setState(() {
      _edits = [..._edits, result];
      _dirty = true;
    });
  }

  Future<void> _editExisting(int index) async {
    final result = await _editItem(_edits[index], isNew: false);
    if (result == null || !mounted) {
      return;
    }
    setState(() {
      final next = [..._edits];
      next[index] = result;
      _edits = next;
      _dirty = true;
    });
  }

  Future<_PlanEdit?> _editItem(_PlanEdit item, {required bool isNew}) async {
    final title = TextEditingController(text: item.title);
    final hours = TextEditingController(text: item.plannedHours);
    DateTime? dueDate = item.dueDate;
    final result = await showDialog<_PlanEdit>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, setDialogState) {
          Future<void> pickDate() async {
            final picked = await showDatePicker(
              context: context,
              initialDate: dueDate ?? DateTime.now(),
              firstDate: DateTime(2020),
              lastDate: DateTime(2100),
              helpText: '选择计划截止日期',
            );
            if (picked != null) {
              setDialogState(() => dueDate = picked);
            }
          }

          return AlertDialog(
            title: Text(isNew ? '新增计划专题' : '编辑计划专题'),
            content: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  TextField(
                    key: const Key('planTitleField'),
                    controller: title,
                    autofocus: isNew,
                    maxLength: 200,
                    decoration: const InputDecoration(
                      labelText: '专题名称',
                      counterText: '',
                    ),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    key: const Key('planHoursField'),
                    controller: hours,
                    keyboardType: const TextInputType.numberWithOptions(
                      decimal: true,
                    ),
                    decoration: const InputDecoration(
                      labelText: '计划学时',
                      hintText: '例如 2.0',
                    ),
                  ),
                  const SizedBox(height: 14),
                  Row(
                    children: [
                      Expanded(
                        child: Text(
                          dueDate == null
                              ? '未设置截止日期'
                              : '截止：${isoDate(dueDate!)}',
                          style: const TextStyle(fontSize: 15),
                        ),
                      ),
                      TextButton.icon(
                        onPressed: pickDate,
                        icon: const Icon(Icons.event_outlined),
                        label: const Text('选择日期'),
                      ),
                    ],
                  ),
                  if (dueDate != null)
                    Align(
                      alignment: Alignment.centerLeft,
                      child: TextButton(
                        onPressed: () => setDialogState(() => dueDate = null),
                        child: const Text('清除截止日期'),
                      ),
                    ),
                  if (item.dueSessionId != null && dueDate == null)
                    const Padding(
                      padding: EdgeInsets.only(top: 4),
                      child: Text(
                        '当前通过课次截止；选择日期后将改为日期截止。',
                        style: TextStyle(fontSize: 13, color: appMuted),
                      ),
                    ),
                ],
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context),
                child: const Text('取消'),
              ),
              FilledButton(
                onPressed: () {
                  if (title.text.trim().isEmpty) {
                    ScaffoldMessenger.of(
                      context,
                    ).showSnackBar(const SnackBar(content: Text('请填写专题名称。')));
                    return;
                  }
                  if (!_validHours(hours.text)) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(content: Text('计划学时请填写最多一位小数的非负数字。')),
                    );
                    return;
                  }
                  Navigator.pop(
                    context,
                    _PlanEdit(
                      localKey: item.localKey,
                      id: item.id,
                      title: title.text.trim(),
                      plannedHours: hours.text.trim(),
                      dueDate: dueDate,
                      dueSessionId: dueDate == null ? item.dueSessionId : null,
                    ),
                  );
                },
                child: Text(isNew ? '添加' : '保存修改'),
              ),
            ],
          );
        },
      ),
    );
    title.dispose();
    hours.dispose();
    return result;
  }

  Future<void> _deleteItem(int index) async {
    final item = _edits[index];
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('删除这个专题？'),
        content: Text('“${item.title}”将不再出现在计划中。已经有授课分配历史的专题，服务端会保留为归档项。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('删除'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) {
      return;
    }
    setState(() {
      final next = [..._edits]..removeAt(index);
      _edits = next;
      _dirty = true;
    });
  }

  Future<void> _save() async {
    if (_controller.saving) {
      return;
    }
    for (final item in _edits) {
      if (item.title.trim().isEmpty) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('每个专题都需要填写名称。')));
        return;
      }
      if (!_validHours(item.plannedHours)) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('计划学时请填写最多一位小数的非负数字。')));
        return;
      }
    }
    final baseVersion = _controller.bundle?.plan?.version ?? 0;
    final result = await _controller.save(
      baseVersion: baseVersion,
      items: [
        for (final item in _edits)
          PlanItemInput(
            id: item.id,
            title: item.title,
            plannedHours: item.plannedHours,
            dueDate: item.dueDate,
            dueSessionId: item.dueSessionId,
          ),
      ],
    );
    if (!mounted) {
      return;
    }
    if (result.conflict) {
      await _showConflict(result.message);
      return;
    }
    if (result.ok && _controller.bundle != null) {
      setState(() => _applyBundle(_controller.bundle!));
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(result.message)));
    } else if (!result.ok) {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(result.message)));
    }
  }

  Future<void> _showConflict(String message) async {
    final reload = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('计划版本已变化'),
        content: Text('$message\n\n可以保留当前输入，稍后手动合并；也可以载入服务器版本。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('保留输入'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('载入服务器版本'),
          ),
        ],
      ),
    );
    if (reload == true && mounted) {
      await _load();
    }
  }

  @override
  Widget build(BuildContext context) {
    final bundle = _controller.bundle;
    final canEdit = bundle?.canEdit == true;
    return Scaffold(
      appBar: AppBar(
        title: const Text('教学计划与进度'),
        actions: [
          IconButton(
            key: const Key('planRefreshButton'),
            tooltip: '刷新',
            onPressed: _controller.loading ? null : _refresh,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: _buildBody(bundle),
      bottomNavigationBar: canEdit && _loadedOnce
          ? SafeArea(
              top: false,
              child: Container(
                padding: const EdgeInsets.fromLTRB(16, 10, 16, 10),
                decoration: const BoxDecoration(
                  color: Colors.white,
                  border: Border(top: BorderSide(color: appLine)),
                ),
                child: Row(
                  children: [
                    Expanded(
                      child: Text(
                        _dirty ? '有未保存修改' : '已与服务端同步',
                        style: TextStyle(
                          fontSize: 14,
                          color: _dirty ? const Color(0xFF8A5A0B) : appMuted,
                        ),
                      ),
                    ),
                    FilledButton.icon(
                      key: const Key('planSaveButton'),
                      onPressed: _controller.saving || !_dirty ? null : _save,
                      icon: _controller.saving
                          ? const SizedBox(
                              width: 18,
                              height: 18,
                              child: CircularProgressIndicator(
                                strokeWidth: 2,
                                color: Colors.white,
                              ),
                            )
                          : const Icon(Icons.save_outlined),
                      label: const Text('保存计划'),
                    ),
                  ],
                ),
              ),
            )
          : null,
    );
  }

  Widget _buildBody(PlanBundle? bundle) {
    if (bundle == null && _controller.loading) {
      return const LoadingView(message: '正在读取教学计划…');
    }
    if (bundle == null && _controller.error != null) {
      return ErrorView(message: _controller.error!, onRetry: _load);
    }
    if (bundle == null) {
      return const SizedBox.shrink();
    }
    final progress = bundle.progress;
    final archived = [
      for (final item in bundle.plan?.items ?? const <PlanItem>[])
        if (item.archived) item,
    ];
    return RefreshIndicator(
      onRefresh: _refresh,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
        children: [
          if (_controller.loading) const LinearProgressIndicator(minHeight: 2),
          if (_controller.error != null) ...[
            TeachingNotice(
              text: _controller.error!,
              tone: TeachingNoticeTone.error,
              icon: Icons.cloud_off_outlined,
            ),
            const SizedBox(height: 12),
          ],
          _ProgressCard(progress: progress),
          const SizedBox(height: 14),
          Row(
            children: [
              Text('计划专题', style: Theme.of(context).textTheme.titleLarge),
              const SizedBox(width: 8),
              TeachingStatusPill(
                label: planStatusText(progress.planStatus),
                color: _planStatusColor(progress.planStatus),
              ),
              const Spacer(),
              if (bundle.canEdit)
                TextButton.icon(
                  key: const Key('planAddButton'),
                  onPressed: _addItem,
                  icon: const Icon(Icons.add),
                  label: const Text('新增'),
                ),
            ],
          ),
          const SizedBox(height: 8),
          if (bundle.plan == null)
            Card(
              key: const Key('planEmptyCard'),
              child: Padding(
                padding: const EdgeInsets.all(18),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const Icon(
                      Icons.playlist_add_outlined,
                      size: 38,
                      color: appMint,
                    ),
                    const SizedBox(height: 10),
                    Text(
                      '未建立计划',
                      textAlign: TextAlign.center,
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                    const SizedBox(height: 6),
                    const Text(
                      '先添加章节/专题和计划学时，保存后服务端会按截止日期计算进度。',
                      textAlign: TextAlign.center,
                      style: TextStyle(fontSize: 14, color: appMuted),
                    ),
                    if (bundle.canEdit) ...[
                      const SizedBox(height: 14),
                      FilledButton.icon(
                        onPressed: _addItem,
                        icon: const Icon(Icons.add),
                        label: const Text('创建第一个专题'),
                      ),
                    ] else
                      const Padding(
                        padding: EdgeInsets.only(top: 12),
                        child: Text(
                          '只有课程负责人可以建立计划。',
                          textAlign: TextAlign.center,
                          style: TextStyle(fontSize: 14, color: appMuted),
                        ),
                      ),
                  ],
                ),
              ),
            )
          else if (_edits.isEmpty)
            const Card(
              child: Padding(
                padding: EdgeInsets.all(18),
                child: Text(
                  '计划还没有专题。',
                  style: TextStyle(fontSize: 15, color: appMuted),
                ),
              ),
            )
          else
            Card(
              child: ListView.separated(
                key: const Key('planItemList'),
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
                itemCount: _edits.length,
                separatorBuilder: (context, index) =>
                    const Divider(height: 1, indent: 16, endIndent: 16),
                itemBuilder: (context, index) {
                  final item = _edits[index];
                  return ListTile(
                    key: ValueKey('plan-item-${item.localKey}'),
                    contentPadding: const EdgeInsets.fromLTRB(16, 8, 8, 8),
                    title: Text(
                      item.title,
                      style: const TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    subtitle: Padding(
                      padding: const EdgeInsets.only(top: 4),
                      child: Text(
                        '${item.plannedHours} 学时'
                        '${item.dueDate == null ? (item.dueSessionId == null ? ' · 未设截止' : ' · 课次截止') : ' · 截止 ${isoDate(item.dueDate!)}'}',
                        style: const TextStyle(fontSize: 14, color: appMuted),
                      ),
                    ),
                    trailing: bundle.canEdit
                        ? Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              IconButton(
                                tooltip: '编辑',
                                onPressed: () => _editExisting(index),
                                icon: const Icon(Icons.edit_outlined),
                              ),
                              IconButton(
                                tooltip: '删除',
                                onPressed: () => _deleteItem(index),
                                icon: const Icon(Icons.delete_outline),
                              ),
                            ],
                          )
                        : null,
                  );
                },
              ),
            ),
          if (archived.isNotEmpty) ...[
            const SizedBox(height: 16),
            const Text(
              '已归档专题',
              style: TextStyle(
                fontSize: 15,
                fontWeight: FontWeight.w700,
                color: appMuted,
              ),
            ),
            const SizedBox(height: 6),
            Card(
              child: Column(
                children: [
                  for (final item in archived)
                    ListTile(
                      key: ValueKey('archived-${item.id}'),
                      dense: true,
                      title: Text(item.title),
                      subtitle: const Text('保留历史分配，不再参与新计划'),
                      trailing: Text('${item.plannedHours} 学时'),
                    ),
                ],
              ),
            ),
          ],
          const SizedBox(height: 16),
          _ProgressItems(progress: progress),
        ],
      ),
    );
  }

  static bool _validHours(String value) =>
      RegExp(r'^\d{1,5}(\.\d)?$').hasMatch(value.trim());

  static Color _planStatusColor(String status) => switch (status) {
    'behind' => const Color(0xFFAD6A12),
    'on_track' => appTeal,
    _ => appMuted,
  };
}

class _ProgressCard extends StatelessWidget {
  const _ProgressCard({required this.progress});

  final OfferingProgress progress;

  @override
  Widget build(BuildContext context) {
    final behind = progress.planStatus == 'behind';
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text('课程进度', style: Theme.of(context).textTheme.titleLarge),
                const Spacer(),
                TeachingStatusPill(
                  label: planStatusText(progress.planStatus),
                  color: behind ? const Color(0xFFAD6A12) : appTeal,
                ),
              ],
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 10,
              runSpacing: 10,
              children: [
                TeachingMetric(
                  label: '已完成学时',
                  value: progress.completedHours,
                  caption: '目标 ${progress.targetHours}',
                  emphasis: true,
                ),
                TeachingMetric(
                  label: '剩余学时',
                  value: progress.remainingHours,
                  caption: progress.overHours == '0.0'
                      ? '按目标学时计算'
                      : '已超出 ${progress.overHours}',
                ),
                TeachingMetric(
                  label: '剩余课次',
                  value: '${progress.remainingSessions}',
                  caption: '未来 ${progress.futureSessions} 节',
                ),
                TeachingMetric(
                  label: '过去待确认',
                  value: '${progress.pastPendingSessions}',
                  caption: '未完成确认的课次',
                ),
                TeachingMetric(
                  label: '未归入专题',
                  value: progress.unallocatedHours,
                  caption: '已完成学时中的未分配部分',
                ),
              ],
            ),
            if (behind) ...[
              const SizedBox(height: 12),
              TeachingNotice(
                tone: TeachingNoticeTone.warning,
                icon: Icons.schedule_outlined,
                text:
                    '按服务端计算，已到期专题合计还差 ${progress.behindHours} 学时'
                    '（提醒阈值 ${progress.behindThresholdHours} 学时）。',
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _ProgressItems extends StatelessWidget {
  const _ProgressItems({required this.progress});

  final OfferingProgress progress;

  @override
  Widget build(BuildContext context) {
    if (progress.items.isEmpty) {
      return const SizedBox.shrink();
    }
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('专题进度', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            for (final item in progress.items)
              ListTile(
                key: ValueKey('progress-${item.planItemId}'),
                dense: true,
                contentPadding: EdgeInsets.zero,
                title: Text(item.title),
                subtitle: Text(
                  '计划 ${item.plannedHours} · 已分配 ${item.actualHours}'
                  '${item.due == null ? '' : ' · 截止 ${isoDate(item.due!)}'}',
                  style: const TextStyle(fontSize: 13.5, color: appMuted),
                ),
                trailing: item.shortfall == '0.0'
                    ? const Icon(Icons.check_circle_outline, color: appMint)
                    : Text(
                        '差 ${item.shortfall}',
                        style: const TextStyle(
                          color: Color(0xFFAD6A12),
                          fontWeight: FontWeight.w600,
                        ),
                      ),
              ),
          ],
        ),
      ),
    );
  }
}
