"""本地演示种子（只在演示容器内执行）。

用法（由 scripts/start-demo.ps1 调用）：
    python manage.py shell < /tmp/seed_demo.py

- 通过现有导入服务首次导入 templates/import 的 12 个虚构 CSV，生成 40 个课次。
- 建立虚构教师甲/乙与教务账号，并关联 TeacherProfile。
- 为教师甲补一条已发布中文备注、两个专题计划；向教师乙发一条虚构交接；
  为 A301 的蓝色笔记本发一条协查，留给教师乙回复。
- 所有额外样例先查已有记录：重复启动不重复创建，也不覆盖演示者已保存的内容。
任何一步非 2xx 或导入有错误都会抛异常，不吞错、不伪造成功。
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.academics.models import CourseOffering, School, TeacherProfile, TeacherStatus
from apps.imports.models import ImportBatch, ImportStatus
from apps.imports.services import commit_batch, preview_batch
from apps.imports.storage import copy_batch_files
from apps.inquiries.models import Inquiry
from apps.inquiries.services import inquiry_candidates
from apps.scheduling.models import ClassSession
from apps.teaching.models import Handoff, TeachingNote, TeachingPlan

SCHOOL_CODE = "DEMO-SCHOOL"
SOURCE_SYSTEM = "demo_csv_v1"
IMPORT_DIR = Path("/tmp/teacher-schedule-demo")
TERM_CODE = "2026-autumn"
TEACHER_PASSWORD = "TeacherDemo2026!"
STAFF_EMAIL = "academic-staff@example.invalid"
STAFF_PASSWORD = "PreviewOffice2026!"
TZ = ZoneInfo("Asia/Shanghai")
HANDOFF_MARKER = "【本地演示】"
INQUIRY_MARKER = "【本地演示】蓝色笔记本"
User = get_user_model()


def fail(message: str) -> None:
    raise RuntimeError(message)


def expect_2xx(response, label: str):
    if not 200 <= response.status_code < 300:
        body = getattr(response, "content", b"")
        text = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else str(body)
        fail(f"{label} 失败：HTTP {response.status_code} {text[:800]}")
    return response


def ensure_user(email: str, password: str, *, staff: bool = False):
    user, created = User.objects.get_or_create(email=email)
    if created:
        user.set_password(password)
    user.is_staff = staff or user.is_staff
    user.is_superuser = staff or user.is_superuser
    user.save()
    EmailAddress.objects.update_or_create(
        user=user,
        email=email,
        defaults={"verified": True, "primary": True},
    )
    return user


def ensure_teachers_imported(school: School, staff) -> None:
    if ImportBatch.objects.filter(school=school, source_system=SOURCE_SYSTEM, status=ImportStatus.COMMITTED).exists():
        return
    files = sorted(IMPORT_DIR.glob("*.csv"))
    if len(files) != 12:
        fail(f"导入模板应复制到 {IMPORT_DIR} 且包含 12 个 CSV，实际 {len(files)} 个：{IMPORT_DIR}")
    batch = ImportBatch.objects.create(
        school=school,
        source_system=SOURCE_SYSTEM,
        source_updated_at=timezone.now(),
        mode="merge",
        uploaded_by=staff,
    )
    batch.file_names, batch.file_hash = copy_batch_files(batch, files)
    batch.save(update_fields=["file_names", "file_hash", "updated_at"])
    preview = preview_batch(batch)
    if preview.has_errors:
        fail(f"导入预览存在错误：{[issue.as_dict() for issue in preview.issues]}")
    result = commit_batch(batch, staff)
    if not result.committed:
        fail(f"导入提交失败：{result.code} {result.message}")


def ensure_teacher_accounts(school: School) -> dict[str, TeacherProfile]:
    teachers: dict[str, TeacherProfile] = {}
    for teacher_no in ("0001", "0002"):
        teacher = TeacherProfile.objects.filter(school=school, teacher_no=teacher_no).first()
        if teacher is None:
            fail(f"导入后未找到教师 {teacher_no}，请检查导入数据。")
        user = ensure_user(teacher.email, TEACHER_PASSWORD)
        if teacher.user_id != user.id:
            teacher.user = user
        teacher.teacher_status = TeacherStatus.ACTIVE
        teacher.save(update_fields=["user", "teacher_status", "updated_at"])
        teachers[teacher_no] = teacher
    return teachers


def ensure_note(session: ClassSession, teacher: TeacherProfile) -> TeachingNote | None:
    note = TeachingNote.objects.filter(session=session, author=teacher).first()
    if note is not None:
        print(f"已有备注，跳过：{note.id}")
        return note
    client = APIClient()
    client.force_authenticate(user=teacher.user)
    draft = {
        "covered": "已讲解虚构案例中的现场勘查记录规范，并演示询问笔录的核对要点。",
        "unfinished": "虚构案例中的证人证言矛盾点尚未讲完，下次继续。",
        "next_plan": "下次用 20 分钟复盘矛盾点，再进入虚构的现场重建演练。",
        "base_version": 0,
        "client_mutation_id": f"demo-seed-note-{session.id}",
    }
    expect_2xx(
        client.put(f"/api/v1/sessions/{session.id}/notes/mine/", draft, format="json"),
        "保存演示备注",
    )
    note = TeachingNote.objects.filter(session=session, author=teacher).first()
    if note is None:
        fail("保存演示备注后未找到记录。")
    expect_2xx(
        client.post(
            f"/api/v1/sessions/{session.id}/notes/mine/publish/",
            {"base_version": note.version, "client_mutation_id": f"demo-seed-publish-{session.id}"},
            format="json",
        ),
        "发布演示备注",
    )
    note.refresh_from_db()
    if note.published_at is None:
        fail("备注发布后仍未找到已发布记录。")
    print(f"已创建并发布演示备注：{note.id}")
    return note


def ensure_plan(offering: CourseOffering, teacher: TeacherProfile) -> TeachingPlan | None:
    plan = TeachingPlan.objects.filter(offering=offering).first()
    if plan is not None:
        print(f"已有教学计划，跳过：{plan.id}")
        return plan
    client = APIClient()
    client.force_authenticate(user=teacher.user)
    payload = {
        "items": [
            {
                "title": "虚构专题一：现场勘查记录",
                "planned_hours": "20.0",
                "due_date": "2026-11-02",
            },
            {
                "title": "虚构专题二：询问笔录核对",
                "planned_hours": "20.0",
                "due_date": "2026-12-21",
            },
        ],
        "base_version": 0,
    }
    expect_2xx(
        client.put(f"/api/v1/offerings/{offering.id}/plan/", payload, format="json"),
        "保存演示教学计划",
    )
    plan = TeachingPlan.objects.filter(offering=offering).first()
    if plan is None:
        fail("教学计划保存后未找到记录。")
    print(f"已创建演示教学计划：{plan.id}")
    return plan


def ensure_handoff(
    offering: CourseOffering,
    sender: TeacherProfile,
    recipient: TeacherProfile,
    source_session: ClassSession,
    note: TeachingNote | None,
) -> None:
    if Handoff.objects.filter(
        offering=offering,
        sender=sender,
        recipient=recipient,
        content_snapshot__startswith=HANDOFF_MARKER,
    ).exists():
        print("已有演示交接，跳过。")
        return
    target = (
        ClassSession.objects.filter(
            offering=offering,
            status="scheduled",
            start_at__gt=timezone.now(),
            session_teachers__teacher=recipient,
        )
        .order_by("start_at")
        .first()
    )
    client = APIClient()
    client.force_authenticate(user=sender.user)
    payload = {
        "offering_id": str(offering.id),
        "recipient_ids": [str(recipient.id)],
        "content": HANDOFF_MARKER + "这是虚构演示交接：请下次课前核对备注中的未完成内容与下次计划。",
        "source_session_id": str(source_session.id),
    }
    if target is not None:
        payload["target_session_id"] = str(target.id)
    if note is not None and note.published_at is not None:
        payload["note_id"] = str(note.id)
    response = expect_2xx(client.post("/api/v1/handoffs/", payload, format="json"), "创建演示交接")
    created = getattr(response, "data", None)
    first_id = created[0].get("id") if isinstance(created, list) and created else "?"
    print(f"已创建演示交接：{first_id}")


def ensure_inquiry(
    classroom,
    initiator: TeacherProfile,
    recipient: TeacherProfile,
) -> None:
    if Inquiry.objects.filter(initiator=initiator, item_description__startswith=INQUIRY_MARKER).exists():
        print("已有演示协查，跳过。")
        return
    from_at = datetime(2026, 10, 26, 8, 0, tzinfo=TZ)
    to_at = datetime(2026, 10, 26, 9, 40, tzinfo=TZ)
    candidates = inquiry_candidates(initiator, classroom, from_at, to_at)
    candidate_ids = {entry.teacher.id for entry in candidates.candidates}
    if recipient.id not in candidate_ids:
        fail("演示协查的接收人不在候选教师中；请检查 2026-10-26 A301 的课次数据。")
    client = APIClient()
    client.force_authenticate(user=initiator.user)
    payload = {
        "classroom_id": str(classroom.id),
        "from_at": from_at.isoformat(),
        "to_at": to_at.isoformat(),
        "item_description": INQUIRY_MARKER + "（虚构物品，仅用于演示，可回复）。",
        "recipient_ids": [str(recipient.id)],
    }
    response = expect_2xx(client.post("/api/v1/inquiries/", payload, format="json"), "创建演示协查")
    print(f"已创建演示协查：{response.data.get('id') if hasattr(response, 'data') else '?'}")


def main() -> None:
    school, _ = School.objects.get_or_create(code=SCHOOL_CODE, defaults={"name": "虚构演示学校"})
    staff = ensure_user(STAFF_EMAIL, STAFF_PASSWORD, staff=True)
    ensure_teachers_imported(school, staff)
    teachers = ensure_teacher_accounts(school)
    teacher_a = teachers["0001"]
    teacher_b = teachers["0002"]

    offering = CourseOffering.objects.filter(term__school=school, offering_code="OFF-CRIME-2026A").first()
    if offering is None:
        fail("未找到 OFF-CRIME-2026A，导入可能未成功。")
    classroom = offering.term.school.classrooms.filter(classroom_code="A301").first()
    if classroom is None:
        fail("未找到 A301 教室。")

    source_session = (
        ClassSession.objects.filter(
            offering=offering,
            status="completed",
            origin_teaching_date=date(2026, 10, 19),
        )
        .order_by("start_at")
        .first()
    )
    if source_session is None:
        fail("未找到 2026-10-19 已完成的刑侦课次，导入数据可能不同。")

    note = ensure_note(source_session, teacher_a)
    ensure_plan(offering, teacher_a)
    ensure_handoff(offering, teacher_a, teacher_b, source_session, note)
    ensure_inquiry(classroom, teacher_a, teacher_b)

    print(
        {
            "school": school.code,
            "teachers": TeacherProfile.objects.filter(school=school).count(),
            "offerings": CourseOffering.objects.filter(term__school=school).count(),
            "sessions": ClassSession.objects.filter(offering__term__school=school).count(),
            "teacher_a": teacher_a.email,
            "teacher_b": teacher_b.email,
            "staff": staff.email,
        }
    )


try:
    main()
except Exception as exc:  # noqa: BLE001 - seed 必须让启动脚本看到真实失败
    print(f"SEED_FAILED: {exc}", file=sys.stderr)
    raise
