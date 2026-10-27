"""Mobile account endpoints not owned by dj-rest-auth."""

from __future__ import annotations

import mimetypes

from dj_rest_auth.views import LogoutView, PasswordChangeView
from django.http import FileResponse
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.preferences import preferences_for, school_for
from apps.accounts.serializers import (
    AvatarSerializer,
    AvatarUploadSerializer,
    DetailSerializer,
    LogoutRequestSerializer,
    MeSerializer,
    MeUpdateSerializer,
    PasswordChangeResponseSerializer,
    PreferencesSerializer,
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
        return Response(MeSerializer(request.user, context={"request": request}).data)

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
        return Response(MeSerializer(request.user, context={"request": request}).data)


class PreferencesView(APIView):
    """Read and partially update the account's own settings.

    Only ``IsAuthenticated`` is required: a pending or unlinked account must be
    able to choose font size and reminders before its teacher profile exists.
    Disabled accounts are rejected, matching the rest of the account API.
    """

    permission_classes = (IsAuthenticated,)

    @extend_schema(
        summary="本人偏好设置",
        description=(
            "返回字号、本地上课提醒、首选学期和远程推送开关。未保存过时返回默认值"
            "（字号 system、提醒开启且提前 15 分钟、推送及四个类别均开启、首选学期 null）。"
            "pending 账号也可读取和修改。"
        ),
        responses={200: PreferencesSerializer, 403: None},
    )
    def get(self, request):
        if teacher_status_for(request.user) == "disabled":
            return Response(
                teacher_disabled_response("账号或教师档案已停用，不能读取个人设置。"),
                status=status.HTTP_403_FORBIDDEN,
            )
        preferences = preferences_for(request.user)
        return Response(PreferencesSerializer(preferences).data)

    @extend_schema(
        summary="修改本人偏好设置",
        description=(
            "部分更新：只修改提交的字段，未提交项保持原值。首选学期用 preferred_term_id 提交，"
            "必须是本校已有学期；传 null 清除。字号为 system/large/extra_large。"
            "本地提醒提前分钟数为 0–180。"
        ),
        request=PreferencesSerializer,
        responses={200: PreferencesSerializer, 400: None, 403: None},
    )
    def patch(self, request):
        if teacher_status_for(request.user) == "disabled":
            return Response(
                teacher_disabled_response("账号或教师档案已停用，不能修改个人设置。"),
                status=status.HTTP_403_FORBIDDEN,
            )
        preferences = preferences_for(request.user)
        serializer = PreferencesSerializer(
            preferences,
            data=request.data,
            partial=True,
            context={"school": school_for(request.user)},
        )
        if not serializer.is_valid():
            return Response(
                {
                    "code": "VALIDATION_ERROR",
                    "detail": "提交的设置项有误。",
                    "field_errors": serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer.save()
        return Response(serializer.data)


class AvatarView(APIView):
    """Authenticated avatar upload, download, replacement and deletion.

    ``MEDIA_ROOT`` also holds private import source files, so it is never
    routed as a static directory.  The only read path is this authenticated
    view, which streams the current user's own image.
    """

    permission_classes = (IsAuthenticated,)
    parser_classes = (MultiPartParser, FormParser)

    def _avatar_response(self, request) -> Response:
        avatar_url = MeSerializer(request.user, context={"request": request}).get_avatar_url(request.user)
        return Response(AvatarSerializer({"avatar_url": avatar_url}).data)

    @extend_schema(
        summary="读取本人头像",
        description="已认证用户读取本人当前头像；没有头像返回 404。不公开 MEDIA_ROOT。",
        responses={(200, "image/*"): bytes, 404: None},
        filters=False,
    )
    def get(self, request):
        avatar = request.user.avatar
        if not avatar:
            return Response(
                {"code": "NOT_FOUND", "detail": "尚未设置头像。", "field_errors": {}},
                status=status.HTTP_404_NOT_FOUND,
            )
        content_type = mimetypes.guess_type(avatar.name)[0] or "application/octet-stream"
        response = FileResponse(avatar.open("rb"), content_type=content_type)
        response["Cache-Control"] = "private, max-age=86400"
        return response

    @extend_schema(
        summary="上传或替换本人头像",
        description="multipart 字段 avatar；支持 JPEG/PNG/WebP，最大 2 MB。替换成功会删除旧文件。",
        request=AvatarUploadSerializer,
        responses={200: AvatarSerializer, 400: None, 403: None},
    )
    def post(self, request):
        if teacher_status_for(request.user) == "disabled":
            return Response(
                teacher_disabled_response("账号或教师档案已停用，不能修改头像。"),
                status=status.HTTP_403_FORBIDDEN,
            )
        upload = AvatarUploadSerializer(data=request.data)
        if not upload.is_valid():
            return Response(
                {
                    "code": "VALIDATION_ERROR",
                    "detail": "头像文件不符合要求。",
                    "field_errors": upload.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        old_name = request.user.avatar.name if request.user.avatar else ""
        old_storage = request.user.avatar.storage if request.user.avatar else None
        request.user.avatar = upload.validated_data["avatar"]
        request.user.save(update_fields=["avatar"])
        if old_name and old_storage is not None and old_name != request.user.avatar.name:
            old_storage.delete(old_name)
        return self._avatar_response(request)

    @extend_schema(
        summary="删除本人头像",
        description="幂等：没有头像时同样返回 204，并清理已存储文件。",
        responses={204: None},
    )
    def delete(self, request):
        avatar = request.user.avatar
        if avatar:
            storage = avatar.storage
            name = avatar.name
            request.user.avatar = None
            request.user.save(update_fields=["avatar"])
            storage.delete(name)
        return Response(status=status.HTTP_204_NO_CONTENT)


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
