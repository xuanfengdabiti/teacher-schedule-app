"""Teacher contact endpoint (B32).

The endpoint never returns a hidden number.  ``phone_status``/``wechat_status``
distinguish "not provided" from "provided but not visible to you" so the App can
show the right message (domain-design 7).
"""

from __future__ import annotations

from uuid import UUID

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.contacts import teacher_contact_queryset, visible_contact_payload
from apps.academics.serializers import ContactErrorSerializer, TeacherContactSerializer
from apps.accounts.services import require_active_teacher


class TeacherContactView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="teachers_contact",
        summary="教师联系方式",
        description=(
            "按当前查看人计算电话和微信的可见范围。private 不返回号码；course_teachers 需要"
            "查看人与目标教师同为一个 offering 的当前有效任课教师，或同为某一课次的 "
            "SessionTeacher；school_teachers 对本校教师可见。本人看自己全部可见；停用档案或"
            "停用账号的目标教师一律不可见。未填写返回 not_provided，填写但不可见返回 hidden。"
            "其他学校教师 404。"
        ),
        responses={200: TeacherContactSerializer, 403: ContactErrorSerializer, 404: None},
    )
    def get(self, request: Request, teacher_id: UUID) -> Response:
        viewer = require_active_teacher(request.user)
        target = get_object_or_404(
            teacher_contact_queryset(viewer.school_id).select_related("user"),
            pk=teacher_id,
        )
        return Response(TeacherContactSerializer(visible_contact_payload(viewer, target)).data)
