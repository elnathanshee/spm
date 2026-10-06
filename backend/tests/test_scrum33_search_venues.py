"""SCRUM-33: Tests for searching and filtering venues against event requirements.

Acceptance criteria covered:
  AC1  Venues can be filtered by date and time (availability checking against existing bookings
       and setup/turnaround buffers per Week 7 Customer Change #1).
  AC2  Venues can be filtered by expected attendance/capacity, location, accessibility,
       supported layout, and required facilities.
  AC3  Filters can be combined (conjunction / AND logic).
  AC4  Venues already unavailable for the requested period are distinguishable in the results,
       and can be filtered using available_only.
"""

import json
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
from backend.models import Event, LoginSession, User, Venue, VenueBooking
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
def seed_data(db_session):
    """Seed users, venues with various attributes, and an existing booking."""
    coord = User(
        username="coord_sarah",
        password_hash=hash_password("password123"),
        role="coordinator",
        is_active_coordinator=True,
    )
    organiser = User(
        username="org_dave",
        password_hash=hash_password("password123"),
        role="organiser",
        is_active_coordinator=False,
    )
    db_session.add_all([coord, organiser])
    db_session.commit()

    # Create login session for coordinator
    session_coord = LoginSession(user_id=coord.id, token="token_coord_sarah", valid=True)
    db_session.add(session_coord)
    db_session.commit()

    # Seed Venues
    # Venue 1: Grand Hall (High capacity, banquet/theatre, rich facilities, wheelchair access)
    v1 = Venue(
        name="Grand Hall",
        location="Building A, Level 2",
        capacity=300,
        facilities=json.dumps(["Projector", "Sound System", "Stage", "Microphone", "WiFi"]),
        accessibility="Wheelchair accessible, ramp access, elevator",
        supported_layouts=json.dumps(["Banquet", "Theatre", "Classroom"]),
        operating_hours="08:00-22:00",
        setup_time_mins=60,
        turnaround_time_mins=60,
        is_active=True,
    )
    # Venue 2: Seminar Room 101 (Medium capacity, classroom/u-shape, basic facilities)
    v2 = Venue(
        name="Seminar Room 101",
        location="Building B, Level 1",
        capacity=50,
        facilities=json.dumps(["Projector", "Whiteboard", "WiFi"]),
        accessibility="Wheelchair accessible",
        supported_layouts=json.dumps(["Classroom", "U-Shape", "Boardroom"]),
        operating_hours="09:00-18:00",
        setup_time_mins=30,
        turnaround_time_mins=30,
        is_active=True,
    )
    # Venue 3: Executive Boardroom (Low capacity, boardroom only, video conference)
    v3 = Venue(
        name="Executive Boardroom",
        location="Building A, Level 5",
        capacity=20,
        facilities=json.dumps(["Video Conference", "TV Display", "WiFi"]),
        accessibility="Standard elevator access",
        supported_layouts=json.dumps(["Boardroom"]),
        operating_hours="08:00-20:00",
        setup_time_mins=15,
        turnaround_time_mins=15,
        is_active=True,
    )
    # Venue 4: Inactive / Retired Hall
    v4 = Venue(
        name="Old Storage Room",
        location="Building C, Basement",
        capacity=100,
        facilities=json.dumps(["WiFi"]),
        accessibility="No wheelchair access",
        supported_layouts=json.dumps(["Theatre"]),
        operating_hours="08:00-17:00",
        setup_time_mins=30,
        turnaround_time_mins=30,
        is_active=False,  # Retired venue
    )
    db_session.add_all([v1, v2, v3, v4])
    db_session.commit()

    # Seed an Event and a Confirmed Booking for Venue 1 (Grand Hall)
    # Booked on 2026-11-15 from 14:00 to 17:00 UTC
    booking_event = Event(
        title="Annual Tech Symposium",
        organiser_id=organiser.id,
        status="Approved",
        proposed_start=datetime(2026, 11, 15, 14, 0, 0),
    )
    db_session.add(booking_event)
    db_session.commit()

    booking = VenueBooking(
        event_id=booking_event.id,
        venue_id=v1.id,
        booking_start=datetime(2026, 11, 15, 14, 0, 0),
        booking_end=datetime(2026, 11, 15, 17, 0, 0),
        status="Confirmed",
    )
    db_session.add(booking)
    db_session.commit()

    return {
        "coord": coord,
        "v1": v1,
        "v2": v2,
        "v3": v3,
        "v4": v4,
        "booking": booking,
    }


# ---------------------------------------------------------------------------
# AC2: Filtering by Specific Requirements (Capacity, Location, Facilities, etc.)
# ---------------------------------------------------------------------------

