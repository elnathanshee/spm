"""SCRUM-82: A venue becomes unavailable after booking (Week 7 customer change 2).

Criteria, in the Product Owner's words:
  AC1  Venue Staff record an unavailability period (start, end, reason) and can
       remove it.
  AC2  The availability check treats the period as blocked, for new requests and
       for approvals. Search and suitability show it with the reason.
  AC3  The calendar shows it in unavailable_periods.
  AC4  Existing Pending and Confirmed bookings that overlap are flagged with the
       SCRUM-80 flag and are not cancelled. The event's information is preserved.
  AC5  The assigned Coordinator of each affected event gets an in-app notice.
  AC6  The Coordinator sees the flag on the booking and can request a replacement.

Times: Grand Hall has 30 min setup and 45 min turnaround. Times below are UTC on
a far-future day, and Singapore is 8 hours ahead.
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
    EventStatusHistory,
    LoginSession,
    Notification,
    User,
    Venue,
    VenueBooking,
    VenueBookingConflictFlag,
    VenueUnavailability,
)
from backend.security import hash_password

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

DAY = datetime(2099, 6, 1)


def at(hour, minute=0, days=0):
    return DAY + timedelta(days=days, hours=hour, minutes=minute)


@pytest.fixture
def db_session():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _user(db_session, name, role):
    user = User(username=name, password_hash=hash_password("not-a-real-secret"), role=role,
                is_active_coordinator=role == "coordinator")
    db_session.add(user)
    db_session.flush()
    db_session.add(LoginSession(user_id=user.id, token=f"tok_{name}", valid=True))
    return user


def _venue(db_session, name):
    venue = Venue(name=name, location="Block A", capacity=100, facilities=json.dumps(["Projector"]),
                  setup_time_mins=30, turnaround_time_mins=45, is_active=True)
    db_session.add(venue)
    db_session.flush()
    return venue


def _event(db_session, title, organiser, coordinator):
    event = Event(title=title, organiser_id=organiser.id, status="Planning", expected_attendance=50,
                  proposed_start=at(18), assigned_coordinator_id=coordinator.id if coordinator else None)
    db_session.add(event)
    db_session.flush()
    return event


def _booking(db_session, venue, event, start, end, status="Confirmed"):
    booking = VenueBooking(venue_id=venue.id, event_id=event.id, booking_start=start, booking_end=end, status=status)
    db_session.add(booking)
    db_session.flush()
    return booking


@pytest.fixture
def seed(db_session):
    staff = _user(db_session, "staff", "venue_staff")
    coord = _user(db_session, "coord", "coordinator")
    other_coord = _user(db_session, "coord2", "coordinator")
    lead = _user(db_session, "lead", "lead")
    organiser = _user(db_session, "org", "organiser")
    hall = _venue(db_session, "Grand Hall")
    room = _venue(db_session, "Small Room")
    event = _event(db_session, "Annual Symposium", organiser, coord)
    db_session.commit()
    return {"staff": staff, "coord": coord, "other_coord": other_coord, "lead": lead, "organiser": organiser,
            "hall": hall, "room": room, "event": event}


def _as(client, name):
    client.cookies.set("connectsphere_session", f"tok_{name}")


def _post_period(client, venue, start=None, end=None, reason="Maintenance"):
    body = {"start_at": (start or at(10)).isoformat(), "end_at": (end or at(12)).isoformat(), "reason": reason}
    return client.post(f"/venues/{venue.id}/unavailability", json=body)


# ---------------------------------------------------------------------------
# AC1: record and remove a period
# ---------------------------------------------------------------------------

def test_tc8201_venue_staff_record_a_period(client, db_session, seed):
    """TC-8201: the period is stored with its reason and the staff member's id."""
    _as(client, "staff")
    res = _post_period(client, seed["hall"], reason="  Equipment failure  ")
    assert res.status_code == 201
    period = res.json()["period"]
    assert period["venue_id"] == seed["hall"].id
    assert period["start_at"] == at(10).isoformat()
    assert period["end_at"] == at(12).isoformat()
    assert period["reason"] == "Equipment failure"
    row = db_session.query(VenueUnavailability).one()
    assert row.created_by_id == seed["staff"].id


def test_tc8202_time_with_an_offset_is_stored_as_naive_utc(client, db_session, seed):
    """TC-8202: 18:00+08:00 is 10:00 UTC, stored without a zone like bookings."""
    _as(client, "staff")
    body = {"start_at": "2099-06-01T18:00:00+08:00", "end_at": "2099-06-01T20:00:00+08:00", "reason": "Safety"}
    res = client.post(f"/venues/{seed['hall'].id}/unavailability", json=body)
    assert res.status_code == 201
    row = db_session.query(VenueUnavailability).one()
    assert row.start_at == at(10)
    assert row.end_at == at(12)


