"""SCRUM-32: View the venue availability calendar.

Acceptance criteria covered:
  AC1  Availability is shown across relevant dates and times for a venue.
  AC2  Existing confirmed bookings are reflected in the calendar.
  AC3  Other recorded periods of unavailability are reflected in the calendar.
  AC4  The calendar is available to Coordinators and Venue Staff, not to
       external roles.

PO decisions tested (see the PR description for the full text):
  D1  GET /venues/{venue_id}/calendar?from=YYYY-MM-DD&to=YYYY-MM-DD. Both dates
      required, inclusive, to not before from, at most 31 days; otherwise 422.
      Unknown or retired venue: 404.
  D2  Busy blocks: every booking with a blocking status (Confirmed, Approved,
      Tentative, Pending) whose occupied window (start minus setup to end plus
      turnaround, as in check_venue_availability) overlaps the day. Each block
      shows the booked period, the occupied window, the status and the event
      id; no organiser or attendee details. Times are naive.
  D3  AC3: the recorded operating hours are the unavailability recorded today
      (the venue is unavailable outside them), shown on every day; the empty
      unavailable_periods list is ready for Week 7 Change 2's closures.
  D4  Coordinators, Leads and Venue Staff 200; Organisers, admin and anyone
      else 403; no session 401.
  D6  Boundaries: a booking ending exactly when the range starts; one starting
      on the last day; one spanning midnight; 31 days ok and 32 days 422;
      to equal to from; to before from.

All dates are relative to today, so the tests never expire.
"""

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

TEST_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

COOKIE = "connectsphere_session"
UNKNOWN_VENUE_ID = 99999

# First day of the calendar range in most tests: 20 days from today.
BASE = date.today() + timedelta(days=20)


def day(offset):
    return BASE + timedelta(days=offset)


def at(offset, hour, minute=0):
    return datetime.combine(day(offset), time(hour, minute))


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
    """One user per role. Token for each: tok_<username>."""
    specs = [
        ("org1", "organiser", False),
        ("coord1", "coordinator", True),
        ("lead1", "lead", False),
        ("venue1", "venue_staff", False),
        ("admin1", "admin", False),
    ]
    created = {}
    for username, role, active_coord in specs:
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
        proposed_start=at(1, 10),
        expected_attendance=80,
        organiser_id=users["org1"].id,
        status="Approved",
        assigned_coordinator_id=users["coord1"].id,
    )
    db_session.add(ev)
    db_session.commit()
    return ev


def add_venue(db_session, name, setup=30, turnaround=45, hours="08:00-22:00", is_active=True):
    venue = Venue(
        name=name,
        location="Block A",
        capacity=100,
        operating_hours=hours,
        setup_time_mins=setup,
        turnaround_time_mins=turnaround,
        is_active=is_active,
    )
    db_session.add(venue)
    db_session.commit()
    return venue


@pytest.fixture
def hall(db_session, users):
    """Grand Hall: 30 min setup, 45 min turnaround, open 08:00-22:00."""
    return add_venue(db_session, "Grand Hall")


def add_booking(db_session, venue, event, start, end, status="Confirmed"):
    booking = VenueBooking(
        venue_id=venue.id, event_id=event.id, booking_start=start, booking_end=end, status=status,
    )
    db_session.add(booking)
    db_session.commit()
    return booking


def get_calendar(client, token, venue_id, frm, to):
    """frm / to: a date, a raw string, or None to leave the parameter out."""
    if token is None:
        client.cookies.clear()
    else:
        client.cookies.set(COOKIE, token)
    params = {}
    if frm is not None:
        params["from"] = frm.isoformat() if isinstance(frm, date) else frm
    if to is not None:
        params["to"] = to.isoformat() if isinstance(to, date) else to
    return client.get(f"/venues/{venue_id}/calendar", params=params)


def busy_on(body, d):
    matches = [x for x in body["days"] if x["date"] == d.isoformat()]
    assert len(matches) == 1, f"expected exactly one entry for {d}"
    return matches[0]["busy"]


def as_dt(text):
    return datetime.fromisoformat(text)


# ---------------------------------------------------------------- happy path


