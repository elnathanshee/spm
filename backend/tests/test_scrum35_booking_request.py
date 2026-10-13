"""SCRUM-35: Tests for submitting a venue booking request.

Acceptance criteria covered:
  AC1  The request carries the event's date, time and venue requirements.
  AC2  The request is visible to Venue Staff as pending a decision.
  AC3  The Coordinator can see that the request is pending.
  AC4  The Organiser does not select the venue directly; the Coordinator does.

Customer clarification (10 Sep 2026):
  - At most one active tentative hold OR one confirmed booking per (venue, time slot).
  - Uniqueness is the core rule to test.
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
    """Seed coordinator, venue staff, organiser, an active venue, and an event."""
    coord = User(
        username="coord_sarah",
        password_hash=hash_password("password123"),
        role="coordinator",
        is_active_coordinator=True,
    )
    staff = User(
        username="staff_alex",
        password_hash=hash_password("password123"),
        role="venue_staff",
        is_active_coordinator=False,
    )
    organiser = User(
        username="org_dave",
        password_hash=hash_password("password123"),
        role="organiser",
        is_active_coordinator=False,
    )
    db_session.add_all([coord, staff, organiser])
    db_session.commit()

    # Create login sessions
    session_coord = LoginSession(user_id=coord.id, token="token_coord_sarah", valid=True)
    session_staff = LoginSession(user_id=staff.id, token="token_staff_alex", valid=True)
    session_org = LoginSession(user_id=organiser.id, token="token_org_dave", valid=True)
    db_session.add_all([session_coord, session_staff, session_org])
    db_session.commit()

    # Active Venue: Grand Hall (30m setup, 30m turnaround)
    venue = Venue(
        name="Grand Hall",
        location="Building A, Level 2",
        capacity=200,
        facilities=json.dumps(["Projector", "Sound System"]),
        accessibility="Wheelchair accessible",
        supported_layouts=json.dumps(["Theatre", "Banquet"]),
        operating_hours="08:00-22:00",
        setup_time_mins=30,
        turnaround_time_mins=30,
        is_active=True,
    )
    # Retired Venue: Old Hall
    retired_venue = Venue(
        name="Old Hall",
        location="Building B, Basement",
        capacity=50,
        facilities=json.dumps(["Whiteboard"]),
        accessibility="No access",
        supported_layouts=json.dumps(["Classroom"]),
        operating_hours="09:00-17:00",
        setup_time_mins=15,
        turnaround_time_mins=15,
        is_active=False,
    )
    db_session.add_all([venue, retired_venue])
    db_session.commit()

    # Event in Submitted / Approved status
    future_start = datetime.utcnow() + timedelta(days=14)
    event = Event(
        title="Annual Tech Symposium",
        organiser_id=organiser.id,
        status="Submitted",
        proposed_start=future_start,
        assigned_coordinator_id=coord.id,
        venue_requirements="Needs high quality AV and staging",
    )
    db_session.add(event)
    db_session.commit()

    return {
        "coord": coord,
        "staff": staff,
        "organiser": organiser,
        "venue": venue,
        "retired_venue": retired_venue,
        "event": event,
    }


# ---------------------------------------------------------------------------
# 1. Happy Path: Submitting a valid venue booking request
# ---------------------------------------------------------------------------

def test_tc3501_coordinator_submits_booking_request_happy_path(client, seed_data):
    """TC-3501: Coordinator successfully submits a booking request.
    Carries event date, time and venue requirements. Status is Pending.
    """
    client.cookies.set("connectsphere_session", "token_coord_sarah")
    venue_id = seed_data["venue"].id
    event_id = seed_data["event"].id

    start = datetime.utcnow() + timedelta(days=20, hours=10)
    end = datetime.utcnow() + timedelta(days=20, hours=14)

    payload = {
        "event_id": event_id,
        "booking_start": start.isoformat(),
        "booking_end": end.isoformat(),
        "venue_requirements": "Requires podium and dual projectors",
    }

    res = client.post(f"/venues/{venue_id}/bookings", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["message"] == "Venue booking request submitted successfully"
    booking = data["booking"]
    assert booking["venue_id"] == venue_id
    assert booking["event_id"] == event_id
    assert booking["status"] == "Pending"
    assert "booking_id" in booking or "id" in booking


# ---------------------------------------------------------------------------
# 2. Negative Path: Uniqueness, Conflicts and Invalid Entities
# ---------------------------------------------------------------------------

def test_tc3502_conflict_with_active_tentative_hold(client, seed_data, db_session):
    """TC-3502: Customer constraint: At most one active tentative hold or confirmed booking
    per (venue, time slot). A second request overlapping an active tentative hold is rejected (409).
    """
    client.cookies.set("connectsphere_session", "token_coord_sarah")
    venue = seed_data["venue"]
    event = seed_data["event"]

    start = datetime.utcnow() + timedelta(days=20, hours=10)
    end = datetime.utcnow() + timedelta(days=20, hours=14)

    # First request: creates a Pending tentative hold
    res1 = client.post(
        f"/venues/{venue.id}/bookings",
        json={
            "event_id": event.id,
            "booking_start": start.isoformat(),
            "booking_end": end.isoformat(),
        },
    )
    assert res1.status_code == 201

    # Second request: overlapping time slot on the same venue
    res2 = client.post(
        f"/venues/{venue.id}/bookings",
        json={
            "event_id": event.id,
            "booking_start": (start + timedelta(hours=1)).isoformat(),
            "booking_end": (end + timedelta(hours=1)).isoformat(),
        },
    )
    assert res2.status_code == 409
    assert "conflict" in res2.json()["detail"].lower()


def test_tc3503_conflict_with_confirmed_booking(client, seed_data, db_session):
    """TC-3503: A booking request overlapping a Confirmed booking is rejected (409)."""
    client.cookies.set("connectsphere_session", "token_coord_sarah")
    venue = seed_data["venue"]
    event = seed_data["event"]

    start = datetime.utcnow() + timedelta(days=22, hours=9)
    end = datetime.utcnow() + timedelta(days=22, hours=12)

    # Seed an existing confirmed booking
    existing = VenueBooking(
        venue_id=venue.id,
        event_id=event.id,
        booking_start=start,
        booking_end=end,
        status="Confirmed",
    )
    db_session.add(existing)
    db_session.commit()

    # Attempt to request overlapping slot
    res = client.post(
        f"/venues/{venue.id}/bookings",
        json={
            "event_id": event.id,
            "booking_start": (start + timedelta(hours=1)).isoformat(),
            "booking_end": (end + timedelta(hours=2)).isoformat(),
        },
    )
    assert res.status_code == 409
    assert "conflict" in res.json()["detail"].lower()


def test_tc3504_non_existent_venue(client, seed_data):
    """TC-3504: Requesting a booking for a non-existent venue ID returns 404."""
    client.cookies.set("connectsphere_session", "token_coord_sarah")
    start = datetime.utcnow() + timedelta(days=25, hours=10)
    end = datetime.utcnow() + timedelta(days=25, hours=12)

    res = client.post(
        "/venues/99999/bookings",
        json={
            "event_id": seed_data["event"].id,
            "booking_start": start.isoformat(),
            "booking_end": end.isoformat(),
        },
    )
    assert res.status_code == 404
    assert "venue not found" in res.json()["detail"].lower()


def test_tc3505_non_existent_event(client, seed_data):
    """TC-3505: Requesting a booking for a non-existent event ID returns 404."""
    client.cookies.set("connectsphere_session", "token_coord_sarah")
    start = datetime.utcnow() + timedelta(days=25, hours=10)
    end = datetime.utcnow() + timedelta(days=25, hours=12)

    res = client.post(
        f"/venues/{seed_data['venue'].id}/bookings",
        json={
            "event_id": 99999,
            "booking_start": start.isoformat(),
            "booking_end": end.isoformat(),
        },
    )
    assert res.status_code == 404
    assert "event not found" in res.json()["detail"].lower()


def test_tc3506_booking_retired_venue(client, seed_data):
    """TC-3506: Requesting a booking for a retired venue returns 400 or 409."""
    client.cookies.set("connectsphere_session", "token_coord_sarah")
    start = datetime.utcnow() + timedelta(days=25, hours=10)
    end = datetime.utcnow() + timedelta(days=25, hours=12)

    res = client.post(
        f"/venues/{seed_data['retired_venue'].id}/bookings",
        json={
            "event_id": seed_data["event"].id,
            "booking_start": start.isoformat(),
            "booking_end": end.isoformat(),
        },
    )
    assert res.status_code in (400, 409)
    assert "retired" in res.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 3. Boundary Path: Time calculations and Buffer Touch Points
# ---------------------------------------------------------------------------

def test_tc3507_invalid_time_slot_end_before_start(client, seed_data):
    """TC-3507: Start time after or equal to end time returns 422."""
    client.cookies.set("connectsphere_session", "token_coord_sarah")
    start = datetime.utcnow() + timedelta(days=25, hours=12)
    end = datetime.utcnow() + timedelta(days=25, hours=10)

    res = client.post(
        f"/venues/{seed_data['venue'].id}/bookings",
        json={
            "event_id": seed_data["event"].id,
            "booking_start": start.isoformat(),
            "booking_end": end.isoformat(),
        },
    )
    assert res.status_code == 422


def test_tc3508_past_booking_time(client, seed_data):
    """TC-3508: Booking in the past returns 422."""
    client.cookies.set("connectsphere_session", "token_coord_sarah")
    start = datetime.utcnow() - timedelta(days=2)
    end = datetime.utcnow() - timedelta(days=2, hours=-3)

    res = client.post(
        f"/venues/{seed_data['venue'].id}/bookings",
        json={
            "event_id": seed_data["event"].id,
            "booking_start": start.isoformat(),
            "booking_end": end.isoformat(),
        },
    )
    assert res.status_code == 422


def test_tc3509_abutting_slots_touching_buffers_allowed(client, seed_data, db_session):
    """TC-3509: Two slots where one starts exactly when the other's buffer ends do not conflict."""
    client.cookies.set("connectsphere_session", "token_coord_sarah")
    venue = seed_data["venue"]  # 30m setup, 30m turnaround
    event = seed_data["event"]

    # Slot 1: 10:00 - 12:00. Occupied: 09:30 - 12:30.
    start1 = datetime(2026, 12, 1, 10, 0, 0)
    end1 = datetime(2026, 12, 1, 12, 0, 0)

    b1 = VenueBooking(
        venue_id=venue.id,
        event_id=event.id,
        booking_start=start1,
        booking_end=end1,
        status="Pending",
    )
    db_session.add(b1)
    db_session.commit()

    # Slot 2: Event start 13:00. With 30m setup, occupied start is 12:30.
    # [09:30, 12:30] and [12:30, ...] only touch at 12:30, so NO conflict!
    start2 = datetime(2026, 12, 1, 13, 0, 0)
    end2 = datetime(2026, 12, 1, 15, 0, 0)

    res = client.post(
        f"/venues/{venue.id}/bookings",
        json={
            "event_id": event.id,
            "booking_start": start2.isoformat(),
            "booking_end": end2.isoformat(),
        },
    )
    assert res.status_code == 201


