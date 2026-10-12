"""SCRUM-38 (part 1): one rule for which bookings block a venue.

Acceptance criteria covered by part 1:
  AC1  Overlapping or incompatible bookings for the same venue are identified.
  AC5  (Week 7 change 1) The occupied window runs from the event start minus
       the venue's setup time to the event end plus its turnaround time.
       Example: 10:00-12:00 with 30 min setup and 45 min turnaround occupies
       09:30-12:45.
  AC6  (Week 7 change 1) Conflict checks compare occupied windows, not event
       times alone.

PO decisions tested:
  D2  backend/booking_rules.py: BLOCKING_STATUSES, occupied_window,
      windows_overlap, is_blocking, find_conflicts (start order, then id;
      exclude_booking_id leaves one booking out).
  D3  check_venue_availability, retire_venue and the calendar use the module,
      and their responses stay the same. The one exception (PO answer 1): when
      several bookings clash, check_venue_availability names the earliest-
      starting one (TC-3813). The old order was undefined.

How RED works: the unit tests get the new module through the `rules` fixture,
so before backend/booking_rules.py exists only they fail. The endpoint
regression tests (TC-3808, TC-3809) don't use it: they pass before and after
the change, which is their point.

All dates are relative to today (D = today + 30 days), so the tests never expire.
"""

import importlib
import os
import sys
from datetime import date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.database import Base, get_db
from backend.main import app
from backend.models import Event, LoginSession, User, Venue, VenueBooking
from backend.security import hash_password
from backend.venues import check_venue_availability

TEST_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

COOKIE = "connectsphere_session"

# Day D: 30 days from today.
D = date.today() + timedelta(days=30)

ALL_STATUSES = ["Confirmed", "Approved", "Tentative", "Pending", "Rejected", "Cancelled"]


def at(hour, minute=0, days=0):
    """A naive datetime on day D (plus `days`) at hour:minute."""
    return datetime.combine(D + timedelta(days=days), time(hour, minute))


@pytest.fixture
def rules():
    """The module under test, imported here so that only the tests using it
    fail while it doesn't exist (RED)."""
    return importlib.import_module("backend.booking_rules")


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
def users(db_session):
    """Token for each: tok_<username>."""
    created = {}
    for username, role, active_coord in [
        ("org1", "organiser", False),
        ("coord1", "coordinator", True),
        ("venue1", "venue_staff", False),
    ]:
        user = User(
            username=username,
            password_hash=hash_password("password123"),
            role=role,
            is_active_coordinator=active_coord,
        )
        db_session.add(user)
        db_session.commit()
        db_session.add(LoginSession(user_id=user.id, token=f"tok_{username}", valid=True))
        db_session.commit()
        created[username] = user
    return created


@pytest.fixture
def event(db_session, users):
    ev = Event(
        title="Annual Gala",
        proposed_start=at(10),
        expected_attendance=80,
        organiser_id=users["org1"].id,
        status="Approved",
        assigned_coordinator_id=users["coord1"].id,
    )
    db_session.add(ev)
    db_session.commit()
    return ev


def add_venue(db_session, name, setup=30, turnaround=45):
    venue = Venue(
        name=name, location="Block A", capacity=100, setup_time_mins=setup, turnaround_time_mins=turnaround,
    )
    db_session.add(venue)
    db_session.commit()
    return venue


@pytest.fixture
def hall(db_session, users):
    """Grand Hall: 30 min setup, 45 min turnaround."""
    return add_venue(db_session, "Grand Hall")


def add_booking(db_session, venue, event, start, end, status="Confirmed"):
    booking = VenueBooking(
        venue_id=venue.id, event_id=event.id, booking_start=start, booking_end=end, status=status,
    )
    db_session.add(booking)
    db_session.commit()
    return booking


def ids(bookings):
    return [b.id for b in bookings]


# ================================================================ occupied window (AC5)