def test_scrum33_tc01_filter_by_min_capacity(client, seed_data):
    """AC2: Filter venues by minimum capacity / expected attendance."""
    client.cookies.set("connectsphere_session", "token_coord_sarah")

    # Min capacity 100 should return only Grand Hall (cap 300)
    res = client.get("/venues/search?min_capacity=100")
    assert res.status_code == 200
    venues = res.json()
    names = [v["name"] for v in venues]
    assert names == ["Grand Hall"]

    # Min capacity 30 should return Grand Hall (300) and Seminar Room 101 (50)
    res2 = client.get("/venues/search?min_capacity=30")
    assert res2.status_code == 200
    names2 = [v["name"] for v in res2.json()]
    assert "Grand Hall" in names2
    assert "Seminar Room 101" in names2
    assert "Executive Boardroom" not in names2


def test_scrum33_tc02_filter_by_location(client, seed_data):
    """AC2: Filter venues by location substring (case-insensitive)."""
    client.cookies.set("connectsphere_session", "token_coord_sarah")

    res = client.get("/venues/search?location=building a")
    assert res.status_code == 200
    names = [v["name"] for v in res.json()]
    assert "Grand Hall" in names
    assert "Executive Boardroom" in names
    assert "Seminar Room 101" not in names


def test_scrum33_tc03_filter_by_supported_layout(client, seed_data):
    """AC2: Filter venues supporting a specific room layout."""
    client.cookies.set("connectsphere_session", "token_coord_sarah")

    # Layout "Banquet" is only supported by Grand Hall
    res = client.get("/venues/search?layout=Banquet")
    assert res.status_code == 200
    names = [v["name"] for v in res.json()]
    assert names == ["Grand Hall"]

    # Layout "Classroom" supported by Grand Hall and Seminar Room 101
    res2 = client.get("/venues/search?layout=Classroom")
    assert res2.status_code == 200
    names2 = [v["name"] for v in res2.json()]
    assert "Grand Hall" in names2
    assert "Seminar Room 101" in names2
    assert "Executive Boardroom" not in names2


def test_scrum33_tc04_filter_by_required_facilities(client, seed_data):
    """AC2: Filter venues containing all specified required facilities."""
    client.cookies.set("connectsphere_session", "token_coord_sarah")

    # Single facility: "Stage" (only in Grand Hall)
    res = client.get("/venues/search?facilities=Stage")
    assert res.status_code == 200
    names = [v["name"] for v in res.json()]
    assert names == ["Grand Hall"]

    # Multiple comma-separated facilities: "Projector,WiFi" (in Grand Hall and Seminar Room 101)
    res2 = client.get("/venues/search?facilities=Projector,WiFi")
    assert res2.status_code == 200
    names2 = [v["name"] for v in res2.json()]
    assert "Grand Hall" in names2
    assert "Seminar Room 101" in names2
    assert "Executive Boardroom" not in names2

    # Facility not available in any venue: "Swimming Pool"
    res3 = client.get("/venues/search?facilities=Swimming Pool")
    assert res3.status_code == 200
    assert len(res3.json()) == 0


def test_scrum33_tc05_filter_by_accessibility(client, seed_data):
    """AC2: Filter venues by accessibility requirement."""
    client.cookies.set("connectsphere_session", "token_coord_sarah")

    res = client.get("/venues/search?accessibility=wheelchair")
    assert res.status_code == 200
    names = [v["name"] for v in res.json()]
    assert "Grand Hall" in names
    assert "Seminar Room 101" in names
    assert "Executive Boardroom" not in names


# ---------------------------------------------------------------------------
# AC3: Combined Multi-Criteria Filtering
# ---------------------------------------------------------------------------

def test_scrum33_tc06_combined_filters_conjunction(client, seed_data):
    """AC3: Combining multiple filters applies AND logic across all criteria."""
    client.cookies.set("connectsphere_session", "token_coord_sarah")

    # Looking for: Building A + Capacity >= 20 + Layout Boardroom + Facility Video Conference
    params = {
        "location": "Building A",
        "min_capacity": 20,
        "layout": "Boardroom",
        "facilities": "Video Conference",
    }
    res = client.get("/venues/search", params=params)
    assert res.status_code == 200
    venues = res.json()
    assert len(venues) == 1
    assert venues[0]["name"] == "Executive Boardroom"


# ---------------------------------------------------------------------------
# AC1 & AC4: Date/Time Availability and Setup/Turnaround Buffers (Week 7 Change #1)
# ---------------------------------------------------------------------------

