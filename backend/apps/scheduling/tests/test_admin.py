"""Admin behaviour tests (B11 acceptance item 8)."""

from __future__ import annotations

from datetime import date

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.cookie import CookieStorage
from django.test import RequestFactory

from apps.academics.admin import TermAdmin
from apps.academics.models import CalendarException, Term
from apps.scheduling.admin import ClassSessionAdmin
from apps.scheduling.models import ClassSession
from apps.scheduling.services import generate_sessions_for_term
from apps.scheduling.tests import factories

pytestmark = pytest.mark.django_db


class TestClassSessionAdmin:
    def test_admin_is_view_only(self) -> None:
        model_admin = ClassSessionAdmin(ClassSession, AdminSite())
        request = RequestFactory().get("/admin/scheduling/classsession/")

        assert model_admin.has_change_permission(request) is False
        assert model_admin.has_add_permission(request) is False
        assert model_admin.has_delete_permission(request) is False

    def test_change_page_renders_read_only(self, client, demo_term) -> None:
        generate_sessions_for_term(demo_term.term)
        user = get_user_model().objects.create_superuser(email="admin@example.com", password="admin-pass-9x")
        client.force_login(user)
        session = ClassSession.objects.filter(offering__term=demo_term.term).first()

        response = client.get(f"/admin/scheduling/classsession/{session.pk}/change/")

        assert response.status_code == 200


class TestTermAdminAction:
    def test_action_generates_sessions_and_reports_counts(self, demo_term) -> None:
        model_admin = TermAdmin(Term, AdminSite())
        request = RequestFactory().post("/admin/academics/term/")
        request._messages = CookieStorage(request)

        model_admin.generate_sessions(request, Term.objects.filter(pk=demo_term.term.pk))

        assert ClassSession.objects.filter(offering__term=demo_term.term).count() == 40
        rendered = [str(message) for message in request._messages]
        assert any("新建 40" in message for message in rendered)
        assert any("需人工核对 0" in message for message in rendered)

    def test_action_reports_calendar_validation_failure(self, demo_term) -> None:
        CalendarException.objects.filter(term=demo_term.term).delete()
        factories.make_exception(
            demo_term.term,
            date=date(2026, 10, 6),
            kind="replace",
            source_teaching_date=date(2026, 10, 5),
        )
        factories.make_exception(
            demo_term.term,
            date=date(2026, 10, 7),
            kind="replace",
            source_teaching_date=date(2026, 10, 6),
        )
        model_admin = TermAdmin(Term, AdminSite())
        request = RequestFactory().post("/admin/academics/term/")
        request._messages = CookieStorage(request)

        model_admin.generate_sessions(request, Term.objects.filter(pk=demo_term.term.pk))

        assert ClassSession.objects.filter(offering__term=demo_term.term).count() == 0
        rendered = [str(message) for message in request._messages]
        assert any("生成失败" in message for message in rendered)
        assert any("禁止替换链" in message for message in rendered)
