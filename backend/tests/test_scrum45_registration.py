"""[Sprint 5] Attendee registration: SCRUM-45, 46, 47, 48, 52 and the registration half of SCRUM-65.

Registration is switched on per event in its own table, and opens only while the
event is Confirmed. An Attendee sees a short whitelist of fields, never internal ones.
"""
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
    EventRegistration,
    EventRegistrationSetting,
    EventStatusHistory,
    LoginSession,
    User,
    Venue,
    VenueBooking,
)
from backend.status import APPROVED, CANCELLED, COMPLETED, CONFIRMED, PLANNING

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# The only keys an Attendee ever receives about an event.
ATTENDEE_KEYS = {"event_id", "title", "description", "proposed_start", "venues", "places_left", "my_status", "event_state"}
INTERNAL_MARKERS = ["Internal purpose", "Internal venue need", "Internal equipment", "Internal access", "Internal reg",
                    "org_a", "coord_a", "Internal history", "purpose", "requirements", "organiser", "coordinator", "status_history"]


@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db):
    def override_get_db():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def users(db):
    roles = {
        "org_a": "organiser", "org_b": "organiser", "coord_a": "coordinator", "coord_b": "coordinator",
        "lead": "lead", "venue": "venue_staff", "tech": "technical_support",
        "att_1": "attendee", "att_2": "attendee", "att_3": "attendee",
    }
    found = {}
    for username, role in roles.items():
        user = User(username=username, password_hash="x", role=role,
                    is_active_coordinator=role == "coordinator")
        db.add(user)
        found[username] = user
    db.flush()
    for username, user in found.items():
        db.add(LoginSession(user_id=user.id, token=f"token-{username}", valid=True))
    db.commit()
    return found


def login(client, username):
    client.cookies.set("connectsphere_session", f"token-{username}")


def make_event(db, users, title="Open Day", status=CONFIRMED, attendance=100, enabled=True, venues=(), **fields):
    """An event with a registration setting (unless enabled is None) and Confirmed bookings at the given venues."""
    event = Event(title=title, description="Public description", purpose="Internal purpose",
                  venue_requirements="Internal venue need", equipment_requirements="Internal equipment",
                  accessibility_needs="Internal access", registration_needs="Internal reg",
                  organiser_id=users["org_a"].id, assigned_coordinator_id=users["coord_a"].id, status=status,
                  expected_attendance=attendance, proposed_start=datetime(2099, 3, 10, 14, 0), **fields)
    db.add(event)
    db.flush()
    db.add(EventStatusHistory(event_id=event.id, previous_status=None, new_status=status,
                              actor_id=users["org_a"].id, reason="Internal history"))
    if enabled is not None:
        db.add(EventRegistrationSetting(event_id=event.id, enabled=enabled, updated_by_id=users["org_a"].id))
    for index, venue in enumerate(venues):
        book(db, event, venue, day=index)
    db.commit()
    return event


def make_venue(db, name="Main Hall", capacity=50, location="Block A"):
    venue = Venue(name=name, location=location, capacity=capacity)
    db.add(venue)
    db.flush()
    return venue


def book(db, event, venue, status="Confirmed", day=0):
    start = datetime(2099, 3, 10 + day, 6, 0)
    booking = VenueBooking(event_id=event.id, venue_id=venue.id, booking_start=start,
                           booking_end=start + timedelta(hours=2), status=status)
    db.add(booking)
    db.flush()
    return booking


def add_registration(db, event, user, status="registered"):
    row = EventRegistration(event_id=event.id, attendee_id=user.id, status=status,
                            withdrawn_at=datetime(2099, 1, 1) if status == "withdrawn" else None)
    db.add(row)
    db.commit()
    return row


def listed(client):
    response = client.get("/registrations/events")
    assert response.status_code == 200
    return response.json()


def assert_no_internal_detail(payload):
    text = str(payload)
    for marker in INTERNAL_MARKERS:
        assert marker not in text, f"{marker!r} leaked in {text}"


