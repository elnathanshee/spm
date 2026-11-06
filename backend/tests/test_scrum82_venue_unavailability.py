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


# ---------------------------------------------------------------------------
# AC2: the availability check treats the period as blocked
# ---------------------------------------------------------------------------

def _request(client, venue, event, start, end):
    body = {"event_id": event.id, "booking_start": start.isoformat(), "booking_end": end.isoformat()}
    return client.post(f"/venues/{venue.id}/bookings", json=body)


def _record(db_session, venue, user, start=None, end=None, reason="Maintenance"):
    """A period saved directly, for tests about what it blocks, not how it is recorded."""
    period = VenueUnavailability(venue_id=venue.id, start_at=start or at(10), end_at=end or at(12),
                                 reason=reason, created_by_id=user.id)
    db_session.add(period)
    db_session.commit()
    return period


def test_tc8211_new_request_in_a_period_is_refused_with_the_reason(client, db_session, seed):
    """TC-8211: 409 naming the reason in Singapore time; no booking is stored."""
    _record(db_session, seed["hall"], seed["staff"], reason="Renovation")
    _as(client, "coord")
    res = _request(client, seed["hall"], seed["event"], at(10, 30), at(11, 30))
    assert res.status_code == 409
    assert "Renovation" in res.json()["detail"]
    assert "2099-06-01 18:00" in res.json()["detail"]
    assert db_session.query(VenueBooking).count() == 0


def test_tc8212_setup_and_turnaround_count_and_touching_edges_do_not_clash(client, db_session, seed):
    """TC-8212: the period is 10:00-12:00, with 30 min setup and 45 min turnaround.
    A request starting 12:30 occupies from 12:00 (touches): accepted. Starting
    12:29 occupies from 11:59: refused. Ending 09:15 occupies to 10:00 (touches):
    accepted. Ending 09:16 occupies to 10:01: refused."""
    _record(db_session, seed["hall"], seed["staff"])
    _as(client, "coord")
    assert _request(client, seed["hall"], seed["event"], at(12, 29), at(12, 40)).status_code == 409
    assert _request(client, seed["hall"], seed["event"], at(8), at(9, 16)).status_code == 409
    assert _request(client, seed["hall"], seed["event"], at(12, 30), at(13)).status_code == 201
    assert _request(client, seed["hall"], seed["event"], at(8), at(9, 15)).status_code == 201


def test_tc8213_period_at_another_venue_does_not_block(client, db_session, seed):
    """TC-8213: the same slot at another venue is still free."""
    _record(db_session, seed["room"], seed["staff"])
    _as(client, "coord")
    assert _request(client, seed["hall"], seed["event"], at(10), at(11)).status_code == 201


def test_tc8214_approval_is_refused_while_a_period_overlaps(client, db_session, seed):
    """TC-8214: a Pending request can't be approved during a period and stays
    Pending. Rejecting still works, because it is how staff resolve it."""
    keep = _booking(db_session, seed["hall"], seed["event"], at(10), at(11), "Pending")
    drop = _booking(db_session, seed["hall"], seed["event"], at(15), at(16), "Pending")
    _record(db_session, seed["hall"], seed["staff"], at(9), at(17), "Safety")
    _as(client, "staff")
    res = client.post(f"/venues/bookings/{keep.id}/approve")
    assert res.status_code == 409
    assert "Cannot approve" in res.json()["detail"] and "Safety" in res.json()["detail"]
    db_session.refresh(keep)
    assert keep.status == "Pending"
    assert client.post(f"/venues/bookings/{drop.id}/reject", json={"reason": "Venue closed"}).status_code == 200


def test_tc8215_approval_goes_through_once_the_period_is_removed(client, db_session, seed):
    """TC-8215: removing the period is all that is needed."""
    booking = _booking(db_session, seed["hall"], seed["event"], at(10), at(11), "Pending")
    period = _record(db_session, seed["hall"], seed["staff"])
    _as(client, "staff")
    assert client.post(f"/venues/bookings/{booking.id}/approve").status_code == 409
    assert client.delete(f"/venues/{seed['hall'].id}/unavailability/{period.id}").status_code == 200
    assert client.post(f"/venues/bookings/{booking.id}/approve").status_code == 200


def test_tc8216_a_hold_cant_be_extended_while_a_period_overlaps(client, db_session, seed):
    """TC-8216: Venue Staff can't extend a hold on a blocked slot (409). The
    Coordinator can still change their own warning time."""
    hold = _booking(db_session, seed["hall"], seed["event"], at(10), at(11), "Pending")
    _record(db_session, seed["hall"], seed["staff"], reason="Equipment failure")
    _as(client, "staff")
    res = client.put(f"/venues/bookings/{hold.id}/hold", json={"expires_at": at(9).isoformat()})
    assert res.status_code == 409
    assert "Equipment failure" in res.json()["detail"]
    _as(client, "coord")
    assert client.put(f"/venues/bookings/{hold.id}/hold/warning", json={"warning_minutes": 30}).status_code == 200


