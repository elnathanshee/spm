"""SCRUM-36: Tests for approving or rejecting a venue booking request.

As Venue Staff, I want to approve or reject a booking request, so that I
retain final say over what is booked at my venue.

Acceptance criteria covered:
  AC1  Venue Staff can see the requested date and time alongside the venue's
       capacity, layout, accessibility and facilities.
  AC2  Approving creates a confirmed booking for that venue, date and time.
  AC3  Rejecting is visible to the requesting Coordinator, along with which
       venue, date and time were rejected.
  AC4  No request is auto-approved or auto-rejected; every decision requires an
       explicit action by a named Venue Staff member.

Out of scope here (other stories):
  - Rejection reason -> SCRUM-37
  - Notifying the Coordinator of the decision -> SCRUM-64
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
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def seed_data(db_session):
    """Seed users, an active venue, an event and one Pending booking request."""
    coord = User(username="coord_sarah", password_hash=hash_password("password123"),
                 role="coordinator", is_active_coordinator=True)
    staff = User(username="staff_alex", password_hash=hash_password("password123"),
                 role="venue_staff", is_active_coordinator=False)
    organiser = User(username="org_dave", password_hash=hash_password("password123"),
                     role="organiser", is_active_coordinator=False)
    db_session.add_all([coord, staff, organiser])
    db_session.commit()

    db_session.add_all([
        LoginSession(user_id=coord.id, token="token_coord_sarah", valid=True),
        LoginSession(user_id=staff.id, token="token_staff_alex", valid=True),
        LoginSession(user_id=organiser.id, token="token_org_dave", valid=True),
    ])
    db_session.commit()

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
    db_session.add(venue)
    db_session.commit()

    event = Event(
        title="Annual Tech Symposium",
        organiser_id=organiser.id,
        status="Submitted",
        proposed_start=datetime.utcnow() + timedelta(days=14),
        assigned_coordinator_id=coord.id,
        venue_requirements="Needs high quality AV and staging",
    )
    db_session.add(event)
    db_session.commit()

    start = (datetime.utcnow() + timedelta(days=20)).replace(hour=10, minute=0, second=0, microsecond=0)
    booking = VenueBooking(
        event_id=event.id,
        venue_id=venue.id,
        booking_start=start,
        booking_end=start + timedelta(hours=4),
        status="Pending",
    )
    db_session.add(booking)
    db_session.commit()

    return {
        "coord": coord,
        "staff": staff,
        "organiser": organiser,
        "venue": venue,
        "event": event,
        "booking": booking,
    }


def _as(client, token):
    client.cookies.set("connectsphere_session", token)


# ---------------------------------------------------------------------------
# 1. Happy Path
# ---------------------------------------------------------------------------

def test_tc3601_venue_staff_views_request_with_venue_details(client, seed_data):
    """TC-3601 (AC1): Venue Staff see the requested date/time alongside the
    venue's capacity, layout, accessibility and facilities."""
    _as(client, "token_staff_alex")
    b = seed_data["booking"]

    res = client.get(f"/venues/bookings/{b.id}")
    assert res.status_code == 200
    data = res.json()

    assert data["booking_id"] == b.id
    assert data["status"] == "Pending"
    assert data["booking_start"] == b.booking_start.isoformat()
    assert data["booking_end"] == b.booking_end.isoformat()

    venue = data["venue"]
    assert venue["capacity"] == 200
    assert venue["supported_layouts"] == ["Theatre", "Banquet"]
    assert venue["accessibility"] == "Wheelchair accessible"
    assert venue["facilities"] == ["Projector", "Sound System"]


def test_tc3602_venue_staff_approves_pending_request(client, seed_data):
    """TC-3602 (AC2, AC4): Approving turns the request into a Confirmed booking
    for the same venue, date and time, recorded against the named staff member."""
    _as(client, "token_staff_alex")
    b = seed_data["booking"]

    res = client.post(f"/venues/bookings/{b.id}/approve")
    assert res.status_code == 200
    booking = res.json()["booking"]

    assert booking["status"] == "Confirmed"
    assert booking["venue_id"] == seed_data["venue"].id
    assert booking["booking_start"] == b.booking_start.isoformat()
    assert booking["booking_end"] == b.booking_end.isoformat()
    assert booking["decision"]["decision"] == "Approved"
    assert booking["decision"]["decided_by"] == "staff_alex"
    assert booking["decision"]["decided_at"] is not None


def test_tc3603_venue_staff_rejects_pending_request(client, seed_data):
    """TC-3603 (AC4): Rejecting moves the request to Rejected, recorded against
    the named staff member."""
    _as(client, "token_staff_alex")
    b = seed_data["booking"]

    res = client.post(f"/venues/bookings/{b.id}/reject", json={"reason": "Venue unavailable"})
    assert res.status_code == 200
    booking = res.json()["booking"]

    assert booking["status"] == "Rejected"
    assert booking["decision"]["decision"] == "Rejected"
    assert booking["decision"]["decided_by"] == "staff_alex"
    assert booking["decision"]["decided_at"] is not None


def test_tc3604_coordinator_sees_rejection_with_venue_date_time(client, seed_data):
    """TC-3604 (AC3): After a rejection, the requesting Coordinator can see which
    venue, date and time were rejected."""
    b = seed_data["booking"]
    _as(client, "token_staff_alex")
    assert client.post(f"/venues/bookings/{b.id}/reject", json={"reason": "Venue unavailable"}).status_code == 200

    _as(client, "token_coord_sarah")
    res = client.get(f"/events/{seed_data['event'].id}/bookings")
    assert res.status_code == 200
    rows = [r for r in res.json() if r["booking_id"] == b.id]
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "Rejected"
    assert row["venue_name"] == "Grand Hall"
    assert row["booking_start"] == b.booking_start.isoformat()
    assert row["booking_end"] == b.booking_end.isoformat()


