"""Cross-row and business validation that does not need database lookups.

Database-dependent checks (foreign keys, source identities and existing-object
comparisons) are completed under ``apps.imports.projection`` after these checks.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apps.imports.parsing import (
    FILE_SPECS,
    ImportIssue,
    ParsedRow,
    ParseResult,
    make_issue,
    validate_timezone,
)

VISIBILITIES = {"private", "course_teachers", "school_teachers"}
EXCEPTION_KINDS = {"cancel", "replace", "custom"}
SESSION_STATUSES = {"scheduled", "completed", "cancelled"}
COVERAGE_KINDS = {
    "calendar",
    "teacher_assignments",
    "teacher_schedule",
    "student_enrollments",
    "student_schedule",
    "classroom_schedule",
    "room_blocks",
    "person_blocks",
}
COMPLETENESS_VALUES = {"complete", "partial", "missing"}


def _issue(
    row: ParsedRow,
    field: str,
    code: str,
    message: str,
    *,
    value: object | None = None,
) -> ImportIssue:
    raw = row.values.get(field) if value is None else value
    return make_issue(
        file=row.file,
        sheet=row.sheet,
        row=row.row_number,
        field=field,
        value=raw,
        code=code,
        message=message,
    )


def _term_ranges(rows: list[ParsedRow]) -> dict[str, tuple[date, date, str, int]]:
    result: dict[str, tuple[date, date, str, int]] = {}
    for row in rows:
        code = row.values.get("term_code")
        start = row.values.get("start_date")
        end = row.values.get("end_date")
        timezone_name = row.values.get("timezone") or "Asia/Shanghai"
        week_count = row.values.get("teaching_week_count") or 0
        if code and isinstance(start, date) and isinstance(end, date):
            result.setdefault(code, (start, end, timezone_name, week_count))
    return result


def _validate_duplicate_ids(parsed: ParseResult) -> list[ImportIssue]:
    issues: list[ImportIssue] = []
    for logical_name, spec in FILE_SPECS.items():
        rows = parsed.rows_for(logical_name)
        grouped: dict[tuple[object, ...], list[ParsedRow]] = defaultdict(list)
        for row in rows:
            key = tuple(row.values.get(field_name) for field_name in spec.identity_fields)
            if any(value is None for value in key):
                continue
            grouped[key].append(row)
        for key, duplicates in grouped.items():
            if len(duplicates) < 2:
                continue
            display_key = "|".join(str(part) for part in key)
            for row in duplicates:
                field_name = spec.identity_fields[0]
                issues.append(
                    _issue(
                        row,
                        field_name,
                        "DUPLICATE_ID",
                        f"同一批次中外部编号 {display_key} 重复出现；即使内容相同，也必须删除重复行。",
                        value=display_key,
                    )
                )
    return issues


def _validate_terms(rows: list[ParsedRow]) -> list[ImportIssue]:
    issues: list[ImportIssue] = []
    for row in rows:
        start = row.values.get("start_date")
        end = row.values.get("end_date")
        first_monday = row.values.get("first_week_monday")
        week_count = row.values.get("teaching_week_count")
        hour_minutes = row.values.get("hour_minutes")
        timezone_name = row.values.get("timezone")
        if isinstance(start, date) and isinstance(end, date) and start > end:
            issues.append(_issue(row, "end_date", "INVALID_DATE_RANGE", "学期结束日期不能早于开始日期。"))
        if isinstance(first_monday, date) and first_monday.weekday() != 0:
            issues.append(_issue(row, "first_week_monday", "NOT_MONDAY", "教学第 1 周周一必须是星期一。"))
        if isinstance(start, date) and isinstance(first_monday, date) and first_monday > start:
            issues.append(
                _issue(
                    row,
                    "first_week_monday",
                    "INVALID_WEEK_ANCHOR",
                    "教学第 1 周周一不能晚于学期开始日期。",
                )
            )
        if isinstance(week_count, int) and week_count <= 0:
            issues.append(_issue(row, "teaching_week_count", "INVALID_WEEK_COUNT", "教学周数必须大于 0。"))
        if isinstance(hour_minutes, int) and hour_minutes <= 0:
            issues.append(_issue(row, "hour_minutes", "INVALID_HOUR_MINUTES", "每学时分钟数必须大于 0。"))
        if isinstance(timezone_name, str) and not validate_timezone(timezone_name):
            issues.append(
                _issue(
                    row,
                    "timezone",
                    "INVALID_TIMEZONE",
                    "时区必须是有效的 IANA 名称，例如 Asia/Shanghai。",
                )
            )
    return issues


def _validate_periods(rows: list[ParsedRow]) -> list[ImportIssue]:
    issues: list[ImportIssue] = []
    valid_by_term: dict[str, list[ParsedRow]] = defaultdict(list)
    for row in rows:
        number = row.values.get("period_number")
        start = row.values.get("start_time")
        end = row.values.get("end_time")
        if isinstance(number, int) and number <= 0:
            issues.append(_issue(row, "period_number", "INVALID_PERIOD_NUMBER", "节次序号必须大于 0。"))
        if isinstance(start, time) and isinstance(end, time) and start >= end:
            issues.append(_issue(row, "end_time", "INVALID_TIME_RANGE", "节次结束时间必须晚于开始时间。"))
        elif isinstance(start, time) and isinstance(end, time) and row.values.get("term_code"):
            valid_by_term[row.values["term_code"]].append(row)

    for term_rows in valid_by_term.values():
        for index, left in enumerate(term_rows):
            left_start = left.values["start_time"]
            left_end = left.values["end_time"]
            for right in term_rows[index + 1 :]:
                right_start = right.values["start_time"]
                right_end = right.values["end_time"]
                if left_start < right_end and right_start < left_end:
                    issues.append(
                        _issue(
                            left,
                            "start_time",
                            "OVERLAPPING_PERIOD",
                            f"与第 {right.values.get('period_number')} 节时间重叠；同学期节次不能重叠。",
                        )
                    )
                    issues.append(
                        _issue(
                            right,
                            "start_time",
                            "OVERLAPPING_PERIOD",
                            f"与第 {left.values.get('period_number')} 节时间重叠；同学期节次不能重叠。",
                        )
                    )
    return issues


def _validate_teachers(rows: list[ParsedRow]) -> list[ImportIssue]:
    issues: list[ImportIssue] = []
    for row in rows:
        for field_name in ("phone_visibility", "wechat_visibility"):
            value = row.values.get(field_name)
            if value and value not in VISIBILITIES:
                issues.append(
                    _issue(
                        row,
                        field_name,
                        "INVALID_VISIBILITY",
                        "可见范围只能是 private、course_teachers 或 school_teachers。",
                    )
                )
    return issues


def _validate_classrooms(rows: list[ParsedRow]) -> list[ImportIssue]:
    issues: list[ImportIssue] = []
    seen_rooms: dict[tuple[object, object, object], ParsedRow] = {}
    for row in rows:
        capacity = row.values.get("capacity")
        if isinstance(capacity, int) and capacity < 0:
            issues.append(_issue(row, "capacity", "INVALID_CAPACITY", "容量不能为负数。"))
        key = (row.values.get("campus"), row.values.get("building"), row.values.get("room_no"))
        if all(value is not None for value in key):
            previous = seen_rooms.get(key)
            if previous is not None:
                issues.append(
                    _issue(
                        row,
                        "room_no",
                        "DUPLICATE_ROOM",
                        "同一学校内校区、楼宇和房间号组合重复；请核对教室编号。",
                    )
                )
            else:
                seen_rooms[key] = row
    return issues


def _validate_offerings(rows: list[ParsedRow]) -> list[ImportIssue]:
    issues: list[ImportIssue] = []
    course_names: dict[str, str] = {}
    class_names: dict[tuple[str, str], str] = {}

    for row in rows:
        target = row.values.get("target_hours")
        teacher_codes = row.values.get("teacher_codes") or []
        lead = row.values.get("lead_teacher_code")
        if isinstance(target, Decimal) and target < 0:
            issues.append(_issue(row, "target_hours", "INVALID_HOURS", "目标学时不能为负数。"))
        if lead and lead not in teacher_codes:
            issues.append(
                _issue(
                    row,
                    "lead_teacher_code",
                    "LEAD_NOT_IN_TEACHERS",
                    "课程负责人必须包含在 teacher_codes 任课范围内。",
                )
            )

        course_code = row.values.get("course_code")
        course_name = row.values.get("course_name")
        if course_code and course_name:
            expected = course_names.setdefault(course_code, course_name)
            if expected != course_name:
                issues.append(
                    _issue(
                        row,
                        "course_name",
                        "INCONSISTENT_COURSE_NAME",
                        f"课程编号 {course_code} 在批次中的名称不一致（首次为“{expected}”）；不允许最后一行覆盖。",
                    )
                )

        class_code = row.values.get("class_code")
        class_name = row.values.get("class_name")
        term_code = row.values.get("term_code")
        if term_code and class_code and class_name:
            key = (term_code, class_code)
            expected = class_names.setdefault(key, class_name)
            if expected != class_name:
                issues.append(
                    _issue(
                        row,
                        "class_name",
                        "INCONSISTENT_CLASS_NAME",
                        f"教学班 {class_code} 在批次中的名称不一致（首次为“{expected}”）；不允许最后一行覆盖。",
                    )
                )
    return issues


def _validate_enrollments(rows: list[ParsedRow]) -> list[ImportIssue]:
    issues: list[ImportIssue] = []
    by_offering_student: dict[tuple[str, str], list[ParsedRow]] = defaultdict(list)
    for row in rows:
        effective_from = row.values.get("effective_from")
        effective_until = row.values.get("effective_until")
        if isinstance(effective_from, date) and isinstance(effective_until, date):
            if effective_until <= effective_from:
                issues.append(
                    _issue(row, "effective_until", "INVALID_DATE_RANGE", "选课失效日期（不含）必须晚于生效日期。")
                )
        if isinstance(effective_from, date):
            offering_code = row.values.get("offering_code")
            student_code = row.values.get("student_code")
            if offering_code and student_code:
                by_offering_student[(offering_code, student_code)].append(row)

    for group in by_offering_student.values():
        for index, left in enumerate(group):
            left_start = left.values.get("effective_from")
            if not isinstance(left_start, date):
                continue
            left_until = left.values.get("effective_until") or date.max
            for right in group[index + 1 :]:
                right_start = right.values.get("effective_from")
                if not isinstance(right_start, date):
                    continue
                right_until = right.values.get("effective_until") or date.max
                if left_start < right_until and right_start < left_until:
                    issues.append(
                        _issue(
                            left,
                            "effective_from",
                            "ENROLLMENT_OVERLAP",
                            "同一课程开设与同一学生的有效选课区间重叠；请拆成不重叠的编号和区间。",
                        )
                    )
                    issues.append(
                        _issue(
                            right,
                            "effective_from",
                            "ENROLLMENT_OVERLAP",
                            "同一课程开设与同一学生的有效选课区间重叠；请拆成不重叠的编号和区间。",
                        )
                    )
    return issues


def _validate_calendar_exceptions(
    rows: list[ParsedRow],
    term_ranges: dict[str, tuple[date, date, str, int]],
) -> list[ImportIssue]:
    issues: list[ImportIssue] = []
    dates: dict[tuple[str, date], ParsedRow] = {}
    replace_targets: set[tuple[str, date]] = set()
    replace_sources: dict[tuple[str, date], ParsedRow] = {}

    for row in rows:
        term_code = row.values.get("term_code")
        actual_date = row.values.get("date")
        source_date = row.values.get("source_teaching_date")
        kind = row.values.get("kind")
        if kind and kind not in EXCEPTION_KINDS:
            issues.append(_issue(row, "kind", "INVALID_EXCEPTION_KIND", "例外类型只能是 cancel、replace 或 custom。"))
        term_range = term_ranges.get(term_code)
        if term_range and isinstance(actual_date, date):
            start, end, _, _ = term_range
            if not (start <= actual_date <= end):
                issues.append(_issue(row, "date", "DATE_OUTSIDE_TERM", "校历例外日期必须在学期起止日期内。"))
        if kind == "replace":
            if not isinstance(source_date, date):
                issues.append(
                    _issue(
                        row,
                        "source_teaching_date",
                        "REQUIRED",
                        "replace 例外必须填写被替换的原教学日期。",
                    )
                )
            else:
                if term_range:
                    start, end, _, _ = term_range
                    if not (start <= source_date <= end):
                        issues.append(
                            _issue(
                                row,
                                "source_teaching_date",
                                "DATE_OUTSIDE_TERM",
                                "被替换的原教学日期必须在学期起止日期内。",
                            )
                        )
                if isinstance(actual_date, date) and source_date == actual_date:
                    issues.append(
                        _issue(
                            row,
                            "source_teaching_date",
                            "INVALID_DATE_RANGE",
                            "replace 的原教学日期不能与目标日期相同。",
                        )
                    )
                source_key = (str(term_code), source_date)
                previous_source = replace_sources.get(source_key)
                if previous_source is not None:
                    issues.append(
                        _issue(
                            row,
                            "source_teaching_date",
                            "REPLACE_SOURCE_DUPLICATED",
                            "同一原教学日期只能迁往一个目标日。",
                        )
                    )
                else:
                    replace_sources[source_key] = row
                replace_targets.add((str(term_code), actual_date) if isinstance(actual_date, date) else source_key)
        elif source_date is not None:
            issues.append(
                _issue(
                    row,
                    "source_teaching_date",
                    "SOURCE_NOT_ALLOWED",
                    "只有 replace 例外可以填写被替换的原教学日期。",
                )
            )

        if term_code and isinstance(actual_date, date):
            key = (str(term_code), actual_date)
            if key in dates:
                issues.append(
                    _issue(
                        row,
                        "date",
                        "DUPLICATE_EXCEPTION_DATE",
                        "同一学期同一日期最多只能有一条校历例外。",
                    )
                )
            else:
                dates[key] = row

    for source_key, row in replace_sources.items():
        if source_key in replace_targets:
            issues.append(
                _issue(
                    row,
                    "source_teaching_date",
                    "REPLACE_CHAIN_NOT_ALLOWED",
                    "禁止替换链：原教学日期已经是另一个 replace 的目标日。",
                )
            )
    return issues


def _validate_rules(
    rows: list[ParsedRow],
    term_ranges: dict[str, tuple[date, date, str, int]],
) -> list[ImportIssue]:
    issues: list[ImportIssue] = []
    for row in rows:
        weekday = row.values.get("weekday")
        start_period = row.values.get("start_period")
        end_period = row.values.get("end_period")
        weeks = row.values.get("weeks") or []
        teacher_codes = row.values.get("teacher_codes") or []
        planned_hours = row.values.get("planned_hours")
        term_code = row.values.get("term_code")

        if isinstance(weekday, int) and not 1 <= weekday <= 7:
            issues.append(_issue(row, "weekday", "INVALID_WEEKDAY", "星期必须是 1（周一）到 7（周日）。"))
        if isinstance(start_period, int) and start_period <= 0:
            issues.append(_issue(row, "start_period", "INVALID_PERIOD_NUMBER", "起始节必须大于 0。"))
        if isinstance(end_period, int) and start_period is not None and end_period < start_period:
            issues.append(_issue(row, "end_period", "INVALID_PERIOD_RANGE", "结束节不能早于起始节。"))
        if not weeks:
            issues.append(_issue(row, "weeks", "REQUIRED", "教学周集合不能为空。"))
        elif any(not isinstance(week, int) for week in weeks):
            issues.append(_issue(row, "weeks", "INVALID_INTEGER_LIST", "教学周必须是整数集合。"))
        else:
            term_range = term_ranges.get(term_code)
            max_week = term_range[3] if term_range else None
            if max_week and any(week < 1 or week > max_week for week in weeks):
                issues.append(
                    _issue(
                        row,
                        "weeks",
                        "WEEK_OUT_OF_RANGE",
                        f"教学周必须落在 1…{max_week} 范围内。",
                    )
                )
        if not teacher_codes:
            issues.append(_issue(row, "teacher_codes", "REQUIRED", "规则必须至少填写一名实际授课教师。"))
        if isinstance(planned_hours, Decimal) and planned_hours < 0:
            issues.append(_issue(row, "planned_hours", "INVALID_HOURS", "计划学时不能为负数。"))
    return issues


def _validate_sessions(
    rows: list[ParsedRow],
    term_ranges: dict[str, tuple[date, date, str, int]],
) -> list[ImportIssue]:
    issues: list[ImportIssue] = []
    for row in rows:
        status = row.values.get("status")
        origin_rule = row.values.get("origin_rule_code")
        origin_date = row.values.get("origin_teaching_date")
        origin_period = row.values.get("origin_start_period")
        session_date = row.values.get("session_date")
        start_period = row.values.get("start_period")
        end_period = row.values.get("end_period")
        start_time = row.values.get("start_time")
        end_time = row.values.get("end_time")
        actual_hours = row.values.get("actual_hours")
        completed_by = row.values.get("completed_by_teacher_code")
        completed_at = row.values.get("completed_at")
        completion_ref = row.values.get("completion_ref")
        planned_hours = row.values.get("planned_hours")
        term_code = row.values.get("term_code")

        if status and status not in SESSION_STATUSES:
            issues.append(
                _issue(row, "status", "INVALID_SESSION_STATUS", "状态只能是 scheduled、completed 或 cancelled。")
            )
        if isinstance(planned_hours, Decimal) and planned_hours < 0:
            issues.append(_issue(row, "planned_hours", "INVALID_HOURS", "计划学时不能为负数。"))
        if isinstance(start_period, int) and start_period <= 0:
            issues.append(_issue(row, "start_period", "INVALID_PERIOD_NUMBER", "开始节必须大于 0。"))
        if isinstance(end_period, int) and isinstance(start_period, int) and end_period < start_period:
            issues.append(_issue(row, "end_period", "INVALID_PERIOD_RANGE", "结束节不能早于开始节。"))

        origin_values = [origin_rule, origin_date, origin_period]
        present_count = sum(value is not None for value in origin_values)
        if 0 < present_count < 3:
            issues.append(
                _issue(
                    row,
                    "origin_rule_code",
                    "ORIGIN_FIELDS_INCOMPLETE",
                    "规则来源字段 origin_rule_code、origin_teaching_date、origin_start_period 必须全有或全无。",
                )
            )

        if (start_time is None) != (end_time is None):
            issues.append(
                _issue(
                    row,
                    "start_time",
                    "TIME_PAIR_REQUIRED",
                    "start_time 与 end_time 必须同时填写；留空时按节次派生。",
                )
            )
        elif isinstance(start_time, time) and isinstance(end_time, time) and end_time <= start_time:
            issues.append(_issue(row, "end_time", "INVALID_TIME_RANGE", "课次结束时间必须晚于开始时间。"))

        term_range = term_ranges.get(term_code)
        if term_range and isinstance(session_date, date):
            start, end, _, _ = term_range
            if not (start <= session_date <= end):
                issues.append(_issue(row, "session_date", "DATE_OUTSIDE_TERM", "课次日期必须在学期起止日期内。"))

        completion_fields = [completed_by, completed_at, completion_ref]
        if status == "scheduled":
            if actual_hours is not None:
                issues.append(
                    _issue(row, "actual_hours", "ACTUAL_HOURS_NOT_ALLOWED", "scheduled 课次的实际学时必须为空。")
                )
            if any(value is not None for value in completion_fields):
                issues.append(
                    _issue(
                        row,
                        "completed_at",
                        "COMPLETION_FIELDS_NOT_ALLOWED",
                        "非 completed 课次不能填写完成确认字段。",
                    )
                )
        elif status == "cancelled":
            if actual_hours is None:
                issues.append(_issue(row, "actual_hours", "REQUIRED", "cancelled 课次必须显式填写 0.0。"))
            elif actual_hours != Decimal("0"):
                issues.append(
                    _issue(row, "actual_hours", "CANCELLED_HOURS_ZERO", "cancelled 课次的 actual_hours 必须为 0.0。")
                )
            if any(value is not None for value in completion_fields):
                issues.append(
                    _issue(
                        row,
                        "completed_at",
                        "COMPLETION_FIELDS_NOT_ALLOWED",
                        "非 completed 课次不能填写完成确认字段。",
                    )
                )
        elif status == "completed":
            if actual_hours is None:
                issues.append(_issue(row, "actual_hours", "REQUIRED", "completed 课次必须填写实际学时。"))
            if completed_by is None:
                issues.append(
                    _issue(row, "completed_by_teacher_code", "REQUIRED", "completed 课次必须填写完成确认教师。")
                )
            if completed_at is None:
                issues.append(_issue(row, "completed_at", "REQUIRED", "completed 课次必须填写带时区的完成确认时间。"))
            if not completion_ref:
                issues.append(_issue(row, "completion_ref", "REQUIRED", "completed 课次必须填写完成记录或确认依据。"))

        if isinstance(actual_hours, Decimal) and actual_hours < 0:
            issues.append(_issue(row, "actual_hours", "INVALID_HOURS", "实际学时不能为负数。"))
    return issues


def _validate_blocks(
    rows: list[ParsedRow],
    term_ranges: dict[str, tuple[date, date, str, int]],
    *,
    person: bool,
) -> list[ImportIssue]:
    issues: list[ImportIssue] = []
    for row in rows:
        start_at = row.values.get("start_at")
        end_at = row.values.get("end_at")
        term_code = row.values.get("term_code")
        if isinstance(start_at, datetime) and isinstance(end_at, datetime) and end_at <= start_at:
            issues.append(_issue(row, "end_at", "INVALID_TIME_RANGE", "结束时间必须晚于开始时间。"))
        term_range = term_ranges.get(term_code)
        if term_range and isinstance(start_at, datetime) and isinstance(end_at, datetime):
            start_date, end_date, timezone_name, _ = term_range
            try:
                tzinfo = ZoneInfo(timezone_name)
            except (ZoneInfoNotFoundError, ValueError):
                tzinfo = None
            if tzinfo is not None:
                local_start = start_at.astimezone(tzinfo)
                local_end = end_at.astimezone(tzinfo)
                if not (start_date <= local_start.date() <= end_date and start_date <= local_end.date() <= end_date):
                    issues.append(
                        _issue(
                            row,
                            "start_at",
                            "BLOCK_OUTSIDE_TERM",
                            "占用起止时间必须落在学期日期范围内。",
                        )
                    )
        if person:
            resource_type = row.values.get("resource_type")
            if resource_type not in {"teacher", "student"}:
                issues.append(
                    _issue(
                        row,
                        "resource_type",
                        "INVALID_RESOURCE_TYPE",
                        "人员不可用的 resource_type 只能是 teacher 或 student。",
                    )
                )
    return issues


def _validate_coverage(rows: list[ParsedRow]) -> list[ImportIssue]:
    issues: list[ImportIssue] = []
    for row in rows:
        kind = row.values.get("kind")
        completeness = row.values.get("completeness")
        from_date = row.values.get("from_date")
        until_date = row.values.get("until_date")
        confirmation_ref = row.values.get("confirmation_ref")
        if kind and kind not in COVERAGE_KINDS:
            issues.append(_issue(row, "kind", "INVALID_COVERAGE_KIND", "coverage.kind 不是规范中的八类之一。"))
        if completeness and completeness not in COMPLETENESS_VALUES:
            issues.append(
                _issue(
                    row,
                    "completeness",
                    "INVALID_COMPLETENESS",
                    "completeness 只能是 complete、partial 或 missing。",
                )
            )
        if isinstance(from_date, date) and isinstance(until_date, date) and until_date <= from_date:
            issues.append(_issue(row, "until_date", "INVALID_DATE_RANGE", "覆盖结束日期（不含）必须晚于开始日期。"))
        if completeness == "complete" and not confirmation_ref:
            issues.append(
                _issue(
                    row,
                    "confirmation_ref",
                    "REQUIRED",
                    "声明 complete 时必须填写学校导出范围或教务确认依据。",
                )
            )
    return issues


def validate_business_rows(parsed: ParseResult) -> list[ImportIssue]:
    """Return all database-independent row and cross-row validation issues."""
    term_rows = parsed.rows_for("terms")
    term_ranges = _term_ranges(term_rows)
    issues: list[ImportIssue] = []
    issues.extend(_validate_duplicate_ids(parsed))
    issues.extend(_validate_terms(term_rows))
    issues.extend(_validate_periods(parsed.rows_for("periods")))
    issues.extend(_validate_teachers(parsed.rows_for("teachers")))
    issues.extend(_validate_classrooms(parsed.rows_for("classrooms")))
    issues.extend(_validate_offerings(parsed.rows_for("offerings")))
    issues.extend(_validate_enrollments(parsed.rows_for("enrollments")))
    issues.extend(_validate_calendar_exceptions(parsed.rows_for("calendar_exceptions"), term_ranges))
    issues.extend(_validate_rules(parsed.rows_for("rules"), term_ranges))
    issues.extend(_validate_sessions(parsed.rows_for("sessions"), term_ranges))
    issues.extend(_validate_blocks(parsed.rows_for("room_blocks"), term_ranges, person=False))
    issues.extend(_validate_blocks(parsed.rows_for("resource_unavailability"), term_ranges, person=True))
    issues.extend(_validate_coverage(parsed.rows_for("coverage")))
    return issues
