"""SCRUM-34: See whether a venue appears suitable for an event.

Acceptance criteria covered:
  AC1  Each candidate venue carries a suitability indication derived from the
       event's requirements and the venue's recorded attributes.
  AC2  A venue whose capacity is below the event's expected attendance is not
       presented as suitable.
  AC3  A venue missing a facility the event requires is not presented as
       suitable.
  AC4  The Coordinator can see which specific requirement caused a venue to be
       flagged unsuitable.
  AC5  Where information a check depends on is missing, the venue is not
       silently marked suitable; the gap is surfaced.

PO decisions tested (see the PR description for the full text):
  D2  facilities left out = "not stated" (unknown); facilities= = none required.
  D3  capacity passes when capacity >= expected attendance; none recorded = unknown.
  D4  facilities match case-insensitively, ignoring surrounding spaces. Venue
      facilities that were never recorded (NULL, "" or blank) = unknown, unless
      nothing is required. A recorded empty list fails.
  D5  any fail -> unsuitable; else any unknown -> unknown; else suitable.
  D6  assigned Coordinator or any Lead: 200. Other roles 403 before the event is
      looked up; unknown event 404; unassigned Coordinator 403. No session 401.
  D7  only active venues are evaluated. The endpoint is advisory: nothing changes.

Expected attendance of 0 or less is not tested: event create, submit and amend
already reject it (validate_event_business_rules), so it cannot be stored.
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
from backend.models import Event, EventStatusHistory, LoginSession, User, Venue, VenueBooking
from backend.security import hash_password

TEST_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

COOKIE = "connectsphere_session"
UNKNOWN_EVENT_ID = 99999


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
    """One user per role, plus a second coordinator. Token for each: tok_<username>."""
    specs = [
        ("org1", "organiser", False),
        ("coord_a", "coordinator", True),
        ("coord_b", "coordinator", True),
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
    """Submitted event expecting 80 people, assigned to coord_a, 30 days from today."""
    ev = Event(
        title="Annual Gala",
        proposed_start=datetime.now().replace(microsecond=0) + timedelta(days=30),
        expected_attendance=80,
        organiser_id=users["org1"].id,
        status="Submitted",
        assigned_coordinator_id=users["coord_a"].id,
        submitted_at=datetime.now(),
    )
    db_session.add(ev)
    db_session.commit()
    return ev


def add_venue(db_session, name, capacity, facilities, is_active=True):
    """facilities: a list is stored as JSON (as serialize_list does); None is stored
    as NULL; a str is stored exactly as given (to simulate a direct DB write)."""
    if isinstance(facilities, list):
        stored = json.dumps(facilities)
    else:
        stored = facilities
    venue = Venue(
        name=name,
        location="Block A",
        capacity=capacity,
        facilities=stored,
        is_active=is_active,
    )
    db_session.add(venue)
    db_session.commit()
    return venue


_NOT_GIVEN = object()


def get_suitability(client, token, event_id, facilities=_NOT_GIVEN):
    if token is None:
        client.cookies.clear()
    else:
        client.cookies.set(COOKIE, token)
    params = {} if facilities is _NOT_GIVEN else {"facilities": facilities}
    return client.get(f"/events/{event_id}/venue-suitability", params=params)


def venue_entry(body, name):
    matches = [v for v in body["venues"] if v["name"] == name]
    assert len(matches) == 1, f"expected exactly one venue named {name!r}"
    return matches[0]


def check_of(entry, check_name):
    matches = [c for c in entry["checks"] if c["check"] == check_name]
    assert len(matches) == 1, f"expected exactly one {check_name!r} check"
    return matches[0]


# ---------------------------------------------------------------- happy path


def test_tc3401_suitable_venue_happy_path(client, db_session, event):
    """TC-3401 | Happy Path | AC1
    Event expects 80 and needs Projector; venue holds 100 with Projector.
    Expected: suitable, both checks pass, and the event's requirements are echoed."""
    hall = add_venue(db_session, "Hall", 100, ["Projector", "WiFi"])

    resp = get_suitability(client, "tok_coord_a", event.id, "Projector")

    assert resp.status_code == 200
    body = resp.json()
    assert body["event_id"] == event.id
    assert body["title"] == "Annual Gala"
    assert body["expected_attendance"] == 80
    assert body["required_facilities"] == ["Projector"]
    entry = venue_entry(body, "Hall")
    assert entry["id"] == hall.id
    assert entry["capacity"] == 100
    assert entry["facilities"] == ["Projector", "WiFi"]
    assert entry["suitability"] == "suitable"
    assert entry["checks"] == [
        {"check": "capacity", "result": "pass", "detail": "capacity 100 meets expected attendance 80"},
        {"check": "facilities", "result": "pass", "detail": "has all required facilities: Projector"},
    ]


