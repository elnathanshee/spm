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

from backend import event_hooks, registrations
from backend.database import Base, get_db
from backend.main import app
from backend.models import (
    Event,
    EventRegistration,
    EventRegistrationSetting,
    EventStatusHistory,
    LoginSession,
    Notification,
    User,
    Venue,
    VenueBooking,
)
from backend.notifications import NotEntitled, can_see_event, notify
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


# --- SCRUM-47: view my registration status and withdraw -----------------------


def withdraw(client, event):
    return client.post(f"/registrations/events/{event.id}/withdraw")


def mine(client):
    response = client.get("/registrations/mine")
    assert response.status_code == 200
    return response.json()


def test_tc4701_my_registrations_show_my_status_and_safe_fields(client, db, users):
    """TC-4701: an Attendee sees their own registrations with the same whitelist, plus when they registered and withdrew."""
    event = make_event(db, users, venues=[make_venue(db)])
    make_event(db, users, title="Someone else's")
    login(client, "att_1")
    assert register(client, event).status_code == 201
    rows = mine(client)
    assert [row["event_id"] for row in rows] == [event.id]
    assert set(rows[0]) == ATTENDEE_KEYS | {"registered_at", "withdrawn_at"}
    assert rows[0]["my_status"] == "registered"
    assert rows[0]["registered_at"]
    assert rows[0]["withdrawn_at"] is None
    assert_no_internal_detail(rows)


def test_tc4702_only_my_own_registrations_are_listed(client, db, users):
    """TC-4702: someone else's registration is never in my list."""
    event = make_event(db, users)
    add_registration(db, event, users["att_2"])
    login(client, "att_1")
    assert mine(client) == []


def test_tc4703_withdrawing_frees_a_place_and_keeps_the_row(client, db, users):
    """TC-4703: withdrawing frees a place at once, and the row stays as history with the time."""
    event = make_event(db, users, attendance=1)
    login(client, "att_1")
    assert register(client, event).status_code == 201
    response = withdraw(client, event)
    assert response.status_code == 200
    assert response.json()["my_status"] == "withdrawn"
    assert response.json()["places_left"] == 1
    assert response.json()["message"]
    rows = rows_for(db, event)
    assert [r.status for r in rows] == ["withdrawn"]
    assert rows[0].withdrawn_at is not None
    assert mine(client)[0]["my_status"] == "withdrawn"
    assert mine(client)[0]["withdrawn_at"]
    login(client, "att_2")
    assert register(client, event).status_code == 201


def test_tc4704_registering_again_reuses_the_row(client, db, users):
    """TC-4704: withdraw then register again: still one row, registered, with the withdrawal time cleared."""
    event = make_event(db, users, attendance=5)
    login(client, "att_1")
    first_id = register(client, event).status_code and rows_for(db, event)[0].id
    assert withdraw(client, event).status_code == 200
    response = register(client, event)
    assert response.status_code == 201
    assert response.json()["places_left"] == 4
    rows = rows_for(db, event)
    assert [(r.id, r.status, r.withdrawn_at) for r in rows] == [(first_id, "registered", None)]


def test_tc4705_registering_again_needs_a_place(client, db, users):
    """TC-4705: a withdrawn Attendee whose place was taken gets the same full message and stays withdrawn."""
    event = make_event(db, users, attendance=1)
    add_registration(db, event, users["att_1"], status="withdrawn")
    add_registration(db, event, users["att_2"])
    login(client, "att_1")
    response = register(client, event)
    assert response.status_code == 409
    assert "full" in response.json()["detail"]
    assert mine(client)[0]["my_status"] == "withdrawn"


def test_tc4706_withdrawing_twice_or_without_registering_is_refused(client, db, users):
    """TC-4706: never registered is a 404; already withdrawn is a 409. Both say so in a sentence."""
    event = make_event(db, users)
    login(client, "att_1")
    response = withdraw(client, event)
    assert response.status_code == 404
    assert "aren't registered" in response.json()["detail"]
    assert register(client, event).status_code == 201
    assert withdraw(client, event).status_code == 200
    response = withdraw(client, event)
    assert response.status_code == 409
    assert "already withdrawn" in response.json()["detail"]


