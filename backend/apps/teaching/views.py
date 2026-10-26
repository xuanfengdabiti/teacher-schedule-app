"""HTTP endpoints for private drafts and published teaching notes (B20)."""

from __future__ import annotations

from uuid import UUID

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.services import require_active_teacher
from apps.teaching.exceptions import TeachingNoteError
from apps.teaching.selectors import (
    get_session_for_notes,
    previous_completed_session,
    published_notes_for_session,
    require_note_read_access,
    require_note_write_access,
    session_notes,
)
from apps.teaching.serializers import (
    MineNoteSerializer,
    NoteDraftWriteSerializer,
    NoteErrorSerializer,
    NotePublishSerializer,
    SessionNotesResponseSerializer,
    mine_payload,
    previous_note_payload,
    previous_session_payload,
    shared_payload,
)
from apps.teaching.services import publish, save_draft


def _error_response(exc: TeachingNoteError) -> Response:
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


def _notes_payload(teacher, session) -> dict:
    notes = session_notes(session)
    mine = next((note for note in notes if note.author_id == teacher.id), None)
    others = [shared_payload(note) for note in notes if note.author_id != teacher.id and note.published_at is not None]

    previous = None
    previous_session = previous_completed_session(session)
    if previous_session is not None:
        previous = {
            "session": previous_session_payload(previous_session),
            "notes": [previous_note_payload(note) for note in published_notes_for_session(previous_session)],
        }

    return {
        "mine": mine_payload(mine) if mine is not None else None,
        "others": others,
        "previous": previous,
    }


class SessionNotesView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        summary="课次教学备注",
        description=(
            "mine 仅返回本人备注（含草稿）；others 只返回该课次其他教师已发布的快照，"
            "任何响应都不包含他人的 draft。previous 在同一 CourseOffering 中按课次实际 "
            "start_at 倒序，选择早于当前课次且 status=completed、至少有一条已发布备注的最近课次；"
            "不按教学班或课程名称跨 offering 共享。仅该课次授课教师或该 offering 当前有效任课教师可读。"
        ),
        responses={
            200: SessionNotesResponseSerializer,
            403: NoteErrorSerializer,
            404: NoteErrorSerializer,
        },
    )
    def get(self, request: Request, session_id: UUID) -> Response:
        teacher = require_active_teacher(request.user)
        try:
            session = get_session_for_notes(session_id)
            require_note_read_access(teacher, session)
        except TeachingNoteError as exc:
            return _error_response(exc)
        return Response(_notes_payload(teacher, session))


class MyNoteView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        summary="保存本人备注草稿",
        description=(
            "只更新本人该课次的 draft_*，不改变 published_* 快照、课次状态或学时。"
            "不存在备注时 base_version 必须为 0；已存在时必须等于当前 version，否则返回 "
            "409 NOTE_VERSION_CONFLICT 并附 current。client_mutation_id 必填；同一账号同 key "
            "同请求返回原响应，不同请求返回 409 MUTATION_KEY_REUSED。仅该课次实际授课教师可写。"
        ),
        request=NoteDraftWriteSerializer,
        responses={
            200: MineNoteSerializer,
            400: NoteErrorSerializer,
            403: NoteErrorSerializer,
            404: NoteErrorSerializer,
            409: NoteErrorSerializer,
        },
    )
    def put(self, request: Request, session_id: UUID) -> Response:
        teacher = require_active_teacher(request.user)
        try:
            session = get_session_for_notes(session_id)
            require_note_write_access(teacher, session)
        except TeachingNoteError as exc:
            return _error_response(exc)

        serializer = NoteDraftWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_error(serializer)
        payload = serializer.validated_data
        try:
            response = save_draft(
                teacher,
                session,
                covered=payload["covered"],
                unfinished=payload["unfinished"],
                next_plan=payload["next_plan"],
                base_version=payload["base_version"],
                client_mutation_id=payload["client_mutation_id"],
                now=timezone.now(),
            )
        except TeachingNoteError as exc:
            return _error_response(exc)
        return Response(response, status=status.HTTP_200_OK)


class MyNotePublishView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        summary="发布本人备注",
        description=(
            "把当前 draft_* 复制为 published_* 快照，published_at=服务器时间、"
            "published_version=新 version，并写 note ChangeLog。没有本人备注或三段草稿去掉首尾"
            "空白后都为空时返回 409 NOTHING_TO_PUBLISH，不创建备注/去重记录/日志。版本和幂等规则"
            "与保存草稿相同；发布不改变课次完成状态或学时。"
        ),
        request=NotePublishSerializer,
        responses={
            200: MineNoteSerializer,
            400: NoteErrorSerializer,
            403: NoteErrorSerializer,
            404: NoteErrorSerializer,
            409: NoteErrorSerializer,
        },
    )
    def post(self, request: Request, session_id: UUID) -> Response:
        teacher = require_active_teacher(request.user)
        try:
            session = get_session_for_notes(session_id)
            require_note_write_access(teacher, session)
        except TeachingNoteError as exc:
            return _error_response(exc)

        serializer = NotePublishSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_error(serializer)
        payload = serializer.validated_data
        try:
            response = publish(
                teacher,
                session,
                base_version=payload["base_version"],
                client_mutation_id=payload["client_mutation_id"],
                now=timezone.now(),
            )
        except TeachingNoteError as exc:
            return _error_response(exc)
        return Response(response, status=status.HTTP_200_OK)