# ---------------------------------------------------------------- capacity (AC2)


def test_tc3402_capacity_just_below_at_and_above_attendance(client, db_session, event):
    """TC-3402 | Boundary Path | AC2 (D3)
    Capacities 79, 80 and 81 against expected attendance 80.
    Expected: fail, pass, pass; equal capacity passes."""
    add_venue(db_session, "Room 79", 79, [])
    add_venue(db_session, "Room 80", 80, [])
    add_venue(db_session, "Room 81", 81, [])

    body = get_suitability(client, "tok_coord_a", event.id, "").json()

    r79 = venue_entry(body, "Room 79")
    assert check_of(r79, "capacity") == {
        "check": "capacity", "result": "fail", "detail": "capacity 79 is below expected attendance 80",
    }
    assert r79["suitability"] == "unsuitable"

    r80 = venue_entry(body, "Room 80")
    assert check_of(r80, "capacity") == {
        "check": "capacity", "result": "pass", "detail": "capacity 80 meets expected attendance 80",
    }
    assert r80["suitability"] == "suitable"

    r81 = venue_entry(body, "Room 81")
    assert check_of(r81, "capacity") == {
        "check": "capacity", "result": "pass", "detail": "capacity 81 meets expected attendance 80",
    }
    assert r81["suitability"] == "suitable"


# ---------------------------------------------------------------- facilities (AC3, AC4)


def test_tc3403_missing_required_facility_is_named(client, db_session, event):
    """TC-3403 | Negative Path | AC3, AC4
    Venue (capacity 100) has Projector; event needs Projector and Stage.
    Expected: unsuitable; facilities check fails with "missing: Stage"."""
    add_venue(db_session, "Hall", 100, ["Projector"])

    entry = venue_entry(get_suitability(client, "tok_coord_a", event.id, "Projector,Stage").json(), "Hall")

    assert entry["suitability"] == "unsuitable"
    assert check_of(entry, "capacity")["result"] == "pass"
    assert check_of(entry, "facilities") == {"check": "facilities", "result": "fail", "detail": "missing: Stage"}


def test_tc3404_both_checks_fail_and_both_are_reported(client, db_session, event):
    """TC-3404 | Negative Path | AC2, AC3, AC4
    Venue holds 50 with Projector; event expects 80 and needs Stage.
    Expected: unsuitable, and both failing requirements are reported."""
    add_venue(db_session, "Small Hall", 50, ["Projector"])

    entry = venue_entry(get_suitability(client, "tok_coord_a", event.id, "Stage").json(), "Small Hall")

    assert entry["suitability"] == "unsuitable"
    assert entry["checks"] == [
        {"check": "capacity", "result": "fail", "detail": "capacity 50 is below expected attendance 80"},
        {"check": "facilities", "result": "fail", "detail": "missing: Stage"},
    ]


def test_tc3411_every_missing_facility_named_in_request_order(client, db_session, event):
    """TC-3411 | Negative Path | AC4
    Venue has Projector; event needs Stage, Projector and Microphone.
    Expected: "missing: Stage, Microphone" (request order, present ones left out)."""
    add_venue(db_session, "Hall", 100, ["Projector"])

    entry = venue_entry(
        get_suitability(client, "tok_coord_a", event.id, "Stage,Projector,Microphone").json(), "Hall"
    )

    assert check_of(entry, "facilities") == {
        "check": "facilities", "result": "fail", "detail": "missing: Stage, Microphone",
    }
    assert entry["suitability"] == "unsuitable"


def test_tc3407_matching_ignores_case_and_surrounding_spaces(client, db_session, event):
    """TC-3407 | Boundary Path (partitioning) | AC3 (D4)
    One venue stores "Projector", another stores " projector ".
    Requests "projector" and " Projector ".
    Expected: the facilities check passes in all four combinations."""
    add_venue(db_session, "Hall One", 100, ["Projector"])
    add_venue(db_session, "Hall Two", 100, [" projector "])

    for requested in ["projector", " Projector "]:
        body = get_suitability(client, "tok_coord_a", event.id, requested).json()
        for name in ["Hall One", "Hall Two"]:
            entry = venue_entry(body, name)
            assert check_of(entry, "facilities")["result"] == "pass", (requested, name)
            assert entry["suitability"] == "suitable", (requested, name)


