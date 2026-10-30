# 空教室查询

本地演示用的只读接口，用于按日期和时段筛选没有已知占用的启用教室。不预约、不占房、不写任何记录。

## 接口

`GET /api/v1/classrooms/availability/`

学校来自当前登录且已关联的教师，不接收 school 参数。

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `date` | 是 | 本地日期 `YYYY-MM-DD` |
| `start_time` | 是 | `HH:mm` |
| `end_time` | 是 | `HH:mm`，必须晚于开始时间且同日 |
| `q` | 否 | 模糊匹配教室编号、校区、楼宇、房间号 |
| `campus` | 否 | 校区精确匹配 |
| `building` | 否 | 楼宇精确匹配 |
| `min_capacity` | 否 | 非负整数，容量下限 |
| `page` | 否 | DRF 标准分页，每页 50 |

时区取查询日期所属学期；当天不在任何学期内时使用 `settings.TIME_ZONE`。查询区间为 `[start_time, end_time)`。

## 响应

```json
{
  "date": "2026-11-02",
  "start_time": "08:00",
  "end_time": "09:40",
  "checked_at": "2026-10-27T08:00:00+08:00",
  "available_count": 1,
  "unverified_count": 0,
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "UUID",
      "classroom_code": "B201",
      "campus": "虚构校区",
      "building": "示例教学楼B",
      "room_no": "201",
      "capacity": 50,
      "is_active": true,
      "availability": "available",
      "data_completeness": "complete"
    }
  ]
}
```

- `available` / `complete`：`classroom_schedule` 与 `room_blocks` 覆盖都能证明该日该教室数据完整。
- `unverified` / `incomplete`：没有已知占用，但缺少完整覆盖声明；UI 必须显示“待核验”，不能称为空教室。
- 已知占用不会因数据不完整而进入结果。
- `available_count`、`unverified_count` 是分页前计数，`count` 为两者之和；分页只影响 `results`。

## 占用规则

- 排除所有非 `cancelled` 的 `ClassSession` 重叠：`start_at < end` 且 `end_at > start`。
- 排除 `RoomBlock` 重叠。
- 边界相接不算占用：08:00–09:40 的课不阻挡 09:40–10:00 的查询。
- 正式调课沿用同一课次行，旧教室在查询中释放、新教室占用；取消课次不占用。
- `completed` 历史课次在对应时段仍算占用。

## 覆盖

复用现有 B30 覆盖语义，只检查 `classroom_schedule` 与 `room_blocks` 两类，范围为 `school` 或 `classroom:{教室代码}`。匹配到的 `partial`/`missing` 声明会降级为待核验；没有匹配声明同样视为不完整。

## 错误

400 使用现有 `ClassroomUsageErrorSerializer` 结构：`{"code": "VALIDATION_ERROR", "detail": "...", "field_errors": {...}}`。空结果为 200 和空 `results`。

## 实现位置

- 选择器：`backend/apps/classrooms/selectors.py::free_classrooms_for_school`
- 批量覆盖 helper：`backend/apps/scheduling/reschedule_options.py::classroom_codes_coverage_complete`
- 测试：`backend/apps/classrooms/tests/test_availability.py`

占用与覆盖均按批次查询，查询次数不随教室数量增长。
