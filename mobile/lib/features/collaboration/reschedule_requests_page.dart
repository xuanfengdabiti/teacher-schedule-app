import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../../core/network/api_client.dart';
import '../../shared/status_views.dart';
import 'collaboration_models.dart';
import 'collaboration_ui.dart';

class RescheduleRequestsPage extends StatefulWidget {
  const RescheduleRequestsPage({super.key, required this.api});

  final ApiClient api;

  @override
  State<RescheduleRequestsPage> createState() => _RescheduleRequestsPageState();
}

class _RescheduleRequestsPageState extends State<RescheduleRequestsPage> {
  List<RescheduleRequestItem>? _items;
  Object? _error;
  bool _loading = true;
  final Set<String> _busy = {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load({bool keepData = false}) async {
    setState(() {
      _loading = true;
      if (!keepData) {
        _error = null;
      }
    });
    try {
      final response = await widget.api.get(
        'reschedule-requests/',
        query: const {'scope': 'mine'},
      );
      final body = mapOf(response.data);
      final items = mapListOf(body['results'])
          .map(RescheduleRequestItem.fromJson)
          .toList(growable: false);
      if (!mounted) {
        return;
      }
      setState(() {
        _items = items;
        _error = null;
        _loading = false;
      });
    } catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _loading = false;
        if (_items == null) {
          _error = error;
        }
      });
      if (_items != null) {
        showCollabError(context, error);
      }
    }
  }

  void _replace(RescheduleRequestItem item) {
    final items = _items;
    if (items == null) {
      return;
    }
    final index = items.indexWhere((existing) => existing.id == item.id);
    if (index < 0) {
      return;
    }
    setState(() {
      _items = [...items]..[index] = item;
    });
  }

  Future<void> _submitDraft(RescheduleRequestItem item) async {
    final confirmed = await confirmCollabAction(
      context,
      title: '提交调课申请',
      message: '提交后进入待审批状态；正式课表仍不会改变。',
      confirmLabel: '提交审批',
    );
    if (!confirmed || !mounted) {
      return;
    }
    await _runAction(
      item.id,
      () => widget.api.post('reschedule-requests/${item.id}/submit/'),
      successMessage: '已提交审批，等待教务处理。',
    );
  }

  Future<void> _withdraw(RescheduleRequestItem item) async {
    final confirmed = await confirmCollabAction(
      context,
      title: '撤回申请',
      message: '撤回后不能恢复这条申请，需要时可重新发起。',
      confirmLabel: '撤回',
    );
    if (!confirmed || !mounted) {
      return;
    }
    await _runAction(
      item.id,
      () => widget.api.post('reschedule-requests/${item.id}/withdraw/'),
      successMessage: '申请已撤回，课表未改变。',
    );
  }

  Future<void> _runAction(
    String id,
    Future<dynamic> Function() action, {
    required String successMessage,
  }) async {
    setState(() => _busy.add(id));
    try {
      final response = await action();
      final item = RescheduleRequestItem.fromJson(mapOf(response.data));
      if (!mounted) {
        return;
      }
      _replace(item);
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(content: Text(successMessage)));
    } catch (error) {
      if (mounted) {
        showCollabError(context, error);
      }
    } finally {
      if (mounted) {
        setState(() => _busy.remove(id));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final items = _items;
    return Scaffold(
      appBar: AppBar(title: const Text('我的调课申请')),
      body: items == null
          ? _loading
                ? const LoadingView(message: '正在读取调课申请…')
                : CollabErrorView(error: _error!, onRetry: () => _load())
          : Column(
              children: [
                if (_loading) const CollabInlineProgress(),
                Expanded(
                  child: RefreshIndicator(
                    onRefresh: () => _load(keepData: true),
                    child: items.isEmpty
                        ? ListView(
                            physics: const AlwaysScrollableScrollPhysics(),
                            children: const [
                              SizedBox(height: 80),
                              EmptyView(
                                title: '还没有调课申请',
                                detail: '从课次详情进入“调课”，提交后会出现在这里。',
                                icon: Icons.swap_horiz_outlined,
                              ),
                            ],
                          )
                        : ListView.separated(
                            physics: const AlwaysScrollableScrollPhysics(),
                            padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
                            itemCount: items.length,
                            separatorBuilder: (_, _) =>
                                const SizedBox(height: 10),
                            itemBuilder: (context, index) {
                              final item = items[index];
                              return _RequestCard(
                                key: ValueKey('reschedule-request-${item.id}'),
                                item: item,
                                busy: _busy.contains(item.id),
                                onSubmit: item.isDraft
                                    ? () => _submitDraft(item)
                                    : null,
                                onWithdraw: item.isSubmitted && item.canWithdraw
                                    ? () => _withdraw(item)
                                    : null,
                              );
                            },
                          ),
                  ),
                ),
              ],
            ),
    );
  }
}

class _RequestCard extends StatelessWidget {
  const _RequestCard({
    super.key,
    required this.item,
    required this.busy,
    required this.onSubmit,
    required this.onWithdraw,
  });

  final RescheduleRequestItem item;
  final bool busy;
  final VoidCallback? onSubmit;
  final VoidCallback? onWithdraw;

  Color get _statusColor => switch (item.status) {
    'submitted' => const Color(0xFF2563EB),
    'approved' => appMint,
    'rejected' => const Color(0xFFB3261E),
    'draft' => appMuted,
    _ => appMuted,
  };

  @override
  Widget build(BuildContext context) {
    return CollabSectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Text(
                  item.session.courseName,
                  style: Theme.of(context).textTheme.titleMedium,
                ),
              ),
              const SizedBox(width: 8),
              CollabStatusPill(label: item.statusLabel, color: _statusColor),
            ],
          ),
          const SizedBox(height: 10),
          CollabDetailRow(label: '原安排', value: item.session.arrangement),
          CollabDetailRow(label: '申请调至', value: item.target.arrangement),
          CollabDetailRow(label: '原因', value: item.reason),
          if (item.needsVerification)
            const Padding(
              padding: EdgeInsets.only(top: 6),
              child: CollabNotice(
                text: '该申请包含待核验时段，需要教务进一步确认；提交不代表已批准。',
                icon: Icons.fact_check_outlined,
              ),
            ),
          if (item.decisionNote.trim().isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(
                '审批说明：${item.decisionNote}',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              if (onSubmit != null)
                FilledButton(
                  onPressed: busy ? null : onSubmit,
                  child: Text(busy ? '处理中…' : '提交审批'),
                ),
              if (onWithdraw != null)
                OutlinedButton(
                  onPressed: busy ? null : onWithdraw,
                  child: const Text('撤回申请'),
                ),
            ],
          ),
        ],
      ),
    );
  }
}