def test_tc3408_facilities_left_out_versus_given_empty(client, db_session, event):
    """TC-3408 | Boundary Path (partitioning) | AC5 (D2)
    Venue holds 100 with Projector.
    Expected: no facilities parameter -> required_facilities null, facilities check
    unknown ("required facilities not stated"), venue unknown.
    facilities= -> required_facilities [], check passes ("no facilities required"), suitable."""
    add_venue(db_session, "Hall", 100, ["Projector"])

    left_out = get_suitability(client, "tok_coord_a", event.id).json()
    assert left_out["required_facilities"] is None
    entry = venue_entry(left_out, "Hall")
    assert check_of(entry, "facilities") == {
        "check": "facilities", "result": "unknown", "detail": "required facilities not stated",
    }
    assert entry["suitability"] == "unknown"

    empty = get_suitability(client, "tok_coord_a", event.id, "").json()
    assert empty["required_facilities"] == []
    entry = venue_entry(empty, "Hall")
    assert check_of(entry, "facilities") == {
        "check": "facilities", "result": "pass", "detail": "no facilities required",
    }
    assert entry["suitability"] == "suitable"


def test_tc3413_blank_entries_dropped_and_duplicates_required_once(client, db_session, event):
    """TC-3413 | Boundary Path (partitioning) | AC1, AC4 (D2, PO answer 1)
    Venue holds 100 with Projector.
    Expected: facilities=" , " reads as none required (required_facilities [], pass).
    "Stage,stage" is required once: ["Stage"], "missing: Stage".
    "Projector,projector" is required once: ["Projector"], passes."""
    add_venue(db_session, "Hall", 100, ["Projector"])

    blanks = get_suitability(client, "tok_coord_a", event.id, " , ").json()
    assert blanks["required_facilities"] == []
    entry = venue_entry(blanks, "Hall")
    assert check_of(entry, "facilities") == {
        "check": "facilities", "result": "pass", "detail": "no facilities required",
    }
    assert entry["suitability"] == "suitable"

    dup_missing = get_suitability(client, "tok_coord_a", event.id, "Stage,stage").json()
    assert dup_missing["required_facilities"] == ["Stage"]
    assert check_of(venue_entry(dup_missing, "Hall"), "facilities") == {
        "check": "facilities", "result": "fail", "detail": "missing: Stage",
    }

    dup_present = get_suitability(client, "tok_coord_a", event.id, "Projector,projector").json()
    assert dup_present["required_facilities"] == ["Projector"]
    assert check_of(venue_entry(dup_present, "Hall"), "facilities") == {
        "check": "facilities", "result": "pass", "detail": "has all required facilities: Projector",
    }


# ---------------------------------------------------------------- missing information (AC5)


def test_tc3405_no_expected_attendance_gives_unknown(client, db_session, event):
    """TC-3405 | Boundary Path (missing info) | AC5 (D3)
    Event has no expected attendance; venue holds 100, nothing required.
    Expected: expected_attendance null; capacity check unknown
    ("event has no expected attendance recorded"); venue unknown, not suitable."""
    event.expected_attendance = None
    db_session.commit()
    add_venue(db_session, "Hall", 100, [])

    body = get_suitability(client, "tok_coord_a", event.id, "").json()

    assert body["expected_attendance"] is None
    entry = venue_entry(body, "Hall")
    assert check_of(entry, "capacity") == {
        "check": "capacity", "result": "unknown", "detail": "event has no expected attendance recorded",
    }
    assert entry["suitability"] == "unknown"


def test_tc3406_facilities_never_recorded_versus_recorded_empty(client, db_session, event):
    """TC-3406 | Boundary Path (missing info) | AC5, AC3 (D4)
    Venue A's facilities are NULL; venue B recorded an empty list. Event needs Projector.
    Expected: A -> facilities null, check unknown ("venue facilities not recorded"), venue unknown.
    B -> facilities [], check fails ("missing: Projector"), venue unsuitable."""
    add_venue(db_session, "Venue A", 100, None)
    add_venue(db_session, "Venue B", 100, [])

    body = get_suitability(client, "tok_coord_a", event.id, "Projector").json()

    a = venue_entry(body, "Venue A")
    assert a["facilities"] is None
    assert check_of(a, "facilities") == {
        "check": "facilities", "result": "unknown", "detail": "venue facilities not recorded",
    }
    assert a["suitability"] == "unknown"

    b = venue_entry(body, "Venue B")
    assert b["facilities"] == []
    assert check_of(b, "facilities") == {"check": "facilities", "result": "fail", "detail": "missing: Projector"}
    assert b["suitability"] == "unsuitable"