def test_scrum33_tc07_venue_available_when_no_time_conflict(client, seed_data):
    """AC1 & AC4: On a completely free day, all candidate venues report is_available=True."""
    client.cookies.set("connectsphere_session", "token_coord_sarah")

    # Querying on 2026-11-20 (5 days after the existing booking)
    params = {
        "start_time": "2026-11-20T10:00:00",
        "end_time": "2026-11-20T12:00:00",
    }
    res = client.get("/venues/search", params=params)
    assert res.status_code == 200
    venues = res.json()
    assert len(venues) == 3
    assert all(v["is_available"] is True for v in venues)
    assert all(v["conflict_reason"] is None for v in venues)


def test_scrum33_tc08_direct_overlap_marks_venue_unavailable(client, seed_data):
    """AC1 & AC4: When time directly overlaps with existing booking, venue is marked unavailable."""
    client.cookies.set("connectsphere_session", "token_coord_sarah")

    # Existing booking on Grand Hall: 2026-11-15 14:00 to 17:00
    # Search request: 2026-11-15 15:00 to 16:00
    params = {
        "start_time": "2026-11-15T15:00:00",
        "end_time": "2026-11-15T16:00:00",
    }
    res = client.get("/venues/search", params=params)
    assert res.status_code == 200
    venues = {v["name"]: v for v in res.json()}

    # Grand Hall is unavailable
    assert venues["Grand Hall"]["is_available"] is False
    assert venues["Grand Hall"]["conflict_reason"] is not None

    # Other venues are available
    assert venues["Seminar Room 101"]["is_available"] is True
    assert venues["Executive Boardroom"]["is_available"] is True


def test_scrum33_tc09_turnaround_buffer_overlap_marks_venue_unavailable(client, seed_data):
    """AC1 & AC4: Grand Hall has 60m setup and 60m turnaround buffers (Week 7 Change #1).
    Booking is 14:00-17:00, so occupied window is 13:00-18:00 (with turnaround buffer up to 18:00).
    A new request at 17:30 to 19:00 falls into the turnaround buffer and must be unavailable.
    """
    client.cookies.set("connectsphere_session", "token_coord_sarah")

    # Request starts at 17:30 (within 60m turnaround time after 17:00)
    params = {
        "start_time": "2026-11-15T17:30:00",
        "end_time": "2026-11-15T19:30:00",
    }
    res = client.get("/venues/search", params=params)
    assert res.status_code == 200
    venues = {v["name"]: v for v in res.json()}

    assert venues["Grand Hall"]["is_available"] is False
    assert "buffer" in venues["Grand Hall"]["conflict_reason"].lower() or "turnaround" in venues["Grand Hall"]["conflict_reason"].lower() or "occupied" in venues["Grand Hall"]["conflict_reason"].lower()


def test_scrum33_tc10_setup_buffer_overlap_marks_venue_unavailable(client, seed_data):
    """AC1 & AC4: Grand Hall booking is 14:00-17:00.
    With 60m setup buffer, venue is blocked from 13:00.
    A new request ending at 13:30 (inside setup window 13:00-14:00) must be unavailable.
    """
    client.cookies.set("connectsphere_session", "token_coord_sarah")

    params = {
        "start_time": "2026-11-15T11:00:00",
        "end_time": "2026-11-15T13:30:00",
    }
    res = client.get("/venues/search", params=params)
    assert res.status_code == 200
    venues = {v["name"]: v for v in res.json()}

    assert venues["Grand Hall"]["is_available"] is False


def test_scrum33_tc11_available_only_filter_excludes_conflicting_venues(client, seed_data):
    """AC4: Passing available_only=true completely filters out unavailable venues from the response list."""
    client.cookies.set("connectsphere_session", "token_coord_sarah")

    params = {
        "start_time": "2026-11-15T14:00:00",
        "end_time": "2026-11-15T16:00:00",
        "available_only": "true",
    }
    res = client.get("/venues/search", params=params)
    assert res.status_code == 200
    venues = res.json()
    names = [v["name"] for v in venues]

    assert "Grand Hall" not in names
    assert "Seminar Room 101" in names
    assert "Executive Boardroom" in names


# ---------------------------------------------------------------------------
# Inactive / Retired Venues & RBAC
# ---------------------------------------------------------------------------

def test_scrum33_tc12_retired_inactive_venues_are_never_returned(client, seed_data):
    """Retired venues (is_active=False) should never be returned in search results."""
    client.cookies.set("connectsphere_session", "token_coord_sarah")

    res = client.get("/venues/search")
    assert res.status_code == 200
    names = [v["name"] for v in res.json()]
    assert "Old Storage Room" not in names


def test_scrum33_tc13_unauthenticated_request_rejected(client):
    """Unauthenticated users cannot access venue search (HTTP 401 Unauthorized)."""
    client.cookies.clear()
    res = client.get("/venues/search")
    assert res.status_code == 401
