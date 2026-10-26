"""HTTP endpoints for B33 lost-item inquiries.

Views only validate shape and call the service layer.  GET candidate/detail
requests never write a first-view timestamp or change status.
"""

from __future__ import annotations

from uuid import UUID

from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.services import require_active_teacher
from apps.classrooms.models import Classroom
from apps.inquiries.exceptions import InquiryError
from apps.inquiries.serializers import (
    InquiryCandidateQuerySerializer,
    InquiryCandidatesResponseSerializer,
    InquiryCloseSerializer,
    InquiryCreateSerializer,
    InquiryErrorSerializer,
    InquiryListQuerySerializer,
    InquiryReplyCreateSerializer,
    InquirySerializer,
    PaginatedInquirySerializer,
    candidate_payload,
    inquiry_payload,
)
from apps.inquiries.services import (
    close_inquiry,
    create_inquiry,
    get_inquiry_for_teacher,
    inquiries_for_box,
    inquiry_candidates,
    mark_read,
    reply_to_inquiry,
)


def _error_response(exc: InquiryError) -> Response:
    return Response(exc.as_dict(), status=exc.status_code)


def _validation_error(serializer, detail: str = "请求参数有误。") -> Response:
    field_errors = {field: [str(message) for message in messages] for field, messages in serializer.errors.items()}
    return Response(
        {"code": "VALIDATION_ERROR", "detail": detail, "field_errors": field_errors},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _classroom_for(teacher, classroom_id: UUID) -> Classroom:
    return get_object_or_404(Classroom.objects.filter(school_id=teacher.school_id), pk=classroom_id)


def _detail_payload(teacher, inquiry_id: UUID) -> dict:
    inquiry = get_inquiry_for_teacher(teacher, inquiry_id)
    return inquiry_payload(inquiry, teacher)


class InquiryCandidatesView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="inquiries_candidates",
        summary="失物协查候选教师",
        description=(
            "只读。返回该教室在 [from_at, to_at) 内重叠的 scheduled/completed 课次的授课教师"
            "（排除本人），附 related_session_ids、课程名和上课时间。跨度最长 7 天，"
            "to_at 必须晚于 from_at。GET 不创建协查、消息或已读记录。"
        ),
        parameters=[InquiryCandidateQuerySerializer],
        responses={
            200: InquiryCandidatesResponseSerializer,
            400: InquiryErrorSerializer,
            403: InquiryErrorSerializer,
            404: None,
        },
    )
    def get(self, request: Request) -> Response:
        teacher = require_active_teacher(request.user)
        query = InquiryCandidateQuerySerializer(data=request.query_params)
        if not query.is_valid():
            return _validation_error(query, "查询参数有误。")
        classroom = _classroom_for(teacher, query.validated_data["classroom_id"])
        try:
            result = inquiry_candidates(
                teacher,
                classroom,
                query.validated_data["from_at"],
                query.validated_data["to_at"],
            )
        except InquiryError as exc:
            return _error_response(exc)
        return Response(InquiryCandidatesResponseSerializer(candidate_payload(result)).data)


class InquiryPagination(PageNumberPagination):
    page_size = 50


class InquiryListCreateView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="inquiries_list",
        summary="协查列表",
        description=(
            "box=sent 返回本人发起的协查，box=received 返回本人作为接收人的协查；"
            "status 可选。按 created_at、id 倒序，DRF 标准分页。"
        ),
        parameters=[
            InquiryListQuerySerializer,
            OpenApiParameter(
                name="page",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="分页页码。",
            ),
        ],
        responses={
            200: PaginatedInquirySerializer,
            400: InquiryErrorSerializer,
            403: InquiryErrorSerializer,
        },
    )
    def get(self, request: Request) -> Response:
        teacher = require_active_teacher(request.user)
        query = InquiryListQuerySerializer(data=request.query_params)
        if not query.is_valid():
            return _validation_error(query, "查询参数有误。")
        queryset = inquiries_for_box(
            teacher,
            box=query.validated_data["box"],
            status=query.validated_data.get("status"),
        )
        paginator = InquiryPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = InquirySerializer([inquiry_payload(inquiry, teacher) for inquiry in page], many=True)
        return paginator.get_paginated_response(serializer.data)

    @extend_schema(
        operation_id="inquiries_create",
        summary="发起失物协查",
        description=(
            "recipient_ids 1–20 个，每个接收人都必须在候选列表中（只看实际选中的教师，"
            "不自动群发）。任一接收人不合法时整体 400，并且一条都不创建。一个事务创建 "
            "Inquiry(open)、每位接收人的 related_session_ids 快照和 inquiry_received 站内消息。"
        ),
        request=InquiryCreateSerializer,
        responses={
            201: InquirySerializer,
            400: InquiryErrorSerializer,
            403: InquiryErrorSerializer,
            404: None,
        },
    )
    def post(self, request: Request) -> Response:
        teacher = require_active_teacher(request.user)
        serializer = InquiryCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_error(serializer, "协查输入有误。")
        payload = serializer.validated_data
        classroom = _classroom_for(teacher, payload["classroom_id"])
        try:
            inquiry = create_inquiry(
                teacher,
                classroom,
                from_at=payload["from_at"],
                to_at=payload["to_at"],
                item_description=payload["item_description"],
                recipient_ids=payload["recipient_ids"],
            )
        except InquiryError as exc:
            return _error_response(exc)
        detail = _detail_payload(teacher, inquiry.id)
        return Response(InquirySerializer(detail).data, status=status.HTTP_201_CREATED)


