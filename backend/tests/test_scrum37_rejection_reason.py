"""SCRUM-37: Tests for providing a mandatory reason when rejecting a venue booking request.

As Venue Staff, I want to give a reason when rejecting a booking, so that the
Coordinator knows why and can take appropriate action.

Acceptance criteria covered:
  AC1  Venue Staff must provide a reason when rejecting a booking request.
  AC2  Empty, missing, or whitespace-only reason strings are rejected (422).
  AC3  The rejection reason is saved in the decision record (venue_booking_decisions.reason).
  AC4  The rejection reason is visible to the Coordinator when inspecting bookings
       for their event (GET /events/{event_id}/bookings) and to Venue Staff
       (GET /venues/bookings/{booking_id}).
  AC5  Non-venue staff (Coordinators, Organisers, Unauthenticated) cannot reject (403/401).
  AC6  Already decided bookings cannot be rejected again (409 Conflict).
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

# Pre-computed bcrypt hash for 'password123' to ensure fast test setup execution
DUMMY_PASSWORD_HASH = "$2b$12$e8POd1GTX1V65uqmepoxQe2i8aKqZ3QvQhD1vLd0uV1z6oVnJveWO"

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
    """Seed coordinator, venue staff, organiser, an active venue, an event and a pending booking."""
    coord = User(
        username="coord_sarah",
        password_hash=DUMMY_PASSWORD_HASH,
        role="coordinator",
        is_active_coordinator=True,
    )
    staff = User(
        username="staff_alex",
        password_hash=DUMMY_PASSWORD_HASH,
        role="venue_staff",
        is_active_coordinator=False,
    )
    organiser = User(
        username="org_dave",
        password_hash=DUMMY_PASSWORD_HASH,
        role="organiser",
        is_active_coordinator=False,
    )
    db_session.add_all([coord, staff, organiser])
    db_session.commit()

    db_session.add_all([
        LoginSession(user_id=coord.id, token="token_coord_sarah", valid=True),
        LoginSession(user_id=staff.id, token="token_staff_alex", valid=True),
        LoginSession(user_id=organiser.id, token="token_org_dave", valid=True),
    ])
    db_session.commit()

    venue = Venue(
        name="Conference Hall A",
        location="Level 3, North Wing",
        capacity=150,
        facilities=json.dumps(["Projector", "Wireless Mic"]),
        accessibility="Wheelchair accessible, elevator access",
        supported_layouts=json.dumps(["Theatre", "Classroom"]),
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
        assigned_coordinator_id=coord.id,
        status="Under Review",
        proposed_start=datetime.utcnow() + timedelta(days=20),
    )
    db_session.add(event)
    db_session.commit()

    start = (datetime.utcnow() + timedelta(days=20)).replace(hour=9, minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=4)
    booking = VenueBooking(
        event_id=event.id,
        venue_id=venue.id,
        booking_start=start,
        booking_end=end,
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


def _as(client: TestClient, token: str) -> None:
    client.cookies.set("connectsphere_session", token)


# ===========================================================================
# 1. Happy Path: Venue Staff Rejects with Valid Reason (AC1, AC3)
# ===========================================================================

def test_tc3701_venue_staff_rejects_booking_with_valid_reason(client, seed_data):
    """TC-3701 (Happy Path, AC1, AC3):
    Venue Staff rejects a Pending booking request providing a valid reason.
    Response confirms rejection and includes the recorded decision reason.
    """
    _as(client, "token_staff_alex")
    b = seed_data["booking"]
    payload = {"reason": "Venue undergoes scheduled maintenance and AV upgrades on this date."}

    res = client.post(f"/venues/bookings/{b.id}/reject", json=payload)
    assert res.status_code == 200, res.text

    data = res.json()
    assert data["message"] == "Booking request rejected"
    booking = data["booking"]
    assert booking["status"] == "Rejected"
    assert booking["decision"] is not None
    assert booking["decision"]["decision"] == "Rejected"
    assert booking["decision"]["decided_by"] == "staff_alex"
    assert booking["decision"]["reason"] == payload["reason"]


# ===========================================================================
# 2. Boundary & Negative Paths: Missing or Blank Rejection Reasons (AC2)
# ===========================================================================

def test_tc3702_rejecting_without_reason_returns_422(client, seed_data):
    """TC-3702 (Negative Path, AC2):
    Calling POST /venues/bookings/{id}/reject without a JSON body or with an
    empty object returns 422 Unprocessable Entity, and booking stays Pending.
    """
    _as(client, "token_staff_alex")
    b = seed_data["booking"]

    # Missing body entirely
    res1 = client.post(f"/venues/bookings/{b.id}/reject")
    assert res1.status_code == 422

    # Empty JSON body
    res2 = client.post(f"/venues/bookings/{b.id}/reject", json={})
    assert res2.status_code == 422

    # Booking must remain unchanged in Pending state
    res_check = client.get(f"/venues/bookings/{b.id}")
    assert res_check.status_code == 200
    assert res_check.json()["status"] == "Pending"
    assert res_check.json()["decision"] is None


def test_tc3703_rejecting_with_blank_or_whitespace_reason_returns_422(client, seed_data):
    """TC-3703 (Boundary Path, AC2):
    Calling POST /venues/bookings/{id}/reject with an empty string or
    whitespace-only string returns 422 Unprocessable Entity.
    """
    _as(client, "token_staff_alex")
    b = seed_data["booking"]

    # Empty string
    res_empty = client.post(f"/venues/bookings/{b.id}/reject", json={"reason": ""})
    assert res_empty.status_code == 422
    assert "reason" in str(res_empty.json()).lower()

    # Whitespace only
    res_ws = client.post(f"/venues/bookings/{b.id}/reject", json={"reason": "   \n\t   "})
    assert res_ws.status_code == 422
    assert "reason" in str(res_ws.json()).lower()

    # Booking must still be Pending
    res_check = client.get(f"/venues/bookings/{b.id}")
    assert res_check.json()["status"] == "Pending"


# ===========================================================================
# 3. Visibility: Coordinator & Staff Visibility of Rejection Reason (AC4)
# ===========================================================================

def test_tc3704_coordinator_sees_rejection_reason_on_event_bookings(client, seed_data):
    """TC-3704 (Happy Path / Visibility, AC4):
    After Venue Staff rejects a booking with a reason, the requesting Coordinator
    can see the rejection reason when querying GET /events/{id}/bookings.
    """
    b = seed_data["booking"]
    reason_text = "Air conditioning overhaul scheduled for Conference Hall A."

    # Staff rejects with reason
    _as(client, "token_staff_alex")
    res_reject = client.post(f"/venues/bookings/{b.id}/reject", json={"reason": reason_text})
    assert res_reject.status_code == 200

    # Coordinator inspects bookings for the event
    _as(client, "token_coord_sarah")
    res_event = client.get(f"/events/{seed_data['event'].id}/bookings")
    assert res_event.status_code == 200

    bookings = res_event.json()
    assert len(bookings) == 1
    item = bookings[0]
    assert item["booking_id"] == b.id
    assert item["status"] == "Rejected"
    assert item["rejection_reason"] == reason_text


def test_tc3705_venue_staff_review_endpoint_includes_rejection_reason(client, seed_data):
    """TC-3705 (Happy Path / Visibility, AC4):
    Venue Staff querying GET /venues/bookings/{id} on a rejected booking sees the
    recorded rejection reason inside the decision object.
    """
    b = seed_data["booking"]
    reason_text = "Sound system undergoing rewiring during this slot."

    _as(client, "token_staff_alex")
    assert client.post(f"/venues/bookings/{b.id}/reject", json={"reason": reason_text}).status_code == 200

    res = client.get(f"/venues/bookings/{b.id}")
    assert res.status_code == 200
    detail = res.json()
    assert detail["status"] == "Rejected"
    assert detail["decision"]["reason"] == reason_text


# ===========================================================================
# 4. Cross-Cutting: Role-Based Access Control & Auth (AC5)
# ===========================================================================

def test_tc3706_coordinator_cannot_reject_booking(client, seed_data):
    """TC-3706 (Cross-Cutting / RBAC, AC5):
    A Coordinator cannot reject a booking request; returns 403 Forbidden.
    """
    b = seed_data["booking"]
    _as(client, "token_coord_sarah")

    res = client.post(f"/venues/bookings/{b.id}/reject", json={"reason": "Trying to cancel"})
    assert res.status_code == 403


def test_tc3707_organiser_cannot_reject_booking(client, seed_data):
    """TC-3707 (Cross-Cutting / RBAC, AC5):
    An Organiser cannot reject a booking request; returns 403 Forbidden.
    """
    b = seed_data["booking"]
    _as(client, "token_org_dave")

    res = client.post(f"/venues/bookings/{b.id}/reject", json={"reason": "Organiser cannot decide"})
    assert res.status_code == 403


def test_tc3708_unauthenticated_user_cannot_reject_booking(client, seed_data):
    """TC-3708 (Cross-Cutting / Auth, AC5):
    An unauthenticated request to reject returns 401 Unauthorized.
    """
    b = seed_data["booking"]
    res = client.post(f"/venues/bookings/{b.id}/reject", json={"reason": "No credentials"})
    assert res.status_code == 401


# ===========================================================================
# 5. Workflow Variation: Conflict on Decided Bookings (AC6)
# ===========================================================================

def test_tc3709_cannot_reject_already_decided_booking(client, seed_data):
    """TC-3709 (Workflow Variation / Conflict, AC6):
    Once a booking has been approved (Confirmed) or rejected (Rejected),
    attempting to reject it again returns 409 Conflict.
    """
    _as(client, "token_staff_alex")
    b = seed_data["booking"]

    # Reject once
    res1 = client.post(f"/venues/bookings/{b.id}/reject", json={"reason": "First rejection"})
    assert res1.status_code == 200

    # Reject again
    res2 = client.post(f"/venues/bookings/{b.id}/reject", json={"reason": "Second rejection"})
    assert res2.status_code == 409
    assert "already been decided" in res2.json()["detail"].lower()