def test_tc3201_calendar_shows_each_day_and_a_booking(client, db_session, hall, event):
    """TC-3201 | Happy Path | AC1, AC2 (D1, D2)
    Grand Hall (30 min setup, 45 min turnaround, 08:00-22:00) has a Confirmed
    booking 10:00-12:00 on day 2 of a 3-day range. A Coordinator views the range.
    Expected: 200; venue header fields; one entry per day in order; day 2 has one
    block with booked 10:00-12:00, occupied 09:30-12:45, status Confirmed and the
    event id; days 1 and 3 are empty; unavailable_periods is []."""
    add_booking(db_session, hall, event, at(1, 10), at(1, 12))

    resp = get_calendar(client, "tok_coord1", hall.id, day(0), day(2))

    assert resp.status_code == 200
    body = resp.json()
    assert body["venue_id"] == hall.id
    assert body["name"] == "Grand Hall"
    assert body["operating_hours"] == "08:00-22:00"
    assert body["setup_time_mins"] == 30
    assert body["turnaround_time_mins"] == 45
    assert body["from"] == day(0).isoformat()
    assert body["to"] == day(2).isoformat()
    assert body["unavailable_periods"] == []
    assert [d["date"] for d in body["days"]] == [day(0).isoformat(), day(1).isoformat(), day(2).isoformat()]
    assert busy_on(body, day(0)) == []
    assert busy_on(body, day(2)) == []
    [block] = busy_on(body, day(1))
    assert block["event_id"] == event.id
    assert block["status"] == "Confirmed"
    assert as_dt(block["booked_start"]) == at(1, 10)
    assert as_dt(block["booked_end"]) == at(1, 12)
    assert as_dt(block["occupied_start"]) == at(1, 9, 30)
    assert as_dt(block["occupied_end"]) == at(1, 12, 45)


# ---------------------------------------------------------------- bookings (AC2)


def test_tc3202_blocking_statuses_shown_cancelled_and_other_venues_not(client, db_session, hall, event):
    """TC-3202 | Negative Path | AC2 (D2)
    On day 1 Grand Hall has Confirmed, Approved, Tentative and Pending bookings
    and a Cancelled one; another venue has a Confirmed booking the same day.
    Expected: the four blocking bookings are shown in start order; the Cancelled
    one and the other venue's booking are not."""
    for hour, status in [(8, "Confirmed"), (11, "Approved"), (14, "Tentative"), (17, "Pending"), (20, "Cancelled")]:
        add_booking(db_session, hall, event, at(0, hour), at(0, hour + 1), status)
    other = add_venue(db_session, "Seminar Room")
    add_booking(db_session, other, event, at(0, 9), at(0, 10))

    body = get_calendar(client, "tok_coord1", hall.id, day(0), day(0)).json()

    assert [b["status"] for b in busy_on(body, day(0))] == ["Confirmed", "Approved", "Tentative", "Pending"]


def test_tc3203_booking_ending_exactly_when_range_starts(client, db_session, users, event):
    """TC-3203 | Boundary Path | AC2 (D6)
    Quick Room has no setup or turnaround. One booking ends exactly at 00:00 on
    the first day of the range; another ends at 00:01.
    Expected: the one ending at 00:00 only touches the range and is not shown;
    the one ending at 00:01 is shown on the first day."""
    room = add_venue(db_session, "Quick Room", setup=0, turnaround=0)
    add_booking(db_session, room, event, at(-1, 22), at(0, 0))
    just_in = add_booking(db_session, room, event, at(-1, 23), at(0, 0, 1))

    body = get_calendar(client, "tok_coord1", room.id, day(0), day(0)).json()

    blocks = busy_on(body, day(0))
    assert len(blocks) == 1
    assert as_dt(blocks[0]["booked_start"]) == just_in.booking_start


def test_tc3204_buffers_bring_bookings_outside_the_day_into_it(client, db_session, hall, event):
    """TC-3204 | Boundary Path | AC2 (D2, Week 7 Change 1)
    Grand Hall (30 min setup, 45 min turnaround). Range is day 1 only.
    A booking ends at 00:00 on day 1 (occupied until 00:45 on day 1).
    A booking starts at 00:15 on day 2 (occupied from 23:45 on day 1).
    Expected: both are shown on day 1, because the venue is occupied that day."""
    add_booking(db_session, hall, event, at(-1, 22), at(0, 0))
    add_booking(db_session, hall, event, at(1, 0, 15), at(1, 2))

    body = get_calendar(client, "tok_coord1", hall.id, day(0), day(0)).json()

    blocks = busy_on(body, day(0))
    assert [as_dt(b["occupied_end"]) for b in blocks] == [at(0, 0, 45), at(1, 2, 45)]
    assert [as_dt(b["occupied_start"]) for b in blocks] == [at(-1, 21, 30), at(0, 23, 45)]