def test_tc4707_withdrawing_is_allowed_when_registration_has_closed(client, db, users):
    """TC-4707: an Attendee can still give up a place when the setting is off or the event went back to Planning."""
    event = make_event(db, users)
    add_registration(db, event, users["att_1"])
    add_registration(db, event, users["att_2"])
    event.registration_setting.enabled = False
    db.commit()
    login(client, "att_1")
    assert mine(client)[0]["event_state"] == "closed"
    assert withdraw(client, event).status_code == 200
    event.status = PLANNING
    db.commit()
    login(client, "att_2")
    assert withdraw(client, event).status_code == 200


@pytest.mark.parametrize("status,state", [(CANCELLED, "cancelled"), (COMPLETED, "completed")])
def test_tc4708_a_finished_event_keeps_the_registration_as_history(client, db, users, status, state):
    """TC-4708: on a cancelled or completed event the row stays registered, shows the state, and can't be withdrawn."""
    event = make_event(db, users, status=status)
    add_registration(db, event, users["att_1"])
    login(client, "att_1")
    rows = mine(client)
    assert (rows[0]["my_status"], rows[0]["event_state"]) == ("registered", state)
    response = withdraw(client, event)
    assert response.status_code == 409
    assert state in response.json()["detail"]
    assert [r.status for r in rows_for(db, event)] == ["registered"]


def test_tc4709_a_closed_event_never_shows_its_internal_status(client, db, users):
    """TC-4709: an event back in Planning reads closed. The internal status and fields stay hidden."""
    event = make_event(db, users, status=PLANNING)
    add_registration(db, event, users["att_1"])
    login(client, "att_1")
    rows = mine(client)
    assert rows[0]["event_state"] == "closed"
    assert_no_internal_detail(rows)
    assert PLANNING not in str(rows)


def test_tc4710_newest_registration_first(client, db, users):
    """TC-4710: my list shows the latest registration first."""
    older = make_event(db, users, title="Older")
    newer = make_event(db, users, title="Newer")
    add_registration(db, older, users["att_1"]).created_at = datetime(2099, 1, 1)
    add_registration(db, newer, users["att_1"]).created_at = datetime(2099, 1, 2)
    db.commit()
    login(client, "att_1")
    assert [row["title"] for row in mine(client)] == ["Newer", "Older"]


@pytest.mark.parametrize("username", ["org_a", "coord_a", "lead", "venue", "tech"])
def test_tc4711_my_registrations_are_for_attendees_only(client, db, users, username):
    """TC-4711: other roles get 403 from both the list and withdraw."""
    event = make_event(db, users)
    add_registration(db, event, users["att_1"])
    login(client, username)
    assert client.get("/registrations/mine").status_code == 403
    assert withdraw(client, event).status_code == 403
    assert [r.status for r in rows_for(db, event)] == ["registered"]


# --- SCRUM-48: the Organiser and assigned Coordinator see registrations ------


def test_tc4801_organiser_sees_who_registered_and_the_counts(client, db, users):
    """TC-4801: the event's Organiser sees each Attendee, their status and the counts, with withdrawn rows kept as history."""
    event = make_event(db, users, attendance=3, venues=[make_venue(db, capacity=50)])
    add_registration(db, event, users["att_1"])
    add_registration(db, event, users["att_2"], status="withdrawn")
    login(client, "org_a")
    response = client.get(f"/events/{event.id}/registrations")
    assert response.status_code == 200
    body = response.json()
    assert (body["capacity"], body["registered_count"], body["places_left"]) == (3, 1, 2)
    assert [(row["attendee"], row["status"]) for row in body["registrations"]] == [("att_1", "registered"), ("att_2", "withdrawn")]
    assert body["registrations"][1]["withdrawn_at"]
    assert body["registrations"][0]["registered_at"]
    assert "password" not in str(body)


@pytest.mark.parametrize("username", ["coord_a", "lead"])
def test_tc4802_assigned_coordinator_and_lead_see_registrations(client, db, users, username):
    """TC-4802: the assigned Coordinator and the Lead get the same list."""
    event = make_event(db, users)
    add_registration(db, event, users["att_1"])
    login(client, username)
    response = client.get(f"/events/{event.id}/registrations")
    assert response.status_code == 200
    assert [row["attendee"] for row in response.json()["registrations"]] == ["att_1"]


