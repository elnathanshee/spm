"""Deploying on Vercel: the backend's two Vercel-only behaviours.

Vercel sets VERCEL=1 in every deployment. Locally and in CI it is unset, so
nothing here changes how the app runs on a laptop or in these tests.

TC-9901: on Vercel, a missing DATABASE_URL is a clear error. The filesystem
there is read-only, so the local fallback (a SQLite file, dev.db) can't work.
TC-9902: on Vercel the site is HTTPS only, so the login cookie is Secure.
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.database import Base, database_url, get_db
from backend.main import app
from backend.models import User
from backend.security import hash_password

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
TEST_PASSWORD = "pass123"


@pytest.fixture
def client():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    session.add(User(username="venue1", password_hash=hash_password(TEST_PASSWORD), role="venue staff"))
    session.commit()
    session.close()

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def test_tc9901_database_url_on_vercel_and_locally(monkeypatch):
    """TC-9901: on Vercel a missing DATABASE_URL raises an error naming it;
    a set one is used (postgres:// normalised). Locally, the dev.db fallback
    and the normalisation are unchanged."""
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        database_url()
    monkeypatch.setenv("DATABASE_URL", "")
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        database_url()
    monkeypatch.setenv("DATABASE_URL", "postgres://user:secret@example.test/connectsphere")
    assert database_url() == "postgresql://user:secret@example.test/connectsphere"

    monkeypatch.delenv("VERCEL")
    monkeypatch.delenv("DATABASE_URL")
    assert database_url() == "sqlite:///./dev.db"
    monkeypatch.setenv("DATABASE_URL", "postgres://user:secret@example.test/connectsphere")
    assert database_url() == "postgresql://user:secret@example.test/connectsphere"


def session_cookie_header(response):
    headers = [h for h in response.headers.get_list("set-cookie") if h.startswith("connectsphere_session=")]
    assert len(headers) == 1, headers
    return headers[0].lower()


def test_tc9902_login_cookie_is_secure_only_on_vercel(client, monkeypatch):
    """TC-9902: on Vercel, login and logout set the session cookie with Secure;
    locally they don't, and the cookie is still HttpOnly, SameSite=Lax, path /."""
    login = {"username": "venue1", "password": TEST_PASSWORD}

    monkeypatch.delenv("VERCEL", raising=False)
    local = session_cookie_header(client.post("/auth/login", json=login))
    assert "secure" not in local
    for attribute in ["httponly", "samesite=lax", "path=/"]:
        assert attribute in local, attribute
    assert "secure" not in session_cookie_header(client.post("/auth/logout"))

    monkeypatch.setenv("VERCEL", "1")
    response = client.post("/auth/login", json=login)
    assert response.status_code == 200
    on_vercel = session_cookie_header(response)
    for attribute in ["secure", "httponly", "samesite=lax", "path=/"]:
        assert attribute in on_vercel, attribute
    assert "secure" in session_cookie_header(client.post("/auth/logout"))
