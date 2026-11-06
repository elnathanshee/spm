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


# --- SCRUM-46: register, within capacity, no duplicates -----------------------


def register(client, event):
    return client.post(f"/registrations/events/{event.id}")


def rows_for(db, event):
    db.expire_all()
    return db.query(EventRegistration).filter(EventRegistration.event_id == event.id).all()


def test_tc4601_attendee_registers(client, db, users):
    """TC-4601: registering on an open event stores a row and shows my status and one place fewer."""
    event = make_event(db, users, attendance=10, venues=[make_venue(db, capacity=50)])
    login(client, "att_1")
    response = register(client, event)
    assert response.status_code == 201
    assert response.json()["my_status"] == "registered"
    assert response.json()["places_left"] == 9
    assert response.json()["message"]
    assert_no_internal_detail(response.json())
    assert [(r.attendee_id, r.status, r.withdrawn_at) for r in rows_for(db, event)] == [(users["att_1"].id, "registered", None)]


@pytest.mark.parametrize("attendance,capacity,expected", [(10, 50, 10), (50, 10, 10), (20, 20, 20)])
def test_tc4602_capacity_is_the_lower_of_attendance_and_venue(client, db, users, attendance, capacity, expected):
    """TC-4602: expected attendance and venue capacity, whichever is lower, whichever way round."""
    make_event(db, users, attendance=attendance, venues=[make_venue(db, capacity=capacity)])
    login(client, "att_1")
    assert listed(client)[0]["places_left"] == expected


def test_tc4603_several_venues_use_the_smallest(client, db, users):
    """TC-4603: decided on the safe side: with several Confirmed venues the smallest capacity decides."""
    make_event(db, users, attendance=100, venues=[make_venue(db, "Big", 80), make_venue(db, "Small", 30), make_venue(db, "Mid", 60)])
    login(client, "att_1")
    assert listed(client)[0]["places_left"] == 30


def test_tc4604_only_confirmed_bookings_count_towards_capacity(client, db, users):
    """TC-4604: a smaller venue that is only pending or rejected does not lower the limit."""
    event = make_event(db, users, attendance=100, venues=[make_venue(db, "Real", 40)])
    book(db, event, make_venue(db, "Tiny pending", 2), status="Pending", day=1)
    book(db, event, make_venue(db, "Tiny rejected", 3), status="Rejected", day=2)
    db.commit()
    login(client, "att_1")
    assert listed(client)[0]["places_left"] == 40


def test_tc4605_one_number_known_is_enough(client, db, users):
    """TC-4605: with no attendance figure the venue decides; with no Confirmed venue the attendance decides."""
    make_event(db, users, title="No attendance", attendance=None, venues=[make_venue(db, capacity=7)])
    make_event(db, users, title="No venue", attendance=9)
    login(client, "att_1")
    assert {row["title"]: row["places_left"] for row in listed(client)} == {"No attendance": 7, "No venue": 9}


def test_tc4606_no_number_known_refuses_with_a_sentence(client, db, users):
    """TC-4606: a Confirmed event with neither number has no known limit, so nobody can register yet."""
    event = make_event(db, users, attendance=None)
    login(client, "att_1")
    assert listed(client)[0]["places_left"] == 0
    response = register(client, event)
    assert response.status_code == 409
    assert "number of places" in response.json()["detail"]
    assert rows_for(db, event) == []


def test_tc4607_the_last_place_can_be_taken_and_then_it_is_full(client, db, users):
    """TC-4607: the boundary: the last place succeeds, the next person gets a 409 with a sentence."""
    event = make_event(db, users, attendance=2)
    login(client, "att_1")
    assert register(client, event).status_code == 201
    login(client, "att_2")
    response = register(client, event)
    assert response.status_code == 201
    assert response.json()["places_left"] == 0
    login(client, "att_3")
    response = register(client, event)
    assert response.status_code == 409
    assert "full" in response.json()["detail"]
    assert listed(client)[0]["places_left"] == 0
    assert len(rows_for(db, event)) == 2