def test_tc4803_the_list_is_refused_to_everyone_else(client, db, users):
    """TC-4803: another Organiser gets 404 (the event stays concealed); another Coordinator, Venue Staff, Technical Support and Attendees get 403."""
    event = make_event(db, users)
    add_registration(db, event, users["att_1"])
    login(client, "org_b")
    assert client.get(f"/events/{event.id}/registrations").status_code == 404
    for username in ["coord_b", "venue", "tech", "att_1", "att_2"]:
        login(client, username)
        assert client.get(f"/events/{event.id}/registrations").status_code == 403, username


def test_tc4804_an_unknown_event_is_404(client, db, users):
    """TC-4804: a missing event answers 404."""
    login(client, "lead")
    assert client.get("/events/99999/registrations").status_code == 404


def test_tc4805_an_empty_list_works_before_anything_is_set(client, db, users):
    """TC-4805: an event with no setting and no registrations gives an empty list, not an error."""
    event = make_event(db, users, status=PLANNING, enabled=None, attendance=None)
    login(client, "org_a")
    body = client.get(f"/events/{event.id}/registrations").json()
    assert body["registrations"] == []
    assert (body["capacity"], body["registered_count"], body["places_left"]) == (None, 0, 0)


def test_tc4806_people_still_holding_places_come_first(client, db, users):
    """TC-4806: registered rows come before withdrawn ones, so a long history doesn't bury the live list."""
    event = make_event(db, users)
    add_registration(db, event, users["att_1"], status="withdrawn")
    add_registration(db, event, users["att_2"])
    add_registration(db, event, users["att_3"], status="withdrawn")
    login(client, "coord_a")
    body = client.get(f"/events/{event.id}/registrations").json()
    assert [row["status"] for row in body["registrations"]] == ["registered", "withdrawn", "withdrawn"]


# --- SCRUM-52: registered Attendees are notified when the event changes -------


def notices_for(db, user):
    db.expire_all()
    return db.query(Notification).filter(Notification.user_id == user.id).all()


def assert_message_is_for_attendees(notice):
    for marker in ["Internal", "Secret reason", "equipment", "Coordinator", "Organiser", "expected_attendance", "proposed_start"]:
        assert marker not in notice.message, notice.message


def test_tc5201_cancelling_tells_everyone_registered(db, users):
    """TC-5201: each registered Attendee gets one notice naming the event. The reason may be internal, so it stays out."""
    event = make_event(db, users)
    add_registration(db, event, users["att_1"])
    add_registration(db, event, users["att_2"])
    event_hooks.event_cancelled(db, event, users["coord_a"], "Secret reason")
    db.commit()
    for name in ["att_1", "att_2"]:
        found = notices_for(db, users[name])
        assert [n.kind for n in found] == [registrations.KIND_EVENT_CANCELLED]
        assert found[0].event_id == event.id
        assert found[0].booking_id is None
        assert "Open Day" in found[0].message
        assert "cancelled" in found[0].message
        assert_message_is_for_attendees(found[0])


def test_tc5202_an_important_change_tells_everyone_registered(db, users):
    """TC-5202: an approved important change sends one plain notice. Changed field names and the reason stay out."""
    event = make_event(db, users)
    add_registration(db, event, users["att_1"])
    event_hooks.important_change(db, event, users["coord_a"], ["proposed_start", "expected_attendance", "equipment_requirements"], "Secret reason")
    db.commit()
    found = notices_for(db, users["att_1"])
    assert [n.kind for n in found] == [registrations.KIND_EVENT_CHANGED]
    assert "Open Day" in found[0].message
    assert_message_is_for_attendees(found[0])


