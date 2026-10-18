"""SCRUM-80: Tests for configuring venue setup and turnaround time.

As Venue Staff, I want to set a setup time and turnaround time for each venue,
so that availability reflects the time needed to prepare and reset it.

Acceptance criteria covered:
  AC1  Each venue has a configurable setup time and turnaround time
       (minutes, >= 0).
  AC2  Changes apply to availability checks immediately.
  AC3  Changing these values re-checks existing bookings at that venue; new
       conflicts are flagged, not removed. (Same rule as SCRUM-38 AC8.)
  AC4  Only Venue Staff can edit these values.

AC1, AC2 and AC4 were built in SCRUM-30/31/32/33; the tests here prove them for
this story. AC3 is new in SCRUM-80.

Times: Grand Hall has 30 min setup and 45 min turnaround. On day D:
  A  Confirmed  10:00-12:00  occupies 09:30-12:45
  B  Pending    13:30-15:00  occupies 13:00-15:45   (15 min gap after A)
  R  Rejected   13:00-14:00  does not hold the venue
  O  Confirmed  10:00-12:00  at Small Room (another venue)
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


def _venue(name, setup, turnaround):
    return Venue(
        name=name,
        location="Building A",
        capacity=200,
        facilities=json.dumps(["Projector"]),
        supported_layouts=json.dumps(["Theatre"]),
        operating_hours="08:00-22:00",
        setup_time_mins=setup,
        turnaround_time_mins=turnaround,
        is_active=True,
    )


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
        LoginSession(user_id=organiser.id, token="token_org_dave", valid=True),
    ])

    hall = _venue("Grand Hall", 30, 45)
    room = _venue("Small Room", 30, 45)
    db_session.add_all([hall, room])
    db_session.commit()

    event = Event(title="Annual Tech Symposium", organiser_id=organiser.id, status="Approved",
                  proposed_start=datetime.utcnow() + timedelta(days=30),
                  assigned_coordinator_id=coord.id)
    db_session.add(event)
    db_session.commit()

    day = (datetime.utcnow() + timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)

    def at(h, m=0):
        return day + timedelta(hours=h, minutes=m)

    a = VenueBooking(event_id=event.id, venue_id=hall.id, booking_start=at(10), booking_end=at(12), status="Confirmed")
    b = VenueBooking(event_id=event.id, venue_id=hall.id, booking_start=at(13, 30), booking_end=at(15), status="Pending")
    r = VenueBooking(event_id=event.id, venue_id=hall.id, booking_start=at(13), booking_end=at(14), status="Rejected")
    o = VenueBooking(event_id=event.id, venue_id=room.id, booking_start=at(10), booking_end=at(12), status="Confirmed")
    db_session.add_all([a, b, r, o])
    db_session.commit()

    return {"hall": hall, "room": room, "event": event, "a": a, "b": b, "r": r, "o": o, "at": at}


def _as(client, token):
    client.cookies.set("connectsphere_session", token)


def _patch(client, venue, body):
    return client.patch(f"/venues/{venue.id}", json=body)


def _flagged_ids(res):
    return sorted(f["booking_id"] for f in res.json()["flagged_bookings"])


# ---------------------------------------------------------------------------
# 1. Happy Path
# ---------------------------------------------------------------------------

def test_tc8001_venue_staff_sets_setup_and_turnaround(client, seed_data):
    """TC-8001 (AC1): Venue Staff set both values; they are saved."""
    _as(client, "token_staff_alex")
    hall = seed_data["hall"]
    res = _patch(client, hall, {"setup_time_mins": 20, "turnaround_time_mins": 40})
    assert res.status_code == 200
    assert res.json()["venue"]["setup_time_mins"] == 20
    assert res.json()["venue"]["turnaround_time_mins"] == 40


def test_tc8002_change_applies_to_availability_immediately(client, db_session, seed_data):
    """TC-8002 (AC2): 16:30-17:00 is free while B occupies the hall to 15:45.
    Once the turnaround grows to 120 min, B occupies it to 17:00 and the same
    slot is unavailable straight away, with no other step."""
    hall, at = seed_data["hall"], seed_data["at"]
    ok, _ = check_venue_availability(hall, at(16, 30), at(17), db_session)
    assert ok is True

    _as(client, "token_staff_alex")
    assert _patch(client, hall, {"turnaround_time_mins": 120}).status_code == 200
    db_session.refresh(hall)

    ok, reason = check_venue_availability(hall, at(16, 30), at(17), db_session)
    assert ok is False
    assert "120m turnaround" in reason


def test_tc8003_longer_turnaround_flags_both_clashing_bookings(client, db_session, seed_data):
    """TC-8003 (AC3): turnaround 45 -> 75 makes A occupy to 13:15, overlapping
    B from 13:00. Both are flagged; neither is removed or changed."""
    _as(client, "token_staff_alex")
    a, b = seed_data["a"], seed_data["b"]
    res = _patch(client, seed_data["hall"], {"turnaround_time_mins": 75})
    assert res.status_code == 200
    assert _flagged_ids(res) == sorted([a.id, b.id])

    flags = {f["booking_id"]: f["conflict_flag"] for f in res.json()["flagged_bookings"]}
    assert flags[a.id]["conflicting_booking_id"] == b.id
    assert flags[b.id]["conflicting_booking_id"] == a.id

    db_session.refresh(a)
    db_session.refresh(b)
    assert a.status == "Confirmed"
    assert b.status == "Pending"
    assert db_session.query(VenueBooking).count() == 4


def test_tc8004_longer_setup_also_flags(client, seed_data):
    """TC-8004 (AC3): setup 30 -> 60 makes B occupy from 12:30, overlapping A
    to 12:45. Setup changes are re-checked too."""
    _as(client, "token_staff_alex")
    res = _patch(client, seed_data["hall"], {"setup_time_mins": 60})
    assert res.status_code == 200
    assert _flagged_ids(res) == sorted([seed_data["a"].id, seed_data["b"].id])


def test_tc8005_flag_is_visible_to_venue_staff(client, seed_data):
    """TC-8005 (AC3): Venue Staff see the flag and its reason in the venue's
    booking list and in the booking review."""
    _as(client, "token_staff_alex")
    hall, a, b = seed_data["hall"], seed_data["a"], seed_data["b"]
    _patch(client, hall, {"turnaround_time_mins": 75})

    listed = {row["booking_id"]: row for row in client.get(f"/venues/{hall.id}/bookings").json()}
    assert listed[a.id]["conflict_flag"]["conflicting_booking_id"] == b.id
    assert "75m turnaround" in listed[a.id]["conflict_flag"]["reason"]
    assert listed[seed_data["r"].id]["conflict_flag"] is None

    review = client.get(f"/venues/bookings/{b.id}").json()
    assert review["conflict_flag"]["conflicting_booking_id"] == a.id


# ---------------------------------------------------------------------------
# 2. Boundary Path
# ---------------------------------------------------------------------------

def test_tc8006_touching_windows_are_not_flagged(client, seed_data):
    """TC-8006 (AC3): turnaround 45 -> 60 makes A occupy to exactly 13:00, when
    B's window starts. Touching windows do not clash, so nothing is flagged."""
    _as(client, "token_staff_alex")
    res = _patch(client, seed_data["hall"], {"turnaround_time_mins": 60})
    assert res.status_code == 200
    assert res.json()["flagged_bookings"] == []