# ---------------------------------------------------------------------------
# 4. Workflow Variation: Released Holds and Visibility
# ---------------------------------------------------------------------------

def test_tc3510_slot_available_after_previous_hold_rejected(client, seed_data, db_session):
    """TC-3510: If a previous booking for that time slot was Rejected, the slot is free
    for a new booking request.
    """
    client.cookies.set("connectsphere_session", "token_coord_sarah")
    venue = seed_data["venue"]
    event = seed_data["event"]

    start = datetime(2026, 12, 5, 14, 0, 0)
    end = datetime(2026, 12, 5, 17, 0, 0)

    # Previous rejected booking
    rejected_booking = VenueBooking(
        venue_id=venue.id,
        event_id=event.id,
        booking_start=start,
        booking_end=end,
        status="Rejected",
    )
    db_session.add(rejected_booking)
    db_session.commit()

    # New request for same slot is accepted
    res = client.post(
        f"/venues/{venue.id}/bookings",
        json={
            "event_id": event.id,
            "booking_start": start.isoformat(),
            "booking_end": end.isoformat(),
        },
    )
    assert res.status_code == 201
    assert res.json()["booking"]["status"] == "Pending"


def test_tc3511_coordinator_can_view_pending_request_status(client, seed_data):
    """TC-3511 (AC3): Coordinator can see that their booking request is Pending."""
    client.cookies.set("connectsphere_session", "token_coord_sarah")
    venue_id = seed_data["venue"].id
    event_id = seed_data["event"].id

    start = datetime.utcnow() + timedelta(days=30, hours=10)
    end = datetime.utcnow() + timedelta(days=30, hours=12)

    # Create booking request
    create_res = client.post(
        f"/venues/{venue_id}/bookings",
        json={
            "event_id": event_id,
            "booking_start": start.isoformat(),
            "booking_end": end.isoformat(),
        },
    )
    assert create_res.status_code == 201

    # View event bookings
    view_res = client.get(f"/events/{event_id}/bookings")
    assert view_res.status_code == 200
    bookings = view_res.json()
    assert len(bookings) >= 1
    assert any(b["venue_id"] == venue_id and b["status"] == "Pending" for b in bookings)


