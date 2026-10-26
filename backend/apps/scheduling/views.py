"""HTTP endpoint for read-only reschedule candidate queries (B30).

This view does not create a reschedule request and never changes a session;
B31 owns applications, approval and formal effective changes.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.academics.models import TeacherProfile, TeachingAssignment
from apps.scheduling.models import ClassSession, SessionTeacher
from apps.scheduling.reschedule_options import (
    OptionQuery,
    RescheduleOptionsError,
    find_reschedule_options,
)
from apps.scheduling.serializers import (
    RescheduleOptionsErrorSerializer,
    RescheduleOptionsQuerySerializer,
    RescheduleOptionsResponseSerializer,
)


def _authorised_teacher_or_403(request: Request, session: ClassSession) -> TeacherProfile:
    try:
        profile = request.user.teacher_profile
    except TeacherProfile.DoesNotExist as exc:
        raise PermissionDenied("当前账号未关联教师档案，不能查看该课次的调课候选。") from exc

    if SessionTeacher.objects.filter(session=session, teacher=profile).exists():
        return profile
    if TeachingAssignment.objects.filter(offering=session.offering, teacher=profile).exists():
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
        409: RescheduleOptionsErrorSerializer,
    },
)
@api_view(["POST"])
@authentication_classes([SessionAuthentication])
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