@pytest.mark.parametrize("field", ["setup_time_mins", "turnaround_time_mins"])
def test_tc8007_zero_allowed_negative_refused(client, db_session, seed_data, field):
    """TC-8007 (AC1): 0 minutes is accepted; -1 is refused (422) and the value
    is unchanged."""
    _as(client, "token_staff_alex")
    hall = seed_data["hall"]
    assert _patch(client, hall, {field: 0}).status_code == 200
    assert _patch(client, hall, {field: -1}).status_code == 422
    db_session.refresh(hall)
    assert getattr(hall, field) == 0


# ---------------------------------------------------------------------------
# 3. Negative Path
# ---------------------------------------------------------------------------

def test_tc8008_non_blocking_and_other_venue_bookings_not_flagged(client, seed_data):
    """TC-8008 (AC3): a Rejected booking and a booking at another venue are
    never flagged, even when the windows would overlap."""
    _as(client, "token_staff_alex")
    res = _patch(client, seed_data["hall"], {"turnaround_time_mins": 75})
    flagged = _flagged_ids(res)
    assert seed_data["r"].id not in flagged
    assert seed_data["o"].id not in flagged


@pytest.mark.parametrize("token", ["token_coord_sarah", "token_org_dave"])
def test_tc8009_only_venue_staff_can_edit(client, db_session, seed_data, token):
    """TC-8009 (AC4): Coordinators and Organisers cannot change the values
    (403); nothing is re-checked or flagged."""
    _as(client, token)
    hall = seed_data["hall"]
    assert _patch(client, hall, {"turnaround_time_mins": 75}).status_code == 403
    db_session.refresh(hall)
    assert hall.turnaround_time_mins == 45

    _as(client, "token_staff_alex")
    rows = client.get(f"/venues/{hall.id}/bookings").json()
    assert all(row["conflict_flag"] is None for row in rows)


# ---------------------------------------------------------------------------
# 4. Workflow Variation
# ---------------------------------------------------------------------------

def test_tc8010_other_field_changes_do_not_recheck(client, seed_data):
    """TC-8010 (AC3): changing only capacity does not re-check bookings."""
    _as(client, "token_staff_alex")
    res = _patch(client, seed_data["hall"], {"capacity": 250})
    assert res.status_code == 200
    assert res.json()["flagged_bookings"] == []


def test_tc8011_repeat_change_does_not_duplicate_flags(client, seed_data):
    """TC-8011 (AC3): only new conflicts are flagged. Making the turnaround
    longer again reports nothing new, and each booking keeps one flag."""
    _as(client, "token_staff_alex")
    hall = seed_data["hall"]
    assert len(_patch(client, hall, {"turnaround_time_mins": 75}).json()["flagged_bookings"]) == 2
    assert _patch(client, hall, {"turnaround_time_mins": 90}).json()["flagged_bookings"] == []

    rows = client.get(f"/venues/{hall.id}/bookings").json()
    assert sum(1 for row in rows if row["conflict_flag"]) == 2


# ---------------------------------------------------------------------------
# 5. Cross-Cutting
# ---------------------------------------------------------------------------

def test_tc8012_unauthenticated_cannot_edit(client, db_session, seed_data):
    """TC-8012 (DoD auth): no session -> 401; value unchanged."""
    hall = seed_data["hall"]
    assert _patch(client, hall, {"turnaround_time_mins": 75}).status_code == 401
    db_session.refresh(hall)
    assert hall.turnaround_time_mins == 45
