"""Classroom catalogue and usage history endpoints (B32)."""

from __future__ import annotations

from uuid import UUID

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.services import require_active_teacher
from apps.classrooms.models import Classroom
from apps.classrooms.selectors import USAGE_NOTICE, classroom_usage_for_day, classrooms_for_school
from apps.classrooms.serializers import (
    ClassroomQuerySerializer,
    ClassroomSerializer,
    ClassroomUsageErrorSerializer,
    ClassroomUsageQuerySerializer,
    ClassroomUsageSerializer,
    PaginatedClassroomSerializer,
)


class ClassroomPagination(PageNumberPagination):
    page_size = 50


def _validation_error(serializer) -> Response:
    field_errors = {field: [str(message) for message in messages] for field, messages in serializer.errors.items()}
    return Response(
        {"code": "VALIDATION_ERROR", "detail": "查询参数有误。", "field_errors": field_errors},
        status=status.HTTP_400_BAD_REQUEST,
    )


class ClassroomListView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="classrooms_list",
        summary="本校教室列表",
        description=(
            "按 campus、building、room_no 排序。q 模糊匹配教室编号、楼宇和房间号；"
            "campus、building 为精确筛选。DRF 标准分页。"
        ),
        parameters=[
            ClassroomQuerySerializer,
            OpenApiParameter(
                name="page",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="分页页码。",
            ),
        ],
        responses={200: PaginatedClassroomSerializer, 400: ClassroomUsageErrorSerializer, 403: None},
    )
    def get(self, request: Request) -> Response:
        teacher = require_active_teacher(request.user)
        query = ClassroomQuerySerializer(data=request.query_params)
        if not query.is_valid():
            return _validation_error(query)

        queryset = classrooms_for_school(
            teacher.school_id,
            q=query.validated_data.get("q", ""),
            campus=query.validated_data.get("campus", ""),
            building=query.validated_data.get("building", ""),
        )
        paginator = ClassroomPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(ClassroomSerializer(page, many=True).data)


class ClassroomUsageView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="classrooms_usage",
        summary="教室某日本地使用记录",
        description=(
            "date 为学校本地日期且必填。entries 按 start_at 排序，kind 为 "
            "session/cancelled_session/moved_out/room_block。moved_out 来自 SessionChange "
            "before_json 中原本位于该教室该日的快照，start_at/end_at 是原时段，current_* "
            "是当前时段和教室；它不会作为有效 session 重复出现。data_completeness 只按 "
            "B30 的 classroom_schedule 与 room_blocks 覆盖判定，缺数据时为 incomplete。"
        ),
        parameters=[ClassroomUsageQuerySerializer],
        responses={
            200: ClassroomUsageSerializer,
            400: ClassroomUsageErrorSerializer,
            403: None,
            404: None,
        },
    )
    def get(self, request: Request, classroom_id: UUID) -> Response:
        teacher = require_active_teacher(request.user)
        classroom = get_object_or_404(
            Classroom.objects.filter(school_id=teacher.school_id),
            pk=classroom_id,
        )
        query = ClassroomUsageQuerySerializer(data=request.query_params)
        if not query.is_valid():
            return _validation_error(query)

        day = query.validated_data["date"]
        usage = classroom_usage_for_day(classroom, day, viewer=teacher)
        payload = {
            "classroom": usage.classroom,
            "date": usage.date,
            "notice": USAGE_NOTICE,
            "data_completeness": usage.data_completeness,
            "entries": usage.entries,
        }
        return Response(ClassroomUsageSerializer(payload).data)