# ---------------------------------------------------------------------------
# 5. Cross-Cutting: RBAC (Role-Based Access Control) & Visibility
# ---------------------------------------------------------------------------

def test_tc3512_organiser_cannot_select_venue_directly(client, seed_data):
    """TC-3512 (AC4): The Organiser does NOT select the venue directly;
    Coordinators do. Organiser calling POST /venues/{id}/bookings gets 403.
    """
    client.cookies.set("connectsphere_session", "token_org_dave")
    start = datetime.utcnow() + timedelta(days=25, hours=10)
    end = datetime.utcnow() + timedelta(days=25, hours=12)

    res = client.post(
        f"/venues/{seed_data['venue'].id}/bookings",
        json={
            "event_id": seed_data["event"].id,
            "booking_start": start.isoformat(),
            "booking_end": end.isoformat(),
        },
    )
    assert res.status_code == 403
    assert "coordinator" in res.json()["detail"].lower()


def test_tc3513_unauthenticated_user_rejected(client, seed_data):
    """TC-3513: Unauthenticated user gets 401."""
    client.cookies.clear()
    start = datetime.utcnow() + timedelta(days=25, hours=10)
    end = datetime.utcnow() + timedelta(days=25, hours=12)

    res = client.post(
        f"/venues/{seed_data['venue'].id}/bookings",
        json={
            "event_id": seed_data["event"].id,
            "booking_start": start.isoformat(),
            "booking_end": end.isoformat(),
        },
    )
    assert res.status_code == 401