class InquiryDetailView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="inquiries_detail",
        summary="协查详情",
        description=(
            "仅发起人和接收人可见，其他人 404。返回接收人列表（首次查看时间、是否已回复）、"
            "全部追加回复和固定 notice。GET 不写首次查看时间，也不改变状态。"
        ),
        responses={200: InquirySerializer, 403: InquiryErrorSerializer, 404: None},
    )
    def get(self, request: Request, inquiry_id: UUID) -> Response:
        teacher = require_active_teacher(request.user)
        try:
            detail = _detail_payload(teacher, inquiry_id)
        except InquiryError as exc:
            return _error_response(exc)
        return Response(InquirySerializer(detail).data)


class InquiryReadView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="inquiries_read",
        summary="接收人首次查看协查",
        description="仅接收人；只在 first_viewed_at 为空时写入，不改变 status（查看不等于已回复）。",
        request=None,
        responses={200: InquirySerializer, 403: InquiryErrorSerializer, 404: None},
    )
    def post(self, request: Request, inquiry_id: UUID) -> Response:
        teacher = require_active_teacher(request.user)
        try:
            inquiry = get_inquiry_for_teacher(teacher, inquiry_id)
            mark_read(teacher, inquiry, timezone.now())
            detail = _detail_payload(teacher, inquiry_id)
        except InquiryError as exc:
            return _error_response(exc)
        return Response(InquirySerializer(detail).data)


class InquiryReplyCreateView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="inquiries_replies_create",
        summary="回复协查",
        description=(
            "仅该协查接收人；可以多次追加，不覆盖旧回复。closed 后返回 409 INQUIRY_CLOSED。"
            "首条回复让 open → replied，每条回复都给发起人发 inquiry_replied，"
            "事件键为 inquiry_reply:{reply_id}。result=other 时 body 必填。"
        ),
        request=InquiryReplyCreateSerializer,
        responses={
            201: InquirySerializer,
            400: InquiryErrorSerializer,
            403: InquiryErrorSerializer,
            404: None,
            409: InquiryErrorSerializer,
        },
    )
    def post(self, request: Request, inquiry_id: UUID) -> Response:
        teacher = require_active_teacher(request.user)
        serializer = InquiryReplyCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_error(serializer, "回复输入有误。")
        try:
            inquiry = get_inquiry_for_teacher(teacher, inquiry_id)
            reply_to_inquiry(
                teacher,
                inquiry,
                result=serializer.validated_data["result"],
                body=serializer.validated_data["body"],
                now=timezone.now(),
            )
            detail = _detail_payload(teacher, inquiry_id)
        except InquiryError as exc:
            return _error_response(exc)
        return Response(InquirySerializer(detail).data, status=status.HTTP_201_CREATED)


class InquiryCloseView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="inquiries_close",
        summary="协查结案",
        description=(
            "仅发起人；open 或 replied → closed，写 closed_at 和可空 close_note。"
            "重复结案幂等；closed 后不能再回复，本期不重新开启。"
        ),
        request=InquiryCloseSerializer,
        responses={200: InquirySerializer, 400: InquiryErrorSerializer, 403: InquiryErrorSerializer, 404: None},
    )
    def post(self, request: Request, inquiry_id: UUID) -> Response:
        teacher = require_active_teacher(request.user)
        serializer = InquiryCloseSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_error(serializer, "结案输入有误。")
        try:
            inquiry = get_inquiry_for_teacher(teacher, inquiry_id)
            close_inquiry(
                teacher,
                inquiry,
                close_note=serializer.validated_data["close_note"],
                now=timezone.now(),
            )
            detail = _detail_payload(teacher, inquiry_id)
        except InquiryError as exc:
            return _error_response(exc)
        return Response(InquirySerializer(detail).data)