def test_tc8217_search_shows_the_venue_as_unavailable_with_the_reason(client, db_session, seed):
    """TC-8217: is_available false and the reason in conflict_reason; available_only
    leaves it out; a period that only touches the occupied window doesn't count."""
    _record(db_session, seed["hall"], seed["staff"], reason="Safety")
    _as(client, "coord")
    params = {"start_time": at(10).isoformat(), "end_time": at(11).isoformat()}
    results = {v["name"]: v for v in client.get("/venues/search", params=params).json()}
    assert results["Grand Hall"]["is_available"] is False
    assert "Safety" in results["Grand Hall"]["conflict_reason"]
    assert results["Small Room"]["is_available"] is True
    only = client.get("/venues/search", params={**params, "available_only": True}).json()
    assert [v["name"] for v in only] == ["Small Room"]
    touching = {"start_time": at(12, 30).isoformat(), "end_time": at(13).isoformat()}
    assert all(v["is_available"] for v in client.get("/venues/search", params=touching).json())


def test_tc8218_suitability_shows_the_venue_as_unavailable_with_the_reason(client, db_session, seed):
    """TC-8218: the event's window overlaps the period, so the venue gets a failed
    availability check naming the reason and is unsuitable. The other venue has
    only its two usual checks."""
    _booking(db_session, seed["room"], seed["event"], at(10), at(11))
    _record(db_session, seed["hall"], seed["staff"], reason="Renovation")
    _as(client, "coord")
    res = client.get(f"/events/{seed['event'].id}/venue-suitability", params={"facilities": "Projector"})
    venues = {v["name"]: v for v in res.json()["venues"]}
    hall = venues["Grand Hall"]
    assert hall["suitability"] == "unsuitable"
    availability = next(c for c in hall["checks"] if c["check"] == "availability")
    assert availability["result"] == "fail"
    assert "Renovation" in availability["detail"]
    assert venues["Small Room"]["suitability"] == "suitable"
    assert [c["check"] for c in venues["Small Room"]["checks"]] == ["capacity", "facilities"]


def test_tc8219_suitability_ignores_a_period_outside_the_events_window(client, db_session, seed):
    """TC-8219: a period on another day leaves the venue suitable."""
    _booking(db_session, seed["room"], seed["event"], at(10), at(11))
    _record(db_session, seed["hall"], seed["staff"], at(10, days=3), at(12, days=3))
    _as(client, "coord")
    res = client.get(f"/events/{seed['event'].id}/venue-suitability", params={"facilities": "Projector"})
    assert {v["name"]: v["suitability"] for v in res.json()["venues"]}["Grand Hall"] == "suitable"


# ---------------------------------------------------------------------------
# AC3: the calendar shows the period
# ---------------------------------------------------------------------------

def _calendar(client, venue, first, last):
    return client.get(f"/venues/{venue.id}/calendar", params={"from": first, "to": last}).json()


def test_tc8220_calendar_lists_the_period_with_its_reason(client, db_session, seed):
    """TC-8220: unavailable_periods has the period, and the day it covers says so.
    The next day has none."""
    period = _record(db_session, seed["hall"], seed["staff"], reason="Maintenance")
    _as(client, "coord")
    body = _calendar(client, seed["hall"], "2099-06-01", "2099-06-02")
    assert [(p["id"], p["reason"], p["start_at"]) for p in body["unavailable_periods"]] == [
        (period.id, "Maintenance", at(10).isoformat())
    ]
    assert [p["id"] for p in body["days"][0]["unavailable"]] == [period.id]
    assert body["days"][1]["unavailable"] == []


def test_tc8221_calendar_days_are_singapore_days(client, db_session, seed):
    """TC-8221: 18:00-19:00 UTC on 1 June is 02:00-03:00 on 2 June in Singapore,
    so it shows on a calendar of 2 June and not on one of 1 June."""
    _record(db_session, seed["hall"], seed["staff"], at(18), at(19))
    _as(client, "coord")
    assert len(_calendar(client, seed["hall"], "2099-06-02", "2099-06-02")["unavailable_periods"]) == 1
    assert _calendar(client, seed["hall"], "2099-06-01", "2099-06-01")["unavailable_periods"] == []


def test_tc8222_calendar_leaves_out_a_period_that_only_touches_the_range(client, db_session, seed):
    """TC-8222: a period ending at exactly midnight Singapore time on 2 June
    (16:00 UTC on 1 June) is not on a calendar of 2 June, and another venue's
    period is on no calendar of this venue."""
    _record(db_session, seed["hall"], seed["staff"], at(10), at(16))
    _record(db_session, seed["room"], seed["staff"], at(16), at(18))
    _as(client, "coord")
    assert _calendar(client, seed["hall"], "2099-06-02", "2099-06-02")["unavailable_periods"] == []
