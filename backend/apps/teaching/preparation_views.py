"""HTTP endpoints for the private AI lesson-preparation draft."""

from __future__ import annotations

from uuid import UUID

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.services import require_active_teacher
from apps.teaching.exceptions import LessonPreparationError, TeachingNoteError
from apps.teaching.lesson_preparation import (
    ai_available,
    current_preparation,
    generate_preparation,
    lesson_context,
    preparation_payload,
    save_preparation,
)
from apps.teaching.preparation_serializers import (
    PreparationDraftSerializer,
    PreparationErrorSerializer,
    PreparationGenerateRequestSerializer,
    PreparationGenerateResponseSerializer,
    PreparationResponseSerializer,
    PreparationSaveRequestSerializer,
)
from apps.teaching.selectors import (
    get_session_for_notes,
    require_note_write_access,
)


def _validation_error(serializer) -> Response:
    return Response(
        {
            "code": "VALIDATION_ERROR",
            "detail": "请求参数有误。",
            "field_errors": {
                field: [str(message) for message in messages] for field, messages in serializer.errors.items()
            },
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


def _error_response(exc: LessonPreparationError | TeachingNoteError) -> Response:
    return Response(exc.as_dict(), status=exc.status_code)


def _own_session(request: Request, session_id: UUID):
    """Only the session's actual teacher may prepare lessons for it."""
    teacher = require_active_teacher(request.user)
    session = get_session_for_notes(session_id)
    require_note_write_access(teacher, session)
    return teacher, session


class PreparationView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        summary="读取本节备课",
        description=(
            "返回课程/日期节次/本节授课分钟、上一备注摘要、当前开课未归档计划专题，"
            "以及本人已保存的备课草稿（没有则为 null）。授课分钟只按计划学时×每学时分钟数计算。"
        ),
        responses={
            200: PreparationResponseSerializer,
            403: PreparationErrorSerializer,
            404: PreparationErrorSerializer,
        },
    )
    def get(self, request: Request, session_id: UUID) -> Response:
        try:
            teacher, session = _own_session(request, session_id)
        except TeachingNoteError as exc:
            return _error_response(exc)
        prep = current_preparation(teacher, session)
        return Response(
            {
                "context": lesson_context(teacher, session),
                "draft": preparation_payload(prep) if prep is not None else None,
                "ai_available": ai_available(),
            }
        )

    @extend_schema(
        summary="保存本节备课",
        description=(
            "保存本人该课次的备课草稿。没有草稿时 base_version 为 0；已有草稿时必须等于当前 "
            "version，否则返回 409 PREPARATION_VERSION_CONFLICT 并附 current。保存不会修改"
            "教学备注、课次状态、学时、交接或正式课表。"
        ),
        request=PreparationSaveRequestSerializer,
        responses={
            200: PreparationDraftSerializer,
            400: PreparationErrorSerializer,
            403: PreparationErrorSerializer,
            404: PreparationErrorSerializer,
            409: PreparationErrorSerializer,
        },
    )
    def put(self, request: Request, session_id: UUID) -> Response:
        try:
            teacher, session = _own_session(request, session_id)
        except TeachingNoteError as exc:
            return _error_response(exc)

        serializer = PreparationSaveRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_error(serializer)
        payload = serializer.validated_data
        try:
            draft = save_preparation(
                teacher,
                session,
                base_version=payload["base_version"],
                topic=payload["topic"],
                outline=payload["outline"],
                case_study=payload["case_study"],
                discussion_questions=payload["discussion_questions"],
                preparation=payload["preparation"],
                generated_model=payload.get("generated_model", ""),
                generated_at=payload.get("generated_at"),
            )
        except LessonPreparationError as exc:
            return _error_response(exc)
        return Response(draft)


class PreparationGenerateView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        summary="生成备课内容",
        description=(
            "按本节主题调用部署配置的兼容 chat completions 服务，返回四段可编辑内容。"
            "生成不会保存草稿，也不修改教学备注、学时或正式课表。未配置密钥、超时、"
            "服务失败或返回缺少字段时返回可读错误，不使用固定样例冒充结果。"
        ),
        request=PreparationGenerateRequestSerializer,
        responses={
            200: PreparationGenerateResponseSerializer,
            400: PreparationErrorSerializer,
            403: PreparationErrorSerializer,
            404: PreparationErrorSerializer,
            502: PreparationErrorSerializer,
            503: PreparationErrorSerializer,
            504: PreparationErrorSerializer,
        },
    )
    def post(self, request: Request, session_id: UUID) -> Response:
        try:
            teacher, session = _own_session(request, session_id)
        except TeachingNoteError as exc:
            return _error_response(exc)

        serializer = PreparationGenerateRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_error(serializer)
        try:
            generated = generate_preparation(
                teacher,
                session,
                serializer.validated_data["topic"],
            )
        except LessonPreparationError as exc:
            return _error_response(exc)
        return Response(generated, status=status.HTTP_200_OK)
