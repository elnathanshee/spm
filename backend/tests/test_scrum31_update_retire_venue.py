"""SCRUM-31: Update or retire a venue record.

Acceptance criteria covered:
  AC1  Venue Staff can update any attribute of an existing venue.
  AC2  Venue Staff can retire a venue so it no longer appears as bookable.
  AC3  Retiring a venue does not delete the history of events already booked there.

PO decisions tested (see the PR description for the full text):
  D1  PATCH /venues/{venue_id}: partial update with the same validation as
      creating a venue (SCRUM-30). Blank name or location, capacity <= 0 or a
      negative buffer: 422. Duplicate name (case-insensitive, ignoring the
      venue itself): 409. Empty body: 422. Unknown id: 404. is_active can't be
      changed here; a retired venue can be corrected but stays retired.
  D2  POST /venues/{venue_id}/retire sets is_active false. Unknown id 404;
      already retired 409. Lists the venue's future bookings with a blocking
      status as affected_bookings, without changing them.
  D3  Venue Staff only: everyone else 403, no session 401.
  D4  Nothing is deleted; GET /venues and the search exclude a retired venue.

"Now" is naive UTC, as in events.py (validate_event_business_rules), so the
tests build booking times from the same clock.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

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


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


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
def hall(db_session, users):
    """An active venue with every attribute recorded."""
    venue = Venue(
        name="Grand Hall",
        location="Block A, Level 2",
        capacity=200,
        facilities=json.dumps(["Projector", "Stage"]),
        accessibility="Wheelchair ramp",
        supported_layouts=json.dumps(["Theatre", "Banquet"]),
        operating_hours="08:00-22:00",
        setup_time_mins=30,
        turnaround_time_mins=45,
        is_active=True,
    )
    db_session.add(venue)
    db_session.commit()
    return venue


@pytest.fixture
def event(db_session, users):
    ev = Event(
        title="Annual Gala",
        proposed_start=utc_now() + timedelta(days=30),
        expected_attendance=80,
        organiser_id=users["org1"].id,
        status="Approved",
        assigned_coordinator_id=users["coord1"].id,
    )
    db_session.add(ev)
    db_session.commit()
    return ev


def add_venue(db_session, name, is_active=True):
    venue = Venue(name=name, location="Block B", capacity=50, is_active=is_active)
    db_session.add(venue)
    db_session.commit()
    return venue


def add_booking(db_session, venue, event, start, end, status="Confirmed"):
    booking = VenueBooking(
        venue_id=venue.id, event_id=event.id, booking_start=start, booking_end=end, status=status,
    )
    db_session.add(booking)
    db_session.commit()
    return booking


def login(client, token):
    if token is None:
        client.cookies.clear()
    else:
        client.cookies.set(COOKIE, token)


def patch(client, token, venue_id, body):
    login(client, token)
    return client.patch(f"/venues/{venue_id}", json=body)


def retire(client, token, venue_id):
    login(client, token)
    return client.post(f"/venues/{venue_id}/retire")


def row(db_session, model, row_id):
    """Every column of one row, read fresh from the database."""
    db_session.expire_all()
    obj = db_session.get(model, row_id)
    return {c.name: getattr(obj, c.name) for c in model.__table__.columns}


# ================================================================ update (AC1)


def test_tc3101_venue_staff_updates_some_attributes(client, db_session, hall):
    """TC-3101 | Happy Path | AC1 (D1)
    Venue Staff sends {"capacity": 250, "facilities": ["Projector", "Stage", "WiFi"]}.
    Expected: 200; capacity and facilities saved; every other attribute untouched;
    updated_fields names the two fields."""
    before = row(db_session, Venue, hall.id)

    resp = patch(client, "tok_venue1", hall.id, {"capacity": 250, "facilities": ["Projector", "Stage", "WiFi"]})

    assert resp.status_code == 200
    body = resp.json()
    assert body["message"] == "Venue updated successfully"
    assert sorted(body["updated_fields"]) == ["capacity", "facilities"]
    assert body["venue"]["capacity"] == 250
    assert body["venue"]["facilities"] == ["Projector", "Stage", "WiFi"]
    after = row(db_session, Venue, hall.id)
    assert after["capacity"] == 250
    assert json.loads(after["facilities"]) == ["Projector", "Stage", "WiFi"]
    for column in before:
        if column not in {"capacity", "facilities"}:
            assert after[column] == before[column], column


def test_tc3102_venue_staff_can_update_every_attribute(client, db_session, hall):
    """TC-3102 | Happy Path | AC1 ("any attribute")
    Venue Staff changes all nine attributes in one request.
    Expected: 200 and every attribute holds its new value."""
    new = {
        "name": "Harbour Hall",
        "location": "Block C, Level 1",
        "capacity": 120,
        "facilities": ["Microphone"],
        "accessibility": "Lift access",
        "supported_layouts": ["Classroom"],
        "operating_hours": "09:00-18:00",
        "setup_time_mins": 15,
        "turnaround_time_mins": 20,
    }

    resp = patch(client, "tok_venue1", hall.id, new)

    assert resp.status_code == 200
    assert sorted(resp.json()["updated_fields"]) == sorted(new)
    after = row(db_session, Venue, hall.id)
    assert after["name"] == "Harbour Hall"
    assert after["location"] == "Block C, Level 1"
    assert after["capacity"] == 120
    assert json.loads(after["facilities"]) == ["Microphone"]
    assert after["accessibility"] == "Lift access"
    assert json.loads(after["supported_layouts"]) == ["Classroom"]
    assert after["operating_hours"] == "09:00-18:00"
    assert after["setup_time_mins"] == 15
    assert after["turnaround_time_mins"] == 20
    assert after["is_active"] is True


def test_tc3103_invalid_values_are_refused_and_nothing_changes(client, db_session, hall):
    """TC-3103 | Negative Path | AC1 (D1: same validation as SCRUM-30)
    Blank name, blank location, capacity 0, capacity -5, setup -1, turnaround -1.
    Expected: 422 for each, and the venue row is unchanged."""
    before = row(db_session, Venue, hall.id)
    bad_bodies = [
        {"name": "   "},
        {"location": ""},
        {"capacity": 0},
        {"capacity": -5},
        {"setup_time_mins": -1},
        {"turnaround_time_mins": -1},
    ]
    for body in bad_bodies:
        assert patch(client, "tok_venue1", hall.id, body).status_code == 422, body
    assert row(db_session, Venue, hall.id) == before


def test_tc3104_smallest_allowed_values_are_accepted(client, db_session, hall):
    """TC-3104 | Boundary Path | AC1 (D1)
    Capacity 1 (just above 0), setup 0 and turnaround 0 (exactly at the limit).
    Expected: 200 and the values are saved."""
    resp = patch(client, "tok_venue1", hall.id, {"capacity": 1, "setup_time_mins": 0, "turnaround_time_mins": 0})

    assert resp.status_code == 200
    after = row(db_session, Venue, hall.id)
    assert (after["capacity"], after["setup_time_mins"], after["turnaround_time_mins"]) == (1, 0, 0)


def test_tc3105_duplicate_name_is_refused(client, db_session, hall):
    """TC-3105 | Negative Path | AC1 (D1)
    Another venue "Seminar Room" exists, and a retired venue "Old Store".
    Rename Grand Hall to "seminar room" and then to "OLD STORE".
    Expected: 409 both times (case-insensitive, retired names stay taken, as in
    SCRUM-30's create); Grand Hall keeps its name."""
    add_venue(db_session, "Seminar Room")
    add_venue(db_session, "Old Store", is_active=False)

    for name in ["seminar room", "OLD STORE"]:
        resp = patch(client, "tok_venue1", hall.id, {"name": name})
        assert resp.status_code == 409, name
    assert row(db_session, Venue, hall.id)["name"] == "Grand Hall"


def test_tc3106_renaming_a_venue_to_its_own_name_is_allowed(client, db_session, hall):
    """TC-3106 | Boundary Path | AC1 (D1: the duplicate check ignores the venue itself)
    Rename Grand Hall to "GRAND HALL".
    Expected: 200 and the new spelling is saved."""
    resp = patch(client, "tok_venue1", hall.id, {"name": "GRAND HALL"})

    assert resp.status_code == 200
    assert row(db_session, Venue, hall.id)["name"] == "GRAND HALL"


def test_tc3107_empty_body_is_refused(client, db_session, hall):
    """TC-3107 | Negative Path | AC1 (D1)
    PATCH with {}.
    Expected: 422 and the venue is unchanged."""
    before = row(db_session, Venue, hall.id)

    resp = patch(client, "tok_venue1", hall.id, {})

    assert resp.status_code == 422
    assert row(db_session, Venue, hall.id) == before


def test_tc3108_is_active_and_unknown_fields_are_refused(client, db_session, hall):
    """TC-3108 | Negative Path | AC1 (D1: is_active can't be changed here)
    PATCH with {"is_active": false}, then with {"colour": "blue"}.
    Expected: 422 both times; the venue stays active and unchanged."""
    before = row(db_session, Venue, hall.id)

    assert patch(client, "tok_venue1", hall.id, {"is_active": False}).status_code == 422
    assert patch(client, "tok_venue1", hall.id, {"colour": "blue"}).status_code == 422
    assert row(db_session, Venue, hall.id) == before


def test_tc3109_null_is_refused_for_required_attributes(client, db_session, hall):
    """TC-3109 | Negative Path | AC1 (D1)
    PATCH with null for name, location, capacity, setup_time_mins, turnaround_time_mins.
    Expected: 422 for each (these columns can't be empty), never a server error."""
    before = row(db_session, Venue, hall.id)
    for field in ["name", "location", "capacity", "setup_time_mins", "turnaround_time_mins"]:
        assert patch(client, "tok_venue1", hall.id, {field: None}).status_code == 422, field
    assert row(db_session, Venue, hall.id) == before


def test_tc3110_null_clears_optional_attributes(client, db_session, hall):
    """TC-3110 | Boundary Path | AC1 (D1)
    PATCH with null for facilities, accessibility, supported_layouts, operating_hours.
    Expected: 200; all four are stored as NULL (not recorded), as when a venue
    is created without them."""
    body = {"facilities": None, "accessibility": None, "supported_layouts": None, "operating_hours": None}

    resp = patch(client, "tok_venue1", hall.id, body)

    assert resp.status_code == 200
    after = row(db_session, Venue, hall.id)
    for field in body:
        assert after[field] is None, field


def test_tc3111_values_are_cleaned_as_on_create(client, db_session, hall):
    """TC-3111 | Boundary Path (partitioning) | AC1 (D1: same handling as SCRUM-30)
    Name "  Riverside Hall  ", facilities [" Projector ", "  "], accessibility "  Lift  ".
    Expected: stored as "Riverside Hall", ["Projector"] and "Lift"."""
    resp = patch(
        client, "tok_venue1", hall.id,
        {"name": "  Riverside Hall  ", "facilities": [" Projector ", "  "], "accessibility": "  Lift  "},
    )

    assert resp.status_code == 200
    after = row(db_session, Venue, hall.id)
    assert after["name"] == "Riverside Hall"
    assert json.loads(after["facilities"]) == ["Projector"]
    assert after["accessibility"] == "Lift"


def test_tc3112_update_unknown_venue_gives_404(client, db_session, users):
    """TC-3112 | Negative Path | D1
    PATCH /venues/99999 with {"capacity": 10}.
    Expected: 404 "Venue not found"."""
    resp = patch(client, "tok_venue1", UNKNOWN_VENUE_ID, {"capacity": 10})

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Venue not found"


def test_tc3113_retired_venue_can_be_corrected_but_stays_retired(client, db_session, hall):
    """TC-3113 | Workflow Variation | AC1, AC2 (D1)
    Retire Grand Hall, then PATCH {"capacity": 180}.
    Expected: 200; capacity saved; is_active still false; still absent from GET /venues."""
    assert retire(client, "tok_venue1", hall.id).status_code == 200

    resp = patch(client, "tok_venue1", hall.id, {"capacity": 180})

    assert resp.status_code == 200
    assert resp.json()["venue"]["is_active"] is False
    after = row(db_session, Venue, hall.id)
    assert after["capacity"] == 180
    assert after["is_active"] is False
    assert "Grand Hall" not in [v["name"] for v in client.get("/venues").json()]


def test_tc3114_only_venue_staff_can_update(client, db_session, hall):
    """TC-3114 | Cross-Cutting | D3
    PATCH {"capacity": 10} as each role.
    Expected: no session 401; organiser, coordinator, lead, admin 403; the venue
    is unchanged after every refusal; venue_staff 200."""
    before = row(db_session, Venue, hall.id)
    for token, code in [(None, 401), ("tok_org1", 403), ("tok_coord1", 403), ("tok_lead1", 403), ("tok_admin1", 403)]:
        assert patch(client, token, hall.id, {"capacity": 10}).status_code == code, token
    assert row(db_session, Venue, hall.id) == before

    assert patch(client, "tok_venue1", hall.id, {"capacity": 10}).status_code == 200


# ================================================================ retire (AC2, AC3)


def test_tc3115_venue_staff_retires_a_venue(client, db_session, hall):
    """TC-3115 | Happy Path | AC2 (D2)
    Venue Staff retires Grand Hall, which has no bookings.
    Expected: 200; is_active false in the response and the database;
    affected_bookings is []."""
    resp = retire(client, "tok_venue1", hall.id)

    assert resp.status_code == 200
    body = resp.json()
    assert body["message"] == "Venue retired successfully"
    assert body["venue"]["is_active"] is False
    assert body["affected_bookings"] == []
    assert row(db_session, Venue, hall.id)["is_active"] is False


def test_tc3116_retired_venue_no_longer_appears_as_bookable(client, db_session, hall):
    """TC-3116 | Workflow Variation | AC2 (D4)
    Retire Grand Hall through the endpoint, then look for it as a Coordinator.
    Expected: absent from GET /venues and GET /venues/search (with and without a
    period); GET /venues/{id} gives 404."""
    add_venue(db_session, "Seminar Room")
    assert retire(client, "tok_venue1", hall.id).status_code == 200

    login(client, "tok_coord1")
    assert [v["name"] for v in client.get("/venues").json()] == ["Seminar Room"]
    assert [v["name"] for v in client.get("/venues/search").json()] == ["Seminar Room"]
    start = (utc_now() + timedelta(days=10)).isoformat()
    end = (utc_now() + timedelta(days=10, hours=2)).isoformat()
    searched = client.get("/venues/search", params={"start_time": start, "end_time": end}).json()
    assert [v["name"] for v in searched] == ["Seminar Room"]
    assert client.get(f"/venues/{hall.id}").status_code == 404


def test_tc3117_retire_lists_affected_bookings_without_changing_them(client, db_session, hall, event):
    """TC-3117 | Boundary Path | AC2, AC3 (D2)
    Grand Hall bookings: future Confirmed, Approved, Tentative and Pending; one in
    progress (started 1 hour ago, ends in 1 hour); one that ended 1 minute ago;
    a future Cancelled one. Another venue has a future Confirmed booking.
    Expected: affected_bookings lists the four future blocking bookings and the
    one in progress, in start order, with booking_id, event_id, event_title,
    booking_start, booking_end and status; the ended, cancelled and other-venue
    bookings are not listed; every booking's status is unchanged."""
    now = utc_now()
    other = add_venue(db_session, "Seminar Room")
    in_progress = add_booking(db_session, hall, event, now - timedelta(hours=1), now + timedelta(hours=1))
    future = []
    for i, status in enumerate(["Pending", "Tentative", "Approved", "Confirmed"], start=1):
        start = now + timedelta(days=i)
        future.append(add_booking(db_session, hall, event, start, start + timedelta(hours=2), status))
    ended = add_booking(db_session, hall, event, now - timedelta(hours=3), now - timedelta(minutes=1))
    cancelled = add_booking(
        db_session, hall, event, now + timedelta(days=9), now + timedelta(days=9, hours=2), "Cancelled",
    )
    elsewhere = add_booking(db_session, other, event, now + timedelta(days=2), now + timedelta(days=2, hours=2))
    statuses_before = {b.id: b.status for b in db_session.query(VenueBooking).all()}

    resp = retire(client, "tok_venue1", hall.id)

    assert resp.status_code == 200
    affected = resp.json()["affected_bookings"]
    assert [a["booking_id"] for a in affected] == [in_progress.id] + [b.id for b in future]
    assert [a["status"] for a in affected] == ["Confirmed", "Pending", "Tentative", "Approved", "Confirmed"]
    first = affected[0]
    assert set(first.keys()) == {"booking_id", "event_id", "event_title", "booking_start", "booking_end", "status"}
    assert first["event_id"] == event.id
    assert first["event_title"] == "Annual Gala"
    assert datetime.fromisoformat(first["booking_start"]) == now - timedelta(hours=1)
    assert datetime.fromisoformat(first["booking_end"]) == now + timedelta(hours=1)
    listed = {a["booking_id"] for a in affected}
    assert ended.id not in listed and cancelled.id not in listed and elsewhere.id not in listed

    db_session.expire_all()
    assert {b.id: b.status for b in db_session.query(VenueBooking).all()} == statuses_before


def test_tc3118_retiring_keeps_all_history(client, db_session, hall, event):
    """TC-3118 | Negative Path | AC3 (D4: nothing is ever deleted)
    Grand Hall has a past booking and a future booking for an event.
    Expected: after retiring, the venue row is unchanged except is_active; both
    booking rows and the event row are unchanged; the booking count is the same."""
    now = utc_now()
    past = add_booking(db_session, hall, event, now - timedelta(days=5), now - timedelta(days=5) + timedelta(hours=2))
    upcoming = add_booking(db_session, hall, event, now + timedelta(days=5), now + timedelta(days=5, hours=2))
    venue_before = row(db_session, Venue, hall.id)
    bookings_before = {b.id: row(db_session, VenueBooking, b.id) for b in (past, upcoming)}
    event_before = row(db_session, Event, event.id)

    assert retire(client, "tok_venue1", hall.id).status_code == 200

    venue_after = row(db_session, Venue, hall.id)
    assert venue_after["is_active"] is False
    assert {k: v for k, v in venue_after.items() if k != "is_active"} == {
        k: v for k, v in venue_before.items() if k != "is_active"
    }
    for booking_id, before in bookings_before.items():
        assert row(db_session, VenueBooking, booking_id) == before
    assert row(db_session, Event, event.id) == event_before
    assert db_session.query(VenueBooking).count() == 2


def test_tc3119_retiring_an_already_retired_venue_gives_409(client, db_session, users):
    """TC-3119 | Negative Path | D2
    "Old Store" is already retired.
    Expected: 409 and the venue row is unchanged."""
    old = add_venue(db_session, "Old Store", is_active=False)
    before = row(db_session, Venue, old.id)

    resp = retire(client, "tok_venue1", old.id)

    assert resp.status_code == 409
    assert row(db_session, Venue, old.id) == before


def test_tc3120_retiring_an_unknown_venue_gives_404(client, db_session, users):
    """TC-3120 | Negative Path | D2
    POST /venues/99999/retire.
    Expected: 404 "Venue not found"."""
    resp = retire(client, "tok_venue1", UNKNOWN_VENUE_ID)

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Venue not found"


def test_tc3121_only_venue_staff_can_retire(client, db_session, hall):
    """TC-3121 | Cross-Cutting | D3
    POST /venues/{id}/retire as each role.
    Expected: no session 401; organiser, coordinator, lead, admin 403; the venue
    is still active after every refusal; venue_staff 200."""
    for token, code in [(None, 401), ("tok_org1", 403), ("tok_coord1", 403), ("tok_lead1", 403), ("tok_admin1", 403)]:
        assert retire(client, token, hall.id).status_code == code, token
    assert row(db_session, Venue, hall.id)["is_active"] is True

    assert retire(client, "tok_venue1", hall.id).status_code == 200
