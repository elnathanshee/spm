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


# ---------------------------------------------------------------------------
# AC4: overlapping bookings are flagged, never cancelled
# ---------------------------------------------------------------------------

def _staff_view(client, venue, booking):
    """The booking as Venue Staff see it in the venue's list."""
    _as(client, "staff")
    rows = client.get(f"/venues/{venue.id}/bookings").json()
    return next(row for row in rows if row["booking_id"] == booking.id)


def test_tc8223_pending_and_confirmed_bookings_are_flagged_and_keep_their_status(client, db_session, seed):
    """TC-8223: both are flagged and returned in affected_bookings with the reason.
    The status of each is unchanged. The flag row names the booking itself."""
    confirmed = _booking(db_session, seed["hall"], seed["event"], at(10), at(11), "Confirmed")
    pending = _booking(db_session, seed["hall"], seed["event"], at(14), at(15), "Pending")
    db_session.commit()
    _as(client, "staff")
    res = _post_period(client, seed["hall"], at(9), at(18), "Renovation")
    assert res.status_code == 201
    body = res.json()
    assert sorted(b["booking_id"] for b in body["affected_bookings"]) == [confirmed.id, pending.id]
    for row in body["affected_bookings"]:
        assert "Renovation" in row["conflict_flag"]["reason"]
    db_session.expire_all()
    assert (confirmed.status, pending.status) == ("Confirmed", "Pending")
    assert confirmed.conflict_flag.conflicting_booking_id == confirmed.id
    assert pending.conflict_flag.conflicting_booking_id == pending.id


def test_tc8224_bookings_that_dont_hold_the_venue_or_dont_overlap_are_not_flagged(client, db_session, seed):
    """TC-8224: Rejected, an expired hold, an overlap at another venue, and a booking
    whose occupied window only touches the period (12:30 occupies from 12:00) are
    left alone. Starting at 12:29, a booking occupies from 11:59 and is flagged."""
    rejected = _booking(db_session, seed["hall"], seed["event"], at(10), at(11), "Rejected")
    expired = _booking(db_session, seed["hall"], seed["event"], at(10), at(11), "Expired")
    elsewhere = _booking(db_session, seed["room"], seed["event"], at(10), at(11))
    touching = _booking(db_session, seed["hall"], seed["event"], at(12, 30), at(13))
    overlapping = _booking(db_session, seed["hall"], seed["event"], at(12, 29), at(12, 30))
    db_session.commit()
    _as(client, "staff")
    body = _post_period(client, seed["hall"]).json()
    assert [b["booking_id"] for b in body["affected_bookings"]] == [overlapping.id]
    assert db_session.query(VenueBookingConflictFlag).count() == 1
    for untouched in (rejected, expired, elsewhere, touching):
        assert untouched.conflict_flag is None


def test_tc8225_a_booking_that_has_ended_is_not_flagged(client, db_session, seed):
    """TC-8225: a period recorded over the past flags nothing: nobody can make
    other arrangements for a booking that is over."""
    past = datetime(2020, 1, 1, 10)
    _booking(db_session, seed["hall"], seed["event"], past, past + timedelta(hours=1))
    db_session.commit()
    _as(client, "staff")
    res = _post_period(client, seed["hall"], past, past + timedelta(hours=2))
    assert res.status_code == 201
    assert res.json()["affected_bookings"] == []
    assert db_session.query(VenueBookingConflictFlag).count() == 0
    assert db_session.query(Notification).count() == 0