def test_tc4608_registering_twice_is_refused(client, db, users):
    """TC-4608: a second try is a 409, and takes no second place."""
    event = make_event(db, users, attendance=5)
    login(client, "att_1")
    assert register(client, event).status_code == 201
    response = register(client, event)
    assert response.status_code == 409
    assert "already registered" in response.json()["detail"]
    assert len(rows_for(db, event)) == 1
    assert listed(client)[0]["places_left"] == 4


def test_tc4609_a_full_event_tells_a_registered_attendee_they_are_registered(client, db, users):
    """TC-4609: someone already holding a place on a full event hears that, not that it is full."""
    event = make_event(db, users, attendance=1)
    login(client, "att_1")
    assert register(client, event).status_code == 201
    assert "already registered" in register(client, event).json()["detail"]


def test_tc4610_the_database_refuses_a_duplicate_and_it_becomes_a_409(client, db, users, monkeypatch):
    """TC-4610: the race: both requests pass the check, the unique constraint stops the second, and the user gets a 409."""
    from backend import registrations

    event = make_event(db, users, attendance=5)
    add_registration(db, event, users["att_1"])
    monkeypatch.setattr(registrations, "_find_registration", lambda *args: None)
    login(client, "att_1")
    response = register(client, event)
    assert response.status_code == 409
    assert "already registered" in response.json()["detail"]
    assert len(rows_for(db, event)) == 1


def test_tc4611_the_setting_row_is_locked_while_counting(client, db, users, monkeypatch):
    """TC-4611: registering asks for a lock on the setting row. SQLite ignores the lock itself, so this checks the request."""
    from sqlalchemy.orm import Query

    seen = []
    real = Query.with_for_update

    def spy(self, *args, **kwargs):
        seen.append(self.column_descriptions[0]["entity"])
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Query, "with_for_update", spy)
    event = make_event(db, users, attendance=5)
    login(client, "att_1")
    assert register(client, event).status_code == 201
    assert seen == [EventRegistrationSetting]


@pytest.mark.parametrize("status", ["Draft", PLANNING, APPROVED, "Safety Review", CANCELLED, COMPLETED])
def test_tc4612_setting_on_but_not_confirmed_refuses(client, db, users, status):
    """TC-4612: the setting can be on early, but registering waits for Confirmed. Same answer as an unknown event, so nothing is revealed."""
    event = make_event(db, users, status=status)
    login(client, "att_1")
    response = register(client, event)
    assert response.status_code == 404
    assert response.json()["detail"] == client.post("/registrations/events/99999").json()["detail"]
    assert "open for registration" in response.json()["detail"]
    assert rows_for(db, event) == []


def test_tc4613_refused_again_when_the_event_stops_being_confirmed(client, db, users):
    """TC-4613: open, then back to Planning: refused, and open again once Confirmed again."""
    event = make_event(db, users)
    login(client, "att_1")
    assert register(client, event).status_code == 201
    event.status = PLANNING
    db.commit()
    login(client, "att_2")
    assert register(client, event).status_code == 404
    assert listed(client) == []
    event.status = CONFIRMED
    db.commit()
    assert register(client, event).status_code == 201


@pytest.mark.parametrize("enabled", [False, None])
def test_tc4614_setting_off_refuses(client, db, users, enabled):
    """TC-4614: a Confirmed event with registration off, or never set, refuses."""
    event = make_event(db, users, enabled=enabled)
    login(client, "att_1")
    assert register(client, event).status_code == 404


@pytest.mark.parametrize("username", ["org_a", "coord_a", "lead", "venue", "tech"])
def test_tc4615_only_attendees_register(client, db, users, username):
    """TC-4615: other roles get 403 and no row is written."""
    event = make_event(db, users)
    login(client, username)
    assert register(client, event).status_code == 403
    assert rows_for(db, event) == []


def test_tc4616_a_list_shows_my_status(client, db, users):
    """TC-4616: my status in the open list is mine, not someone else's."""
    event = make_event(db, users, attendance=5)
    add_registration(db, event, users["att_1"])
    login(client, "att_1")
    assert listed(client)[0]["my_status"] == "registered"
    login(client, "att_2")
    assert listed(client)[0]["my_status"] is None
    assert listed(client)[0]["places_left"] == 4
