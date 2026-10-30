import '../../core/json.dart';

class FreeRoomQuery {
  const FreeRoomQuery({
    required this.date,
    required this.startTime,
    required this.endTime,
    this.q = '',
    this.campus = '',
    this.building = '',
    this.minCapacity,
  });

  final DateTime date;
  final String startTime;
  final String endTime;
  final String q;
  final String campus;
  final String building;
  final int? minCapacity;

  Map<String, dynamic> toQuery({int page = 1}) {
    return {
      'date': isoDate(date),
      'start_time': startTime,
      'end_time': endTime,
      if (q.trim().isNotEmpty) 'q': q.trim(),
      if (campus.trim().isNotEmpty) 'campus': campus.trim(),
      if (building.trim().isNotEmpty) 'building': building.trim(),
      if (minCapacity != null) 'min_capacity': minCapacity,
      if (page > 1) 'page': page,
    };
  }
}

class FreeRoomItem {
  const FreeRoomItem({
    required this.id,
    required this.classroomCode,
    required this.campus,
    required this.building,
    required this.roomNo,
    required this.capacity,
    required this.isActive,
    required this.availability,
    required this.dataCompleteness,
  });

  final String id;
  final String classroomCode;
  final String campus;
  final String building;
  final String roomNo;
  final int capacity;
  final bool isActive;
  final String availability;
  final String dataCompleteness;

  bool get isAvailable => availability == 'available';
  bool get isUnverified => availability == 'unverified';

  String get statusLabel => isAvailable ? '空闲' : '需确认';

  String get location =>
      [campus, building, roomNo].where((part) => part.isNotEmpty).join(' · ');

  factory FreeRoomItem.fromJson(Map<String, dynamic> json) {
    return FreeRoomItem(
      id: jsonString(json['id']) ?? '',
      classroomCode: jsonString(json['classroom_code']) ?? '教室未知',
      campus: jsonString(json['campus']) ?? '',
      building: jsonString(json['building']) ?? '',
      roomNo: jsonString(json['room_no']) ?? '',
      capacity: jsonInt(json['capacity']) ?? 0,
      isActive: json['is_active'] != false,
      availability: jsonString(json['availability']) ?? 'unverified',
      dataCompleteness: jsonString(json['data_completeness']) ?? 'incomplete',
    );
  }
}

class FreeRoomPageData {
  const FreeRoomPageData({
    required this.date,
    required this.startTime,
    required this.endTime,
    required this.checkedAt,
    required this.availableCount,
    required this.unverifiedCount,
    required this.count,
    required this.next,
    required this.previous,
    required this.results,
  });

  final DateTime? date;
  final String startTime;
  final String endTime;
  final DateTime? checkedAt;
  final int availableCount;
  final int unverifiedCount;
  final int count;
  final String? next;
  final String? previous;
  final List<FreeRoomItem> results;

  bool get hasMore => next != null;

  FreeRoomPageData withResults(List<FreeRoomItem> results) {
    return FreeRoomPageData(
      date: date,
      startTime: startTime,
      endTime: endTime,
      checkedAt: checkedAt,
      availableCount: availableCount,
      unverifiedCount: unverifiedCount,
      count: count,
      next: next,
      previous: previous,
      results: results,
    );
  }

  factory FreeRoomPageData.fromJson(Map<String, dynamic> json) {
    return FreeRoomPageData(
      date: jsonDate(json['date']),
      startTime: jsonString(json['start_time']) ?? '',
      endTime: jsonString(json['end_time']) ?? '',
      checkedAt: jsonDateTime(json['checked_at']),
      availableCount: jsonInt(json['available_count']) ?? 0,
      unverifiedCount: jsonInt(json['unverified_count']) ?? 0,
      count: jsonInt(json['count']) ?? 0,
      next: jsonString(json['next']),
      previous: jsonString(json['previous']),
      results: (json['results'] is List)
          ? (json['results'] as List)
                .map(jsonMap)
                .map(FreeRoomItem.fromJson)
                .toList(growable: false)
          : const [],
    );
  }
}
