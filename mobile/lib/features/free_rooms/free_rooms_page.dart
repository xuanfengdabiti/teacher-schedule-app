import 'package:flutter/material.dart';

import '../../app/theme.dart';
import '../../core/json.dart';
import '../../core/network/api_client.dart';
import '../../core/network/api_exception.dart';
import '../../shared/status_views.dart';
import '../collaboration/classroom_usage_page.dart';
import 'free_rooms_models.dart';
import 'free_rooms_repository.dart';

class FreeRoomsPage extends StatefulWidget {
  const FreeRoomsPage({super.key, required this.api});

  final ApiClient api;

  @override
  State<FreeRoomsPage> createState() => _FreeRoomsPageState();
}

class _FreeRoomsPageState extends State<FreeRoomsPage> {
  final TextEditingController _q = TextEditingController();
  final TextEditingController _campus = TextEditingController();
  final TextEditingController _building = TextEditingController();
  final TextEditingController _minCapacity = TextEditingController();

  late final FreeRoomsRepository _repository;
  late DateTime _date;
  TimeOfDay _start = const TimeOfDay(hour: 8, minute: 10);
  TimeOfDay _end = const TimeOfDay(hour: 9, minute: 40);

  FreeRoomPageData? _result;
  List<FreeRoomItem> _items = const [];
  Object? _error;
  String? _validationError;
  bool _loading = true;
  bool _loadingMore = false;
  bool _dirty = false;
  bool _showMoreFilters = false;
  int _page = 1;
  int _requestNo = 0;

  bool get _hasExactFilters =>
      _campus.text.trim().isNotEmpty || _building.text.trim().isNotEmpty;

  @override
  void initState() {
    super.initState();
    final now = DateTime.now();
    _date = DateTime(now.year, now.month, now.day);
    _repository = FreeRoomsRepository(widget.api);
    _q.addListener(_onFiltersChanged);
    _campus.addListener(_onFiltersChanged);
    _building.addListener(_onFiltersChanged);
    _minCapacity.addListener(_onFiltersChanged);
    _search();
  }

  @override
  void dispose() {
    _q
      ..removeListener(_onFiltersChanged)
      ..dispose();
    _campus
      ..removeListener(_onFiltersChanged)
      ..dispose();
    _building
      ..removeListener(_onFiltersChanged)
      ..dispose();
    _minCapacity
      ..removeListener(_onFiltersChanged)
      ..dispose();
    super.dispose();
  }

  void _onFiltersChanged() {
    if (!mounted) {
      return;
    }
    _requestNo++;
    setState(() {
      _result = null;
      _items = const [];
      _error = null;
      _validationError = null;
      _loading = false;
      _loadingMore = false;
      _dirty = true;
      _page = 1;
    });
  }

  FreeRoomQuery? _buildQuery() {
    final now = DateTime.now();
    if (_date.isAfter(DateTime(now.year + 3, 12, 31))) {
      setState(() => _validationError = '查询日期不能超过三年后。');
      return null;
    }
    final startMinutes = _start.hour * 60 + _start.minute;
    final endMinutes = _end.hour * 60 + _end.minute;
    if (endMinutes <= startMinutes) {
      setState(() => _validationError = '结束时间必须晚于开始时间。');
      return null;
    }
    final capacityText = _minCapacity.text.trim();
    int? minCapacity;
    if (capacityText.isNotEmpty) {
      minCapacity = int.tryParse(capacityText);
      if (minCapacity == null || minCapacity < 0) {
        setState(() => _validationError = '最小容量必须是非负整数。');
        return null;
      }
    }
    setState(() => _validationError = null);
    return FreeRoomQuery(
      date: _date,
      startTime: _timeText(_start),
      endTime: _timeText(_end),
      q: _q.text,
      campus: _campus.text,
      building: _building.text,
      minCapacity: minCapacity,
    );
  }

