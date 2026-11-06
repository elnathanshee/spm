"""Sprint 5 equipment: SCRUM-41, 42, 43, 44, 40 and the equipment half of SCRUM-65.

The expected numbers come from the PO's formula: available = total - overlapping
active reservations - overlapping unavailable units, floored at 0.
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
from backend.models import Event, EquipmentReservation, LoginSession, User, Venue, VenueBooking
from backend.security import hash_password

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

MAX_QUANTITY = 2147483647
# Hashing is slow on purpose, so every account in this file shares one hash.
TEST_HASH = hash_password("test-only")


@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db):
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
def users(db):
    """One account per role. Session token for each: tok_<username>."""
    specs = [
        ("tech", "technical support"),
        ("tech2", "technical_support"),
        ("coord", "coordinator"),
        ("coord_other", "coordinator"),
        ("lead", "lead"),
        ("org", "organiser"),
        ("venue", "venue_staff"),
        ("attendee", "attendee"),
    ]
    created = {}
    for username, role in specs:
        user = User(username=username, password_hash=TEST_HASH, role=role)
        db.add(user)
        db.commit()
        db.add(LoginSession(user_id=user.id, token=f"tok_{username}", valid=True))
        db.commit()
        created[username] = user
    return created


def login(client, username):
    client.cookies.clear()
    if username:
        client.cookies.set("connectsphere_session", f"tok_{username}")


def add_item(client, username="tech", **overrides):
    login(client, username)
    body = {"equipment_type": "Audio", "name": "Wireless microphone", "total_quantity": 10, "notes": "Spare batteries"}
    body.update(overrides)
    return client.post("/equipment/items", json=body)


NOT_TECH = ["coord", "lead", "org", "venue", "attendee"]


# ---------------------------------------------------------------------------
# SCRUM-41: the catalogue
# ---------------------------------------------------------------------------


def test_tc4101_technical_support_adds_an_item_and_sees_it_in_the_catalogue(client, users):
    response = add_item(client)
    assert response.status_code == 201
    item = response.json()["item"]
    assert item["equipment_type"] == "Audio"
    assert item["name"] == "Wireless microphone"
    assert item["total_quantity"] == 10
    assert item["notes"] == "Spare batteries"

    # auth.py accepts both spellings of the role, so both work here.
    login(client, "tech2")
    listed = client.get("/equipment/items")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()["items"]] == [item["id"]]


@pytest.mark.parametrize("username", NOT_TECH)
def test_tc4102_only_technical_support_can_read_or_add_to_the_catalogue(client, users, username):
    assert add_item(client, username).status_code == 403
    login(client, username)
    assert client.get("/equipment/items").status_code == 403


def test_tc4103_signed_out_is_refused(client, users):
    login(client, None)
    assert client.get("/equipment/items").status_code == 401
    assert client.post("/equipment/items", json={}).status_code == 401


@pytest.mark.parametrize("quantity", [0, -1, 1.5, "many", None, MAX_QUANTITY + 1, 10**30])
def test_tc4104_total_quantity_outside_the_limits_is_a_422(client, users, quantity):
    assert add_item(client, total_quantity=quantity).status_code == 422
    login(client, "tech")
    assert client.get("/equipment/items").json()["items"] == []


@pytest.mark.parametrize("quantity", [1, MAX_QUANTITY])
def test_tc4105_total_quantity_at_the_limits_is_accepted(client, users, quantity):
    response = add_item(client, total_quantity=quantity)
    assert response.status_code == 201
    assert response.json()["item"]["total_quantity"] == quantity


@pytest.mark.parametrize(
    "field,value",
    [
        ("equipment_type", ""),
        ("equipment_type", "   "),
        ("equipment_type", "x" * 101),
        ("name", ""),
        ("name", "   "),
        ("name", "x" * 256),
    ],
)
def test_tc4106_type_and_name_must_be_present_and_fit_their_columns(client, users, field, value):
    assert add_item(client, **{field: value}).status_code == 422


def test_tc4107_the_same_type_and_name_cannot_be_added_twice_whatever_the_case(client, users):
    assert add_item(client).status_code == 201
    duplicate = add_item(client, equipment_type=" audio ", name="WIRELESS MICROPHONE")
    assert duplicate.status_code == 409
    assert "already" in duplicate.json()["detail"]
    assert add_item(client, equipment_type="Backup", name="Wireless microphone").status_code == 201


def test_tc4108_notes_are_optional_and_blank_notes_are_not_recorded(client, users):
    assert add_item(client, name="One", notes="   ").json()["item"]["notes"] is None
    body = {"equipment_type": "Audio", "name": "Two", "total_quantity": 1}
    login(client, "tech")
    assert client.post("/equipment/items", json=body).json()["item"]["notes"] is None


# ---------------------------------------------------------------------------
# SCRUM-42: update an item, record units unavailable for a period
# ---------------------------------------------------------------------------

PERIOD = {"units": 2, "start_at": "2099-03-01T01:00:00", "end_at": "2099-03-01T05:00:00", "reason": "Repairs"}


def add_period(client, item_id, username="tech", **overrides):
    login(client, username)
    return client.post(f"/equipment/items/{item_id}/unavailability", json={**PERIOD, **overrides})


def test_tc4201_update_changes_only_the_fields_sent(client, users):
    item = add_item(client).json()["item"]
    response = client.patch(f"/equipment/items/{item['id']}", json={"total_quantity": 7, "notes": "Two are old"})
    assert response.status_code == 200
    updated = response.json()["item"]
    assert (updated["total_quantity"], updated["notes"]) == (7, "Two are old")
    assert (updated["equipment_type"], updated["name"]) == ("Audio", "Wireless microphone")
    assert client.patch(f"/equipment/items/{item['id']}", json={"notes": None}).json()["item"]["notes"] is None
    assert client.get("/equipment/items").json()["items"][0]["total_quantity"] == 7


@pytest.mark.parametrize("username", NOT_TECH)
def test_tc4202_only_technical_support_can_update_an_item(client, users, username):
    item = add_item(client).json()["item"]
    login(client, username)
    assert client.patch(f"/equipment/items/{item['id']}", json={"total_quantity": 1}).status_code == 403
    login(client, "tech")
    assert client.get("/equipment/items").json()["items"][0]["total_quantity"] == 10


def test_tc4203_updating_an_item_that_does_not_exist_is_a_404(client, users):
    login(client, "tech")
    assert client.patch("/equipment/items/999", json={"total_quantity": 1}).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"total_quantity": 0},
        {"total_quantity": MAX_QUANTITY + 1},
        {"total_quantity": None},
        {"name": None},
        {"name": "  "},
        {"equipment_type": None},
        {"equipment_type": "x" * 101},
    ],
)
def test_tc4204_a_bad_update_is_a_422_and_changes_nothing(client, users, body):
    item = add_item(client).json()["item"]
    assert client.patch(f"/equipment/items/{item['id']}", json=body).status_code == 422
    assert client.get("/equipment/items").json()["items"][0] == item


def test_tc4205_an_update_cannot_turn_an_item_into_a_duplicate(client, users):
    first = add_item(client, name="First").json()["item"]
    second = add_item(client, name="Second").json()["item"]
    clash = client.patch(f"/equipment/items/{second['id']}", json={"name": "first"})
    assert clash.status_code == 409
    # Changing only the case of its own name isn't a clash with itself.
    assert client.patch(f"/equipment/items/{first['id']}", json={"name": "FIRST"}).status_code == 200


def test_tc4206_units_unavailable_for_a_period_are_recorded_and_listed(client, users):
    item = add_item(client).json()["item"]
    response = add_period(client, item["id"])
    assert response.status_code == 201
    period = response.json()["period"]
    assert period["units"] == 2
    assert period["start_at"] == "2099-03-01T01:00:00"
    assert period["end_at"] == "2099-03-01T05:00:00"
    assert period["reason"] == "Repairs"
    listed = client.get(f"/equipment/items/{item['id']}/unavailability")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()["periods"]] == [period["id"]]


def test_tc4207_a_time_with_a_zone_is_stored_as_naive_utc_and_the_reason_is_optional(client, users):
    item = add_item(client).json()["item"]
    response = add_period(
        client, item["id"], start_at="2099-03-01T09:00:00+08:00", end_at="2099-03-01T13:00:00+08:00", reason="  "
    )
    assert response.status_code == 201
    period = response.json()["period"]
    assert period["start_at"] == "2099-03-01T01:00:00"
    assert period["end_at"] == "2099-03-01T05:00:00"
    assert period["reason"] is None


@pytest.mark.parametrize("username", NOT_TECH)
def test_tc4208_only_technical_support_can_read_or_change_unavailability(client, users, username):
    item = add_item(client).json()["item"]
    period = add_period(client, item["id"]).json()["period"]
    assert add_period(client, item["id"], username).status_code == 403
    login(client, username)
    assert client.get(f"/equipment/items/{item['id']}/unavailability").status_code == 403
    assert client.delete(f"/equipment/items/{item['id']}/unavailability/{period['id']}").status_code == 403


def test_tc4209_unavailability_for_an_item_that_does_not_exist_is_a_404(client, users):
    assert add_period(client, 999).status_code == 404
    assert client.get("/equipment/items/999/unavailability").status_code == 404


@pytest.mark.parametrize("units", [0, -1, 1.5, None, MAX_QUANTITY + 1])
def test_tc4210_units_outside_the_limits_are_a_422(client, users, units):
    item = add_item(client).json()["item"]
    assert add_period(client, item["id"], units=units).status_code == 422


def test_tc4211_units_can_equal_the_total_but_not_exceed_it(client, users):
    item = add_item(client, total_quantity=3).json()["item"]
    assert add_period(client, item["id"], units=3).status_code == 201
    too_many = add_period(client, item["id"], units=4)
    assert too_many.status_code == 400
    assert "3" in too_many.json()["detail"]


@pytest.mark.parametrize(
    "start_at,end_at",
    [
        ("2099-03-01T05:00:00", "2099-03-01T05:00:00"),
        ("2099-03-01T06:00:00", "2099-03-01T05:00:00"),
        ("not a date", "2099-03-01T05:00:00"),
        ("0001-01-01T00:00:00+08:00", "2099-03-01T05:00:00"),
        ("2099-03-01T05:00:00", "9999-12-31T23:59:59-08:00"),
    ],
)
def test_tc4212_a_period_must_end_after_it_starts_and_be_a_real_time(client, users, start_at, end_at):
    item = add_item(client).json()["item"]
    assert add_period(client, item["id"], start_at=start_at, end_at=end_at).status_code == 422


def test_tc4213_a_period_can_be_removed_but_only_through_its_own_item(client, users):
    first = add_item(client, name="First").json()["item"]
    second = add_item(client, name="Second").json()["item"]
    period = add_period(client, first["id"]).json()["period"]
    assert client.delete(f"/equipment/items/{second['id']}/unavailability/{period['id']}").status_code == 404
    assert client.delete(f"/equipment/items/{first['id']}/unavailability/999").status_code == 404
    assert client.delete(f"/equipment/items/{first['id']}/unavailability/{period['id']}").status_code == 200
    assert client.get(f"/equipment/items/{first['id']}/unavailability").json()["periods"] == []


# ---------------------------------------------------------------------------
# SCRUM-43: availability for an event's date and time
# ---------------------------------------------------------------------------

# 10:00-12:00 UTC at a venue with 30 minutes' setup and 45 minutes' turnaround
# occupies 09:30-12:45 UTC.
BOOKED_START = datetime(2099, 3, 1, 10, 0)
BOOKED_END = datetime(2099, 3, 1, 12, 0)
# 14:00 Singapore wall time is 06:00 UTC, and that Singapore day ends at 16:00 UTC.
PROPOSED_START = datetime(2099, 4, 1, 14, 0)


def make_event(db, users, proposed_start=PROPOSED_START, title="Gala", coordinator="coord", status="Approved"):
    event = Event(
        title=title,
        proposed_start=proposed_start,
        organiser_id=users["org"].id,
        assigned_coordinator_id=users[coordinator].id if coordinator else None,
        status=status,
    )
    db.add(event)
    db.commit()
    return event


def book(db, event, start=BOOKED_START, end=BOOKED_END, booking_status="Confirmed", name="Main Hall"):
    venue = Venue(name=name, location="Level 1", capacity=100, setup_time_mins=30, turnaround_time_mins=45)
    db.add(venue)
    db.commit()
    db.add(VenueBooking(event_id=event.id, venue_id=venue.id, booking_start=start, booking_end=end, status=booking_status))
    db.commit()


def hold_units(db, users, item, event, quantity):
    """A reservation put straight in the table, so these tests only depend on availability."""
    db.add(EquipmentReservation(item_id=item["id"], event_id=event.id, quantity=quantity, created_by_id=users["tech"].id))
    db.commit()


def availability(client, event, username="tech"):
    login(client, username)
    response = client.get(f"/equipment/availability?event_id={event.id}")
    assert response.status_code == 200, response.text
    return response.json()


def test_tc4301_with_nothing_reserved_or_unavailable_everything_is_available(client, db, users):
    item = add_item(client).json()["item"]
    event = make_event(db, users)
    result = availability(client, event)
    assert result["event_id"] == event.id
    row = result["items"][0]
    assert row["item_id"] == item["id"]
    assert row["name"] == "Wireless microphone"
    assert (row["total_quantity"], row["reserved"], row["unavailable"], row["available"]) == (10, 0, 0, 10)


def test_tc4302_available_is_total_minus_reservations_minus_unavailable_units(client, db, users):
    item = add_item(client).json()["item"]
    event = make_event(db, users)
    other = make_event(db, users, title="Other")
    book(db, event)
    book(db, other, name="Side Hall")
    hold_units(db, users, item, other, 3)
    assert add_period(client, item["id"], units=2, start_at="2099-03-01T11:00:00", end_at="2099-03-01T13:00:00").status_code == 201
    row = availability(client, event)["items"][0]
    assert (row["reserved"], row["unavailable"], row["available"]) == (3, 2, 5)


@pytest.mark.parametrize(
    "start_at,end_at,counted",
    [
        ("2099-03-01T09:00:00", "2099-03-01T09:30:00", False),  # ends as setup starts
        ("2099-03-01T09:00:00", "2099-03-01T09:31:00", True),  # one minute into setup
        ("2099-03-01T12:44:00", "2099-03-01T13:00:00", True),  # the last minute of turnaround
        ("2099-03-01T12:45:00", "2099-03-01T13:00:00", False),  # starts as turnaround ends
    ],
)
def test_tc4303_the_window_includes_the_venues_setup_and_turnaround(client, db, users, start_at, end_at, counted):
    item = add_item(client).json()["item"]
    event = make_event(db, users)
    book(db, event)
    assert add_period(client, item["id"], start_at=start_at, end_at=end_at).status_code == 201
    result = availability(client, event)
    assert result["window_start"] == "2099-03-01T09:30:00"
    assert result["window_end"] == "2099-03-01T12:45:00"
    assert result["items"][0]["available"] == (8 if counted else 10)


@pytest.mark.parametrize(
    "start_at,end_at,counted",
    [
        ("2099-04-01T05:00:00", "2099-04-01T06:00:00", False),
        ("2099-04-01T05:00:00", "2099-04-01T06:01:00", True),
        ("2099-04-01T15:59:00", "2099-04-01T17:00:00", True),
        ("2099-04-01T16:00:00", "2099-04-01T17:00:00", False),
    ],
)
def test_tc4304_without_a_booking_the_window_is_the_rest_of_the_singapore_day(client, db, users, start_at, end_at, counted):
    item = add_item(client).json()["item"]
    event = make_event(db, users)
    assert add_period(client, item["id"], start_at=start_at, end_at=end_at).status_code == 201
    result = availability(client, event)
    assert result["window_start"] == "2099-04-01T06:00:00"
    assert result["window_end"] == "2099-04-01T16:00:00"
    assert result["items"][0]["available"] == (8 if counted else 10)


def test_tc4305_a_reservation_counts_only_when_its_events_window_overlaps(client, db, users):
    item = add_item(client).json()["item"]
    event = make_event(db, users)
    book(db, event)
    before = make_event(db, users, title="Before")
    # Its turnaround ends exactly when this event's setup starts, so they don't clash.
    book(db, before, datetime(2099, 3, 1, 7, 0), datetime(2099, 3, 1, 8, 45), name="Side Hall")
    hold_units(db, users, item, before, 4)
    assert availability(client, event)["items"][0]["reserved"] == 0
    overlapping = make_event(db, users, title="Overlapping")
    book(db, overlapping, datetime(2099, 3, 1, 7, 0), datetime(2099, 3, 1, 8, 46), name="Other Hall")
    hold_units(db, users, item, overlapping, 1)
    assert availability(client, event)["items"][0]["reserved"] == 1


def test_tc4306_a_reservation_follows_its_events_bookings_because_it_stores_no_window(client, db, users):
    item = add_item(client).json()["item"]
    event = make_event(db, users)
    book(db, event)
    other = make_event(db, users, title="Other")
    hold_units(db, users, item, other, 4)  # no booking, so on its own day in April
    assert availability(client, event)["items"][0]["reserved"] == 0
    book(db, other, name="Side Hall")  # now it's at the same time
    assert availability(client, event)["items"][0]["reserved"] == 4


def test_tc4307_released_reservations_and_other_items_do_not_count(client, db, users):
    item = add_item(client, name="First").json()["item"]
    second = add_item(client, name="Second").json()["item"]
    event = make_event(db, users)
    hold_units(db, users, item, event, 2)
    db.add(
        EquipmentReservation(
            item_id=item["id"], event_id=event.id, quantity=5, created_by_id=users["tech"].id,
            released_at=datetime(2099, 1, 1), released_by_id=users["tech"].id,
        )
    )
    db.commit()
    rows = {row["name"]: row["available"] for row in availability(client, event)["items"]}
    assert rows == {"First": 8, "Second": 10}
    assert second["id"] != item["id"]


def test_tc4308_available_never_goes_below_zero(client, db, users):
    item = add_item(client).json()["item"]
    event = make_event(db, users)
    hold_units(db, users, item, event, 9)
    assert add_period(client, item["id"], units=5, start_at="2099-04-01T07:00:00", end_at="2099-04-01T08:00:00").status_code == 201
    assert availability(client, event)["items"][0]["available"] == 0


def test_tc4309_periods_and_reservations_are_summed_even_if_they_do_not_overlap_each_other(client, db, users):
    # The PO's formula: deliberately simple, and on the safe side.
    item = add_item(client).json()["item"]
    event = make_event(db, users)
    for start_at, end_at in [("2099-04-01T07:00:00", "2099-04-01T08:00:00"), ("2099-04-01T09:00:00", "2099-04-01T10:00:00")]:
        assert add_period(client, item["id"], units=3, start_at=start_at, end_at=end_at).status_code == 201
    assert availability(client, event)["items"][0]["available"] == 4


def test_tc4310_an_event_with_no_window_is_a_400_with_a_sentence(client, db, users):
    event = make_event(db, users, proposed_start=None)
    login(client, "tech")
    response = client.get(f"/equipment/availability?event_id={event.id}")
    assert response.status_code == 400
    assert "date" in response.json()["detail"]


def test_tc4311_a_reservation_whose_event_lost_its_window_does_not_count(client, db, users):
    item = add_item(client).json()["item"]
    event = make_event(db, users)
    undated = make_event(db, users, proposed_start=None, title="Undated")
    hold_units(db, users, item, undated, 4)
    assert availability(client, event)["items"][0]["reserved"] == 0


def test_tc4312_technical_support_the_assigned_coordinator_and_the_lead_can_read_availability(client, db, users):
    event = make_event(db, users)
    for username in ["tech", "tech2", "coord", "lead"]:
        availability(client, event, username)


@pytest.mark.parametrize("username", ["coord_other", "org", "venue", "attendee"])
def test_tc4313_nobody_else_can_read_availability(client, db, users, username):
    event = make_event(db, users)
    login(client, username)
    assert client.get(f"/equipment/availability?event_id={event.id}").status_code == 403


def test_tc4314_an_unknown_event_is_a_404_but_only_for_those_who_could_read_it(client, db, users):
    login(client, "tech")
    assert client.get("/equipment/availability?event_id=999").status_code == 404
    assert client.get("/equipment/availability").status_code == 422
    login(client, "org")
    assert client.get("/equipment/availability?event_id=999").status_code == 403
    login(client, None)
    assert client.get("/equipment/availability?event_id=999").status_code == 401