def test_tc8226_the_events_own_information_is_preserved(client, db_session, seed):
    """TC-8226: recording and removing a period leaves the event's fields and its
    status history exactly as they were, and the booking's times too."""
    event = seed["event"]
    booking = _booking(db_session, seed["hall"], event, at(10), at(11))
    db_session.commit()
    before = (event.title, event.status, event.proposed_start, event.expected_attendance, event.assigned_coordinator_id)
    history_before = db_session.query(EventStatusHistory).count()
    _as(client, "staff")
    period_id = _post_period(client, seed["hall"]).json()["period"]["id"]
    client.delete(f"/venues/{seed['hall'].id}/unavailability/{period_id}")
    db_session.expire_all()
    assert (event.title, event.status, event.proposed_start, event.expected_attendance,
            event.assigned_coordinator_id) == before
    assert db_session.query(EventStatusHistory).count() == history_before
    assert (booking.booking_start, booking.booking_end, booking.status) == (at(10), at(11), "Confirmed")


def test_tc8227_removing_the_period_clears_the_flag_without_further_work(client, db_session, seed):
    """TC-8227: the flag shows while the period exists, naming its reason in
    Singapore time. Once removed it is gone, and the stored row reads Resolved."""
    booking = _booking(db_session, seed["hall"], seed["event"], at(10), at(11))
    db_session.commit()
    _as(client, "staff")
    period_id = _post_period(client, seed["hall"], at(9), at(12), "Equipment failure").json()["period"]["id"]
    flag = _staff_view(client, seed["hall"], booking)["conflict_flag"]
    assert "Equipment failure" in flag["reason"] and "2099-06-01 17:00" in flag["reason"]
    assert flag["unavailability_id"] == period_id

    client.delete(f"/venues/{seed['hall'].id}/unavailability/{period_id}")
    row = _staff_view(client, seed["hall"], booking)
    assert row["conflict_flag"] is None
    assert row["conflict_flag_record"]["state"] == "Resolved"
    assert db_session.query(VenueBookingConflictFlag).count() == 1


def test_tc8228_a_booking_with_a_clash_flag_reuses_its_row(client, db_session, seed):
    """TC-8228: the flag table allows one row per booking. A booking already flagged
    for a clash keeps its row and the clash it names. The period shows while it
    exists, then the clash shows again."""
    first = _booking(db_session, seed["hall"], seed["event"], at(10), at(11))
    other = _booking(db_session, seed["hall"], seed["event"], at(10, 30), at(11, 30))
    db_session.add(VenueBookingConflictFlag(booking_id=first.id, conflicting_booking_id=other.id, reason="Old clash"))
    db_session.commit()
    _as(client, "staff")
    res = _post_period(client, seed["hall"], at(9), at(12), "Safety")
    assert res.status_code == 201
    row = db_session.query(VenueBookingConflictFlag).filter_by(booking_id=first.id).one()
    assert (row.conflicting_booking_id, row.reason) == (other.id, "Old clash")
    assert "Safety" in _staff_view(client, seed["hall"], first)["conflict_flag"]["reason"]

    client.delete(f"/venues/{seed['hall'].id}/unavailability/{res.json()['period']['id']}")
    flag = _staff_view(client, seed["hall"], first)["conflict_flag"]
    assert flag["conflicting_booking_id"] == other.id
    assert "Safety" not in flag["reason"]


def test_tc8229_two_periods_keep_the_flag_until_both_are_gone(client, db_session, seed):
    """TC-8229: with two overlapping periods, removing the first shows the second's
    reason; removing that clears the flag. A later period flags the booking again
    with its own reason, on the same row."""
    booking = _booking(db_session, seed["hall"], seed["event"], at(10), at(11))
    db_session.commit()
    _as(client, "staff")
    url = f"/venues/{seed['hall'].id}/unavailability"
    first = _post_period(client, seed["hall"], at(9), at(10, 30), "Maintenance").json()["period"]["id"]
    second = _post_period(client, seed["hall"], at(10, 30), at(12), "Safety").json()["period"]["id"]
    assert "Maintenance" in _staff_view(client, seed["hall"], booking)["conflict_flag"]["reason"]
    client.delete(f"{url}/{first}")
    assert "Safety" in _staff_view(client, seed["hall"], booking)["conflict_flag"]["reason"]
    client.delete(f"{url}/{second}")
    assert _staff_view(client, seed["hall"], booking)["conflict_flag"] is None

    body = _post_period(client, seed["hall"], at(9), at(12), "Renovation").json()
    assert "Renovation" in body["affected_bookings"][0]["conflict_flag"]["reason"]
    row = db_session.query(VenueBookingConflictFlag).one()
    assert "Renovation" in row.reason