  Future<void> _search() async {
    FocusManager.instance.primaryFocus?.unfocus();
    final query = _buildQuery();
    if (query == null) {
      return;
    }
    final requestNo = ++_requestNo;
    setState(() {
      _loading = true;
      _loadingMore = false;
      _result = null;
      _items = const [];
      _error = null;
      _dirty = false;
      _page = 1;
    });
    try {
      final result = await _repository.search(query);
      if (!mounted || requestNo != _requestNo) {
        return;
      }
      setState(() {
        _result = result;
        _items = result.results;
        _loading = false;
      });
    } catch (error) {
      if (!mounted || requestNo != _requestNo) {
        return;
      }
      setState(() {
        _loading = false;
        _error = error;
      });
    }
  }

  Future<void> _loadMore() async {
    final result = _result;
    if (_loading ||
        _loadingMore ||
        _dirty ||
        result == null ||
        !result.hasMore) {
      return;
    }
    final query = _buildQuery();
    if (query == null) {
      return;
    }
    final requestNo = ++_requestNo;
    final nextPage = _page + 1;
    setState(() => _loadingMore = true);
    try {
      final next = await _repository.search(query, page: nextPage);
      if (!mounted || requestNo != _requestNo) {
        return;
      }
      final ids = _items.map((item) => item.id).toSet();
      final merged = [
        ..._items,
        ...next.results.where((item) => ids.add(item.id)),
      ];
      setState(() {
        _page = nextPage;
        _items = merged;
        _result = next.withResults(merged);
        _loadingMore = false;
      });
    } catch (error) {
      if (!mounted || requestNo != _requestNo) {
        return;
      }
      setState(() => _loadingMore = false);
      _showError(error);
    }
  }

  Future<void> _pickDate() async {
    final now = DateTime.now();
    final selected = await showDatePicker(
      context: context,
      initialDate: _date,
      firstDate: DateTime(2020),
      lastDate: DateTime(now.year + 3, 12, 31),
      helpText: '选择查询日期',
    );
    if (selected == null || !mounted) {
      return;
    }
    setState(
      () => _date = DateTime(selected.year, selected.month, selected.day),
    );
    _onFiltersChanged();
  }

  Future<void> _pickStart() async {
    final selected = await showTimePicker(
      context: context,
      initialTime: _start,
      helpText: '选择开始时间',
    );
    if (selected == null || !mounted) {
      return;
    }
    setState(() => _start = selected);
    _onFiltersChanged();
  }

  Future<void> _pickEnd() async {
    final selected = await showTimePicker(
      context: context,
      initialTime: _end,
      helpText: '选择结束时间',
    );
    if (selected == null || !mounted) {
      return;
    }
    setState(() => _end = selected);
    _onFiltersChanged();
  }