def test_tc5203_a_routine_edit_through_the_api_tells_everyone_registered(client, db, users):
    """TC-5203: the real path: the Organiser edits the title through PATCH /events/{id}, and the Attendee is told the new title."""
    event = make_event(db, users)
    add_registration(db, event, users["att_1"])
    login(client, "org_a")
    assert client.patch(f"/events/{event.id}", json={"title": "Renamed Day"}).status_code == 200
    found = notices_for(db, users["att_1"])
    assert [n.kind for n in found] == [registrations.KIND_DETAILS_CHANGED]
    assert "Renamed Day" in found[0].message
    assert_message_is_for_attendees(found[0])
    login(client, "att_1")
    assert [n["kind"] for n in client.get("/notifications").json()] == [registrations.KIND_DETAILS_CHANGED]


@pytest.mark.parametrize("field", ["purpose", "accessibility_needs", "registration_needs"])
def test_tc5204_an_edit_to_an_internal_field_sends_nothing(client, db, users, field):
    """TC-5204: an Attendee can't see these fields, so a notice would point at nothing that changed."""
    event = make_event(db, users)
    add_registration(db, event, users["att_1"])
    login(client, "org_a")
    assert client.patch(f"/events/{event.id}", json={field: "New internal text"}).status_code == 200
    assert notices_for(db, users["att_1"]) == []


def test_tc5205_nobody_withdrawn_or_never_registered_is_told(db, users):
    """TC-5205: all three hooks skip someone who withdrew, and anyone with no registration."""
    event = make_event(db, users)
    add_registration(db, event, users["att_1"], status="withdrawn")
    event_hooks.event_cancelled(db, event, users["coord_a"], "Reason")
    event_hooks.important_change(db, event, users["coord_a"], ["proposed_start"], "Reason")
    event_hooks.details_changed(db, event, users["coord_a"], ["title"])
    db.commit()
    assert notices_for(db, users["att_1"]) == []
    assert notices_for(db, users["att_2"]) == []


def test_tc5206_another_events_attendees_are_not_told(db, users):
    """TC-5206: only this event's registrations count."""
    event, other = make_event(db, users), make_event(db, users, title="Other")
    add_registration(db, other, users["att_1"])
    event_hooks.event_cancelled(db, event, users["coord_a"], "Reason")
    db.commit()
    assert notices_for(db, users["att_1"]) == []


def test_tc5207_the_change_and_its_notice_are_saved_together(db, users):
    """TC-5207: handlers don't commit. If the caller rolls back, no notice remains."""
    event = make_event(db, users)
    add_registration(db, event, users["att_1"])
    event_hooks.event_cancelled(db, event, users["coord_a"], "Reason")
    db.rollback()
    assert notices_for(db, users["att_1"]) == []


def test_tc5208_several_notices_about_one_event_are_all_kept(db, users):
    """TC-5208: event-level notices leave booking_id empty, so the booking uniqueness rule doesn't stop a second notice."""
    event = make_event(db, users)
    add_registration(db, event, users["att_1"])
    event_hooks.details_changed(db, event, users["coord_a"], ["title"])
    db.commit()
    event_hooks.details_changed(db, event, users["coord_a"], ["description"])
    db.commit()
    assert len(notices_for(db, users["att_1"])) == 2


def test_tc5209_entitlement_follows_the_registered_row(db, users):
    """TC-5209: can_see_event is true only while an Attendee holds a registered row, and notify() refuses otherwise."""
    event = make_event(db, users)
    assert can_see_event(users["att_1"], event) is False
    with pytest.raises(NotEntitled):
        notify(db, users["att_1"], event, "test", "Hello")
    row = add_registration(db, event, users["att_1"])
    assert can_see_event(users["att_1"], event) is True
    assert can_see_event(users["att_2"], event) is False
    row.status = "withdrawn"
    db.commit()
    assert can_see_event(users["att_1"], event) is False


def test_tc5210_a_cancelled_events_notice_stays_readable(client, db, users):
    """TC-5210: the row outlives the event being cancelled, so GET /notifications still shows the notice."""
    event = make_event(db, users)
    add_registration(db, event, users["att_1"])
    event_hooks.event_cancelled(db, event, users["coord_a"], "Reason")
    event.status = CANCELLED
    db.commit()
    login(client, "att_1")
    assert [n["kind"] for n in client.get("/notifications").json()] == [registrations.KIND_EVENT_CANCELLED]


