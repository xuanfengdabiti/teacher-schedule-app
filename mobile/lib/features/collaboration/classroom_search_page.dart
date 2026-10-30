import 'dart:async';

import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../../core/network/api_client.dart';
import '../../shared/status_views.dart';
import 'classroom_usage_page.dart';
import 'collaboration_models.dart';
import 'collaboration_ui.dart';

class ClassroomSearchPage extends StatefulWidget {
  const ClassroomSearchPage({
    super.key,
    required this.api,
    this.embedded = false,
    this.onInquirySent,
    this.onSelect,
    this.title = '教室记录',
  });

  final ApiClient api;
  final bool embedded;
  final VoidCallback? onInquirySent;
  final String title;

  /// When set, the page is used as a classroom picker: tapping a room returns
  /// it to the caller instead of opening the usage detail.
  final ValueChanged<ClassroomItem>? onSelect;

  @override
  State<ClassroomSearchPage> createState() => _ClassroomSearchPageState();
}

class _ClassroomSearchPageState extends State<ClassroomSearchPage> {
  final TextEditingController _search = TextEditingController();
  Timer? _debounce;
  List<ClassroomItem>? _items;
  Object? _error;
  bool _loading = true;
  int _requestNo = 0;

  @override
  void initState() {
    super.initState();
    _search.addListener(_onSearchChanged);
    _load();
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _search
      ..removeListener(_onSearchChanged)
      ..dispose();
    super.dispose();
  }

  void _onSearchChanged() {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 300), _load);
  }

  Future<void> _load() async {
    final requestNo = ++_requestNo;
    setState(() {
      _loading = true;
      if (_items == null) {
        _error = null;
      }
    });
    try {
      final response = await widget.api.get(
        'classrooms/',
        query: {'q': _search.text.trim()},
      );
      final body = mapOf(response.data);
      final items = mapListOf(body['results'])
          .map(ClassroomItem.fromJson)
          .toList(growable: false);
      if (!mounted || requestNo != _requestNo) {
        return;
      }
      setState(() {
        _items = items;
        _error = null;
        _loading = false;
      });
    } catch (error) {
      if (!mounted || requestNo != _requestNo) {
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

  void _openClassroom(ClassroomItem room) {
    final select = widget.onSelect;
    if (select != null) {
      select(room);
      return;
    }
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (context) => ClassroomUsagePage(
          api: widget.api,
          classroomId: room.id,
          onInquirySent: widget.onInquirySent,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final items = _items;
    final content = Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
          child: TextField(
            key: const ValueKey('classroom-search'),
            controller: _search,
            textInputAction: TextInputAction.search,
            decoration: const InputDecoration(
              prefixIcon: Icon(Icons.search),
              hintText: '搜索教室代码、校区或楼宇',
              suffixIcon: Icon(Icons.room_outlined),
            ),
          ),
        ),
        if (_loading) const CollabInlineProgress(),
        Expanded(
          child: items == null
              ? _loading
                    ? const LoadingView(message: '正在读取教室…')
                    : CollabErrorView(error: _error!, onRetry: () => _load())
              : RefreshIndicator(
                  onRefresh: _load,
                  child: items.isEmpty
                      ? ListView(
                          physics: const AlwaysScrollableScrollPhysics(),
                          children: const [
                            SizedBox(height: 80),
                            EmptyView(
                              title: '没有找到教室',
                              detail: '换一个教室代码、楼宇或校区试试。',
                              icon: Icons.meeting_room_outlined,
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
                            final room = items[index];
                            return CollabSectionCard(
                              key: ValueKey('classroom-${room.id}'),
                              onTap: () => _openClassroom(room),
                              child: Row(
                                children: [
                                  Container(
                                    width: 46,
                                    height: 46,
                                    decoration: BoxDecoration(
                                      color: const Color(0xFFDDEFEA),
                                      borderRadius: BorderRadius.circular(14),
                                    ),
                                    child: const Icon(
                                      Icons.meeting_room_outlined,
                                      color: appTeal,
                                    ),
                                  ),
                                  const SizedBox(width: 12),
                                  Expanded(
                                    child: Column(
                                      crossAxisAlignment:
                                          CrossAxisAlignment.start,
                                      children: [
                                        Row(
                                          children: [
                                            Expanded(
                                              child: Text(
                                                room.classroomCode,
                                                style: Theme.of(context)
                                                    .textTheme
                                                    .titleMedium,
                                              ),
                                            ),
                                            if (!room.isActive)
                                              const CollabStatusPill(
                                                label: '停用',
                                                color: appMuted,
                                              ),
                                          ],
                                        ),
                                        const SizedBox(height: 3),
                                        Text(
                                          room.location,
                                          style: Theme.of(context)
                                              .textTheme
                                              .bodySmall,
                                        ),
                                        Text(
                                          '容量 ${room.capacity}',
                                          style: Theme.of(context)
                                              .textTheme
                                              .bodySmall,
                                        ),
                                      ],
                                    ),
                                  ),
                                  const Icon(
                                    Icons.chevron_right,
                                    color: appMuted,
                                  ),
                                ],
                              ),
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
      appBar: AppBar(title: Text(widget.title)),
      body: content,
    );
  }
}