def test_tc3205_booking_on_the_last_day_is_shown(client, db_session, hall, event):
    """TC-3205 | Boundary Path | AC2 (D1 inclusive, D6)
    7-day range; a booking 20:00-21:00 on the last day.
    Expected: shown on the last day only."""
    add_booking(db_session, hall, event, at(6, 20), at(6, 21))

    body = get_calendar(client, "tok_coord1", hall.id, day(0), day(6)).json()

    assert len(body["days"]) == 7
    assert len(busy_on(body, day(6))) == 1
    for offset in range(6):
        assert busy_on(body, day(offset)) == [], offset


def test_tc3206_booking_spanning_midnight_appears_on_both_days(client, db_session, hall, event):
    """TC-3206 | Boundary Path | AC2 (D6)
    A booking 22:00 on day 1 to 02:00 on day 2.
    Expected: the same block (booked 22:00-02:00, occupied 21:30-02:45) appears
    on both days."""
    add_booking(db_session, hall, event, at(0, 22), at(1, 2))

    body = get_calendar(client, "tok_coord1", hall.id, day(0), day(1)).json()

    first = busy_on(body, day(0))
    second = busy_on(body, day(1))
    assert len(first) == 1 and first == second
    assert as_dt(first[0]["booked_start"]) == at(0, 22)
    assert as_dt(first[0]["booked_end"]) == at(1, 2)
    assert as_dt(first[0]["occupied_start"]) == at(0, 21, 30)
    assert as_dt(first[0]["occupied_end"]) == at(1, 2, 45)


def test_tc3207_bookings_well_outside_the_range_are_not_shown(client, db_session, hall, event):
    """TC-3207 | Negative Path | AC2 (D2)
    Bookings 5 days before and 10 days after a 3-day range.
    Expected: every day in the range is empty."""
    add_booking(db_session, hall, event, at(-5, 10), at(-5, 12))
    add_booking(db_session, hall, event, at(10, 10), at(10, 12))

    body = get_calendar(client, "tok_coord1", hall.id, day(0), day(2)).json()

    assert all(d["busy"] == [] for d in body["days"])


def test_tc3208_blocks_within_a_day_are_in_start_order(client, db_session, hall, event):
    """TC-3208 | Workflow Variation | AC1, AC2 (D2)
    On one day, a booking 15:00-16:00 is added before one 09:00-10:00.
    Expected: the day lists 09:00 first, then 15:00."""
    add_booking(db_session, hall, event, at(0, 15), at(0, 16))
    add_booking(db_session, hall, event, at(0, 9), at(0, 10))

    body = get_calendar(client, "tok_coord1", hall.id, day(0), day(0)).json()

    assert [as_dt(b["booked_start"]) for b in busy_on(body, day(0))] == [at(0, 9), at(0, 15)]


def test_tc3209_blocks_carry_no_organiser_or_attendee_details(client, db_session, hall, event):
    """TC-3209 | Cross-Cutting | AC4 (D2: no organiser or attendee details)
    One booking for "Annual Gala", organised by org1.
    Expected: each block has exactly event_id, status, booked_start, booked_end,
    occupied_start and occupied_end; the response mentions neither the event's
    title nor the organiser."""
    add_booking(db_session, hall, event, at(0, 10), at(0, 12))

    resp = get_calendar(client, "tok_coord1", hall.id, day(0), day(0))

    [block] = busy_on(resp.json(), day(0))
    assert set(block.keys()) == {
        "event_id", "status", "booked_start", "booked_end", "occupied_start", "occupied_end",
    }
    assert "Annual Gala" not in resp.text
    assert "org1" not in resp.text


# ---------------------------------------------------------------- other unavailability (AC3)


