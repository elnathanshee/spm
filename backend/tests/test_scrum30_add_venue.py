"""SCRUM-30: Tests for adding a venue to the catalogue (Venue Staff).

Acceptance criteria covered:
  AC1  A venue record captures location, capacity, facilities, accessibility,
       supported room layouts, operating information, and setup/turnaround times.
  AC2  A newly added venue becomes findable through venue search / listing.
  AC3  Only Venue Staff can create venue records.
  AC4  (Week 7 Change #1): Configurable setup time and turnaround time buffers.
"""

import os
import sys
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Setup path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.database import Base, get_db
from backend.main import app
from backend.models import LoginSession, User, Venue
from backend.security import hash_password

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
def seed_users(db_session):
    venue_staff = User(
        username="venue_alice",
        password_hash=hash_password("secret123"),
        role="venue_staff",
        is_active_coordinator=False,
    )
    coordinator = User(
        username="coord_bob",
        password_hash=hash_password("secret123"),
        role="coordinator",
        is_active_coordinator=True,
    )
    organiser = User(
        username="org_charlie",
        password_hash=hash_password("secret123"),
        role="organiser",
        is_active_coordinator=False,
    )
    db_session.add_all([venue_staff, coordinator, organiser])
    db_session.commit()

    # Create sessions
    session_venue = LoginSession(user_id=venue_staff.id, token="token_venue_staff", valid=True)
    session_coord = LoginSession(user_id=coordinator.id, token="token_coordinator", valid=True)
    session_org = LoginSession(user_id=organiser.id, token="token_organiser", valid=True)
    db_session.add_all([session_venue, session_coord, session_org])
    db_session.commit()

    return {
        "venue_staff": venue_staff,
        "coordinator": coordinator,
        "organiser": organiser,
    }


# ---------------------------------------------------------------------------
# AC1 & AC4: Comprehensive Venue Creation with Buffer Times
# ---------------------------------------------------------------------------