# ---------------------------------------------------------------------------
# 2. Negative Path
# ---------------------------------------------------------------------------

def test_tc3605_decide_non_existent_booking_returns_404(client, seed_data):
    """TC-3605: Approving, rejecting or viewing a booking that doesn't exist -> 404."""
    _as(client, "token_staff_alex")
    for res in (
        client.post("/venues/bookings/99999/approve"),
        client.post("/venues/bookings/99999/reject", json={"reason": "Venue unavailable"}),
        client.get("/venues/bookings/99999"),
    ):
        assert res.status_code == 404
        assert "booking not found" in res.json()["detail"].lower()


def test_tc3606_cannot_approve_already_approved_booking(client, seed_data):
    """TC-3606: A booking can only be decided once. Approving twice -> 409,
    and the original decision is unchanged."""
    _as(client, "token_staff_alex")
    b = seed_data["booking"]
    assert client.post(f"/venues/bookings/{b.id}/approve").status_code == 200

    res = client.post(f"/venues/bookings/{b.id}/approve")
    assert res.status_code == 409
    assert client.get(f"/venues/bookings/{b.id}").json()["status"] == "Confirmed"


def test_tc3607_cannot_reverse_a_decision(client, seed_data):
    """TC-3607: Once rejected, a booking cannot then be approved (and vice versa)."""
    _as(client, "token_staff_alex")
    b = seed_data["booking"]
    assert client.post(f"/venues/bookings/{b.id}/reject", json={"reason": "Venue unavailable"}).status_code == 200

    res = client.post(f"/venues/bookings/{b.id}/approve")
    assert res.status_code == 409
    assert client.get(f"/venues/bookings/{b.id}").json()["status"] == "Rejected"


# ---------------------------------------------------------------------------
# 3. Cross-Cutting (role guards / auth)
# ---------------------------------------------------------------------------

def test_tc3608_coordinator_cannot_approve_or_reject(client, seed_data):
    """TC-3608 (AC4): Only Venue Staff decide. A Coordinator gets 403 and the
    booking stays Pending."""
    _as(client, "token_coord_sarah")
    b = seed_data["booking"]
    assert client.post(f"/venues/bookings/{b.id}/approve").status_code == 403
    assert client.post(f"/venues/bookings/{b.id}/reject").status_code == 403

    _as(client, "token_staff_alex")
    assert client.get(f"/venues/bookings/{b.id}").json()["status"] == "Pending"


def test_tc3609_organiser_cannot_approve_or_reject(client, seed_data):
    """TC-3609 (AC4): An Organiser gets 403."""
    _as(client, "token_org_dave")
    b = seed_data["booking"]
    assert client.post(f"/venues/bookings/{b.id}/approve").status_code == 403
    assert client.post(f"/venues/bookings/{b.id}/reject").status_code == 403


def test_tc3610_unauthenticated_user_cannot_decide(client, seed_data):
    """TC-3610 (DoD auth): No session -> 401."""
    b = seed_data["booking"]
    assert client.post(f"/venues/bookings/{b.id}/approve").status_code == 401
    assert client.post(f"/venues/bookings/{b.id}/reject").status_code == 401


# ---------------------------------------------------------------------------
# 4. Workflow Variation
# ---------------------------------------------------------------------------

def test_tc3611_new_request_is_never_auto_decided(client, seed_data):
    """TC-3611 (AC4): A newly submitted request stays Pending with no decision
    recorded until a Venue Staff member acts."""
    _as(client, "token_coord_sarah")
    start = (datetime.utcnow() + timedelta(days=30)).replace(hour=10, minute=0, second=0, microsecond=0)
    res = client.post(
        f"/venues/{seed_data['venue'].id}/bookings",
        json={
            "event_id": seed_data["event"].id,
            "booking_start": start.isoformat(),
            "booking_end": (start + timedelta(hours=2)).isoformat(),
        },
    )
    assert res.status_code == 201
    new_id = res.json()["booking"]["booking_id"]

    _as(client, "token_staff_alex")
    detail = client.get(f"/venues/bookings/{new_id}").json()
    assert detail["status"] == "Pending"
    assert detail["decision"] is None


def test_tc3612_rejected_slot_can_be_requested_again(client, seed_data):
    """TC-3612: Rejecting releases the hold, so the Coordinator can request the
    same venue slot again."""
    b = seed_data["booking"]
    _as(client, "token_staff_alex")
    assert client.post(f"/venues/bookings/{b.id}/reject", json={"reason": "Venue unavailable"}).status_code == 200

    _as(client, "token_coord_sarah")
    res = client.post(
        f"/venues/{seed_data['venue'].id}/bookings",
        json={
            "event_id": seed_data["event"].id,
            "booking_start": b.booking_start.isoformat(),
            "booking_end": b.booking_end.isoformat(),
        },
    )
    assert res.status_code == 201


def test_tc3613_approved_slot_still_blocks_new_requests(client, seed_data):
    """TC-3613: A Confirmed booking keeps holding the slot, so an overlapping
    request is refused (one hold or confirmed booking per venue slot)."""
    b = seed_data["booking"]
    _as(client, "token_staff_alex")
    assert client.post(f"/venues/bookings/{b.id}/approve").status_code == 200

    _as(client, "token_coord_sarah")
    res = client.post(
        f"/venues/{seed_data['venue'].id}/bookings",
        json={
            "event_id": seed_data["event"].id,
            "booking_start": b.booking_start.isoformat(),
            "booking_end": b.booking_end.isoformat(),
        },
    )
    assert res.status_code == 409