  void _openRoom(FreeRoomItem room) {
    final queryDate = _result?.date ?? _date;
    Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (context) => ClassroomUsagePage(
          api: widget.api,
          classroomId: room.id,
          initialDate: queryDate,
        ),
      ),
    );
  }

  void _showError(Object error) {
    if (!mounted) {
      return;
    }
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(
        SnackBar(content: Text(ApiException.fromUnknown(error).message)),
      );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('空教室查询')),
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (_loading) const LinearProgressIndicator(minHeight: 2),
          Expanded(
            child: RefreshIndicator(
              onRefresh: _search,
              child: CustomScrollView(
                physics: const AlwaysScrollableScrollPhysics(),
                slivers: [
                  SliverPadding(
                    padding: const EdgeInsets.fromLTRB(16, 10, 16, 0),
                    sliver: SliverToBoxAdapter(child: _buildForm(context)),
                  ),
                  const SliverToBoxAdapter(child: SizedBox(height: 14)),
                  ..._buildStatusSlivers(context),
                  const SliverToBoxAdapter(child: SizedBox(height: 20)),
                ],
              ),
            ),
          ),
          _buildQueryBar(context),
        ],
      ),
    );
  }

  List<Widget> _buildStatusSlivers(BuildContext context) {
    if (_dirty) {
      return [
        _messageSliver(
          const EmptyView(
            title: '筛选条件已修改',
            detail: '请点击下方“查询空教室”重新查询。',
            icon: Icons.refresh_outlined,
          ),
        ),
      ];
    }
    if (_loading) {
      return [_messageSliver(const LoadingView(message: '正在查询空教室…'))];
    }
    if (_error != null) {
      return [
        _messageSliver(
          ErrorView(
            message: ApiException.fromUnknown(_error!).message,
            onRetry: _search,
          ),
        ),
      ];
    }
    if (_result == null) {
      return [
        _messageSliver(
          const EmptyView(
            title: '设置条件后查询',
            detail: '默认今天 08:10–09:40，可直接查询。',
            icon: Icons.meeting_room_outlined,
          ),
        ),
      ];
    }
    return _buildResultSlivers(context, _result!);
  }

  Widget _messageSliver(Widget child) {
    return SliverPadding(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      sliver: SliverToBoxAdapter(
        child: Padding(padding: const EdgeInsets.only(top: 28), child: child),
      ),
    );
  }

  Widget _buildForm(BuildContext context) {
    final dateText = isoDate(_date);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('查询条件', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 10),
            OutlinedButton.icon(
              key: const ValueKey('free-rooms-date'),
              onPressed: _pickDate,
              icon: const Icon(Icons.calendar_today_outlined, size: 18),
              label: Text('$dateText · ${weekdayZh(_date.weekday)}'),
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(
                  child: OutlinedButton.icon(
                    key: const ValueKey('free-rooms-start'),
                    onPressed: _pickStart,
                    icon: const Icon(Icons.schedule, size: 18),
                    label: Text('开始 ${_timeText(_start)}'),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: OutlinedButton.icon(
                    key: const ValueKey('free-rooms-end'),
                    onPressed: _pickEnd,
                    icon: const Icon(Icons.schedule_outlined, size: 18),
                    label: Text('结束 ${_timeText(_end)}'),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            TextField(
              key: const ValueKey('free-rooms-q'),
              controller: _q,
              textInputAction: TextInputAction.search,
              decoration: const InputDecoration(
                prefixIcon: Icon(Icons.search),
                labelText: '校区 / 楼宇 / 教室编号',
                hintText: '可选，支持模糊搜索',
              ),
            ),
            const SizedBox(height: 8),
            TextField(
              key: const ValueKey('free-rooms-min-capacity'),
              controller: _minCapacity,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(
                labelText: '最小容量（可选）',
                hintText: '例如 50',
                isDense: true,
              ),
            ),
            Align(
              alignment: Alignment.centerLeft,
              child: TextButton.icon(
                key: const ValueKey('free-rooms-more-filters'),
                onPressed: () =>
                    setState(() => _showMoreFilters = !_showMoreFilters),
                style: TextButton.styleFrom(
                  padding: EdgeInsets.zero,
                  visualDensity: VisualDensity.compact,
                  alignment: Alignment.centerLeft,
                ),
                icon: Icon(
                  _showMoreFilters ? Icons.expand_less : Icons.tune,
                  size: 18,
                ),
                label: Text(
                  _showMoreFilters
                      ? '收起更多筛选'
                      : '更多筛选${_hasExactFilters ? '（已设置）' : ''}',
                ),
              ),
            ),
            if (_showMoreFilters) ...[
              TextField(
                key: const ValueKey('free-rooms-campus'),
                controller: _campus,
                decoration: const InputDecoration(
                  labelText: '校区（可选，精确匹配）',
                  isDense: true,
                ),
              ),
              const SizedBox(height: 8),
              TextField(
                key: const ValueKey('free-rooms-building'),
                controller: _building,
                decoration: const InputDecoration(
                  labelText: '楼宇（可选，精确匹配）',
                  isDense: true,
                ),
              ),
              const SizedBox(height: 8),
            ],
            if (_validationError != null) ...[
              const SizedBox(height: 6),
              Text(
                _validationError!,
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            ],
            const SizedBox(height: 10),
            Text(
              '请选择同一天的起止时间；标记“需确认”的教室请进一步核实。',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ],
        ),
      ),
    );
  }

  List<Widget> _buildResultSlivers(
    BuildContext context,
    FreeRoomPageData result,
  ) {
    final dateText = result.date == null
        ? isoDate(_date)
        : isoDate(result.date!);
    return [
      SliverPadding(
        padding: const EdgeInsets.symmetric(horizontal: 16),
        sliver: SliverToBoxAdapter(
          child: Card(
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '$dateText '
                    '${result.startTime.isEmpty ? _timeText(_start) : result.startTime}'
                    '–${result.endTime.isEmpty ? _timeText(_end) : result.endTime}',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  const SizedBox(height: 8),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      InfoPill(
                        label: '空闲 ${result.availableCount}',
                        color: appMint,
                      ),
                      InfoPill(
                        label: '需确认 ${result.unverifiedCount}',
                        color: const Color(0xFFB7791F),
                      ),
                      InfoPill(label: '共 ${result.count}', color: appMuted),
                    ],
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
      const SliverToBoxAdapter(child: SizedBox(height: 10)),
      if (_items.isEmpty)
        _messageSliver(
          const EmptyView(
            title: '没有匹配的空教室',
            detail: '可以调整日期、时段、搜索词或最小容量后重新查询。',
            icon: Icons.event_busy_outlined,
          ),
        )
      else
        SliverPadding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          sliver: SliverList.builder(
            itemCount: _items.length,
            itemBuilder: (context, index) {
              final room = _items[index];
              return Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: _RoomCard(
                  key: ValueKey('free-room-${room.id}'),
                  room: room,
                  onTap: () => _openRoom(room),
                ),
              );
            },
          ),
        ),
      if (result.hasMore)
        SliverPadding(
          padding: const EdgeInsets.fromLTRB(16, 4, 16, 0),
          sliver: SliverToBoxAdapter(
            child: OutlinedButton(
              key: const ValueKey('free-rooms-load-more'),
              onPressed: _loadingMore ? null : _loadMore,
              child: _loadingMore
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Text('加载更多'),
            ),
          ),
        ),
    ];
  }

  Widget _buildQueryBar(BuildContext context) {
    return Material(
      key: const ValueKey('free-rooms-query-bar'),
      color: Colors.white,
      elevation: 8,
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
          child: FilledButton.icon(
            key: const ValueKey('free-rooms-search'),
            onPressed: _loading ? null : _search,
            icon: _loading
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      color: Colors.white,
                    ),
                  )
                : const Icon(Icons.search),
            label: Text(_loading ? '查询中…' : '查询空教室'),
          ),
        ),
      ),
    );
  }
}

