"""Tests for ``config.settings.parse_database_url``."""

from __future__ import annotations

from config.settings import parse_database_url


class TestParseDatabaseURL:
    """Unit tests for the DATABASE_URL parser."""

    def test_full_url(self) -> None:
        result = parse_database_url("postgres://user:pass@host:5432/dbname")
        assert result == {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "dbname",
            "USER": "user",
            "PASSWORD": "pass",
            "HOST": "host",
            "PORT": "5432",
        }

    def test_default_port(self) -> None:
        """When port is absent, 5432 is used."""
        result = parse_database_url("postgres://user:pass@host/dbname")
        assert result["PORT"] == "5432"

    def test_password_with_special_chars(self) -> None:
        """URL-encoded special characters in password are decoded."""
        # %40 = @, %3A = :
        result = parse_database_url("postgres://user:p%40ss%3Aw0rd@host:5432/db")
        assert result["PASSWORD"] == "p@ss:w0rd"

    def test_postgresql_scheme(self) -> None:
        """postgresql:// scheme works the same as postgres://."""
        result = parse_database_url("postgresql://u:p@h:5432/db")
        assert result["ENGINE"] == "django.db.backends.postgresql"
        assert result["NAME"] == "db"

    def test_empty_user(self) -> None:
        result = parse_database_url("postgres://:pass@host:5432/db")
        assert result["USER"] == ""

    def test_empty_password(self) -> None:
        result = parse_database_url("postgres://user@host:5432/db")
        assert result["PASSWORD"] == ""

    def test_percent_encoded_slash_in_password(self) -> None:
        """%2F (/) in password is decoded correctly."""
        result = parse_database_url("postgres://u:pa%2Fss@h:5432/db")
        assert result["PASSWORD"] == "pa/ss"