def test_tc5211_a_registered_row_opens_no_event_details(client, db, users):
    """TC-5211: can_see_event is for notifications. A registered Attendee still gets the short view of GET /events/{id}, and 404 once it's not Confirmed."""
    event = make_event(db, users)
    add_registration(db, event, users["att_1"])
    login(client, "att_1")
    detail = client.get(f"/events/{event.id}")
    assert set(detail.json()) == {"name", "description", "date", "time", "venue_name"}
    assert client.get(f"/events/{event.id}/bookings").status_code == 403
    assert client.get(f"/events/{event.id}/registrations").status_code == 403
    event.status = PLANNING
    db.commit()
    assert client.get(f"/events/{event.id}").status_code == 404


def test_tc5212_someone_no_longer_an_attendee_does_not_block_the_change(db, users):
    """TC-5212: a registered user whose role has since changed isn't told, and the cancellation isn't stopped by an error."""
    event = make_event(db, users)
    add_registration(db, event, users["att_1"])
    add_registration(db, event, users["att_2"])
    users["att_1"].role = "venue_staff"
    db.commit()
    event_hooks.event_cancelled(db, event, users["coord_a"], "Reason")
    db.commit()
    assert notices_for(db, users["att_1"]) == []
    assert len(notices_for(db, users["att_2"])) == 1


def test_tc5213_a_venue_approved_through_the_api_tells_everyone_registered(client, db, users):
    """TC-5213: the real path: Venue Staff approve a booking, so the venue and the places change, and each Attendee holding a place gets one plain notice."""
    event = make_event(db, users, attendance=100)
    booking = book(db, event, make_venue(db, capacity=5), status="Pending")
    add_registration(db, event, users["att_1"])
    add_registration(db, event, users["att_2"], status="withdrawn")
    login(client, "venue")
    assert client.post(f"/venues/bookings/{booking.id}/approve").status_code == 200
    notices = notices_for(db, users["att_1"])
    assert [n.kind for n in notices] == [registrations.KIND_VENUE_CHANGED]
    assert "Open Day" in notices[0].message
    assert_message_is_for_attendees(notices[0])
    assert notices_for(db, users["att_2"]) == []
    login(client, "att_1")
    row = mine(client)[0]
    assert row["venues"] == [{"name": "Main Hall", "location": "Block A"}]
    assert row["places_left"] == 4


def test_tc5214_a_rejected_booking_tells_nobody_registered(client, db, users):
    """TC-5214: a Pending booking was never shown to an Attendee, so rejecting it changes nothing they can see."""
    event = make_event(db, users, attendance=100)
    booking = book(db, event, make_venue(db, capacity=5), status="Pending")
    add_registration(db, event, users["att_1"])
    login(client, "venue")
    response = client.post(f"/venues/bookings/{booking.id}/reject", json={"reason": "The hall is closed that week."})
    assert response.status_code == 200
    assert notices_for(db, users["att_1"]) == []


# --- SCRUM-65 (registration half): registration is switched on per event ------


def put_setting(client, event, enabled=True):
    return client.put(f"/events/{event.id}/registration-settings", json={"enabled": enabled})


@pytest.mark.parametrize("username", ["org_a", "coord_a"])
def test_tc6550_organiser_or_coordinator_switch_it_on_and_off(client, db, users, username):
    """TC-6550: the first PUT creates the row and records who and when; the second switches it off in the same row."""
    event = make_event(db, users, enabled=None)
    login(client, username)
    response = put_setting(client, event, True)
    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert response.json()["open"] is True
    assert response.json()["updated_by"] == username
    assert response.json()["updated_at"]
    assert response.json()["message"]
    assert put_setting(client, event, False).json()["enabled"] is False
    db.expire_all()
    rows = db.query(EventRegistrationSetting).filter(EventRegistrationSetting.event_id == event.id).all()
    assert [(r.enabled, r.updated_by_id) for r in rows] == [(False, users[username].id)]


def test_tc6551_a_new_event_starts_with_registration_off(client, db, users):
    """TC-6551: with no row, GET answers off rather than an error, and writes nothing."""
    event = make_event(db, users, enabled=None)
    login(client, "org_a")
    body = client.get(f"/events/{event.id}/registration-settings").json()
    assert (body["enabled"], body["open"], body["updated_by"], body["updated_at"]) == (False, False, None, None)
    db.expire_all()
    assert db.query(EventRegistrationSetting).count() == 0


