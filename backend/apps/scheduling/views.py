"""HTTP endpoints for schedule reads (B13) and reschedule options (B30).

The read endpoints never create or change sessions; B31 owns applications,
approval and formal effective changes.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from uuid import UUID

from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import TeacherProfile, Term
from apps.academics.selectors import active_student_ids
from apps.accounts.services import require_active_teacher
from apps.scheduling.models import ClassSession, SessionStatus, SessionTeacher
from apps.scheduling.reschedule_options import (
    OptionQuery,
    RescheduleOptionsError,
    find_reschedule_options,
)
from apps.scheduling.selectors import (
    active_assignments_for_teacher,
    current_term_for_school,
    session_local_date,
    visible_offerings_for_read,
    visible_offerings_for_teacher,
    visible_sessions_for_read,
    visible_teaching_classes_for_teacher,
)
from apps.scheduling.serializers import (
    OfferingSerializer,
    RescheduleOptionsErrorSerializer,
    RescheduleOptionsQuerySerializer,
    RescheduleOptionsResponseSerializer,
    ScheduleErrorSerializer,
    SessionDetailSerializer,
    SessionListResponseSerializer,
    SessionQuerySerializer,
    TermCalendarSerializer,
    TermSummarySerializer,
)


def _authorised_teacher_or_403(request: Request, session: ClassSession) -> TeacherProfile:
    profile = require_active_teacher(request.user)

    if SessionTeacher.objects.filter(session=session, teacher=profile).exists():
        return profile
    if active_assignments_for_teacher(profile).filter(offering_id=session.offering_id).exists():
        return profile
    raise PermissionDenied("只有该课次的授课教师或课程任课教师可以查询调课候选。")


@extend_schema(
    summary="查询课次调课候选",
    description=(
        "只读查询目标日期范围内的候选时段、教室、教师/学生/教室冲突和数据完整性。不会创建调课申请，也不会修改课次。"
    ),
    request=RescheduleOptionsQuerySerializer,
    responses={
        200: RescheduleOptionsResponseSerializer,
        400: RescheduleOptionsErrorSerializer,
        403: RescheduleOptionsErrorSerializer,
        409: RescheduleOptionsErrorSerializer,
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def reschedule_options(request: Request, session_id: UUID) -> Response:
    session = get_object_or_404(
        ClassSession.objects.select_related(
            "offering__term",
            "offering__course",
            "classroom",
        ),
        pk=session_id,
    )
    _authorised_teacher_or_403(request, session)

    query_serializer = RescheduleOptionsQuerySerializer(data=request.data)
    query_serializer.is_valid(raise_exception=True)
    payload = query_serializer.validated_data

    now = timezone.now()
    term = session.offering.term
    date_from = payload.get("date_from") or now.astimezone(term.tzinfo).date()
    date_until = payload.get("date_until") or date_from + timedelta(days=14)
    allowed_block_codes = payload.get("allowed_block_codes")
    query = OptionQuery(
        date_from=date_from,
        date_until=date_until,
        allowed_block_codes=set(allowed_block_codes) if allowed_block_codes is not None else None,
        classroom_ids=payload.get("classroom_ids"),
        allow_weekend=payload.get("allow_weekend", False),
    )

    try:
        result = find_reschedule_options(session, query, now=now)
    except RescheduleOptionsError as exc:
        return Response(exc.as_dict(), status=exc.status_code)

    response_serializer = RescheduleOptionsResponseSerializer(result)
    return Response(response_serializer.data, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# B13 schedule read APIs
# ---------------------------------------------------------------------------


def _error_response(errors: dict, *, code: str, detail: str) -> Response:
    return Response(
        {"code": code, "detail": detail, "field_errors": errors},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _query_error_response(serializer: SessionQuerySerializer) -> Response:
    errors = serializer.errors
    if "from" in errors or "to" in errors:
        return _error_response(
            errors,
            code="INVALID_DATE_RANGE",
            detail="日期范围无效；from/to 必填，使用半开区间且跨度不超过 42 天。",
        )
    return _error_response(errors, code="VALIDATION_ERROR", detail="查询参数有误。")


def _term_containing(teacher: TeacherProfile, day) -> Term | None:
    return (
        Term.objects.filter(
            school_id=teacher.school_id,
            start_date__lte=day,
            end_date__gte=day,
        )
        .order_by("-start_date", "-code")
        .first()
    )


class TermListView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        summary="本校全部学期",
        description="按开始日期倒序返回本校学期；is_current 按各学期学校时区的今天判断。",
        responses={200: TermSummarySerializer(many=True), 403: ScheduleErrorSerializer},
    )
    def get(self, request: Request) -> Response:
        teacher = require_active_teacher(request.user)
        terms = Term.objects.filter(school_id=teacher.school_id).order_by("-start_date", "-code")
        return Response(TermSummarySerializer(terms, many=True).data)


class TermCalendarView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        summary="学期校历与节次",
        description=(
            "返回学期字段、按序号排列的节次、校历例外和全部教学周（含周一/周日日期）。"
            "本校一个学期只有一套节次，因此不另设 period-schemes 接口。"
        ),
        responses={200: TermCalendarSerializer, 403: ScheduleErrorSerializer, 404: None},
    )
    def get(self, request: Request, term_id: UUID) -> Response:
        teacher = require_active_teacher(request.user)
        term = get_object_or_404(
            Term.objects.filter(school_id=teacher.school_id).prefetch_related("periods", "calendar_exceptions"),
            pk=term_id,
        )
        return Response(TermCalendarSerializer(term).data)


class SessionListView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="sessions_list",
        summary="按学校本地日期查询课次",
        description=(
            "from/to 为半开区间 [from, to)，按学校本地日期筛选课次；跨度 1–42 天。"
            "scope=mine 只返回本人实际上课的课次，scope=offering/teaching_class 需要对应 ID。"
            "日期落在任何学期之外时返回 term=null 和空 results。"
        ),
        parameters=[SessionQuerySerializer],
        responses={
            200: SessionListResponseSerializer,
            400: ScheduleErrorSerializer,
            403: ScheduleErrorSerializer,
            404: None,
        },
    )
    def get(self, request: Request) -> Response:
        teacher = require_active_teacher(request.user)
        query = SessionQuerySerializer(data=request.query_params)
        if not query.is_valid():
            return _query_error_response(query)
        params = query.validated_data

        date_from = params["from"]
        date_to = params["to"]
        term = _term_containing(teacher, date_from)
        if term is None:
            return Response(
                {
                    "from": date_from.isoformat(),
                    "to": date_to.isoformat(),
                    "term": None,
                    "results": [],
                }
            )

        sessions = visible_sessions_for_read(teacher)
        scope = params["scope"]
        if scope == "offering":
            offering = get_object_or_404(
                visible_offerings_for_teacher(teacher),
                pk=params["offering_id"],
            )
            sessions = sessions.filter(offering_id=offering.id)
        elif scope == "teaching_class":
            teaching_class = get_object_or_404(
                visible_teaching_classes_for_teacher(teacher),
                pk=params["teaching_class_id"],
            )
            sessions = sessions.filter(offering__teaching_class_id=teaching_class.id)
        else:
            sessions = sessions.filter(is_mine=True)

        start_at = datetime.combine(date_from, time.min, tzinfo=term.tzinfo)
        end_at = datetime.combine(date_to, time.min, tzinfo=term.tzinfo)
        sessions = sessions.filter(
            offering__term=term,
            start_at__gte=start_at,
            start_at__lt=end_at,
        )
        if not params["include_cancelled"]:
            sessions = sessions.exclude(status=SessionStatus.CANCELLED)
        sessions = sessions.order_by("start_at", "offering__course__course_code", "id")

        payload = {
            "from": date_from,
            "to": date_to,
            "term": term,
            "results": sessions,
        }
        return Response(SessionListResponseSerializer(payload, context={"teacher": teacher}).data)


class SessionDetailView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="sessions_retrieve",
        summary="课次详情",
        description="返回课次摘要、计划/实际学时、完成时间、课次日期有效选课人数及课程目标学时。",
        responses={200: SessionDetailSerializer, 403: ScheduleErrorSerializer, 404: None},
    )
    def get(self, request: Request, session_id: UUID) -> Response:
        teacher = require_active_teacher(request.user)
        session = get_object_or_404(visible_sessions_for_read(teacher), pk=session_id)
        enrolled_count = len(active_student_ids(session.offering, session_local_date(session)))
        return Response(
            SessionDetailSerializer(
                session,
                context={"teacher": teacher, "enrolled_count": enrolled_count},
            ).data
        )


class OfferingPagination(PageNumberPagination):
    page_size = 50


class OfferingListView(generics.ListAPIView):
    permission_classes = (IsAuthenticated,)
    serializer_class = OfferingSerializer
    pagination_class = OfferingPagination

    @extend_schema(
        summary="本人可见的课程开设",
        description=(
            "返回本人任课的 offering 以及同教学班的其他 offering；is_assigned 表示本人是否在该 offering 任课。"
            "不传 term_id 时使用当前学期，没有当前学期则返回空页。"
        ),
        parameters=[
            OpenApiParameter(
                name="term_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=False,
                description="学期 ID；默认当前学期。",
            )
        ],
        responses={200: OfferingSerializer(many=True), 400: None, 403: ScheduleErrorSerializer, 404: None},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        teacher = require_active_teacher(self.request.user)
        raw_term_id = self.request.query_params.get("term_id")
        if raw_term_id is not None:
            try:
                term_id = UUID(raw_term_id)
            except (TypeError, ValueError) as exc:
                raise ValidationError({"term_id": ["学期 ID 格式无效。"]}) from exc
            term = get_object_or_404(Term.objects.filter(school_id=teacher.school_id), pk=term_id)
        else:
            term = current_term_for_school(teacher.school_id)
            if term is None:
                return visible_offerings_for_read(teacher).none()

        return visible_offerings_for_read(teacher).filter(term=term).order_by("offering_code", "id")
