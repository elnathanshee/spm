import os
import sys
from datetime import datetime, timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Setup path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.database import Base, get_db
from backend.main import app
from backend.models import CoordinatorAssignmentHistory, Event, EventStatusHistory, LoginSession, User
from backend.security import hash_password
from backend.status import DRAFT, SUBMITTED

# Use an in-memory SQLite database with StaticPool for tests
TEST_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="function")
def db_session():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(db_session):
    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def seed_data(db_session):
    organiser = User(
        username="org_test",
        password_hash=hash_password("pass123"),
        role="organiser",
        is_active_coordinator=False,
    )
    coord1 = User(
        username="coord1_test",
        password_hash=hash_password("pass123"),
        role="coordinator",
        is_active_coordinator=True,
    )
    coord2 = User(
        username="coord2_test",
        password_hash=hash_password("pass123"),
        role="coordinator",
        is_active_coordinator=True,
    )
    coord_inactive = User(
        username="coord_inactive_test",
        password_hash=hash_password("pass123"),
        role="coordinator",
        is_active_coordinator=False,
    )
    db_session.add_all([organiser, coord1, coord2, coord_inactive])
    db_session.commit()
    return {
        "organiser": organiser,
        "coord1": coord1,
        "coord2": coord2,
        "coord_inactive": coord_inactive,
    }


def test_auth_login_empty_fields(client, seed_data):
    # Empty username/password
    response = client.post("/auth/login", json={"username": "", "password": ""})
    assert response.status_code == 422 or response.status_code == 400


def test_auth_login_invalid_credentials(client, seed_data):
    # Wrong password
    response = client.post("/auth/login", json={"username": "org_test", "password": "wrongpassword"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"

    # Non-existent user
    response = client.post("/auth/login", json={"username": "unknown_user", "password": "pass123"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"


def test_auth_login_and_logout_success(client, seed_data):
    # Valid login
    response = client.post("/auth/login", json={"username": "org_test", "password": "pass123"})
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Login successful"
    assert data["role"] == "organiser"
    assert data["redirect_url"] == "/events"
    assert "connectsphere_session" in response.cookies

    # Logout
    logout_response = client.post("/auth/logout")
    assert logout_response.status_code == 200
    assert logout_response.json()["message"] == "Logged out successfully"


def test_event_draft_creation_and_validation(client, seed_data):
    # Login as organiser
    client.post("/auth/login", json={"username": "org_test", "password": "pass123"})

    # Invalid proposed_start (in past)
    past_time = (datetime.utcnow() - timedelta(days=1)).isoformat()
    resp_past = client.post(
        "/events",
        json={
            "title": "Past Event",
            "proposed_start": past_time,
            "expected_attendance": 50,
        },
    )
    assert resp_past.status_code == 400
    assert "past" in resp_past.json()["detail"]

    # Invalid attendance (<= 0)
    future_time = (datetime.utcnow() + timedelta(days=10)).isoformat()
    resp_att = client.post(
        "/events",
        json={
            "title": "Invalid Attendance Event",
            "proposed_start": future_time,
            "expected_attendance": 0,
        },
    )
    assert resp_att.status_code == 400
    assert "Expected attendance must be greater than 0" in resp_att.json()["detail"]

    # Valid draft creation
    resp_valid = client.post(
        "/events",
        json={
            "title": "Annual Gala",
            "purpose": "Charity fundraiser",
            "description": "Annual gala event with auctions",
            "proposed_start": future_time,
            "expected_attendance": 150,
            "venue_requirements": "Main Hall",
        },
    )
    assert resp_valid.status_code == 201
    data = resp_valid.json()
    assert data["title"] == "Annual Gala"
    assert data["status"] == DRAFT
    assert data["assigned_coordinator_id"] is None


def test_event_submit_and_round_robin_assignment(client, seed_data, db_session):
    # Login as organiser
    client.post("/auth/login", json={"username": "org_test", "password": "pass123"})

    future_time = (datetime.utcnow() + timedelta(days=10)).isoformat()

    # Create event 1
    resp1 = client.post("/events", json={"title": "Event 1", "proposed_start": future_time, "expected_attendance": 50})
    assert resp1.status_code == 201
    event1_id = resp1.json()["id"]

    # Create event 2
    resp2 = client.post("/events", json={"title": "Event 2", "proposed_start": future_time, "expected_attendance": 80})
    assert resp2.status_code == 201
    event2_id = resp2.json()["id"]

    # Submit event 1 -> assigned to first active coordinator (coord1)
    sub1 = client.post(f"/events/{event1_id}/submit")
    assert sub1.status_code == 200
    data1 = sub1.json()
    assert data1["status"] == SUBMITTED
    assigned_1 = data1["assigned_coordinator_id"]
    assert assigned_1 in [seed_data["coord1"].id, seed_data["coord2"].id]

    # Submit event 2 -> round-robin assigns to the other active coordinator (coord2)
    sub2 = client.post(f"/events/{event2_id}/submit")
    assert sub2.status_code == 200
    data2 = sub2.json()
    assert data2["status"] == SUBMITTED
    assigned_2 = data2["assigned_coordinator_id"]
    assert assigned_2 in [seed_data["coord1"].id, seed_data["coord2"].id]
    assert assigned_1 != assigned_2  # Different coordinators assigned!


def test_submit_fails_when_no_coordinator_available(client, db_session):
    # Create organiser but NO active coordinator
    organiser = User(
        username="org_alone",
        password_hash=hash_password("pass123"),
        role="organiser",
        is_active_coordinator=False,
    )
    db_session.add(organiser)
    db_session.commit()

    client.post("/auth/login", json={"username": "org_alone", "password": "pass123"})
    future_time = (datetime.utcnow() + timedelta(days=5)).isoformat()

    resp = client.post("/events", json={"title": "Orphan Event", "proposed_start": future_time, "expected_attendance": 20})
    assert resp.status_code == 201
    event_id = resp.json()["id"]

    sub = client.post(f"/events/{event_id}/submit")
    assert sub.status_code == 400
    assert "No active coordinator available" in sub.json()["detail"]
