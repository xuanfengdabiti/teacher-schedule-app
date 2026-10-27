"""Query-count guards for the B13 schedule read APIs."""

from __future__ import annotations

from datetime import date

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.academics.models import TeacherStatus
from apps.scheduling.models import ClassSession
from apps.scheduling.services import generate_sessions_for_term

pytestmark = pytest.mark.django_db


@pytest.fixture
def generated_demo(demo_term):
    generate_sessions_for_term(demo_term.term)
    return demo_term


def _client_for(teacher) -> APIClient:
    user = get_user_model().objects.create_user(
        email=f"{teacher.teacher_no}@query-count.invalid", password="pass-12345"
    )
    teacher.user = user
    teacher.teacher_status = TeacherStatus.ACTIVE
    teacher.save(update_fields=["user", "teacher_status", "updated_at"])
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
    return client


def _sessions_url(*, date_from: date, date_to: date, teaching_class_id) -> str:
    return (
        f"/api/v1/sessions/?from={date_from.isoformat()}&to={date_to.isoformat()}"
        f"&scope=teaching_class&teaching_class_id={teaching_class_id}"
    )


def test_one_week_and_six_weeks_use_the_same_number_of_queries(
    generated_demo,
    django_assert_max_num_queries,
) -> None:
    # Budget includes the one batched query for `previous_note_preview`; the
    # number of cards must not add further queries.
    client = _client_for(generated_demo.teachers["0002"])
    teaching_class_id = generated_demo.offerings["OFF-DATA-2026A"].teaching_class_id

    with django_assert_max_num_queries(7) as one_week:
        one_week_response = client.get(
            _sessions_url(
                date_from=date(2026, 10, 26),
                date_to=date(2026, 11, 2),
                teaching_class_id=teaching_class_id,
            )
        )
    with django_assert_max_num_queries(7) as six_weeks:
        six_weeks_response = client.get(
            _sessions_url(
                date_from=date(2026, 10, 5),
                date_to=date(2026, 11, 16),
                teaching_class_id=teaching_class_id,
            )
        )

    assert one_week_response.status_code == 200
    assert six_weeks_response.status_code == 200
    assert six_weeks_response.data["results"]  # type: ignore[index]
    assert len(one_week.captured_queries) == len(six_weeks.captured_queries)
    print(
        f"\nB13 sessions query counts: 1 week={len(one_week.captured_queries)}, "
        f"6 weeks={len(six_weeks.captured_queries)}"
    )


def test_each_schedule_read_endpoint_has_a_constant_query_budget(
    generated_demo,
    django_assert_max_num_queries,
) -> None:
    client = _client_for(generated_demo.teachers["0002"])
    term = generated_demo.term
    session = ClassSession.objects.get(
        rule=generated_demo.rules["R-CRIME-EVEN"],
        origin_teaching_date=date(2026, 10, 26),
    )

    with django_assert_max_num_queries(3) as terms_queries:
        terms_response = client.get("/api/v1/terms/")
    with django_assert_max_num_queries(5) as calendar_queries:
        calendar_response = client.get(f"/api/v1/terms/{term.id}/calendar/")
    with django_assert_max_num_queries(6) as detail_queries:
        detail_response = client.get(f"/api/v1/sessions/{session.id}/")
    with django_assert_max_num_queries(6) as offerings_queries:
        offerings_response = client.get("/api/v1/offerings/")

    assert terms_response.status_code == 200
    assert calendar_response.status_code == 200
    assert detail_response.status_code == 200
    assert offerings_response.status_code == 200
    print(
        "\nB13 query counts: "
        f"terms={len(terms_queries.captured_queries)}, "
        f"calendar={len(calendar_queries.captured_queries)}, "
        f"detail={len(detail_queries.captured_queries)}, "
        f"offerings={len(offerings_queries.captured_queries)}"
    )
    assert len(terms_queries.captured_queries) <= 3
    assert len(calendar_queries.captured_queries) <= 5
    assert len(detail_queries.captured_queries) <= 6
    assert len(offerings_queries.captured_queries) <= 6