class _RoomCard extends StatelessWidget {
  const _RoomCard({super.key, required this.room, required this.onTap});

  final FreeRoomItem room;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final color = room.isAvailable ? appMint : const Color(0xFFB7791F);
    final coverage = room.dataCompleteness == 'complete' ? '' : ' · 部分安排待确认';
    return Card(
      child: InkWell(
        borderRadius: BorderRadius.circular(18),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Row(
            children: [
              Container(
                width: 44,
                height: 44,
                decoration: BoxDecoration(
                  color: color.withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(14),
                ),
                child: Icon(
                  room.isAvailable
                      ? Icons.meeting_room_outlined
                      : Icons.fact_check_outlined,
                  color: color,
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            room.classroomCode,
                            style: Theme.of(context).textTheme.titleMedium,
                          ),
                        ),
                        InfoPill(label: room.statusLabel, color: color),
                      ],
                    ),
                    const SizedBox(height: 3),
                    Text(
                      room.location,
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                    Text(
                      '容量 ${room.capacity}$coverage',
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ],
                ),
              ),
              const Icon(Icons.chevron_right, color: appMuted),
            ],
          ),
        ),
      ),
    );
  }
}

String _timeText(TimeOfDay value) {
  final hour = value.hour.toString().padLeft(2, '0');
  final minute = value.minute.toString().padLeft(2, '0');
  return '$hour:$minute';
}
