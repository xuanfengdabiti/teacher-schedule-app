"""HTTP endpoints for teaching plans, completion and server-side progress."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.services import require_active_teacher
from apps.scheduling.selectors import current_school_date
from apps.teaching.completion import record_completion
from apps.teaching.completion_serializers import (
    CompletionDetailSerializer,
    CompletionErrorSerializer,
    CompletionWriteSerializer,
    completion_detail_payload,
)
from apps.teaching.exceptions import (
    TeachingProgressError,
    completion_not_session_teacher,
    not_plan_owner,
)
from apps.teaching.models import CompletionRecord, PlanItem, TeachingPlan
from apps.teaching.plan_serializers import (
    OfferingPlanResponseSerializer,
    PlanWriteSerializer,
    ProgressErrorSerializer,
    plan_payload,
)
from apps.teaching.plans import save_plan
from apps.teaching.progress import offering_progress
from apps.teaching.selectors import (
    get_offering_for_progress,
    get_session_for_completion,
    is_session_teacher_for,
    require_offering_read_access,
)


def _error_response(exc: TeachingProgressError) -> Response:
    return Response(exc.as_dict(), status=exc.status_code)


def _validation_error(serializer) -> Response:
    field_errors = {field: [str(message) for message in messages] for field, messages in serializer.errors.items()}
    return Response(
        {
            "code": "VALIDATION_ERROR",
            "detail": "请求参数有误。",
            "field_errors": field_errors,
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


def _plan_response(offering, teacher, *, today: date, now) -> Response:
    plan = TeachingPlan.objects.select_related("owner").filter(offering=offering).first()
    items = list(plan.items.select_related("due_session").order_by("position", "id")) if plan is not None else []
    return Response(
        {
            "plan": plan_payload(plan, items=items),
            "progress": offering_progress(offering, today=today, now=now),
            "can_edit": offering.lead_teacher_id == teacher.id,
        }
    )


def _completion_response(session) -> Response:
    available_items = list(
        PlanItem.objects.filter(
            plan__offering=session.offering,
            archived=False,
        ).order_by("position", "id")
    )
    history = list(
        CompletionRecord.objects.filter(session=session)
        .select_related("actor", "session__offering__term")
        .order_by("-created_at", "-id")
    )
    return Response(
        completion_detail_payload(
            session,
            available_items=available_items,
            history=history,
        )
    )


class OfferingPlanView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        summary="读取课程教学计划与进度",
        description=(
            "仅该 offering 当前有效任课教师或该 offering 课次的实际授课教师可读；"
            "同一教学班其他课程教师返回 403 NOT_OFFERING_TEACHER，完全不可见返回 404。"
            "progress 由服务端唯一算法计算，包含目标/已完成/剩余/超额学时、剩余课次、"
            "过去待确认课次、未归入专题学时、专题到期差额和 behind/on_track/not_set 状态。"
            "没有计划时 plan=null 且 progress.plan_status=not_set，不判定落后。"
        ),
        responses={
            200: OfferingPlanResponseSerializer,
            403: ProgressErrorSerializer,
            404: ProgressErrorSerializer,
        },
    )
    def get(self, request: Request, offering_id: UUID) -> Response:
        teacher = require_active_teacher(request.user)
        try:
            offering = get_offering_for_progress(offering_id)
            require_offering_read_access(teacher, offering)
        except TeachingProgressError as exc:
            return _error_response(exc)
        return _plan_response(
            offering,
            teacher,
            today=current_school_date(),
            now=timezone.now(),
        )

    @extend_schema(
        summary="保存课程教学计划",
        description=(
            "仅 offering 的 lead_teacher 可保存。首次保存 base_version=0；已有计划时必须等于当前 "
            "version，否则 409 PLAN_VERSION_CONFLICT。items 顺序即 position；带 id 更新原条目，"
            "不带 id 新建。请求中消失且无 SessionProgress 的条目删除；有分配历史的条目改为 "
            "archived=true，保留历史分配且不再接受新分配。每次成功保存追加一条完整 PlanRevision。"
        ),
        request=PlanWriteSerializer,
        responses={
            200: OfferingPlanResponseSerializer,
            400: ProgressErrorSerializer,
            403: ProgressErrorSerializer,
            404: ProgressErrorSerializer,
            409: ProgressErrorSerializer,
        },
    )
    def put(self, request: Request, offering_id: UUID) -> Response:
        teacher = require_active_teacher(request.user)
        try:
            offering = get_offering_for_progress(offering_id)
            require_offering_read_access(teacher, offering)
            if offering.lead_teacher_id != teacher.id:
                raise not_plan_owner()
        except TeachingProgressError as exc:
            return _error_response(exc)

        serializer = PlanWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_error(serializer)
        try:
            save_plan(
                teacher,
                offering,
                items=serializer.validated_data["items"],
                base_version=serializer.validated_data["base_version"],
                now=timezone.now(),
            )
        except TeachingProgressError as exc:
            return _error_response(exc)
        return _plan_response(
            offering,
            teacher,
            today=current_school_date(),
            now=timezone.now(),
        )


class SessionCompletionView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        summary="读取课次完成信息",
        description=(
            "返回当前 status/actual_hours/completed_by/completed_at/completion_ref/version、"
            "当前专题分配、该 offering 未归档可分配专题和 CompletionRecord 历史。"
            "仅该 offering 当前任课教师或该 offering 课次的实际授课教师可读；同教学班非任课 "
            "403，完全不可见 404。"
        ),
        responses={
            200: CompletionDetailSerializer,
            403: CompletionErrorSerializer,
            404: CompletionErrorSerializer,
        },
    )
    def get(self, request: Request, session_id: UUID) -> Response:
        teacher = require_active_teacher(request.user)
        try:
            session = get_session_for_completion(session_id)
            require_offering_read_access(teacher, session.offering)
        except TeachingProgressError as exc:
            return _error_response(exc)
        return _completion_response(session)

    @extend_schema(
        summary="确认、更正或撤销授课完成",
        description=(
            "仅该课次 SessionTeacher 可写，否则 403 NOT_SESSION_TEACHER。complete 要求课次 "
            "status=scheduled 且 start_at<=now；correct/revert 只适用于 completed，且 reason 必填。"
            "complete/correct 必须填写 actual_hours，allocations 整体替换该课次专题分配，合计不得"
            "超过 actual_hours（400 ALLOCATION_EXCEEDS_SESSION_HOURS）；可小于实际学时。"
            "revert 清空 actual_hours/完成字段和全部专题分配并回到 scheduled。base_version 不等于"
            "当前课次 version 返回 409 SESSION_VERSION_CONFLICT 并附 current。每次成功操作课次 "
            "version+1、写 session ChangeLog 和 CompletionRecord。"
        ),
        request=CompletionWriteSerializer,
        responses={
            200: CompletionDetailSerializer,
            400: CompletionErrorSerializer,
            403: CompletionErrorSerializer,
            404: CompletionErrorSerializer,
            409: CompletionErrorSerializer,
        },
    )
    def put(self, request: Request, session_id: UUID) -> Response:
        teacher = require_active_teacher(request.user)
        try:
            session = get_session_for_completion(session_id)
            require_offering_read_access(teacher, session.offering)
            if not is_session_teacher_for(teacher, session):
                raise completion_not_session_teacher()
        except TeachingProgressError as exc:
            return _error_response(exc)

        serializer = CompletionWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_error(serializer)
        payload = serializer.validated_data
        try:
            record_completion(
                teacher,
                session,
                action=payload["action"],
                actual_hours=payload.get("actual_hours"),
                allocations=payload.get("allocations", []),
                reason=payload.get("reason", ""),
                base_version=payload["base_version"],
                now=timezone.now(),
            )
            current = get_session_for_completion(session.id)
        except TeachingProgressError as exc:
            return _error_response(exc)
        return _completion_response(current)