def test_tc3801_occupied_window_adds_setup_and_turnaround(rules, db_session, hall):
    """TC-3801 | Happy Path | AC5
    Grand Hall (30 min setup, 45 min turnaround); booked D 10:00-12:00.
    Expected: occupied D 09:30-12:45."""
    assert rules.occupied_window(at(10), at(12), hall) == (at(9, 30), at(12, 45))


def test_tc3812_occupied_window_uses_each_venues_own_buffers(rules, db_session, users):
    """TC-3812 | Boundary Path | AC5
    A venue with 60 min setup and 60 min turnaround; booked D 10:00-12:00.
    Expected: occupied D 09:00-13:00."""
    venue = add_venue(db_session, "Long Buffer Hall", setup=60, turnaround=60)

    assert rules.occupied_window(at(10), at(12), venue) == (at(9), at(13))


# ================================================================ overlap rule (AC6)


def test_tc3811_windows_overlap_only_when_they_really_overlap(rules):
    """TC-3811 | Boundary Path | AC6
    Pairs of windows on day D.
    Expected: partial overlap, one inside the other and identical windows
    overlap; windows that only touch (either way round) and disjoint windows
    don't."""
    assert rules.windows_overlap(at(10), at(12), at(11), at(13)) is True
    assert rules.windows_overlap(at(10), at(14), at(11), at(12)) is True
    assert rules.windows_overlap(at(10), at(12), at(10), at(12)) is True
    assert rules.windows_overlap(at(10), at(12), at(12), at(13)) is False
    assert rules.windows_overlap(at(12), at(13), at(10), at(12)) is False
    assert rules.windows_overlap(at(10), at(11), at(15), at(16)) is False


def test_tc3802_occupied_windows_clash_though_booked_times_dont(rules, db_session, hall, event):
    """TC-3802 | Happy Path | AC1, AC6
    Booking D 10:00-12:00 (occupied 09:30-12:45). Request D 13:00-14:00
    (occupied 12:30-14:45).
    Expected: the booked times don't overlap but the windows do: a conflict."""
    booking = add_booking(db_session, hall, event, at(10), at(12))

    assert ids(rules.find_conflicts(db_session, hall, at(13), at(14))) == [booking.id]


def test_tc3803_turnaround_edge_just_before_at_and_after(rules, db_session, hall, event):
    """TC-3803 | Boundary Path | AC6
    Booking D 10:00-12:00 (occupied until 12:45). Requests starting at 13:14,
    13:15 and 13:16, each to 14:00 (occupied from 12:44, 12:45 and 12:46).
    Expected: conflict; no conflict (the windows only touch); no conflict."""
    booking = add_booking(db_session, hall, event, at(10), at(12))

    assert ids(rules.find_conflicts(db_session, hall, at(13, 14), at(14))) == [booking.id]
    assert rules.find_conflicts(db_session, hall, at(13, 15), at(14)) == []
    assert rules.find_conflicts(db_session, hall, at(13, 16), at(14)) == []


def test_tc3810_setup_edge_just_before_at_and_after(rules, db_session, hall, event):
    """TC-3810 | Boundary Path | AC6
    Booking D 10:00-12:00 (occupied from 09:30). Requests from 08:00 ending at
    08:44, 08:45 and 08:46 (occupied until 09:29, 09:30 and 09:31).
    Expected: no conflict; no conflict (the windows only touch); conflict."""
    booking = add_booking(db_session, hall, event, at(10), at(12))

    assert rules.find_conflicts(db_session, hall, at(8), at(8, 44)) == []
    assert rules.find_conflicts(db_session, hall, at(8), at(8, 45)) == []
    assert ids(rules.find_conflicts(db_session, hall, at(8), at(8, 46))) == [booking.id]


def test_tc3804_no_buffers_window_equals_booked_times(rules, db_session, users, event):
    """TC-3804 | Boundary Path | AC5, AC6
    Quick Room has 0 setup and 0 turnaround; booking D 10:00-12:00.
    Expected: the occupied window equals the booked times; a back-to-back
    request D 12:00-13:00 doesn't clash; D 11:59-13:00 does."""
    room = add_venue(db_session, "Quick Room", setup=0, turnaround=0)
    booking = add_booking(db_session, room, event, at(10), at(12))

    assert rules.occupied_window(at(10), at(12), room) == (at(10), at(12))
    assert rules.find_conflicts(db_session, room, at(12), at(13)) == []
    assert ids(rules.find_conflicts(db_session, room, at(11, 59), at(13))) == [booking.id]