def test_tc3514_venue_staff_can_view_pending_requests(client, seed_data):
    """TC-3514 (AC2): The request is visible to Venue Staff as pending a decision."""
    client.cookies.set("connectsphere_session", "token_coord_sarah")
    venue_id = seed_data["venue"].id
    event_id = seed_data["event"].id

    start = datetime.utcnow() + timedelta(days=32, hours=10)
    end = datetime.utcnow() + timedelta(days=32, hours=14)

    # Submit request as coordinator
    client.post(
        f"/venues/{venue_id}/bookings",
        json={
            "event_id": event_id,
            "booking_start": start.isoformat(),
            "booking_end": end.isoformat(),
        },
    )

    # Log in as Venue Staff and check venue bookings
    client.cookies.set("connectsphere_session", "token_staff_alex")
    res = client.get(f"/venues/{venue_id}/bookings")
    assert res.status_code == 200
    bookings = res.json()
    assert any(b["status"] == "Pending" and b["event_id"] == event_id for b in bookings)


def test_tc3515_list_bookings_non_existent_venue_returns_404(client, seed_data):
    """TC-3515 (Negative Path): Listing bookings for a non-existent venue returns 404.
    Guards against query parameter shadowing FastAPI's status module.
    """
    client.cookies.set("connectsphere_session", "token_staff_alex")
    res = client.get("/venues/99999/bookings")
    assert res.status_code == 404
    assert "venue not found" in res.json()["detail"].lower()