def test_tc8230_longer_buffers_can_reach_a_period_and_flag_the_booking(client, db_session, seed):
    """TC-8230: the booking ends 09:15, so with 45 min turnaround it only touches
    the 10:00 period. A turnaround of 60 min overlaps it: SCRUM-80's re-check flags
    the booking and reports it, with the period's reason."""
    booking = _booking(db_session, seed["hall"], seed["event"], at(8), at(9, 15))
    db_session.commit()
    _as(client, "staff")
    assert _post_period(client, seed["hall"], reason="Maintenance").json()["affected_bookings"] == []
    res = client.patch(f"/venues/{seed['hall'].id}", json={"turnaround_time_mins": 60})
    flagged = res.json()["flagged_bookings"]
    assert [b["booking_id"] for b in flagged] == [booking.id]
    assert "Maintenance" in flagged[0]["conflict_flag"]["reason"]


def test_tc8231_a_row_left_by_a_removed_period_doesnt_hide_a_new_clash(client, db_session, seed):
    """TC-8231: after the period is removed the row remains as history. A later
    buffer change that makes a real clash still counts as new and is reported,
    naming the other booking."""
    first = _booking(db_session, seed["hall"], seed["event"], at(10), at(11))
    other = _booking(db_session, seed["hall"], seed["event"], at(12), at(13))
    db_session.commit()
    _as(client, "staff")
    period_id = _post_period(client, seed["hall"], at(9), at(10, 30)).json()["period"]["id"]
    client.delete(f"/venues/{seed['hall'].id}/unavailability/{period_id}")
    res = client.patch(f"/venues/{seed['hall'].id}", json={"turnaround_time_mins": 120})
    flagged = {b["booking_id"]: b for b in res.json()["flagged_bookings"]}
    assert flagged[first.id]["conflict_flag"]["conflicting_booking_id"] == other.id


# ---------------------------------------------------------------------------
# AC5: the assigned Coordinator is told
# ---------------------------------------------------------------------------

def test_tc8232_coordinator_is_told_once_per_event_with_the_details(client, db_session, seed):
    """TC-8232: an event with two affected bookings gets one notice for its
    Coordinator. It names the venue, the Singapore-time period, the reason and
    that alternative arrangements are needed, and nobody else is told."""
    _booking(db_session, seed["hall"], seed["event"], at(10), at(11))
    _booking(db_session, seed["hall"], seed["event"], at(14), at(15), "Pending")
    db_session.commit()
    _as(client, "staff")
    assert _post_period(client, seed["hall"], at(9), at(18), "Equipment failure").status_code == 201
    notice = db_session.query(Notification).one()
    assert notice.user_id == seed["coord"].id
    assert notice.event_id == seed["event"].id
    assert notice.booking_id is None
    assert notice.kind == "venue_unavailable"
    for text in ("Grand Hall", "Equipment failure", "2099-06-01 17:00", "2099-06-02 02:00", "alternative arrangements"):
        assert text in notice.message


def test_tc8233_each_affected_event_tells_its_own_coordinator(client, db_session, seed):
    """TC-8233: two events with different Coordinators: one notice each."""
    second = _event(db_session, "Careers Fair", seed["organiser"], seed["other_coord"])
    _booking(db_session, seed["hall"], seed["event"], at(10), at(11))
    _booking(db_session, seed["hall"], second, at(14), at(15))
    db_session.commit()
    _as(client, "staff")
    _post_period(client, seed["hall"], at(9), at(18))
    told = {(n.user_id, n.event_id) for n in db_session.query(Notification).all()}
    assert told == {(seed["coord"].id, seed["event"].id), (seed["other_coord"].id, second.id)}
    assert db_session.query(Notification).count() == 2


