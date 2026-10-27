import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../../core/network/api_client.dart';
import '../../shared/status_views.dart';
import 'collaboration_models.dart';
import 'collaboration_ui.dart';
import 'inquiry_detail_page.dart';

class InquiryListPage extends StatefulWidget {
  const InquiryListPage({
    super.key,
    required this.api,
    this.embedded = false,
    this.initialBox = 'received',
    this.refreshToken = 0,
  });

  final ApiClient api;
  final bool embedded;
  final String initialBox;
  final int refreshToken;

  @override
  State<InquiryListPage> createState() => InquiryListPageState();
}

class InquiryListPageState extends State<InquiryListPage> {
  late String _box = widget.initialBox;
  final Map<String, List<InquiryItem>> _cache = {};
  Object? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load(_box);
  }

  @override
  void didUpdateWidget(covariant InquiryListPage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.refreshToken != oldWidget.refreshToken) {
      refreshSent();
    }
  }

  Future<void> refreshSent() async {
    _cache.clear();
    _error = null;
    _loading = true;
    await _load('sent', forceRefresh: true);
  }

  List<InquiryItem>? get _items => _cache[_box];

  Future<void> _load(
    String box, {
    bool keepData = false,
    bool forceRefresh = false,
  }) async {
    setState(() {
      _box = box;
      _loading = true;
      if (!keepData || forceRefresh || _cache[box] == null) {
        _error = null;
      }
    });
    try {
      final response = await widget.api.get('inquiries/', query: {'box': box});
      final body = mapOf(response.data);
      final items = mapListOf(body['results'])
          .map(InquiryItem.fromJson)
          .toList(growable: false);
      if (!mounted) {
        return;
      }
      setState(() {
        _cache[box] = items;
        _error = null;
        _loading = false;
      });
    } catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _loading = false;
        if (_cache[box] == null) {
          _error = error;
        }
      });
      if (_cache[box] != null) {
        showCollabError(context, error);
      }
    }
  }

  Future<void> _open(InquiryItem item) async {
    final changed = await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (context) =>
            InquiryDetailPage(api: widget.api, inquiryId: item.id),
      ),
    );
    if (changed == true && mounted) {
      await _load(_box, keepData: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final items = _items;
    final content = Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
          child: SegmentedButton<String>(
            segments: const [
              ButtonSegment(
                value: 'received',
                icon: Icon(Icons.inbox_outlined),
                label: Text('收到的'),
              ),
              ButtonSegment(
                value: 'sent',
                icon: Icon(Icons.outbox_outlined),
                label: Text('我发起的'),
              ),
            ],
            selected: {_box},
            onSelectionChanged: (selection) {
              final next = selection.first;
              if (next == _box) {
                return;
              }
              final cached = _cache[next];
              setState(() {
                _box = next;
                _error = null;
                _loading = cached == null;
              });
              if (cached == null) {
                _load(next);
              }
            },
          ),
        ),
        if (_loading) const CollabInlineProgress(),
        Expanded(
          child: items == null
              ? _loading
                    ? const LoadingView(message: '正在读取失物消息…')
                    : CollabErrorView(
                        error: _error!,
                        onRetry: () => _load(_box),
                      )
              : RefreshIndicator(
                  onRefresh: () => _load(_box, keepData: true),
                  child: items.isEmpty
                      ? ListView(
                          physics: const AlwaysScrollableScrollPhysics(),
                          children: const [
                            SizedBox(height: 80),
                            EmptyView(
                              title: '暂无失物消息',
                              detail: '可以在教室记录中查看使用记录并发起失物询问。',
                              icon: Icons.inventory_2_outlined,
                            ),
                          ],
                        )
                      : ListView.separated(
                          physics: const AlwaysScrollableScrollPhysics(),
                          padding: const EdgeInsets.fromLTRB(16, 4, 16, 24),
                          itemCount: items.length,
                          separatorBuilder: (_, _) =>
                              const SizedBox(height: 10),
                          itemBuilder: (context, index) {
                            final item = items[index];
                            return _InquiryCard(
                              key: ValueKey('inquiry-${item.id}'),
                              item: item,
                              onTap: () => _open(item),
                            );
                          },
                        ),
                ),
        ),
      ],
    );
    if (widget.embedded) {
      return content;
    }
    return Scaffold(
      appBar: AppBar(title: const Text('失物消息')),
      body: content,
    );
  }
}

class _InquiryCard extends StatelessWidget {
  const _InquiryCard({super.key, required this.item, required this.onTap});

  final InquiryItem item;
  final VoidCallback onTap;

  Color get _statusColor => switch (item.status) {
    'open' => const Color(0xFFB7791F),
    'replied' => appTeal,
    'closed' => appMuted,
    _ => appMuted,
  };

  @override
  Widget build(BuildContext context) {
    return CollabSectionCard(
      onTap: onTap,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  item.itemDescription,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: Theme.of(context).textTheme.titleMedium,
                ),
              ),
              const SizedBox(width: 8),
              CollabStatusPill(label: item.statusLabel, color: _statusColor),
            ],
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              const Icon(
                Icons.meeting_room_outlined,
                size: 17,
                color: appMuted,
              ),
              const SizedBox(width: 5),
              Expanded(
                child: Text(
                  '${item.classroom.classroomCode} · ${item.classroom.location}',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            rangeLabel(item.fromAt, item.toAt),
            style: Theme.of(context).textTheme.bodySmall,
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: Text(
                  item.isInitiator
                      ? '我发起 · ${item.recipients.length} 位接收人'
                      : '${item.initiator.name} 发起',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ),
              Text(
                '${item.replies.length} 条回复',
                style: Theme.of(context).textTheme.bodySmall,
              ),
              const SizedBox(width: 4),
              const Icon(Icons.chevron_right, size: 20, color: appMuted),
            ],
          ),
        ],
      ),
    );
  }
}