def test_tc6552_switching_on_early_waits_for_confirmed(client, db, users):
    """TC-6552: it can be switched on before Confirmed. Open stays false, Attendees see nothing, and it opens at Confirmed."""
    event = make_event(db, users, status=PLANNING, enabled=None)
    login(client, "org_a")
    body = put_setting(client, event, True).json()
    assert (body["enabled"], body["open"]) == (True, False)
    assert "Confirmed" in body["message"]
    login(client, "att_1")
    assert listed(client) == []
    assert register(client, event).status_code == 404
    event.status = CONFIRMED
    db.commit()
    assert [row["event_id"] for row in listed(client)] == [event.id]
    assert register(client, event).status_code == 201


def test_tc6553_switching_off_closes_and_keeps_registrations(client, db, users):
    """TC-6553: switching off stops new registrations, keeps existing places, and they can still be seen and withdrawn."""
    event = make_event(db, users)
    add_registration(db, event, users["att_1"])
    login(client, "coord_a")
    assert put_setting(client, event, False).json()["open"] is False
    login(client, "att_2")
    assert listed(client) == []
    assert register(client, event).status_code == 404
    login(client, "att_1")
    assert mine(client)[0]["my_status"] == "registered"
    login(client, "org_a")
    assert client.get(f"/events/{event.id}/registrations").json()["registered_count"] == 1


def test_tc6554_the_lead_can_read_but_not_switch(client, db, users):
    """TC-6554: the Lead sees the setting and cannot change it."""
    event = make_event(db, users)
    login(client, "lead")
    assert client.get(f"/events/{event.id}/registration-settings").json()["enabled"] is True
    assert put_setting(client, event, False).status_code == 403
    db.expire_all()
    assert db.query(EventRegistrationSetting).one().enabled is True


def test_tc6555_everyone_else_is_refused(client, db, users):
    """TC-6555: another Organiser gets 404; another Coordinator, Venue Staff, Technical Support and Attendees get 403, for read and write."""
    event = make_event(db, users, enabled=None)
    login(client, "org_b")
    assert client.get(f"/events/{event.id}/registration-settings").status_code == 404
    assert put_setting(client, event).status_code == 404
    for username in ["coord_b", "venue", "tech", "att_1"]:
        login(client, username)
        assert client.get(f"/events/{event.id}/registration-settings").status_code == 403, username
        assert put_setting(client, event).status_code == 403, username
    db.expire_all()
    assert db.query(EventRegistrationSetting).count() == 0


@pytest.mark.parametrize("body", [{}, {"enabled": "maybe"}, {"enabled": None}])
def test_tc6556_a_bad_body_is_refused(client, db, users, body):
    """TC-6556: enabled must be true or false, and nothing is written otherwise."""
    event = make_event(db, users, enabled=None)
    login(client, "org_a")
    assert client.put(f"/events/{event.id}/registration-settings", json=body).status_code == 422
    db.expire_all()
    assert db.query(EventRegistrationSetting).count() == 0


def test_tc6557_two_first_saves_at_once_give_a_409(client, db, users, monkeypatch):
    """TC-6557: both requests find no row; the unique constraint stops the second, and the user is asked to try again."""
    event = make_event(db, users)
    monkeypatch.setattr(registrations, "_find_setting", lambda *args: None)
    login(client, "org_a")
    response = put_setting(client, event, False)
    assert response.status_code == 409
    assert "try again" in response.json()["detail"]
    db.expire_all()
    assert db.query(EventRegistrationSetting).one().enabled is True


def test_tc6558_the_new_tables_are_new_and_events_is_unchanged(db, users):
    """TC-6558: production only creates missing tables, so this story adds tables and adds no column to events."""
    assert {"event_registration_settings", "event_registrations"} <= set(Base.metadata.tables)
    assert "registration_enabled" not in Event.__table__.columns
    assert "uq_event_registration_attendee" in {c.name for c in EventRegistration.__table__.constraints}