# ================================================================ which bookings block (AC1)


def test_tc3805_only_blocking_statuses_hold_the_venue(rules, db_session, hall, event):
    """TC-3805 | Negative Path | AC1 (D2, partitioning)
    One booking per status at D 10:00-12:00: Confirmed, Approved, Tentative,
    Pending, Rejected, Cancelled.
    Expected: is_blocking is true for the first four and false for Rejected and
    Cancelled; BLOCKING_STATUSES is exactly the four; find_conflicts for
    D 10:00-12:00 returns only the four, in id order (same start)."""
    bookings = {s: add_booking(db_session, hall, event, at(10), at(12), s) for s in ALL_STATUSES}

    assert rules.BLOCKING_STATUSES == ["Confirmed", "Approved", "Tentative", "Pending"]
    for status in ["Confirmed", "Approved", "Tentative", "Pending"]:
        assert rules.is_blocking(bookings[status]) is True, status
    for status in ["Rejected", "Cancelled"]:
        assert rules.is_blocking(bookings[status]) is False, status
    found = rules.find_conflicts(db_session, hall, at(10), at(12))
    assert [b.status for b in found] == ["Confirmed", "Approved", "Tentative", "Pending"]


def test_tc3806_booking_at_another_venue_never_conflicts(rules, db_session, hall, event):
    """TC-3806 | Negative Path | AC1
    Seminar Room has a booking D 10:00-12:00; Grand Hall has none.
    Expected: find_conflicts for Grand Hall, D 10:00-12:00, is empty."""
    other = add_venue(db_session, "Seminar Room")
    add_booking(db_session, other, event, at(10), at(12))

    assert rules.find_conflicts(db_session, hall, at(10), at(12)) == []


def test_tc3807_every_clash_in_start_order_and_exclude_booking_id(rules, db_session, hall, event):
    """TC-3807 | Workflow Variation | AC1 (D2)
    Bookings added in the order A D 14:00-15:00, B D 10:00-11:00, C D 12:00-13:00.
    Expected: a request D 10:00-15:00 clashes with all three, returned in start
    order B, C, A. Checking B's own times with exclude_booking_id=B returns only
    C (B occupies 09:30-11:45, C 11:30-13:45), never B itself."""
    a = add_booking(db_session, hall, event, at(14), at(15))
    b = add_booking(db_session, hall, event, at(10), at(11))
    c = add_booking(db_session, hall, event, at(12), at(13))

    assert ids(rules.find_conflicts(db_session, hall, at(10), at(15))) == [b.id, c.id, a.id]
    assert ids(rules.find_conflicts(db_session, hall, at(10), at(11), exclude_booking_id=b.id)) == [c.id]


def test_tc3814_find_conflicts_goes_through_is_blocking(rules, db_session, hall, event, monkeypatch):
    """TC-3814 | Workflow Variation | AC1 (PO answer 2: is_blocking is the one gate)
    A Pending hold D 10:00-12:00 and a Confirmed booking D 13:00-14:00. is_blocking
    is replaced for this test only, so that Pending no longer blocks (standing in
    for SCRUM-81's hold expiry).
    Expected: find_conflicts for D 10:00-14:00 returns only the Confirmed
    booking, so changing is_blocking alone changes which bookings clash."""
    add_booking(db_session, hall, event, at(10), at(12), "Pending")
    confirmed = add_booking(db_session, hall, event, at(13), at(14), "Confirmed")
    monkeypatch.setattr(rules, "is_blocking", lambda booking: booking.status != "Pending")

    assert ids(rules.find_conflicts(db_session, hall, at(10), at(14))) == [confirmed.id]