def test_tc3210_operating_hours_shown_every_day_and_periods_list_ready(client, db_session, users):
    """TC-3210 | Happy Path | AC3 (D3)
    Grand Hall records "08:00-22:00"; Pop-up Room records no operating hours.
    Expected: every day shows "08:00-22:00" for Grand Hall and null for Pop-up
    Room, exactly as recorded; unavailable_periods is [] for both."""
    hall = add_venue(db_session, "Grand Hall", hours="08:00-22:00")
    popup = add_venue(db_session, "Pop-up Room", hours=None)

    hall_body = get_calendar(client, "tok_coord1", hall.id, day(0), day(2)).json()
    popup_body = get_calendar(client, "tok_coord1", popup.id, day(0), day(2)).json()

    assert [d["operating_hours"] for d in hall_body["days"]] == ["08:00-22:00"] * 3
    assert [d["operating_hours"] for d in popup_body["days"]] == [None] * 3
    assert popup_body["operating_hours"] is None
    assert hall_body["unavailable_periods"] == []
    assert popup_body["unavailable_periods"] == []


# ---------------------------------------------------------------- the date range (D1)


def test_tc3211_31_days_allowed_32_days_refused(client, db_session, hall):
    """TC-3211 | Boundary Path | D1, D6
    from = day 1; to = day 31 (31 days, inclusive), then to = day 32 (32 days).
    Expected: 31 days gives 200 with 31 day entries; 32 days gives 422."""
    ok = get_calendar(client, "tok_coord1", hall.id, day(0), day(30))
    assert ok.status_code == 200
    assert len(ok.json()["days"]) == 31

    assert get_calendar(client, "tok_coord1", hall.id, day(0), day(31)).status_code == 422


def test_tc3212_one_day_range(client, db_session, hall):
    """TC-3212 | Boundary Path | D1, D6
    to equal to from.
    Expected: 200 with exactly one day entry, for that date."""
    resp = get_calendar(client, "tok_coord1", hall.id, day(0), day(0))

    assert resp.status_code == 200
    assert [d["date"] for d in resp.json()["days"]] == [day(0).isoformat()]


def test_tc3213_to_before_from_is_refused(client, db_session, hall):
    """TC-3213 | Negative Path | D1, D6
    to is the day before from.
    Expected: 422."""
    assert get_calendar(client, "tok_coord1", hall.id, day(1), day(0)).status_code == 422


def test_tc3214_missing_or_malformed_dates_are_refused(client, db_session, hall):
    """TC-3214 | Negative Path | D1 (both dates required)
    No from; no to; neither; from "2026-13-01"; to "tomorrow".
    Expected: 422 for each."""
    cases = [
        (None, day(1)),
        (day(0), None),
        (None, None),
        ("2026-13-01", day(1)),
        (day(0), "tomorrow"),
    ]
    for frm, to in cases:
        assert get_calendar(client, "tok_coord1", hall.id, frm, to).status_code == 422, (frm, to)


# ---------------------------------------------------------------- venue lookup (D1)


def test_tc3215_unknown_or_retired_venue_gives_404(client, db_session, users):
    """TC-3215 | Negative Path | D1
    Venue 99999 doesn't exist; "Old Store" is retired.
    Expected: 404 "Venue not found" for both."""
    old = add_venue(db_session, "Old Store", is_active=False)

    for venue_id in [UNKNOWN_VENUE_ID, old.id]:
        resp = get_calendar(client, "tok_coord1", venue_id, day(0), day(1))
        assert resp.status_code == 404, venue_id
        assert resp.json()["detail"] == "Venue not found"


# ---------------------------------------------------------------- who can see it (AC4)


def test_tc3216_internal_roles_only(client, db_session, hall):
    """TC-3216 | Cross-Cutting | AC4 (D4)
    The same request as each role.
    Expected: no session 401; organiser 403; admin 403; coordinator, lead and
    venue_staff 200."""
    expected = [
        (None, 401),
        ("tok_org1", 403),
        ("tok_admin1", 403),
        ("tok_coord1", 200),
        ("tok_lead1", 200),
        ("tok_venue1", 200),
    ]
    for token, code in expected:
        assert get_calendar(client, token, hall.id, day(0), day(1)).status_code == code, token
