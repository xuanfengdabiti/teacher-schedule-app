import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../../core/network/api_client.dart';
import '../../shared/status_views.dart';
import 'teaching_controllers.dart';
import 'teaching_models.dart';
import 'teaching_repository.dart';
import 'teaching_widgets.dart';

class HandoffListPage extends StatefulWidget {
  const HandoffListPage({super.key, required this.api});

  final ApiClient api;

  @override
  State<HandoffListPage> createState() => _HandoffListPageState();
}

class _HandoffListPageState extends State<HandoffListPage> {
  late final HandoffListController _controller;

  @override
  void initState() {
    super.initState();
    _controller = HandoffListController(api: TeachingRepository(widget.api));
    _controller.addListener(_onChanged);
    _controller.load();
  }

  void _onChanged() {
    if (mounted) {
      setState(() {});
    }
  }

  @override
  void dispose() {
    _controller
      ..removeListener(_onChanged)
      ..dispose();
    super.dispose();
  }

  Future<void> _open(Handoff handoff) async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute(
        builder: (context) => _HandoffDetailPage(
          api: widget.api,
          handoffId: handoff.id,
          readOnOpen: _controller.box == 'inbox',
          onUpdated: _controller.replace,
        ),
      ),
    );
  }

  Future<void> _switchBox(String box) async {
    if (box == _controller.box) {
      return;
    }
    await _controller.load(nextBox: box);
  }

  @override
  Widget build(BuildContext context) {
    final items = _controller.items;
    return Scaffold(
      appBar: AppBar(
        title: const Text('进度交接'),
        actions: [
          IconButton(
            key: const Key('handoffRefreshButton'),
            tooltip: '刷新',
            onPressed: _controller.loading ? null : _controller.load,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 6, 16, 6),
            child: SegmentedButton<String>(
              key: const Key('handoffBoxSwitcher'),
              segments: const [
                ButtonSegment(
                  value: 'inbox',
                  label: Text('收到的'),
                  icon: Icon(Icons.inbox_outlined),
                ),
                ButtonSegment(
                  value: 'sent',
                  label: Text('我发出的'),
                  icon: Icon(Icons.send_outlined),
                ),
              ],
              selected: {_controller.box},
              onSelectionChanged: _controller.loading
                  ? null
                  : (selection) => _switchBox(selection.first),
            ),
          ),
          Expanded(child: _buildBody(items)),
        ],
      ),
    );
  }

  Widget _buildBody(List<Handoff> items) {
    if (items.isEmpty && _controller.loading) {
      return const LoadingView(message: '正在读取进度交接…');
    }
    if (items.isEmpty && _controller.error != null) {
      return ErrorView(message: _controller.error!, onRetry: _controller.load);
    }
    if (items.isEmpty) {
      return RefreshIndicator(
        onRefresh: _controller.load,
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          children: [
            const SizedBox(height: 80),
            EmptyView(
              title: _controller.box == 'inbox' ? '还没有收到进度交接' : '还没有发出进度交接',
              detail: _controller.box == 'inbox'
                  ? '同课老师发来进度交接后会出现在这里。'
                  : '从课次详情发起进度交接后会出现在这里。',
              icon: _controller.box == 'inbox'
                  ? Icons.inbox_outlined
                  : Icons.send_outlined,
            ),
          ],
        ),
      );
    }
    return RefreshIndicator(
      onRefresh: _controller.load,
      child: ListView.builder(
        key: const Key('handoffList'),
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 24),
        itemCount: items.length + 1,
        itemBuilder: (context, index) {
          if (index == 0) {
            return Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Row(
                children: [
                  if (_controller.loading)
                    const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    ),
                  if (_controller.loading) const SizedBox(width: 8),
                  Text(
                    '共 ${_controller.total} 条',
                    style: const TextStyle(fontSize: 14, color: appMuted),
                  ),
                  if (_controller.error != null) ...[
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        _controller.error!,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          fontSize: 13,
                          color: Color(0xFF9A3B30),
                        ),
                      ),
                    ),
                  ],
                ],
              ),
            );
          }
          final handoff = items[index - 1];
          return Padding(
            key: ValueKey('handoff-${handoff.id}'),
            padding: const EdgeInsets.only(bottom: 10),
            child: _HandoffCard(
              handoff: handoff,
              showUnread: _controller.box == 'inbox',
              onTap: () => _open(handoff),
            ),
          );
        },
      ),
    );
  }
}

class _HandoffCard extends StatelessWidget {
  const _HandoffCard({
    required this.handoff,
    required this.showUnread,
    required this.onTap,
  });