def test_tc3813_several_clashes_name_the_earliest_starting(db_session, hall, event):
    """TC-3813 | Workflow Variation | AC1 (PO answer 1: start order, then id)
    Booking D 13:00-14:00 is added before booking D 10:00-12:00. Request
    D 11:00-13:30 clashes directly with both.
    Expected: check_venue_availability names the earliest-starting one:
    (False, "Direct conflict with existing booking (D 10:00 - 12:00)")."""
    add_booking(db_session, hall, event, at(13), at(14))
    add_booking(db_session, hall, event, at(10), at(12))

    available, reason = check_venue_availability(hall, at(11), at(13, 30), db_session)

    assert available is False
    assert reason == f"Direct conflict with existing booking ({D.isoformat()} 10:00 - 12:00)"


# ================================================================ regression: endpoints


def test_tc3808_search_calendar_and_retire_agree_on_what_blocks(client, db_session, users, hall, event):
    """TC-3808 | Workflow Variation (regression) | AC1
    Grand Hall has one booking per status, 10:00-12:00 on days D to D+5, in the
    order Confirmed, Approved, Tentative, Pending, Rejected, Cancelled.
    Expected, before and after the change: search for each day's 10:00-12:00
    shows Grand Hall unavailable exactly on the four blocking days; the
    calendar for D to D+5 shows busy blocks on exactly those days; retiring
    lists exactly those four as affected."""
    for offset, status in enumerate(ALL_STATUSES):
        add_booking(db_session, hall, event, at(10, days=offset), at(12, days=offset), status)
    blocking_days = [0, 1, 2, 3]

    client.cookies.set(COOKIE, "tok_coord1")
    unavailable = []
    for offset in range(len(ALL_STATUSES)):
        params = {"start_time": at(10, days=offset).isoformat(), "end_time": at(12, days=offset).isoformat()}
        [hall_row] = [v for v in client.get("/venues/search", params=params).json() if v["name"] == "Grand Hall"]
        if not hall_row["is_available"]:
            unavailable.append(offset)
    assert unavailable == blocking_days

    calendar = client.get(
        f"/venues/{hall.id}/calendar",
        params={"from": D.isoformat(), "to": (D + timedelta(days=5)).isoformat()},
    ).json()
    busy = [i for i, day in enumerate(calendar["days"]) if day["busy"]]
    assert busy == blocking_days
    assert [day["busy"][0]["status"] for day in calendar["days"] if day["busy"]] == ALL_STATUSES[:4]

    client.cookies.set(COOKIE, "tok_venue1")
    affected = client.post(f"/venues/{hall.id}/retire").json()["affected_bookings"]
    assert [a["status"] for a in affected] == ALL_STATUSES[:4]


def test_tc3809_conflict_reason_is_word_for_word_unchanged(client, db_session, users, hall, event):
    """TC-3809 | Negative Path (regression) | AC6
    Booking D 10:00-12:00 at Grand Hall (30 min setup, 45 min turnaround).
    Search D 11:00-13:00 (a direct clash), then D 13:00-14:00 (a buffer-only clash).
    Expected, before and after the change:
    "Direct conflict with existing booking (D 10:00 - 12:00)", then
    "Conflicts with booking (D 10:00 - 12:00) once 30m setup and 45m turnaround are included"."""
    add_booking(db_session, hall, event, at(10), at(12))
    client.cookies.set(COOKIE, "tok_coord1")

    def reason(start, end):
        params = {"start_time": start.isoformat(), "end_time": end.isoformat()}
        [row] = [v for v in client.get("/venues/search", params=params).json() if v["name"] == "Grand Hall"]
        assert row["is_available"] is False
        return row["conflict_reason"]

    day = D.isoformat()
    assert reason(at(11), at(13)) == f"Direct conflict with existing booking ({day} 10:00 - 12:00)"
    assert reason(at(13), at(14)) == (
        f"Conflicts with booking ({day} 10:00 - 12:00) once 30m setup and 45m turnaround are included"
    )
