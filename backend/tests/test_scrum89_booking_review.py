"""SCRUM-89: Tests for reviewing a booking with full context and managing its hold.

As Venue Staff, I want everything I need in the booking review, including the
hold's expiry and any clash, so that I decide correctly and on time.

Acceptance criteria covered here (backend):
  AC1  The review shows the venue's capacity, layouts, accessibility and
       facilities, the hold's expiry and warning time, and any conflict flag.
       (Data from SCRUM-36, SCRUM-80 and SCRUM-81; proved here for this story.)
  AC2  Venue Staff can set or extend the hold's expiry with
       PUT /venues/bookings/{id}/hold. The expiry must be in the future and not
       after the booking's start (422); an expired hold is refused (409).
       (Endpoint from SCRUM-81; proved here for this story.)
  AC5  The requester is shown by name: GET /venues/bookings/{id} returns the
       requester's name. New in SCRUM-89.

AC3 and AC4, and the screen side of AC1, AC2 and AC5, are tested in
frontend/tests/venue-staff.test.js (TC-8904 to TC-8910).

Bookings, at Grand Hall on day D (30 days ahead):
  P  Pending    10:00-12:00  requested by coord_sarah, with a conflict flag
  N  Pending    14:00-16:00  no requester recorded (requests made before SCRUM-35)
  X  Pending    18:00-20:00  hold already expired
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.database import Base, get_db
from backend.main import app
from backend.models import (
    Event,
    LoginSession,
    User,
    Venue,
    VenueBooking,
    VenueBookingConflictFlag,
    VenueHold,
)
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
    staff = User(username="staff_alex", password_hash=hash_password("password123"), role="venue_staff")
    coord = User(username="coord_sarah", password_hash=hash_password("password123"),
                 role="coordinator", is_active_coordinator=True)
    organiser = User(username="org_dave", password_hash=hash_password("password123"), role="organiser")
    db_session.add_all([staff, coord, organiser])
    db_session.commit()
    db_session.add_all([
        LoginSession(user_id=staff.id, token="token_staff_alex", valid=True),
        LoginSession(user_id=coord.id, token="token_coord_sarah", valid=True),
    ])

    hall = Venue(
        name="Grand Hall",
        location="Building A",
        capacity=200,
        facilities=json.dumps(["Projector", "PA system"]),
        supported_layouts=json.dumps(["Theatre", "Banquet"]),
        accessibility="Step-free entrance, lift to all floors",
        operating_hours="08:00-22:00",
        setup_time_mins=30,
        turnaround_time_mins=45,
        is_active=True,
    )
    db_session.add(hall)
    db_session.commit()

    event = Event(title="Annual Tech Symposium", organiser_id=organiser.id, status="Approved",
                  proposed_start=datetime.utcnow() + timedelta(days=30),
                  assigned_coordinator_id=coord.id)
    db_session.add(event)
    db_session.commit()

    day = (datetime.utcnow() + timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)

    def at(h, m=0):
        return day + timedelta(hours=h, minutes=m)

    p = VenueBooking(event_id=event.id, venue_id=hall.id, requester_id=coord.id,
                     booking_start=at(10), booking_end=at(12), status="Pending")
    n = VenueBooking(event_id=event.id, venue_id=hall.id, requester_id=None,
                     booking_start=at(14), booking_end=at(16), status="Pending")
    x = VenueBooking(event_id=event.id, venue_id=hall.id, requester_id=coord.id,
                     booking_start=at(18), booking_end=at(20), status="Pending")
    db_session.add_all([p, n, x])
    db_session.commit()

    db_session.add_all([
        VenueBookingConflictFlag(booking_id=p.id, conflicting_booking_id=n.id,
                                 reason="Setup time now overlaps booking N"),
        VenueHold(booking_id=p.id, expires_at=datetime.utcnow() + timedelta(days=3), warning_minutes=120),
        VenueHold(booking_id=x.id, expires_at=datetime.utcnow() - timedelta(hours=1)),
    ])
    db_session.commit()

    return {"hall": hall, "event": event, "coord": coord, "p": p, "n": n, "x": x}


def _as(client, token):
    client.cookies.set("connectsphere_session", token)


def _review(client, booking):
    return client.get(f"/venues/bookings/{booking.id}")


def _set_hold(client, booking, expires_at):
    return client.put(f"/venues/bookings/{booking.id}/hold", json={"expires_at": expires_at.isoformat()})


# ---------------------------------------------------------------------------
# AC5: requester by name (new in SCRUM-89)
# ---------------------------------------------------------------------------

def test_tc8901_review_shows_requester_by_name(client, seed_data):
    """TC-8901 (AC5, Happy Path + Edge): the review returns the requester's
    name, and None (not an error) when no requester was recorded."""
    _as(client, "token_staff_alex")

    res = _review(client, seed_data["p"])
    assert res.status_code == 200
    assert res.json()["requester_name"] == "coord_sarah"
    assert res.json()["requester_id"] == seed_data["coord"].id  # unchanged

    res = _review(client, seed_data["n"])
    assert res.status_code == 200
    assert res.json()["requester_name"] is None


# ---------------------------------------------------------------------------
# AC1: the review's context (proves SCRUM-36 / 80 / 81 data for this story)
# ---------------------------------------------------------------------------

def test_tc8902_review_has_venue_hold_and_conflict_context(client, seed_data):
    """TC-8902 (AC1, Happy Path): the review includes the venue's capacity,
    layouts, accessibility and facilities, the hold's expiry and warning time,
    and the conflict flag."""
    _as(client, "token_staff_alex")
    body = _review(client, seed_data["p"]).json()

    venue = body["venue"]
    assert venue["capacity"] == 200
    assert venue["supported_layouts"] == ["Theatre", "Banquet"]
    assert venue["accessibility"] == "Step-free entrance, lift to all floors"
    assert venue["facilities"] == ["Projector", "PA system"]

    assert body["hold_expires_at"] is not None
    assert body["warning_minutes"] == 120

    assert body["conflict_flag"]["conflicting_booking_id"] == seed_data["n"].id
    assert body["conflict_flag"]["reason"] == "Setup time now overlaps booking N"


# ---------------------------------------------------------------------------
# AC2: set or extend the hold (proves SCRUM-81 D3 for this story)
# ---------------------------------------------------------------------------

def test_tc8903_hold_expiry_rules(client, seed_data):
    """TC-8903 (AC2, Negative + Happy Path): the new expiry must be in the
    future (422) and not after the booking's start (422); an expired hold is
    refused (409); a valid expiry is saved and returned."""
    _as(client, "token_staff_alex")
    p = seed_data["p"]

    res = _set_hold(client, p, datetime.utcnow() - timedelta(minutes=5))
    assert res.status_code == 422
    assert "future" in res.json()["detail"]

    res = _set_hold(client, p, p.booking_start + timedelta(minutes=1))
    assert res.status_code == 422
    assert "start" in res.json()["detail"]

    res = _set_hold(client, seed_data["x"], datetime.utcnow() + timedelta(days=1))
    assert res.status_code == 409
    assert "expired" in res.json()["detail"]

    new_expiry = (datetime.utcnow() + timedelta(days=5)).replace(microsecond=0)
    res = _set_hold(client, p, new_expiry)
    assert res.status_code == 200
    assert res.json()["booking"]["hold_expires_at"] == new_expiry.isoformat()