def test_tc8234_a_second_period_is_a_second_notice(client, db_session, seed):
    """TC-8234: once per event per period: another period tells them again, and
    the unique rule on notifications doesn't get in the way."""
    _booking(db_session, seed["hall"], seed["event"], at(10), at(11))
    db_session.commit()
    _as(client, "staff")
    assert _post_period(client, seed["hall"], at(9), at(11), "Maintenance").status_code == 201
    assert _post_period(client, seed["hall"], at(10), at(12), "Safety").status_code == 201
    assert db_session.query(Notification).count() == 2


def test_tc8235_no_coordinator_means_no_notice_but_the_booking_is_returned(client, db_session, seed):
    """TC-8235: the event has no assigned Coordinator: 201, the booking is flagged
    and returned in affected_bookings, and nobody is notified."""
    unassigned = _event(db_session, "Unassigned Meetup", seed["organiser"], None)
    booking = _booking(db_session, seed["hall"], unassigned, at(10), at(11))
    db_session.commit()
    _as(client, "staff")
    res = _post_period(client, seed["hall"])
    assert res.status_code == 201
    assert [b["booking_id"] for b in res.json()["affected_bookings"]] == [booking.id]
    assert db_session.query(Notification).count() == 0


def test_tc8236_a_period_with_nothing_affected_tells_nobody(client, db_session, seed):
    """TC-8236: no bookings in the period: an empty list and no notices."""
    _booking(db_session, seed["hall"], seed["event"], at(10, days=5), at(11, days=5))
    db_session.commit()
    _as(client, "staff")
    assert _post_period(client, seed["hall"]).json()["affected_bookings"] == []
    assert db_session.query(Notification).count() == 0


# ---------------------------------------------------------------------------
# AC6: the Coordinator sees the flag and can request a replacement
# ---------------------------------------------------------------------------

def test_tc8237_coordinator_sees_the_flag_on_the_events_bookings(client, db_session, seed):
    """TC-8237: GET /events/{id}/bookings carries the flag on the affected booking
    only, and not once the period is removed."""
    hit = _booking(db_session, seed["hall"], seed["event"], at(10), at(11))
    miss = _booking(db_session, seed["room"], seed["event"], at(10), at(11))
    db_session.commit()
    _as(client, "staff")
    period_id = _post_period(client, seed["hall"], reason="Safety").json()["period"]["id"]
    _as(client, "coord")
    rows = {r["id"]: r for r in client.get(f"/events/{seed['event'].id}/bookings").json()}
    assert "Safety" in rows[hit.id]["conflict_flag"]["reason"]
    assert rows[miss.id]["conflict_flag"] is None

    _as(client, "staff")
    client.delete(f"/venues/{seed['hall'].id}/unavailability/{period_id}")
    _as(client, "coord")
    rows = {r["id"]: r for r in client.get(f"/events/{seed['event'].id}/bookings").json()}
    assert rows[hit.id]["conflict_flag"] is None


def test_tc8238_coordinator_can_find_and_request_a_replacement(client, db_session, seed):
    """TC-8238: search shows the flagged venue as unavailable and the other as free.
    The request at the other venue succeeds and the flagged booking stays."""
    original = _booking(db_session, seed["hall"], seed["event"], at(10), at(11))
    db_session.commit()
    _as(client, "staff")
    _post_period(client, seed["hall"])
    _as(client, "coord")
    params = {"start_time": at(10).isoformat(), "end_time": at(11).isoformat(), "available_only": True}
    assert [v["name"] for v in client.get("/venues/search", params=params).json()] == ["Small Room"]
    assert _request(client, seed["room"], seed["event"], at(10), at(11)).status_code == 201
    db_session.expire_all()
    assert original.status == "Confirmed"
    assert original.conflict_flag is not None