  final Handoff handoff;
  final bool showUnread;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final statusColor = switch (handoff.status) {
      'acknowledged' => appTeal,
      'withdrawn' => appMuted,
      _ => const Color(0xFFAD6A12),
    };
    return Card(
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(15),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      handoff.courseName,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                  TeachingStatusPill(
                    label: handoffStatusText(handoff.status),
                    color: statusColor,
                  ),
                ],
              ),
              const SizedBox(height: 3),
              Text(
                handoff.sessionLabel,
                style: const TextStyle(fontSize: 13.5, color: appMuted),
              ),
              const SizedBox(height: 8),
              Row(
                children: [
                  const Icon(Icons.person_outline, size: 17, color: appMuted),
                  const SizedBox(width: 4),
                  Expanded(
                    child: Text(
                      '${handoff.sender.name} → ${handoff.recipient.name}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 14),
                    ),
                  ),
                  Text(
                    teachingDateTime(handoff.createdAt),
                    style: const TextStyle(fontSize: 12.5, color: appMuted),
                  ),
                ],
              ),
              const SizedBox(height: 7),
              Text(
                handoff.contentSnapshot,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 14.5, height: 1.4),
              ),
              if (showUnread &&
                  handoff.readAt == null &&
                  handoff.status == 'pending') ...[
                const SizedBox(height: 6),
                const Text(
                  '未读',
                  style: TextStyle(fontSize: 12.5, color: appMuted),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _HandoffDetailPage extends StatefulWidget {
  const _HandoffDetailPage({
    required this.api,
    required this.handoffId,
    required this.readOnOpen,
    required this.onUpdated,
  });

  final ApiClient api;
  final String handoffId;
  final bool readOnOpen;
  final ValueChanged<Handoff> onUpdated;

  @override
  State<_HandoffDetailPage> createState() => _HandoffDetailPageState();
}

class _HandoffDetailPageState extends State<_HandoffDetailPage> {
  late final HandoffDetailController _controller;
  String? _inlineError;
  String? _inlineNotice;

  @override
  void initState() {
    super.initState();
    _controller = HandoffDetailController(
      api: TeachingRepository(widget.api),
      handoffId: widget.handoffId,
      readOnOpen: widget.readOnOpen,
    );
    _controller.addListener(_onChanged);
    _load();
  }

  Future<void> _load() async {
    await _controller.load();
    if (!mounted) {
      return;
    }
    final detail = _controller.detail;
    if (detail != null) {
      widget.onUpdated(detail);
    }
    setState(() {});
  }

  void _onChanged() {
    if (mounted) {
      setState(() {});
    }
  }

  @override
  void dispose() {
    _controller
      ..removeListener(_onChanged)
      ..dispose();
    super.dispose();
  }

  Future<void> _acknowledge() async {
    final confirmed = await _confirm(
      title: '确认收到进度交接？',
      content: '确认后会记录确认时间，并通知发送老师。仅打开详情不会完成确认。',
      action: '确认接收',
    );
    if (!confirmed) {
      return;
    }
    final result = await _controller.acknowledge();
    if (!mounted) {
      return;
    }
    if (result.ok && result.detail != null) {
      widget.onUpdated(result.detail!);
      setState(() => _inlineNotice = result.message);
    } else {
      setState(() => _inlineError = result.message);
    }
  }

  Future<void> _withdraw() async {
    final confirmed = await _confirm(
      title: '撤回这条进度交接？',
      content: '撤回后接收老师不能再确认。已确认的进度交接不能撤回。',
      action: '撤回',
    );
    if (!confirmed) {
      return;
    }
    final result = await _controller.withdraw();
    if (!mounted) {
      return;
    }
    if (result.ok && result.detail != null) {
      widget.onUpdated(result.detail!);
      setState(() => _inlineNotice = result.message);
    } else {
      setState(() => _inlineError = result.message);
    }
  }

  Future<bool> _confirm({
    required String title,
    required String content,
    required String action,
  }) async {
    return await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
            title: Text(title),
            content: Text(content),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context, false),
                child: const Text('取消'),
              ),
              FilledButton(
                onPressed: () => Navigator.pop(context, true),
                child: Text(action),
              ),
            ],
          ),
        ) ??
        false;
  }

  @override
  Widget build(BuildContext context) {
    final detail = _controller.detail;
    return Scaffold(
      appBar: AppBar(title: const Text('进度交接详情')),
      body: _buildBody(detail),
      bottomNavigationBar: detail == null ? null : _buildActions(detail),
    );
  }

  Widget _buildBody(Handoff? detail) {
    if (detail == null && _controller.loading) {
      return const LoadingView(message: '正在读取交接详情…');
    }
    if (detail == null && _controller.error != null) {
      return ErrorView(message: _controller.error!, onRetry: _load);
    }
    if (detail == null) {
      return const SizedBox.shrink();
    }
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(16, 10, 16, 24),
        children: [
          if (_controller.loading) const LinearProgressIndicator(minHeight: 2),
          if (_controller.error != null) ...[
            TeachingNotice(
              text: _controller.error!,
              tone: TeachingNoticeTone.error,
              icon: Icons.cloud_off_outlined,
            ),
            const SizedBox(height: 10),
          ],
          if (_controller.readError != null) ...[
            TeachingNotice(
              text: _controller.readError!,
              tone: TeachingNoticeTone.warning,
              icon: Icons.sync_problem,
            ),
            const SizedBox(height: 10),
          ],
          if (_inlineError != null) ...[
            TeachingNotice(
              text: _inlineError!,
              tone: TeachingNoticeTone.error,
              icon: Icons.error_outline,
            ),
            const SizedBox(height: 10),
          ],
          if (_inlineNotice != null) ...[
            TeachingNotice(
              text: _inlineNotice!,
              icon: Icons.check_circle_outline,
            ),
            const SizedBox(height: 10),
          ],
          Row(
            children: [
              Expanded(
                child: Text(
                  detail.courseName,
                  style: Theme.of(context).textTheme.titleLarge,
                ),
              ),
              TeachingStatusPill(label: handoffStatusText(detail.status)),
            ],
          ),
          const SizedBox(height: 2),
          Text(
            teachingSessionText(detail.sourceSession),
            style: const TextStyle(fontSize: 14, color: appMuted),
          ),
          const SizedBox(height: 12),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: _PersonLine(
                          icon: Icons.person_outline,
                          label: '发送老师',
                          name: detail.sender.name,
                        ),
                      ),
                      const Icon(
                        Icons.arrow_forward,
                        size: 18,
                        color: appMuted,
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: _PersonLine(
                          icon: Icons.person_pin_circle_outlined,
                          label: '接收老师',
                          name: detail.recipient.name,
                        ),
                      ),
                    ],
                  ),
                  const Divider(height: 24),
                  _InfoRow(
                    label: '来源课次',
                    value: teachingSessionText(detail.sourceSession),
                  ),
                  _InfoRow(
                    label: '目标课次',
                    value: teachingSessionText(detail.targetSession),
                  ),
                  _InfoRow(
                    label: '关联上次发布',
                    value: detail.notePublishedVersion == null
                        ? '未关联'
                        : '已关联上次发布内容',
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 12),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('交接内容', style: Theme.of(context).textTheme.titleMedium),
                  const SizedBox(height: 10),
                  SelectableText(
                    detail.contentSnapshot.trim().isEmpty
                        ? '（空）'
                        : detail.contentSnapshot,
                    style: const TextStyle(fontSize: 15.5, height: 1.55),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 12),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('状态与时间', style: Theme.of(context).textTheme.titleMedium),
                  const SizedBox(height: 8),
                  _InfoRow(
                    label: '当前状态',
                    value: handoffStatusText(detail.status),
                  ),
                  _InfoRow(
                    label: '发送时间',
                    value: teachingDateTime(detail.createdAt),
                  ),
                  _InfoRow(
                    label: '已读时间',
                    value: teachingDateTime(detail.readAt),
                  ),
                  _InfoRow(
                    label: '确认时间',
                    value: teachingDateTime(detail.acknowledgedAt),
                  ),
                  _InfoRow(
                    label: '撤回时间',
                    value: teachingDateTime(detail.withdrawnAt),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildActions(Handoff detail) {
    final canAck = detail.canAcknowledge;
    final canWithdraw = detail.canWithdraw;
    if (!canAck && !canWithdraw) {
      return SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Text(
            detail.status == 'acknowledged'
                ? '进度交接已确认，不能再次修改。'
                : detail.status == 'withdrawn'
                ? '进度交接已撤回。'
                : '当前账号只能查看。',
            textAlign: TextAlign.center,
            style: const TextStyle(fontSize: 14, color: appMuted),
          ),
        ),
      );
    }
    return SafeArea(
      top: false,
      child: Container(
        padding: const EdgeInsets.fromLTRB(16, 10, 16, 10),
        decoration: const BoxDecoration(
          color: Colors.white,
          border: Border(top: BorderSide(color: appLine)),
        ),
        child: Row(
          children: [
            if (canWithdraw)
              Expanded(
                child: OutlinedButton(
                  key: const Key('handoffWithdrawButton'),
                  onPressed: _controller.acting ? null : _withdraw,
                  style: OutlinedButton.styleFrom(
                    foregroundColor: const Color(0xFF9A3B30),
                    side: const BorderSide(color: Color(0xFFD6A39B)),
                  ),
                  child: const Text('撤回进度交接'),
                ),
              ),
            if (canWithdraw && canAck) const SizedBox(width: 10),
            if (canAck)
              Expanded(
                child: FilledButton.icon(
                  key: const Key('handoffAcknowledgeButton'),
                  onPressed: _controller.acting ? null : _acknowledge,
                  icon: _controller.acting
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            color: Colors.white,
                          ),
                        )
                      : const Icon(Icons.task_alt),
                  label: const Text('确认已接收'),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _PersonLine extends StatelessWidget {
  const _PersonLine({
    required this.icon,
    required this.label,
    required this.name,
  });

  final IconData icon;
  final String label;
  final String name;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Icon(icon, size: 17, color: appMuted),
            const SizedBox(width: 4),
            Text(
              label,
              style: const TextStyle(fontSize: 12.5, color: appMuted),
            ),
          ],
        ),
        const SizedBox(height: 2),
        Text(
          name,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600),
        ),
      ],
    );
  }
}

class _InfoRow extends StatelessWidget {
  const _InfoRow({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 84,
            child: Text(
              label,
              style: const TextStyle(fontSize: 14, color: appMuted),
            ),
          ),
          Expanded(child: Text(value, style: const TextStyle(fontSize: 14.5))),
        ],
      ),
    );
  }
}
