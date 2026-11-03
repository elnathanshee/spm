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



# ---------------------------------------------------------------------------
# PR #14 review fixes
#
# Week 7 Customer Change #1: every booking occupies the venue from its start
# minus setup to its end plus turnaround, and two bookings at one venue
# conflict if those windows overlap. Windows that only touch do not conflict.
# Dates below are relative to today so these tests never expire.
# ---------------------------------------------------------------------------

BUFFER_DAY = (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%d")


def at(hhmm):
    """An ISO timestamp on BUFFER_DAY, e.g. at("13:00")."""
    return f"{BUFFER_DAY}T{hhmm}:00"


@pytest.fixture
def buffer_venue(db_session, seed_data):
    """A venue with 30m setup and 45m turnaround, booked 10:00 to 12:00.

    With its buffers, that booking occupies the venue from 09:30 to 12:45.
    """
    venue = Venue(
        name="Buffer Test Hall",
        location="Building D",
        capacity=80,
        setup_time_mins=30,
        turnaround_time_mins=45,
        is_active=True,
    )
    db_session.add(venue)
    db_session.commit()

    organiser = db_session.query(User).filter_by(username="org_dave").one()
    event = Event(title="Existing booked event", organiser_id=organiser.id, status="Approved")
    db_session.add(event)
    db_session.commit()

    day = datetime.strptime(BUFFER_DAY, "%Y-%m-%d")
    db_session.add(
        VenueBooking(
            event_id=event.id,
            venue_id=venue.id,
            booking_start=day.replace(hour=10),
            booking_end=day.replace(hour=12),
            status="Confirmed",
        )
    )
    db_session.commit()
    return venue


def buffer_hall_result(client, start, end):
    """Search as a Coordinator and return the Buffer Test Hall entry."""
    client.cookies.set("connectsphere_session", "token_coord_sarah")
    res = client.get("/venues/search", params={"start_time": at(start), "end_time": at(end)})
    assert res.status_code == 200
    return {v["name"]: v for v in res.json()}["Buffer Test Hall"]


def test_scrum33_tc14_new_event_setup_cannot_overlap_previous_turnaround(client, buffer_venue):
    """TC-3314 (SCRUM-33 AC4, Change #1): 13:00 to 14:00 is unavailable.

    The new event's own 30m setup starts at 12:30, inside the 45m turnaround
    that runs to 12:45 after the 10:00 to 12:00 booking.
    """
    hall = buffer_hall_result(client, "13:00", "14:00")

    assert hall["is_available"] is False
    assert "setup" in hall["conflict_reason"]
    assert "turnaround" in hall["conflict_reason"]


def test_scrum33_tc15_new_event_turnaround_cannot_overlap_next_setup(client, buffer_venue):
    """TC-3315 (SCRUM-33 AC4, Change #1): 07:00 to 09:00 is unavailable.

    The new event's own 45m turnaround runs to 09:45, past the 30m setup that
    starts at 09:30 before the 10:00 to 12:00 booking.
    """
    hall = buffer_hall_result(client, "07:00", "09:00")

    assert hall["is_available"] is False
    assert "setup" in hall["conflict_reason"]
    assert "turnaround" in hall["conflict_reason"]


def test_scrum33_tc16_windows_that_only_touch_after_a_booking_are_available(client, buffer_venue):
    """TC-3316 (SCRUM-33 AC4, Change #1, boundary): 13:15 to 14:00 is available.

    Its setup starts at 12:45, exactly when the previous turnaround ends. The
    windows touch but do not overlap.
    """
    hall = buffer_hall_result(client, "13:15", "14:00")

    assert hall["is_available"] is True
    assert hall["conflict_reason"] is None


def test_scrum33_tc17_windows_that_only_touch_before_a_booking_are_available(client, buffer_venue):
    """TC-3317 (SCRUM-33 AC4, Change #1, boundary): 06:00 to 08:45 is available.

    Its turnaround ends at 09:30, exactly when the next setup starts. The
    windows touch but do not overlap.
    """
    hall = buffer_hall_result(client, "06:00", "08:45")

    assert hall["is_available"] is True
    assert hall["conflict_reason"] is None


@pytest.fixture
def role_tokens(db_session, seed_data):
    """A logged-in session for each role, keyed by role name."""
    tokens = {"coordinator": "token_coord_sarah"}
    for role in ["lead", "venue_staff", "organiser", "admin"]:
        user = User(
            username=f"{role}_tc33",
            password_hash=hash_password("password123"),
            role=role,
            is_active_coordinator=False,
        )
        db_session.add(user)
        db_session.commit()
        token = f"token_{role}_tc33"
        db_session.add(LoginSession(user_id=user.id, token=token, valid=True))
        db_session.commit()
        tokens[role] = token
    return tokens


@pytest.mark.parametrize("role", ["coordinator", "lead", "venue_staff"])
def test_scrum33_tc18_internal_planning_roles_can_search(client, role_tokens, role):
    """TC-3318 (SCRUM-33, SCRUM-17 AC3, DoD role-based access): Coordinators,
    Leads and Venue Staff can search venues."""
    client.cookies.set("connectsphere_session", role_tokens[role])

    res = client.get("/venues/search")

    assert res.status_code == 200
    assert len(res.json()) == 3


def test_scrum33_tc19_organiser_cannot_search_or_see_other_bookings(client, role_tokens):
    """TC-3319 (SCRUM-33, SCRUM-17 AC3, DoD role-based access): an Organiser is
    refused with 403 and a reason, and the response carries none of another
    event's booking times."""
    client.cookies.set("connectsphere_session", role_tokens["organiser"])

    # This period overlaps the Grand Hall booking (2026-11-15 14:00 to 17:00).
    res = client.get(
        "/venues/search",
        params={"start_time": "2026-11-15T15:00:00", "end_time": "2026-11-15T16:00:00"},
    )

    assert res.status_code == 403
    assert "Coordinators, Leads and Venue Staff" in res.json()["detail"]
    assert "conflict_reason" not in res.text
    assert "14:00" not in res.text


def test_scrum33_tc20_admin_cannot_search(client, role_tokens):
    """TC-3320 (SCRUM-33, SCRUM-17 AC3, DoD role-based access): the customer has
    said there is no system admin role, so an admin account is refused like any
    other role outside internal planning."""
    client.cookies.set("connectsphere_session", role_tokens["admin"])

    res = client.get("/venues/search")

    assert res.status_code == 403


@pytest.fixture
def coord_client(client, seed_data):
    """A client logged in as the seeded Coordinator."""
    client.cookies.set("connectsphere_session", "token_coord_sarah")
    return client


def test_scrum33_tc21_start_time_without_end_time_is_refused(coord_client):
    """TC-3321 (SCRUM-33 AC1, DoD refusals say why): a period with only
    start_time is refused with 400 instead of being silently ignored."""
    res = coord_client.get("/venues/search", params={"start_time": "2026-11-15T15:00:00"})

    assert res.status_code == 400
    assert "both start_time and end_time" in res.json()["detail"]


def test_scrum33_tc22_end_time_without_start_time_is_refused(coord_client):
    """TC-3322 (SCRUM-33 AC1, DoD refusals say why): a period with only
    end_time is refused with 400 instead of being silently ignored."""
    res = coord_client.get("/venues/search", params={"end_time": "2026-11-15T16:00:00"})

    assert res.status_code == 400
    assert "both start_time and end_time" in res.json()["detail"]


def test_scrum33_tc23_end_before_start_is_refused(coord_client):
    """TC-3323 (SCRUM-33 AC1, DoD refusals say why): an end_time earlier than
    start_time is refused with 400."""
    res = coord_client.get(
        "/venues/search",
        params={"start_time": "2026-11-15T16:00:00", "end_time": "2026-11-15T15:00:00"},
    )

    assert res.status_code == 400
    assert "end_time must be after start_time" in res.json()["detail"]


def test_scrum33_tc24_zero_length_period_is_refused(coord_client):
    """TC-3324 (SCRUM-33 AC1, boundary): start_time equal to end_time is a
    zero-length period and is refused with 400."""
    res = coord_client.get(
        "/venues/search",
        params={"start_time": "2026-11-15T15:00:00", "end_time": "2026-11-15T15:00:00"},
    )

    assert res.status_code == 400
    assert "end_time must be after start_time" in res.json()["detail"]


def test_scrum33_tc25_offset_on_one_end_only_does_not_crash(coord_client):
    """TC-3325 (SCRUM-33 AC1, boundary): a period with a time zone offset on
    start_time but not on end_time is handled, not a 500.

    Guards the start/end comparison added for TC-3323: comparing a datetime
    that has an offset with one that does not raises TypeError in Python.
    """
    res = coord_client.get(
        "/venues/search",
        params={"start_time": "2026-11-20T10:00:00+08:00", "end_time": "2026-11-20T11:00:00"},
    )

    assert res.status_code == 200


def test_scrum33_tc26_offset_on_end_time_only_does_not_crash(coord_client):
    """TC-3326 (SCRUM-33 AC1, boundary): a period with a time zone offset on
    end_time but not on start_time is handled, not a 500.

    The mirror of TC-3325. Together they cover converting each end to UTC
    before the start/end comparison ([SCRUM-95]: 11:00+08:00 is 03:00 UTC, so
    the period starts at 02:00 to stay valid).
    """
    res = coord_client.get(
        "/venues/search",
        params={"start_time": "2026-11-20T02:00:00", "end_time": "2026-11-20T11:00:00+08:00"},
    )

    assert res.status_code == 200
