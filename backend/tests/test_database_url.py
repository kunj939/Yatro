"""Hosted databases hand out postgres:// or postgresql:// URLs; the app's driver is psycopg 3."""
import pytest

from app.core.config import Settings


@pytest.mark.parametrize("given,expected", [
    ("postgres://u:p@host/db?sslmode=require", "postgresql+psycopg://u:p@host/db?sslmode=require"),
    ("postgresql://u:p@host:5432/db", "postgresql+psycopg://u:p@host:5432/db"),
    ("postgresql+psycopg://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
    ("sqlite:///./tripai.db", "sqlite:///./tripai.db"),
])
def test_database_url_gets_the_installed_driver(given, expected):
    assert Settings(database_url=given).database_url == expected
