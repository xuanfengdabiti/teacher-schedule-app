"""HTTP endpoints for B21 teaching handoffs.

Views validate input shape and translate business errors; every status rule is
implemented in ``apps.teaching.handoffs``.  GET requests never write read_at or
any other business field.
"""

from __future__ import annotations

from uuid import UUID

from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import CourseOffering, TeacherProfile
from apps.accounts.services import require_active_teacher
from apps.scheduling.models import ClassSession
from apps.teaching.exceptions import HandoffError, handoff_invalid_note, handoff_invalid_recipients, handoff_not_found
from apps.teaching.handoff_serializers import (
    HandoffCreateSerializer,
    HandoffErrorSerializer,
    HandoffListQuerySerializer,
    HandoffPrefillQuerySerializer,
    HandoffPrefillSerializer,
    HandoffSerializer,
    PaginatedHandoffListSerializer,
    handoff_payload,
)
from apps.teaching.handoffs import (
    acknowledge,
    create_handoffs,
    get_handoff_for_teacher,
    handoffs_for_box,
    mark_read,
    prefill_for_session,
    withdraw,
)
from apps.teaching.models import TeachingNote


def _error_response(exc: HandoffError) -> Response:
    return Response(exc.as_dict(), status=exc.status_code)


def _validation_error(serializer, detail: str = "请求参数有误。") -> Response:
    field_errors = {field: [str(message) for message in messages] for field, messages in serializer.errors.items()}
    return Response(
        {
            "code": "VALIDATION_ERROR",
            "detail": detail,
            "field_errors": field_errors,
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


def _load_session(session_id: UUID) -> ClassSession | None:
    return (
        ClassSession.objects.select_related(
            "offering__term",
            "offering__course",
            "offering__teaching_class",
        )
        .filter(pk=session_id)
        .first()
    )


class HandoffPrefillView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="handoffs_prefill",
        summary="交接发送表单预填",
        description=(
            "只读：不创建交接或消息。recipients 为该 offering 当前有效任课教师（排除本人），"
            "is_suggested 标记同一 offering 中来源课次之后第一个 scheduled 课次的授课教师；"
            "content 只取本人在来源课次上已发布备注的未完成内容与下次计划，草稿不参与。"
        ),
        parameters=[HandoffPrefillQuerySerializer],
        responses={
            200: HandoffPrefillSerializer,
            400: HandoffErrorSerializer,
            403: HandoffErrorSerializer,
            404: HandoffErrorSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        teacher = require_active_teacher(request.user)
        query = HandoffPrefillQuerySerializer(data=request.query_params)
        if not query.is_valid():
            return _validation_error(query, "查询参数有误。")

        session = _load_session(query.validated_data["source_session_id"])
        if session is None:
            return _error_response(handoff_not_found("课次不存在或不在本人可见范围。"))
        try:
            payload = prefill_for_session(teacher, session)
        except HandoffError as exc:
            return _error_response(exc)
        return Response(HandoffPrefillSerializer(payload).data)


class HandoffPagination(PageNumberPagination):
    page_size = 50


class HandoffListCreateView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="handoffs_list",
        summary="交接列表",
        description=(
            "box=inbox 返回本人收到的交接，box=sent 返回本人发出的交接；status 可选。"
            "按 created_at、id 倒序，DRF 标准分页。can_acknowledge/can_withdraw 按当前教师身份计算。"
        ),
        parameters=[
            HandoffListQuerySerializer,
            OpenApiParameter(
                name="page",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="分页页码。",
            ),
        ],
        responses={
            200: PaginatedHandoffListSerializer,
            400: HandoffErrorSerializer,
            403: HandoffErrorSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        teacher = require_active_teacher(request.user)
        query = HandoffListQuerySerializer(data=request.query_params)
        if not query.is_valid():
            return _validation_error(query, "查询参数有误。")

        queryset = handoffs_for_box(
            teacher,
            box=query.validated_data["box"],
            status=query.validated_data.get("status"),
        )
        # Keep pagination explicit on GET only.  If it were a view-level
        # ``pagination_class``, drf-spectacular would also wrap the POST create
        # response as a paginated page and emit the wrong 201 schema.
        paginator = HandoffPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = HandoffSerializer([handoff_payload(handoff, teacher) for handoff in page], many=True)
        return paginator.get_paginated_response(serializer.data)

    @extend_schema(
        operation_id="handoffs_create",
        summary="发送教学交接",
        description=(
            "为每个接收人创建一条 pending 交接并发送 handoff_received 站内消息；"
            "recipient_ids 1–10 个、自动去重，任一接收人不是该 offering 当前有效任课教师或是本人时整体 400。"
            "content 写入 content_snapshot；关联备注只能选本人在来源课次上已发布的备注，"
            "并保存当时的 published_version。交接不写 ChangeLog，也不支持修改正文。"
        ),
        request=HandoffCreateSerializer,
        responses={
            201: HandoffSerializer(many=True),
            400: HandoffErrorSerializer,
            403: HandoffErrorSerializer,
            404: HandoffErrorSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        teacher = require_active_teacher(request.user)
        serializer = HandoffCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_error(serializer, "交接输入有误。")
        payload = serializer.validated_data

        offering = (
            CourseOffering.objects.select_related("course", "term", "teaching_class")
            .filter(pk=payload["offering_id"])
            .first()
        )
        if offering is None:
            return _error_response(handoff_not_found("课程开设不存在。"))

        recipient_ids = payload["recipient_ids"]
        profiles = TeacherProfile.objects.filter(pk__in=recipient_ids)
        by_id = {profile.id: profile for profile in profiles}
        if len(by_id) != len(set(recipient_ids)):
            return _error_response(handoff_invalid_recipients("部分接收人不存在。"))
        recipients = [by_id[recipient_id] for recipient_id in recipient_ids]

        source_session = None
        source_id = payload.get("source_session_id")
        if source_id is not None:
            source_session = _load_session(source_id)
            if source_session is None:
                return _error_response(handoff_not_found("来源课次不存在。"))

        target_session = None
        target_id = payload.get("target_session_id")
        if target_id is not None:
            target_session = _load_session(target_id)
            if target_session is None:
                return _error_response(handoff_not_found("目标课次不存在。"))

        note = None
        note_id = payload.get("note_id")
        if note_id is not None:
            note = TeachingNote.objects.select_related("session__offering__term", "author").filter(pk=note_id).first()
            if note is None:
                return _error_response(handoff_invalid_note("关联备注不存在。"))

        try:
            created = create_handoffs(
                teacher,
                offering,
                recipients,
                payload["content"],
                source_session,
                target_session,
                note,
                timezone.now(),
            )
        except HandoffError as exc:
            return _error_response(exc)
        return Response(
            HandoffSerializer([handoff_payload(handoff, teacher) for handoff in created], many=True).data,
            status=status.HTTP_201_CREATED,
        )


class HandoffDetailView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="handoffs_detail",
        summary="交接详情",
        description="仅发送人或接收人可看，其他人 404。GET 不写 read_at，也不改变状态。",
        responses={200: HandoffSerializer, 403: HandoffErrorSerializer, 404: HandoffErrorSerializer},
    )
    def get(self, request: Request, handoff_id: UUID) -> Response:
        teacher = require_active_teacher(request.user)
        try:
            handoff = get_handoff_for_teacher(teacher, handoff_id)
        except HandoffError as exc:
            return _error_response(exc)
        return Response(HandoffSerializer(handoff_payload(handoff, teacher)).data)


class HandoffReadView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="handoffs_read",
        summary="标记交接已读",
        description="仅接收人；只写 read_at，不改变 status。重复调用返回同一 read_at。",
        request=None,
        responses={200: HandoffSerializer, 403: HandoffErrorSerializer, 404: HandoffErrorSerializer},
    )
    def post(self, request: Request, handoff_id: UUID) -> Response:
        teacher = require_active_teacher(request.user)
        try:
            handoff = get_handoff_for_teacher(teacher, handoff_id)
            handoff = mark_read(teacher, handoff, timezone.now())
        except HandoffError as exc:
            return _error_response(exc)
        return Response(HandoffSerializer(handoff_payload(handoff, teacher)).data)


class HandoffAcknowledgeView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="handoffs_acknowledge",
        summary="确认收到交接",
        description=(
            "仅接收人；pending → acknowledged，写 acknowledged_at，read_at 为空时一并写入，"
            "并给发送人发 handoff_acknowledged 消息。重复确认返回同一结果且不重复通知；"
            "已撤回返回 409 HANDOFF_WITHDRAWN。"
        ),
        request=None,
        responses={
            200: HandoffSerializer,
            403: HandoffErrorSerializer,
            404: HandoffErrorSerializer,
            409: HandoffErrorSerializer,
        },
    )
    def post(self, request: Request, handoff_id: UUID) -> Response:
        teacher = require_active_teacher(request.user)
        try:
            handoff = get_handoff_for_teacher(teacher, handoff_id)
            handoff = acknowledge(teacher, handoff, timezone.now())
        except HandoffError as exc:
            return _error_response(exc)
        return Response(HandoffSerializer(handoff_payload(handoff, teacher)).data)


class HandoffWithdrawView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="handoffs_withdraw",
        summary="撤回交接",
        description=(
            "仅发送人；只能撤回 pending。已确认返回 409 HANDOFF_ALREADY_ACKNOWLEDGED；重复撤回幂等。撤回不发送消息。"
        ),
        request=None,
        responses={
            200: HandoffSerializer,
            403: HandoffErrorSerializer,
            404: HandoffErrorSerializer,
            409: HandoffErrorSerializer,
        },
    )
    def post(self, request: Request, handoff_id: UUID) -> Response:
        teacher = require_active_teacher(request.user)
        try:
            handoff = get_handoff_for_teacher(teacher, handoff_id)
            handoff = withdraw(teacher, handoff, timezone.now())
        except HandoffError as exc:
            return _error_response(exc)
        return Response(HandoffSerializer(handoff_payload(handoff, teacher)).data)
