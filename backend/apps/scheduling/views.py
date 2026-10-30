"""HTTP endpoints for schedule reads (B13), reschedule options (B30) and
teacher reschedule requests (B31).

Request creation/submission/withdrawal never changes the timetable.  Approval is
Admin-only and calls the transaction service in ``scheduling.changes``.
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
from apps.classrooms.models import Classroom
from apps.scheduling.models import ClassSession, RescheduleRequest, SessionStatus
from apps.scheduling.requests import (
    RescheduleRequestError,
    create_request,
    submit_request,
    withdraw_request,
)
from apps.scheduling.reschedule_options import (
    OptionQuery,
    RescheduleOptionsError,
    find_reschedule_options,
)
from apps.scheduling.selectors import (
    can_reschedule_session,
    current_term_for_school,
    session_local_date,
    visible_offerings_for_read,
    visible_offerings_for_teacher,
    visible_reschedule_requests,
    visible_sessions_for_read,
    visible_teaching_classes_for_teacher,
)
from apps.scheduling.serializers import (
    OfferingSerializer,
    PaginatedRescheduleRequestListSerializer,
    RescheduleOptionsErrorSerializer,
    RescheduleOptionsQuerySerializer,
    RescheduleOptionsResponseSerializer,
    RescheduleRequestCreateSerializer,
    RescheduleRequestErrorSerializer,
    RescheduleRequestListQuerySerializer,
    RescheduleRequestSerializer,
    ScheduleErrorSerializer,
    SessionDetailSerializer,
    SessionListResponseSerializer,
    SessionQuerySerializer,
    TermCalendarSerializer,
    TermSummarySerializer,
)
from apps.teaching.previous_notes import previous_note_previews


def _authorised_teacher_or_403(request: Request, session: ClassSession) -> TeacherProfile:
    profile = require_active_teacher(request.user)
    if can_reschedule_session(profile, session):
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
# B31 reschedule requests
# ---------------------------------------------------------------------------


def _workflow_error_response(exc: RescheduleRequestError) -> Response:
    return Response(exc.as_dict(), status=exc.status_code)


class RescheduleRequestPagination(PageNumberPagination):
    page_size = 50


class RescheduleRequestListCreateView(APIView):
    permission_classes = (IsAuthenticated,)
    pagination_class = RescheduleRequestPagination

    @extend_schema(
        summary="调课申请列表",
        description=(
            "scope=mine 返回本人申请；scope=related 返回本人可见课次上的申请，"
            "包括同教学班教师可见的关联申请。按 created_at 倒序，DRF 标准分页。"
        ),
        parameters=[RescheduleRequestListQuerySerializer],
        responses={
            200: PaginatedRescheduleRequestListSerializer,
            400: RescheduleRequestErrorSerializer,
            403: ScheduleErrorSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        teacher = require_active_teacher(request.user)
        query = RescheduleRequestListQuerySerializer(data=request.query_params)
        if not query.is_valid():
            return _error_response(
                query.errors,
                code="VALIDATION_ERROR",
                detail="查询参数有误。",
            )
        params = query.validated_data
        queryset = visible_reschedule_requests(teacher, scope=params["scope"])
        if params.get("status"):
            queryset = queryset.filter(status=params["status"])
        queryset = queryset.order_by("-created_at", "-id")

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = RescheduleRequestSerializer(page, many=True, context={"teacher": teacher})
        return paginator.get_paginated_response(serializer.data)

    @extend_schema(
        summary="创建调课申请",
        description=(
            "submit=true（默认）时服务端重新检查目标；available 直接提交，unverified 提交并标记待核验，"
            "conflict 返回 409 RESCHEDULE_CONFLICT 且不创建记录。submit=false 可保存 conflict 草稿。"
            "目标硬条件不满足返回 409 TARGET_NOT_ALLOWED。申请不改变正式课表。"
        ),
        request=RescheduleRequestCreateSerializer,
        responses={
            201: RescheduleRequestSerializer,
            400: RescheduleRequestErrorSerializer,
            403: ScheduleErrorSerializer,
            404: None,
            409: RescheduleRequestErrorSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        teacher = require_active_teacher(request.user)
        serializer = RescheduleRequestCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return _error_response(
                serializer.errors,
                code="VALIDATION_ERROR",
                detail="调课申请输入有误。",
            )
        payload = serializer.validated_data
        session = get_object_or_404(
            ClassSession.objects.select_related(
                "offering__term",
                "offering__course",
                "offering__teaching_class",
                "classroom",
            ),
            pk=payload["session_id"],
        )
        classroom = Classroom.objects.filter(pk=payload["target_classroom_id"]).first()
        if classroom is None:
            return _error_response(
                {"target_classroom_id": ["目标教室不存在。"]},
                code="VALIDATION_ERROR",
                detail="目标教室不存在。",
            )

        try:
            request_obj = create_request(
                teacher,
                session,
                target_date=payload["target_date"],
                target_start_period=payload["target_start_period"],
                target_end_period=payload["target_end_period"],
                target_classroom=classroom,
                reason=payload["reason"],
                submit=payload["submit"],
                now=timezone.now(),
            )
        except RescheduleRequestError as exc:
            return _workflow_error_response(exc)
        return Response(
            RescheduleRequestSerializer(request_obj, context={"teacher": teacher}).data,
            status=status.HTTP_201_CREATED,
        )


class RescheduleRequestSubmitView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        summary="提交调课申请草稿",
        description="仅申请人可提交草稿；服务端重新检查目标，conflict 返回 409 且保持 draft。",
        request=None,
        responses={
            200: RescheduleRequestSerializer,
            400: RescheduleRequestErrorSerializer,
            403: ScheduleErrorSerializer,
            404: None,
            409: RescheduleRequestErrorSerializer,
        },
    )
    def post(self, request: Request, request_id: UUID) -> Response:
        teacher = require_active_teacher(request.user)
        request_obj = get_object_or_404(RescheduleRequest, pk=request_id)
        try:
            request_obj = submit_request(request_obj, teacher, timezone.now())
        except RescheduleRequestError as exc:
            return _workflow_error_response(exc)
        return Response(RescheduleRequestSerializer(request_obj, context={"teacher": teacher}).data)


class RescheduleRequestWithdrawView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        summary="撤回调课申请",
        description="仅申请人可撤回 draft/submitted；撤回不改变课表。",
        request=None,
        responses={
            200: RescheduleRequestSerializer,
            403: ScheduleErrorSerializer,
            404: None,
            409: RescheduleRequestErrorSerializer,
        },
    )
    def post(self, request: Request, request_id: UUID) -> Response:
        teacher = require_active_teacher(request.user)
        request_obj = get_object_or_404(RescheduleRequest, pk=request_id)
        try:
            request_obj = withdraw_request(request_obj, teacher)
        except RescheduleRequestError as exc:
            return _workflow_error_response(exc)
        return Response(RescheduleRequestSerializer(request_obj, context={"teacher": teacher}).data)


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

        session_rows = list(sessions)
        previews = previous_note_previews(teacher, session_rows)
        payload = {
            "from": date_from,
            "to": date_to,
            "term": term,
            "results": session_rows,
        }
        return Response(
            SessionListResponseSerializer(
                payload,
                context={"teacher": teacher, "previous_note_previews": previews},
            ).data
        )


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
        previews = previous_note_previews(teacher, [session])
        return Response(
            SessionDetailSerializer(
                session,
                context={
                    "teacher": teacher,
                    "enrolled_count": enrolled_count,
                    "previous_note_previews": previews,
                },
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