def test_scrum30_ac1_ac4_venue_staff_can_create_valid_venue(client, seed_users):
    """Venue staff can successfully add a new venue with all required attributes and buffer times."""
    client.cookies.set("connectsphere_session", "token_venue_staff")
    payload = {
        "name": "Grand Hall A",
        "location": "Building 1, Level 3",
        "capacity": 250,
        "facilities": ["Projector", "Sound System", "Stage", "Wireless Mics"],
        "accessibility": "Wheelchair accessible, ramp at south entrance, elevator access",
        "supported_layouts": ["Banquet", "Theatre", "Classroom", "U-Shape"],
        "operating_hours": "08:00-22:00",
        "setup_time_mins": 45,
        "turnaround_time_mins": 60,
    }
    response = client.post("/venues", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["message"] == "Venue created successfully"
    venue = data["venue"]
    assert venue["name"] == "Grand Hall A"
    assert venue["location"] == "Building 1, Level 3"
    assert venue["capacity"] == 250
    assert "Projector" in venue["facilities"]
    assert "Banquet" in venue["supported_layouts"]
    assert venue["setup_time_mins"] == 45
    assert venue["turnaround_time_mins"] == 60
    assert venue["is_active"] is True


def test_scrum30_ac4_default_setup_and_turnaround_buffers(client, seed_users):
    """If setup and turnaround times are omitted, system defaults (30m setup, 45m turnaround) are used."""
    client.cookies.set("connectsphere_session", "token_venue_staff")
    payload = {
        "name": "Seminar Room 204",
        "location": "North Wing, Level 2",
        "capacity": 40,
    }
    response = client.post("/venues", json=payload)
    assert response.status_code == 201
    venue = response.json()["venue"]
    assert venue["setup_time_mins"] == 30
    assert venue["turnaround_time_mins"] == 45


# ---------------------------------------------------------------------------
# AC2: Findable in Venue Listing / Retrieval
# ---------------------------------------------------------------------------

def test_scrum30_ac2_newly_added_venue_is_findable_and_retrievable(client, seed_users):
    """Newly added venue immediately appears in GET /venues and GET /venues/{id}."""
    client.cookies.set("connectsphere_session", "token_venue_staff")
    payload = {
        "name": "Auditorium 1",
        "location": "Central Campus",
        "capacity": 500,
    }
    create_res = client.post("/venues", json=payload)
    assert create_res.status_code == 201
    venue_id = create_res.json()["venue"]["id"]

    # Listing check
    list_res = client.get("/venues")
    assert list_res.status_code == 200
    venues = list_res.json()
    assert any(v["id"] == venue_id and v["name"] == "Auditorium 1" for v in venues)

    # Retrieval by ID check
    get_res = client.get(f"/venues/{venue_id}")
    assert get_res.status_code == 200
    assert get_res.json()["name"] == "Auditorium 1"


# ---------------------------------------------------------------------------
# AC3: Role-Based Access Control Guards
# ---------------------------------------------------------------------------

def test_scrum30_ac3_non_venue_staff_cannot_create_venue(client, seed_users):
    """Organisers and Coordinators cannot create venues (HTTP 403 Forbidden)."""
    payload = {
        "name": "Unauthorized Room",
        "location": "Building 2",
        "capacity": 30,
    }

    # Attempt as organiser
    client.cookies.set("connectsphere_session", "token_organiser")
    res_org = client.post("/venues", json=payload)
    assert res_org.status_code == 403
    assert "Only Venue Staff" in res_org.json()["detail"]

    # Attempt as coordinator
    client.cookies.set("connectsphere_session", "token_coordinator")
    res_coord = client.post("/venues", json=payload)
    assert res_coord.status_code == 403
    assert "Only Venue Staff" in res_coord.json()["detail"]


def test_scrum30_ac3_unauthenticated_request_rejected(client):
    """Unauthenticated users cannot create venues (HTTP 401 Unauthorized)."""
    client.cookies.clear()
    payload = {
        "name": "Anonymous Room",
        "location": "Building 2",
        "capacity": 30,
    }
    response = client.post("/venues", json=payload)
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Input Validation and Duplicate Conflicts
# ---------------------------------------------------------------------------

def test_scrum30_duplicate_venue_name_rejected_with_conflict(client, seed_users):
    """Creating a venue with a duplicate name (case-insensitive) returns HTTP 409 Conflict."""
    client.cookies.set("connectsphere_session", "token_venue_staff")
    payload = {
        "name": "Innovation Lab",
        "location": "Level 4",
        "capacity": 60,
    }
    first_res = client.post("/venues", json=payload)
    assert first_res.status_code == 201

    # Attempt duplicate with same name (different casing)
    duplicate_payload = {
        "name": "innovation lab",
        "location": "Level 5",
        "capacity": 80,
    }
    second_res = client.post("/venues", json=duplicate_payload)
    assert second_res.status_code == 409
    assert "already exists" in second_res.json()["detail"]


def test_scrum30_invalid_capacity_rejected(client, seed_users):
    """Capacity must be greater than 0; 0 or negative capacities return HTTP 422 Unprocessable Entity."""
    client.cookies.set("connectsphere_session", "token_venue_staff")
    payload_zero = {
        "name": "Zero Capacity Room",
        "location": "Level 1",
        "capacity": 0,
    }
    res_zero = client.post("/venues", json=payload_zero)
    assert res_zero.status_code == 422

    payload_negative = {
        "name": "Negative Capacity Room",
        "location": "Level 1",
        "capacity": -10,
    }
    res_negative = client.post("/venues", json=payload_negative)
    assert res_negative.status_code == 422


def test_scrum30_blank_name_or_location_rejected(client, seed_users):
    """Blank or whitespace-only name and location are rejected with HTTP 422."""
    client.cookies.set("connectsphere_session", "token_venue_staff")
    payload_blank_name = {
        "name": "   ",
        "location": "Level 1",
        "capacity": 50,
    }
    res_name = client.post("/venues", json=payload_blank_name)
    assert res_name.status_code == 422

    payload_blank_loc = {
        "name": "Conference Room B",
        "location": "   ",
        "capacity": 50,
    }
    res_loc = client.post("/venues", json=payload_blank_loc)
    assert res_loc.status_code == 422


def test_scrum30_negative_buffer_times_rejected(client, seed_users):
    """Negative setup_time_mins or turnaround_time_mins return HTTP 422."""
    client.cookies.set("connectsphere_session", "token_venue_staff")
    payload = {
        "name": "Buffer Test Room",
        "location": "Level 1",
        "capacity": 50,
        "setup_time_mins": -15,
    }
    res = client.post("/venues", json=payload)
    assert res.status_code == 422


def test_scrum30_duplicate_name_check_handles_underscores_without_wildcard_collision(client, seed_users):
    """Ensure that names containing underscores (e.g. 'Hall_1') do not collide with 'HallA1'."""
    client.cookies.set("connectsphere_session", "token_venue_staff")

    # 1. Create venue named 'HallA1'
    res1 = client.post(
        "/venues",
        json={"name": "HallA1", "location": "Wing A", "capacity": 50},
    )
    assert res1.status_code == 201

    # 2. Create venue named 'Hall_1' (underscore should NOT match 'A' via SQL wildcard)
    res2 = client.post(
        "/venues",
        json={"name": "Hall_1", "location": "Wing B", "capacity": 60},
    )
    assert res2.status_code == 201
    assert res2.json()["venue"]["name"] == "Hall_1"

    # 3. Attempt duplicate 'hall_1' (case-insensitive exact match) -> should be rejected
    res3 = client.post(
        "/venues",
        json={"name": "hall_1", "location": "Wing C", "capacity": 70},
    )
    assert res3.status_code == 409
    assert "already exists" in res3.json()["detail"]

