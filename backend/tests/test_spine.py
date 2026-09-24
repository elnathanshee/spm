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
from backend.status import APPROVED, DRAFT, REJECTED, RETURNED, SUBMITTED

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


def test_tc001_login_valid_credentials(client, seed_data):
    """TC-001 (SCRUM-16 AC1): Log in with valid credentials lands user on role landing view."""
    response = client.post("/auth/login", json={"username": "coord1_test", "password": "pass123"})
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Login successful"
    assert data["role"] == "coordinator"
    assert data["redirect_url"] == "/coordinator/events"
    assert "connectsphere_session" in response.cookies


def test_tc002_login_wrong_password(client, seed_data):
    """TC-002 (SCRUM-16 AC3): Log in with correct username and wrong password is refused with generic message."""
    response = client.post("/auth/login", json={"username": "coord1_test", "password": "WrongPass1"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"


def test_tc003_login_unknown_user(client, seed_data):
    """TC-003 (SCRUM-16 AC4): Log in with unknown user is refused with the SAME message as TC-002."""
    response = client.post("/auth/login", json={"username": "nosuchuser", "password": "Test1234!"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"


def test_tc004_login_empty_fields(client, seed_data):
    """TC-004 (SCRUM-16 AC5): Submit login form with empty fields is refused before any credential check."""
    response = client.post("/auth/login", json={"username": "", "password": ""})
    assert response.status_code in [400, 422]


def test_tc005_logout_ends_session(client, seed_data):
    """TC-005 (SCRUM-16 AC2): Log out ends session; accessing protected endpoint requires logging in again."""
    # 1. Log in
    login_resp = client.post("/auth/login", json={"username": "coord1_test", "password": "pass123"})
    assert login_resp.status_code == 200

    # Verify protected access works when logged in
    resp_authed = client.get("/events")
    assert resp_authed.status_code == 200

    # 2. Click log out
    logout_resp = client.post("/auth/logout")
    assert logout_resp.status_code == 200
    assert logout_resp.json()["message"] == "Logged out successfully"

    # 3. Access protected page again -> refused (session ended)
    resp_after_logout = client.get("/events")
    assert resp_after_logout.status_code == 401
    assert resp_after_logout.json()["detail"] in ["Not authenticated", "Invalid or expired session"]


def test_tc006_direct_url_while_logged_out(client):
    """TC-006 (SCRUM-16 AC6): Reach protected endpoint directly while logged out is refused (401 Unauthorized)."""
    response = client.get("/events")
    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


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


def test_event_coordinator_return_and_resubmit_option_a(client, seed_data, db_session):
    """Option A Test: Coordinator returns event for clarification (status: Returned), organiser resubmits."""
    # 1. Organiser logs in and creates draft
    client.post("/auth/login", json={"username": "org_test", "password": "pass123"})
    future_time = (datetime.utcnow() + timedelta(days=14)).isoformat()
    resp = client.post("/events", json={"title": "Clarification Needed Event", "proposed_start": future_time, "expected_attendance": 100})
    assert resp.status_code == 201
    event_id = resp.json()["id"]

    # 2. Organiser submits event -> status: Submitted
    sub_resp = client.post(f"/events/{event_id}/submit")
    assert sub_resp.status_code == 200
    assert sub_resp.json()["status"] == SUBMITTED

    # 3. Coordinator logs in and returns event with a note -> status: Returned (Option A)
    client.post("/auth/login", json={"username": "coord1_test", "password": "pass123"})
    ret_resp = client.post(f"/events/{event_id}/return", json={"reason": "Please provide technical AV requirements."})
    assert ret_resp.status_code == 200
    assert ret_resp.json()["status"] == RETURNED
    assert ret_resp.json()["clarification_reason"] == "Please provide technical AV requirements."

    # 4. Organiser logs in again and resubmits event -> status: Submitted
    client.post("/auth/login", json={"username": "org_test", "password": "pass123"})
    resub_resp = client.post(f"/events/{event_id}/submit")
    assert resub_resp.status_code == 200
    assert resub_resp.json()["status"] == SUBMITTED

    # 5. Coordinator approves event -> status: Approved
    client.post("/auth/login", json={"username": "coord1_test", "password": "pass123"})
    appr_resp = client.post(f"/events/{event_id}/approve")
    assert appr_resp.status_code == 200
    assert appr_resp.json()["status"] == APPROVED