@pytest.mark.parametrize("start,end", [
    (at(12), at(12)),  # end equal to start
    (at(12), at(10)),  # end before start
])
def test_tc8203_end_must_be_after_start(client, db_session, seed, start, end):
    """TC-8203: an end at or before the start gives 422 and stores nothing."""
    _as(client, "staff")
    res = _post_period(client, seed["hall"], start, end)
    assert res.status_code == 422
    assert db_session.query(VenueUnavailability).count() == 0


def test_tc8204_end_is_compared_after_converting_mixed_offsets(client, db_session, seed):
    """TC-8204: an offset on one end only is a real comparison, not a crash.
    18:00+08:00 is 10:00 UTC, so an end of 09:00 UTC is before the start."""
    _as(client, "staff")
    body = {"start_at": "2099-06-01T18:00:00+08:00", "end_at": "2099-06-01T09:00:00", "reason": "Safety"}
    assert client.post(f"/venues/{seed['hall'].id}/unavailability", json=body).status_code == 422


@pytest.mark.parametrize("reason", ["", "    "])
def test_tc8205_reason_is_required(client, db_session, seed, reason):
    """TC-8205: a blank reason gives 422, and so does leaving it out."""
    _as(client, "staff")
    assert _post_period(client, seed["hall"], reason=reason).status_code == 422
    body = {"start_at": at(10).isoformat(), "end_at": at(12).isoformat()}
    assert client.post(f"/venues/{seed['hall'].id}/unavailability", json=body).status_code == 422
    assert db_session.query(VenueUnavailability).count() == 0


@pytest.mark.parametrize("name", ["coord", "lead", "org"])
def test_tc8206_only_venue_staff_can_write(client, db_session, seed, name):
    """TC-8206: other roles get 403 on POST and DELETE, and nothing changes."""
    _as(client, "staff")
    period_id = _post_period(client, seed["hall"]).json()["period"]["id"]
    _as(client, name)
    assert _post_period(client, seed["hall"], at(14), at(15)).status_code == 403
    assert client.delete(f"/venues/{seed['hall'].id}/unavailability/{period_id}").status_code == 403
    assert db_session.query(VenueUnavailability).count() == 1


def test_tc8207_list_periods_in_start_order(client, seed):
    """TC-8207: Venue Staff and Coordinators read a venue's periods, earliest
    first. An Organiser cannot, as with the venue calendar."""
    _as(client, "staff")
    _post_period(client, seed["hall"], at(14), at(15), "Renovation")
    _post_period(client, seed["hall"], at(10), at(11), "Maintenance")
    _post_period(client, seed["room"], at(10), at(11), "Safety")
    url = f"/venues/{seed['hall'].id}/unavailability"
    assert [p["reason"] for p in client.get(url).json()] == ["Maintenance", "Renovation"]
    _as(client, "coord")
    assert client.get(url).status_code == 200
    _as(client, "org")
    assert client.get(url).status_code == 403


def test_tc8208_remove_a_period(client, db_session, seed):
    """TC-8208: DELETE removes the row; it no longer appears in the list."""
    _as(client, "staff")
    period_id = _post_period(client, seed["hall"]).json()["period"]["id"]
    res = client.delete(f"/venues/{seed['hall'].id}/unavailability/{period_id}")
    assert res.status_code == 200
    assert client.get(f"/venues/{seed['hall'].id}/unavailability").json() == []
    assert db_session.query(VenueUnavailability).count() == 0


def test_tc8209_unknown_venue_or_another_venues_period_gives_404(client, db_session, seed):
    """TC-8209: a period can only be removed through its own venue."""
    _as(client, "staff")
    period_id = _post_period(client, seed["hall"]).json()["period"]["id"]
    assert client.delete(f"/venues/{seed['room'].id}/unavailability/{period_id}").status_code == 404
    assert client.delete(f"/venues/{seed['hall'].id}/unavailability/99999").status_code == 404
    assert client.get("/venues/99999/unavailability").status_code == 404
    body = {"start_at": at(10).isoformat(), "end_at": at(12).isoformat(), "reason": "Safety"}
    assert client.post("/venues/99999/unavailability", json=body).status_code == 404
    assert db_session.query(VenueUnavailability).count() == 1


def test_tc8210_same_period_twice_is_refused(client, db_session, seed):
    """TC-8210: a double submit gives 409, so nobody is told twice. A different
    reason over the same times is a different period and is kept."""
    _as(client, "staff")
    assert _post_period(client, seed["hall"]).status_code == 201
    assert _post_period(client, seed["hall"]).status_code == 409
    assert _post_period(client, seed["hall"], reason="Safety").status_code == 201
    assert db_session.query(VenueUnavailability).count() == 2