def test_tc3414_facilities_never_recorded_but_none_required_passes(client, db_session, event):
    """TC-3414 | Boundary Path | AC5 (D4 "unless no facilities are required")
    Venue's facilities are NULL; facilities= (none required).
    Expected: facilities check passes ("no facilities required"); venue suitable."""
    add_venue(db_session, "Hall", 100, None)

    entry = venue_entry(get_suitability(client, "tok_coord_a", event.id, "").json(), "Hall")

    assert check_of(entry, "facilities") == {
        "check": "facilities", "result": "pass", "detail": "no facilities required",
    }
    assert entry["suitability"] == "suitable"


def test_tc3420_empty_or_blank_stored_facilities_treated_as_not_recorded(client, db_session, event):
    """TC-3420 | Boundary Path (missing info) | AC5 (D4, PO answer 2)
    Venues whose facilities are stored as "" and as "   " (only possible by a direct
    DB write). Event needs Projector.
    Expected: both treated like NULL: facilities null, check unknown
    ("venue facilities not recorded"), venue unknown."""
    add_venue(db_session, "Empty Text Hall", 100, "")
    add_venue(db_session, "Blank Text Hall", 100, "   ")

    body = get_suitability(client, "tok_coord_a", event.id, "Projector").json()

    for name in ["Empty Text Hall", "Blank Text Hall"]:
        entry = venue_entry(body, name)
        assert entry["facilities"] is None, name
        assert check_of(entry, "facilities") == {
            "check": "facilities", "result": "unknown", "detail": "venue facilities not recorded",
        }, name
        assert entry["suitability"] == "unknown", name


# ---------------------------------------------------------------- overall result (D5)


def test_tc3415_a_failure_outweighs_an_unknown(client, db_session, event):
    """TC-3415 | Boundary Path | AC2, AC5 (D5)
    Venue holds 50 (below 80); facilities not stated.
    Expected: capacity fail, facilities unknown, venue unsuitable."""
    add_venue(db_session, "Small Hall", 50, ["Projector"])

    entry = venue_entry(get_suitability(client, "tok_coord_a", event.id).json(), "Small Hall")

    assert check_of(entry, "capacity")["result"] == "fail"
    assert check_of(entry, "facilities")["result"] == "unknown"
    assert entry["suitability"] == "unsuitable"


def test_tc3416_an_unknown_outweighs_a_pass(client, db_session, event):
    """TC-3416 | Boundary Path | AC5 (D5)
    Venue holds 100 (meets 80); facilities not stated.
    Expected: capacity pass, facilities unknown, venue unknown (never suitable)."""
    add_venue(db_session, "Hall", 100, ["Projector"])

    entry = venue_entry(get_suitability(client, "tok_coord_a", event.id).json(), "Hall")

    assert check_of(entry, "capacity")["result"] == "pass"
    assert check_of(entry, "facilities")["result"] == "unknown"
    assert entry["suitability"] == "unknown"


def test_tc3417_every_candidate_gets_its_own_indication(client, db_session, event):
    """TC-3417 | Workflow Variation | AC1 (D1)
    Three active venues, added out of name order: Gamma (100, NULL facilities),
    Alpha (100, Projector), Beta (50, Projector). Event needs Projector.
    Expected: three entries in name order, each with its own indication
    (Alpha suitable, Beta unsuitable, Gamma unknown), each with exactly the
    D1 fields and a capacity check followed by a facilities check."""
    add_venue(db_session, "Gamma Room", 100, None)
    add_venue(db_session, "Alpha Room", 100, ["Projector"])
    add_venue(db_session, "Beta Room", 50, ["Projector"])

    body = get_suitability(client, "tok_coord_a", event.id, "Projector").json()

    assert set(body.keys()) == {"event_id", "title", "expected_attendance", "required_facilities", "venues"}
    assert [v["name"] for v in body["venues"]] == ["Alpha Room", "Beta Room", "Gamma Room"]
    assert [v["suitability"] for v in body["venues"]] == ["suitable", "unsuitable", "unknown"]
    for entry in body["venues"]:
        assert set(entry.keys()) == {"id", "name", "capacity", "facilities", "suitability", "checks"}
        assert [c["check"] for c in entry["checks"]] == ["capacity", "facilities"]
        for c in entry["checks"]:
            assert set(c.keys()) == {"check", "result", "detail"}
            assert c["result"] in {"pass", "fail", "unknown"}
            assert c["detail"]


