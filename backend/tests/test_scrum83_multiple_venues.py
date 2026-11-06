"""SCRUM-83: one event can hold several venue bookings (Week 7 customer change 3).

Each booking stands on its own: its venue is checked on its own, and rejecting,
expiring or cancelling it leaves the event's other bookings as they were.
Frontend side: frontend/tests/scrum83-multiple-venues.test.js.
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

from backend import booking_rules
from backend.booking_rules import utc_now
from backend.database import Base, get_db
from backend.main import app
from backend.models import Event, LoginSession, Notification, User, Venue, VenueBooking
from backend.security import hash_password

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# One slot, far in the future, used by every booking unless a test says otherwise.
SLOT = {"booking_start": "2099-03-01T02:00:00", "booking_end": "2099-03-01T06:00:00"}


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
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def seed(db_session):
    """Two Coordinators and two events, both assigned to coord; two venues with the default setup and turnaround."""
    people = {}
    for username, role in [
        ("coord", "coordinator"),
        ("other_coord", "coordinator"),
        ("lead", "lead"),
        ("staff", "venue_staff"),
        ("organiser", "organiser"),
        ("guest", "attendee"),
    ]:
        user = User(username=username, password_hash=hash_password("test-only"), role=role)
        db_session.add(user)
        db_session.flush()
        db_session.add(LoginSession(user_id=user.id, token=f"token_{username}", valid=True))
        people[username] = user

    hall = Venue(name="Grand Hall", location="A", capacity=200, facilities=json.dumps(["Projector"]), is_active=True)
    room = Venue(name="Room B", location="B", capacity=50, facilities=json.dumps(["Projector"]), is_active=True)
    gala = Event(title="Gala", organiser_id=people["organiser"].id, status="Approved",
                 assigned_coordinator_id=people["coord"].id, expected_attendance=100)
    fair = Event(title="Fair", organiser_id=people["organiser"].id, status="Approved",
                 assigned_coordinator_id=people["coord"].id)
    db_session.add_all([hall, room, gala, fair])
    db_session.commit()
    return {"hall": hall, "room": room, "gala": gala, "fair": fair, **people}


def _as(client, username):
    client.cookies.set("connectsphere_session", f"token_{username}")


def _request(client, venue, event):
    """POST a booking request as the assigned Coordinator."""
    _as(client, "coord")
    return client.post(f"/venues/{venue.id}/bookings", json={"event_id": event.id, **SLOT})


def _both_venues(client, seed):
    """The Gala holds both venues for the same slot. Returns the two booking ids."""
    hall = _request(client, seed["hall"], seed["gala"]).json()["booking"]["id"]
    room = _request(client, seed["room"], seed["gala"]).json()["booking"]["id"]
    return hall, room


def _statuses(client, event):
    """{venue name: status} as GET /events/{id}/bookings shows them."""
    _as(client, "coord")
    return {b["venue_name"]: b["status"] for b in client.get(f"/events/{event.id}/bookings").json()}


def test_tc8301_two_venues_for_the_same_slot_are_both_accepted(client, seed):
    first = _request(client, seed["hall"], seed["gala"])
    second = _request(client, seed["room"], seed["gala"])

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["booking"]["id"] != second.json()["booking"]["id"]
    assert _statuses(client, seed["gala"]) == {"Grand Hall": "Pending", "Room B": "Pending"}


def test_tc8302_a_clash_at_one_venue_is_refused_without_affecting_the_other(client, seed):
    assert _request(client, seed["hall"], seed["fair"]).status_code == 201

    clash = _request(client, seed["hall"], seed["gala"])
    other = _request(client, seed["room"], seed["gala"])

    assert clash.status_code == 409
    assert "conflict" in clash.json()["detail"].lower()
    assert other.status_code == 201
    assert _statuses(client, seed["gala"]) == {"Room B": "Pending"}
    assert _statuses(client, seed["fair"]) == {"Grand Hall": "Pending"}


def test_tc8303_the_same_event_cannot_hold_one_venue_twice_for_a_slot(client, seed):
    _both_venues(client, seed)

    again = _request(client, seed["hall"], seed["gala"])

    assert again.status_code == 409
    assert _statuses(client, seed["gala"]) == {"Grand Hall": "Pending", "Room B": "Pending"}
    _as(client, "coord")
    assert len(client.get(f"/events/{seed['gala'].id}/bookings").json()) == 2


def test_tc8304_rejecting_one_booking_leaves_the_others(client, seed):
    hall, _room = _both_venues(client, seed)

    _as(client, "staff")
    assert client.post(f"/venues/bookings/{hall}/reject", json={"reason": "Closed for repairs"}).status_code == 200

    assert _statuses(client, seed["gala"]) == {"Grand Hall": "Rejected", "Room B": "Pending"}
    # The rejected venue is free again; the other is still held.
    assert _request(client, seed["hall"], seed["fair"]).status_code == 201
    assert _request(client, seed["room"], seed["fair"]).status_code == 409


def test_tc8305_expiring_one_booking_leaves_the_others(client, seed, db_session):
    hall, _room = _both_venues(client, seed)
    booking = db_session.query(VenueBooking).filter(VenueBooking.id == hall).one()
    booking.hold.expires_at = utc_now() - timedelta(minutes=1)
    db_session.commit()

    assert _statuses(client, seed["gala"]) == {"Grand Hall": "Expired", "Room B": "Pending"}
    assert _request(client, seed["hall"], seed["fair"]).status_code == 201
    assert _request(client, seed["room"], seed["fair"]).status_code == 409


def test_tc8306_cancelling_one_booking_leaves_the_others_and_frees_its_slot(client, seed):
    hall, _room = _both_venues(client, seed)

    _as(client, "coord")
    res = client.post(f"/venues/bookings/{hall}/cancel")

    assert res.status_code == 200
    assert res.json()["booking"]["status"] == "Cancelled"
    # The row stays as history, and only this booking changed.
    assert _statuses(client, seed["gala"]) == {"Grand Hall": "Cancelled", "Room B": "Pending"}
    assert _request(client, seed["hall"], seed["fair"]).status_code == 201
    assert _request(client, seed["room"], seed["fair"]).status_code == 409


def test_tc8307_a_confirmed_booking_can_be_cancelled_by_the_lead(client, seed):
    hall, _room = _both_venues(client, seed)
    _as(client, "staff")
    assert client.post(f"/venues/bookings/{hall}/approve").status_code == 200

    _as(client, "lead")
    res = client.post(f"/venues/bookings/{hall}/cancel")

    assert res.status_code == 200
    assert _statuses(client, seed["gala"]) == {"Grand Hall": "Cancelled", "Room B": "Pending"}
    assert _request(client, seed["hall"], seed["fair"]).status_code == 201


@pytest.mark.parametrize("username", ["other_coord", "staff", "organiser"])
def test_tc8308_only_the_assigned_coordinator_or_lead_can_cancel(client, seed, username):
    hall, _room = _both_venues(client, seed)

    _as(client, username)
    res = client.post(f"/venues/bookings/{hall}/cancel")

    assert res.status_code == 403
    assert _statuses(client, seed["gala"])["Grand Hall"] == "Pending"


def test_tc8309_cancelling_a_booking_that_does_not_exist_is_404(client, seed):
    _as(client, "lead")
    assert client.post("/venues/bookings/99999/cancel").status_code == 404


def test_tc8310_a_booking_no_longer_holding_the_venue_cannot_be_cancelled(client, seed, db_session):
    rejected, cancelled = _both_venues(client, seed)
    _as(client, "staff")
    client.post(f"/venues/bookings/{rejected}/reject", json={"reason": "Closed for repairs"})
    _as(client, "coord")
    assert client.post(f"/venues/bookings/{cancelled}/cancel").status_code == 200

    assert client.post(f"/venues/bookings/{rejected}/cancel").status_code == 400
    again = client.post(f"/venues/bookings/{cancelled}/cancel")
    assert again.status_code == 400
    assert "Cancelled" in again.json()["detail"]
    assert _statuses(client, seed["gala"]) == {"Grand Hall": "Rejected", "Room B": "Cancelled"}


def test_tc8311_an_expired_hold_cannot_be_cancelled_and_stays_expired(client, seed, db_session):
    hall, _room = _both_venues(client, seed)
    booking = db_session.query(VenueBooking).filter(VenueBooking.id == hall).one()
    booking.hold.expires_at = utc_now() - timedelta(minutes=1)
    db_session.commit()

    _as(client, "coord")
    res = client.post(f"/venues/bookings/{hall}/cancel")

    assert res.status_code == 400
    assert "Expired" in res.json()["detail"]
    assert _statuses(client, seed["gala"]) == {"Grand Hall": "Expired", "Room B": "Pending"}


def test_tc8312_a_booking_that_has_ended_cannot_be_cancelled(client, seed, db_session):
    # A past booking no longer holds the venue, and cancelling it would rewrite the record.
    now = utc_now().replace(microsecond=0)
    ended = VenueBooking(event_id=seed["gala"].id, venue_id=seed["hall"].id, status="Confirmed",
                         booking_start=now - timedelta(hours=3), booking_end=now - timedelta(hours=1))
    db_session.add(ended)
    db_session.commit()

    _as(client, "coord")
    assert client.post(f"/venues/bookings/{ended.id}/cancel").status_code == 400
    assert _statuses(client, seed["gala"]) == {"Grand Hall": "Confirmed"}


def test_tc8313_the_events_bookings_list_shows_all_of_them(client, seed, db_session):
    third = Venue(name="Studio C", location="C", capacity=30, is_active=True)
    fourth = Venue(name="Terrace D", location="D", capacity=30, is_active=True)
    db_session.add_all([third, fourth])
    db_session.commit()
    hall, room = _both_venues(client, seed)
    studio = _request(client, third, seed["gala"]).json()["booking"]["id"]
    _request(client, fourth, seed["gala"])

    _as(client, "staff")
    client.post(f"/venues/bookings/{hall}/approve")
    client.post(f"/venues/bookings/{room}/reject", json={"reason": "Closed for repairs"})
    _as(client, "coord")
    client.post(f"/venues/bookings/{studio}/cancel")

    assert _statuses(client, seed["gala"]) == {
        "Grand Hall": "Confirmed",
        "Room B": "Rejected",
        "Studio C": "Cancelled",
        "Terrace D": "Pending",
    }
    # Another event's booking does not appear in this event's list.
    assert _statuses(client, seed["fair"]) == {}


def test_tc8314_each_booked_venue_is_checked_for_suitability_on_its_own(client, seed):
    # The Gala expects 100 guests: Grand Hall (200) fits, Room B (50) does not.
    _both_venues(client, seed)

    _as(client, "coord")
    res = client.get(f"/events/{seed['gala'].id}/venue-suitability", params={"facilities": "Projector"})

    assert res.status_code == 200
    by_name = {v["name"]: v for v in res.json()["venues"]}
    assert by_name["Grand Hall"]["suitability"] == "suitable"
    assert by_name["Room B"]["suitability"] == "unsuitable"
    room_checks = {c["check"]: c["result"] for c in by_name["Room B"]["checks"]}
    assert room_checks == {"capacity": "fail", "facilities": "pass"}
    # An unsuitable venue's booking does not change the other booking, or its own request.
    assert _statuses(client, seed["gala"]) == {"Grand Hall": "Pending", "Room B": "Pending"}


def test_tc8320_cancelling_a_confirmed_events_last_confirmed_booking_leaves_it_with_no_venue(client, seed, db_session):
    # This pins what happens today. The confirm step warns about it, and the PO decides whether to refuse it instead.
    hall, room = _both_venues(client, seed)
    _as(client, "staff")
    assert client.post(f"/venues/bookings/{hall}/approve").status_code == 200
    assert client.post(f"/venues/bookings/{room}/approve").status_code == 200
    seed["gala"].status = "Confirmed"
    db_session.commit()

    _as(client, "coord")
    assert client.post(f"/venues/bookings/{hall}/cancel").status_code == 200
    _as(client, "guest")
    assert client.get(f"/events/{seed['gala'].id}").json()["venue_name"] == "Room B"

    _as(client, "coord")
    assert client.post(f"/venues/bookings/{room}/cancel").status_code == 200
    _as(client, "guest")
    seen = client.get(f"/events/{seed['gala'].id}")
    assert seen.status_code == 200
    assert seen.json()["venue_name"] is None
    db_session.refresh(seed["gala"])
    assert seed["gala"].status == "Confirmed"


def test_tc8321_a_cancelled_hold_stays_cancelled(client, seed, db_session):
    hall, _room = _both_venues(client, seed)
    _as(client, "coord")
    assert client.post(f"/venues/bookings/{hall}/cancel").status_code == 200

    # Nobody can decide or change the hold any more.
    _as(client, "staff")
    assert client.post(f"/venues/bookings/{hall}/approve").status_code == 409
    assert client.post(f"/venues/bookings/{hall}/reject", json={"reason": "Closed for repairs"}).status_code == 409
    assert client.put(f"/venues/bookings/{hall}/hold", json={"expires_at": "2098-01-01T00:00:00"}).status_code == 409
    _as(client, "coord")
    assert client.put(f"/venues/bookings/{hall}/hold/warning", json={"warning_minutes": 60}).status_code == 409

    # Its expiry passing changes nothing and sends no notice. Any request runs the hold checks.
    booking = db_session.query(VenueBooking).filter(VenueBooking.id == hall).one()
    booking.hold.expires_at = utc_now() - timedelta(minutes=1)
    db_session.commit()

    assert _statuses(client, seed["gala"]) == {"Grand Hall": "Cancelled", "Room B": "Pending"}
    db_session.refresh(booking)
    assert booking.status == "Cancelled"
    assert db_session.query(Notification).filter(Notification.kind.like("hold\\_%", escape="\\")).count() == 0


def test_tc8322_the_ended_check_follows_the_one_clock(client, seed, monkeypatch):
    # booking_rules.utc_now is the clock tests set, so the cancel route has to read it there.
    hall, _room = _both_venues(client, seed)
    _as(client, "staff")
    assert client.post(f"/venues/bookings/{hall}/approve").status_code == 200
    _as(client, "coord")

    # At exactly its end the booking has ended (SLOT ends 06:00 UTC).
    monkeypatch.setattr(booking_rules, "utc_now", lambda: datetime(2099, 3, 1, 6, 0))
    assert client.post(f"/venues/bookings/{hall}/cancel").status_code == 400

    monkeypatch.setattr(booking_rules, "utc_now", lambda: datetime(2099, 3, 1, 5, 59))
    assert client.post(f"/venues/bookings/{hall}/cancel").status_code == 200