# --- SCRUM-45: Attendees see appropriate information for open events ----------


def test_tc4501_attendee_sees_only_whitelisted_fields(client, db, users):
    """TC-4501: an open event shows title, description, date and time, venues, places left and own status, and nothing else."""
    event = make_event(db, users, venues=[make_venue(db)])
    login(client, "att_1")
    rows = listed(client)
    assert len(rows) == 1
    assert set(rows[0]) == ATTENDEE_KEYS
    assert rows[0]["event_id"] == event.id
    assert rows[0]["title"] == "Open Day"
    assert rows[0]["description"] == "Public description"
    assert rows[0]["proposed_start"] == "2099-03-10T14:00:00"
    assert rows[0]["venues"] == [{"name": "Main Hall", "location": "Block A"}]
    assert rows[0]["places_left"] == 50
    assert rows[0]["my_status"] is None
    assert rows[0]["event_state"] == "open"


def test_tc4502_internal_fields_are_absent(client, db, users):
    """TC-4502: purpose, requirements, Organiser, Coordinator, status history and notes never reach an Attendee."""
    make_event(db, users, venues=[make_venue(db)])
    login(client, "att_1")
    assert_no_internal_detail(listed(client))


@pytest.mark.parametrize("status", ["Draft", "Submitted", "Under Review", APPROVED, PLANNING, "Safety Review", COMPLETED, CANCELLED, "Rejected", "Returned"])
def test_tc4503_only_confirmed_events_are_listed(client, db, users, status):
    """TC-4503: an enabled event that isn't Confirmed is not open, whatever its status."""
    make_event(db, users, status=status)
    login(client, "att_1")
    assert listed(client) == []


@pytest.mark.parametrize("enabled", [False, None])
def test_tc4504_a_confirmed_event_without_registration_on_is_not_listed(client, db, users, enabled):
    """TC-4504: Confirmed is not enough: the setting must be on. A missing row counts as off."""
    make_event(db, users, enabled=enabled)
    login(client, "att_1")
    assert listed(client) == []


def test_tc4505_only_confirmed_bookings_name_a_venue(client, db, users):
    """TC-4505: pending and rejected bookings are internal, so their venues are not shown."""
    event = make_event(db, users, venues=[make_venue(db, "Real Hall")])
    book(db, event, make_venue(db, "Pending Hall"), status="Pending", day=1)
    book(db, event, make_venue(db, "Rejected Hall"), status="Rejected", day=2)
    db.commit()
    login(client, "att_1")
    assert [v["name"] for v in listed(client)[0]["venues"]] == ["Real Hall"]


def test_tc4506_two_bookings_of_one_venue_show_it_once(client, db, users):
    """TC-4506: a venue booked on two days is listed once."""
    hall = make_venue(db)
    make_event(db, users, venues=[hall, hall])
    login(client, "att_1")
    assert listed(client)[0]["venues"] == [{"name": "Main Hall", "location": "Block A"}]


@pytest.mark.parametrize("username", ["org_a", "coord_a", "lead", "venue", "tech"])
def test_tc4507_the_open_list_is_for_attendees_only(client, db, users, username):
    """TC-4507: other roles get 403, because this list is the Attendee's own view."""
    make_event(db, users)
    login(client, username)
    assert client.get("/registrations/events").status_code == 403


def test_tc4508_signed_out_is_refused(client, db, users):
    """TC-4508: no session, no list."""
    make_event(db, users)
    client.cookies.clear()
    assert client.get("/registrations/events").status_code == 401


def test_tc4509_soonest_event_first(client, db, users):
    """TC-4509: the list runs soonest first, with undated events last."""
    later = make_event(db, users, title="Later")
    sooner = make_event(db, users, title="Sooner")
    undated = make_event(db, users, title="Undated")
    sooner.proposed_start = later.proposed_start - timedelta(days=1)
    undated.proposed_start = None
    db.commit()
    login(client, "att_1")
    assert [row["title"] for row in listed(client)] == ["Sooner", "Later", "Undated"]
