import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../../core/network/api_client.dart';
import '../../shared/status_views.dart';
import 'collaboration_models.dart';
import 'collaboration_ui.dart';

class NotificationListPage extends StatefulWidget {
  const NotificationListPage({super.key, required this.api, this.onOpenObject});

  final ApiClient api;
  final void Function(String objectType, String objectId)? onOpenObject;

  @override
  State<NotificationListPage> createState() => _NotificationListPageState();
}

class _NotificationListPageState extends State<NotificationListPage> {
  final List<NotificationItem> _items = [];
  final Set<String> _ids = {};
  String? _nextCursor;
  int _unreadCount = 0;
  Object? _error;
  bool _loading = true;
  bool _loadingMore = false;
  bool _loadedOnce = false;

  @override
  void initState() {
    super.initState();
    _load(reset: true);
  }

  Future<void> _load({required bool reset}) async {
    if (!reset && (_loadingMore || _nextCursor == null)) {
      return;
    }
    setState(() {
      if (reset) {
        _loading = true;
        _error = null;
      } else {
        _loadingMore = true;
      }
    });
    try {
      final response = await widget.api.get(
        'notifications/',
        query: {
          'limit': 20,
          if (!reset && _nextCursor != null) 'cursor': _nextCursor,
        },
      );
      final page = NotificationPageData.fromJson(mapOf(response.data));
      if (!mounted) {
        return;
      }
      var nextCursor = page.nextCursor;
      if (!reset && nextCursor == _nextCursor) {
        nextCursor = null;
      }
      setState(() {
        if (reset) {
          _items
            ..clear()
            ..addAll(page.results);
          _ids
            ..clear()
            ..addAll(page.results.map((item) => item.id));
        } else {
          for (final item in page.results) {
            if (_ids.add(item.id)) {
              _items.add(item);
            }
          }
        }
        _nextCursor = nextCursor;
        _unreadCount = page.unreadCount;
        _error = null;
        _loading = false;
        _loadingMore = false;
        _loadedOnce = true;
      });
    } catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _loading = false;
        _loadingMore = false;
        if (_items.isEmpty) {
          _error = error;
        }
      });
      if (_items.isNotEmpty) {
        showCollabError(context, error);
      }
    }
  }

  void _replace(NotificationItem item) {
    final index = _items.indexWhere((existing) => existing.id == item.id);
    if (index < 0) {
      return;
    }
    setState(() {
      _items[index] = item;
      _unreadCount = item.isRead
          ? (_unreadCount > 0 ? _unreadCount - 1 : 0)
          : _unreadCount;
    });
  }

  Future<void> _open(NotificationItem item) async {
    var current = item;
    if (!current.isRead) {
      try {
        final response = await widget.api.post(
          'notifications/${current.id}/read/',
        );
        current = NotificationItem.fromJson(mapOf(response.data));
        if (!mounted) {
          return;
        }
        _replace(current);
      } catch (error) {
        if (mounted) {
          showCollabError(context, error);
        }
        return;
      }
    }
    if (!mounted) {
      return;
    }
    final objectId = current.objectId;
    if (widget.onOpenObject != null &&
        objectId != null &&
        objectId.isNotEmpty) {
      widget.onOpenObject!(current.objectType, objectId);
      return;
    }
    await _showDetail(current);
  }

  Future<void> _showDetail(NotificationItem item) async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      builder: (context) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 20, 20, 28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      item.title,
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                  ),
                  CollabStatusPill(
                    label: item.isRead ? '已读' : '未读',
                    color: item.isRead ? appMuted : appTeal,
                  ),
                ],
              ),
              const SizedBox(height: 10),
              Text(item.body, style: Theme.of(context).textTheme.bodyLarge),
              const SizedBox(height: 12),
              Text(
                '${fullDateTimeLabel(item.createdAt)} · ${item.type}',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('消息'),
        actions: [
          if (_unreadCount > 0)
            Padding(
              padding: const EdgeInsets.only(right: 16),
              child: Center(
                child: CollabStatusPill(
                  label: '未读 $_unreadCount',
                  color: appTeal,
                ),
              ),
            ),
        ],
      ),
      body: Column(
        children: [
          if (_loading && _loadedOnce) const CollabInlineProgress(),
          Expanded(
            child: _items.isEmpty
                ? _loading
                      ? const LoadingView(message: '正在读取消息…')
                      : _error != null
                      ? CollabErrorView(
                          error: _error!,
                          onRetry: () => _load(reset: true),
                        )
                      : RefreshIndicator(
                          onRefresh: () => _load(reset: true),
                          child: ListView(
                            physics: const AlwaysScrollableScrollPhysics(),
                            children: const [
                              SizedBox(height: 80),
                              EmptyView(
                                title: '暂无消息',
                                detail: '课表变更、审批和失物询问提醒会显示在这里。',
                                icon: Icons.notifications_none_outlined,
                              ),
                            ],
                          ),
                        )
                : RefreshIndicator(
                    onRefresh: () => _load(reset: true),
                    child: ListView.separated(
                      physics: const AlwaysScrollableScrollPhysics(),
                      padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
                      itemCount: _items.length + 1,
                      separatorBuilder: (_, _) => const SizedBox(height: 8),
                      itemBuilder: (context, index) {
                        if (index == _items.length) {
                          if (_loadingMore) {
                            return const Padding(
                              padding: EdgeInsets.all(16),
                              child: Center(
                                child: CircularProgressIndicator(
                                  strokeWidth: 2,
                                ),
                              ),
                            );
                          }
                          if (_nextCursor == null) {
                            return const SizedBox(height: 8);
                          }
                          return Padding(
                            padding: const EdgeInsets.only(top: 4),
                            child: OutlinedButton(
                              key: const ValueKey('notifications-load-more'),
                              onPressed: () => _load(reset: false),
                              child: const Text('加载更多'),
                            ),
                          );
                        }
                        final item = _items[index];
                        return _NotificationCard(
                          key: ValueKey('notification-${item.id}'),
                          item: item,
                          onTap: () => _open(item),
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

class _NotificationCard extends StatelessWidget {
  const _NotificationCard({super.key, required this.item, required this.onTap});

  final NotificationItem item;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return CollabSectionCard(
      onTap: onTap,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 7),
            child: Container(
              width: 8,
              height: 8,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: item.isRead ? appLine : appTeal,
              ),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(
                      child: Text(
                        item.title,
                        style: Theme.of(context).textTheme.titleMedium
                            ?.copyWith(
                              fontWeight: item.isRead
                                  ? FontWeight.w600
                                  : FontWeight.w700,
                            ),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Text(
                      dateTimeLabel(item.createdAt),
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ],
                ),
                const SizedBox(height: 4),
                Text(
                  item.body,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
