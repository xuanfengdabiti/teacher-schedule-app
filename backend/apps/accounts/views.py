"""Mobile account endpoints not owned by dj-rest-auth."""

from __future__ import annotations

from dj_rest_auth.views import LogoutView, PasswordChangeView
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.serializers import (
    DetailSerializer,
    LogoutRequestSerializer,
    MeSerializer,
    MeUpdateSerializer,
    PasswordChangeResponseSerializer,
)
from apps.accounts.services import deactivate_user, teacher_profile_for, teacher_status_for
from apps.common.exceptions import teacher_disabled_response, teacher_not_linked_response


@extend_schema_view(
    get=extend_schema(exclude=True),
    post=extend_schema(
        summary="退出登录",
        description="将请求体中的 refresh token 加入 SimpleJWT blacklist。",
        request=LogoutRequestSerializer,
        responses={200: DetailSerializer},
    ),
)
class LogoutAPIView(LogoutView):
    """dj-rest-auth logout, restricted to an authenticated access token."""

    permission_classes = (IsAuthenticated,)
    serializer_class = LogoutRequestSerializer


class PasswordChangeAPIView(PasswordChangeView):
    """Password change with a response that tells the App to sign in again."""

    @extend_schema(
        summary="修改密码",
        description="修改成功后撤销该用户所有 refresh token，客户端必须重新登录。",
        responses={200: PasswordChangeResponseSerializer},
    )
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK:
            response.data = {
                "detail": "密码已修改，请重新登录。",
                "reauthentication_required": True,
            }
        return response


class MeView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        summary="当前账号资料",
        description=(
            "返回邮箱验证状态、教师关联状态、任课范围版本、当前学期以及本人联系方式可见范围。"
            "教师档案未关联时 teacher 为 null。"
        ),
        responses={200: MeSerializer},
    )
    def get(self, request):
        return Response(MeSerializer(request.user).data)

    @extend_schema(
        summary="修改本人教师资料",
        description=(
            "只允许修改显示名、院系、电话/微信及其可见范围。工号、正式姓名、邮箱和任课关系只读；"
            "提交这些字段会返回 400。未关联教师档案返回 400 TEACHER_NOT_LINKED，"
            "教师档案停用返回 403 TEACHER_DISABLED。"
        ),
        request=MeUpdateSerializer,
        responses={200: MeSerializer},
    )
    def patch(self, request):
        teacher_status = teacher_status_for(request.user)
        if teacher_status == "disabled":
            return Response(
                teacher_disabled_response("教师档案已停用，不能修改教师资料。"),
                status=status.HTTP_403_FORBIDDEN,
            )

        profile = teacher_profile_for(request.user)
        if profile is None or teacher_status != "active":
            return Response(
                teacher_not_linked_response("当前账号未关联教师档案，不能修改教师资料。"),
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = MeUpdateSerializer(profile, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {
                    "code": "VALIDATION_ERROR",
                    "detail": "提交的资料有误。",
                    "field_errors": serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if serializer.validated_data:
            for field, value in serializer.validated_data.items():
                setattr(profile, field, value)
            profile.save(update_fields=[*serializer.validated_data.keys(), "updated_at"])
        return Response(MeSerializer(request.user).data)


class DeactivationView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        summary="停用本人账号",
        description="立即停用账号、撤销全部 refresh token，并清空本人教师档案的自愿联系方式。历史课次保留。",
        request=None,
        responses={204: None},
    )
    def post(self, request):
        deactivate_user(request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