# ---------------------------------------------------------------- scope (D7)


def test_tc3412_retired_venue_is_never_listed(client, db_session, event):
    """TC-3412 | Negative Path | AC1 (D7)
    One active venue and one retired venue (is_active false), both otherwise suitable.
    Expected: only the active venue is listed."""
    add_venue(db_session, "Open Hall", 100, ["Projector"])
    add_venue(db_session, "Closed Hall", 100, ["Projector"], is_active=False)

    body = get_suitability(client, "tok_coord_a", event.id, "Projector").json()

    assert [v["name"] for v in body["venues"]] == ["Open Hall"]


def test_tc3418_advisory_only_nothing_is_changed_or_booked(client, db_session, event):
    """TC-3418 | Negative Path | Story note (the system advises; nothing is booked or changed)
    Event and venue saved. Call the endpoint, then re-read the database.
    Expected: event and venue rows unchanged; no VenueBooking and no status history created."""
    hall = add_venue(db_session, "Hall", 100, ["Projector"])
    event_before = {c.name: getattr(event, c.name) for c in Event.__table__.columns}
    venue_before = {c.name: getattr(hall, c.name) for c in Venue.__table__.columns}
    history_before = db_session.query(EventStatusHistory).count()

    resp = get_suitability(client, "tok_coord_a", event.id, "Projector")
    assert resp.status_code == 200

    db_session.expire_all()
    event_after = db_session.get(Event, event.id)
    venue_after = db_session.get(Venue, hall.id)
    assert {c.name: getattr(event_after, c.name) for c in Event.__table__.columns} == event_before
    assert {c.name: getattr(venue_after, c.name) for c in Venue.__table__.columns} == venue_before
    assert db_session.query(VenueBooking).count() == 0
    assert db_session.query(EventStatusHistory).count() == history_before


# ---------------------------------------------------------------- who can call it (D6)


def test_tc3409_who_can_see_suitability(client, db_session, users, event):
    """TC-3409 | Cross-Cutting | D6 (Week 7 Change #5)
    Event assigned to coord_a.
    Expected: no session 401; organiser 403; coord_b (not assigned) 403;
    coord_a (assigned) 200; lead 200; venue_staff 403; admin 403.
    A second event with no assigned coordinator: coord_a 403; lead 200 (PO answer 3)."""
    add_venue(db_session, "Hall", 100, ["Projector"])

    expected = [
        (None, 401),
        ("tok_org1", 403),
        ("tok_coord_b", 403),
        ("tok_coord_a", 200),
        ("tok_lead1", 200),
        ("tok_venue1", 403),
        ("tok_admin1", 403),
    ]
    for token, code in expected:
        assert get_suitability(client, token, event.id, "Projector").status_code == code, token

    unassigned = Event(
        title="Unassigned Talk",
        proposed_start=datetime.now().replace(microsecond=0) + timedelta(days=30),
        expected_attendance=40,
        organiser_id=users["org1"].id,
        status="Submitted",
        assigned_coordinator_id=None,
    )
    db_session.add(unassigned)
    db_session.commit()

    assert get_suitability(client, "tok_coord_a", unassigned.id, "Projector").status_code == 403
    assert get_suitability(client, "tok_lead1", unassigned.id, "Projector").status_code == 200


def test_tc3410_unknown_event_gives_404(client, db_session, users):
    """TC-3410 | Negative Path | D6
    Logged in as the Lead; event id 99999 does not exist.
    Expected: 404 "Event not found"."""
    resp = get_suitability(client, "tok_lead1", UNKNOWN_EVENT_ID, "Projector")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Event not found"


def test_tc3419_role_check_runs_before_event_lookup(client, db_session, users):
    """TC-3419 | Cross-Cutting | D6 (PO answer 3: house pattern)
    Event id 99999 does not exist.
    Expected: organiser, venue_staff and admin get 403, so they learn nothing about
    which events exist; coord_b (a coordinator) gets 404 "Event not found"."""
    for token in ["tok_org1", "tok_venue1", "tok_admin1"]:
        assert get_suitability(client, token, UNKNOWN_EVENT_ID, "Projector").status_code == 403, token

    resp = get_suitability(client, "tok_coord_b", UNKNOWN_EVENT_ID, "Projector")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Event not found"
