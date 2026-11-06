"""SCRUM-84: role-based access, the tests PR #18 ("SCRUM 17") did not include.

Jira can't be read from here, so the criteria come from the PR #18 description:
  C1  Role-specific workspaces, navigation and function visibility.
  C2  Role-based redirection after login, and access-denied handling.
  C3  Backend authorisation stops unauthorised API access.
  C4  Organiser and Coordinator screens identify each event request.
  C5  Internal planning features are protected from external roles.

test_scrum17_rbac.py already covers C2 and four session-protected paths. It uses
a Submitted event throughout. These tests add every other route, and rows that
really exist in later statuses, so a refusal can't be a plain "not found".
"""

import os
import sys
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.database import Base, get_db
from backend.main import app
from backend.models import EquipmentRequest, Event, EventStatusHistory, User, Venue, VenueBooking
from backend.security import hash_password
from backend.status import APPROVED

SECRET_TITLE = "Confidential planning event"
EXTERNAL_ROLES = ["venue1", "technical1", "attendee1"]
NON_COORDINATORS = ["organiser1", "organiser2", "lead1"] + EXTERNAL_ROLES

# A fixed list on purpose: other streams add routes that may be public by design.
PROTECTED_ROUTES = [
    ("GET", "/auth/me"),
    ("GET", "/coordinator/events"),
    ("GET", "/coordinator/events/all-data"),
    ("GET", "/coordinator/events/data"),
    ("GET", "/equipment-requests"),
    ("GET", "/events"),
    ("GET", "/events/1"),
    ("GET", "/events/1/bookings"),
    ("GET", "/events/1/equipment-requests"),
    ("GET", "/events/1/venue-suitability"),
    ("GET", "/lead/coordinators"),
    ("GET", "/lead/events"),
    ("GET", "/notifications"),
    ("GET", "/venues"),
    ("GET", "/venues/1"),
    ("GET", "/venues/1/bookings"),
    ("GET", "/venues/1/calendar"),
    ("GET", "/venues/bookings/1"),
    ("GET", "/venues/search"),
    ("PATCH", "/events/1"),
    ("PATCH", "/venues/1"),
    ("POST", "/events"),
    ("POST", "/events/1/amend"),
    ("POST", "/events/1/approve"),
    ("POST", "/events/1/assign"),
    ("POST", "/events/1/equipment-requests"),
    ("POST", "/events/1/reassign"),
    ("POST", "/events/1/reject"),
    ("POST", "/events/1/return"),
    ("POST", "/events/1/review"),
    ("POST", "/events/1/submit"),
    ("POST", "/notifications/1/read"),
    ("POST", "/venues"),
    ("POST", "/venues/1/bookings"),
    ("POST", "/venues/1/retire"),
    ("POST", "/venues/bookings/1/approve"),
    ("POST", "/venues/bookings/1/reject"),
    ("PUT", "/venues/bookings/1/hold"),
    ("PUT", "/venues/bookings/1/hold/warning"),
]

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


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
def world(db_session):
    """Every role, plus an Approved event with a booking and an equipment line."""
    roles = {
        "organiser1": "organiser",
        "organiser2": "organiser",
        "coord1": "coordinator",
        "coord2": "coordinator",
        "lead1": "lead",
        "venue1": "venue_staff",
        "technical1": "technical support",
        "attendee1": "attendee",
    }
    users = {}
    for username, role in roles.items():
        user = User(
            username=username,
            password_hash=hash_password("pass123"),
            role=role,
            is_active_coordinator=role == "coordinator",
        )
        db_session.add(user)
        users[username] = user
    db_session.flush()
    event = Event(
        title=SECRET_TITLE,
        organiser_id=users["organiser1"].id,
        assigned_coordinator_id=users["coord1"].id,
        status=APPROVED,
        proposed_start=datetime(2099, 6, 1, 10, 0),
        expected_attendance=50,
        submitted_at=datetime(2099, 1, 1),
    )
    venue = Venue(name="Main Hall", location="Level 1", capacity=100)
    db_session.add_all([event, venue])
    db_session.flush()
    booking = VenueBooking(
        event_id=event.id,
        venue_id=venue.id,
        booking_start=datetime(2099, 6, 1, 2, 0),
        booking_end=datetime(2099, 6, 1, 4, 0),
        status="Pending",
        requester_id=users["coord1"].id,
    )
    equipment = EquipmentRequest(event_id=event.id, equipment_type="Projector", quantity=1, requested_by_id=users["coord1"].id)
    db_session.add_all([booking, equipment])
    db_session.commit()
    return {
        "event": event.id,
        "venue": venue.id,
        "booking": booking.id,
        "coord2": users["coord2"].id,
    }


def login(client, username):
    assert client.post("/auth/login", json={"username": username, "password": "pass123"}).status_code == 200


def event_row(db_session, event_id):
    db_session.expire_all()
    return db_session.get(Event, event_id)


# ---------- C3: no session, no access ----------

@pytest.mark.parametrize(("method", "path"), PROTECTED_ROUTES)
def test_tc8401_every_route_needs_a_session(client, method, path):
    response = client.request(method, path, json=None if method == "GET" else {})

    assert response.status_code == 401


# ---------- C3 + C5: real rows in a later status ----------

@pytest.mark.parametrize("username", EXTERNAL_ROLES)
@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("approve", None),
        ("reject", {"reason": "No"}),
        ("return", {"reason": "More detail"}),
        ("submit", None),
    ],
)
def test_tc8402_external_roles_cannot_decide_a_later_event(client, db_session, world, username, path, body):
    login(client, username)

    response = client.post(f"/events/{world['event']}/{path}", json=body)

    assert response.status_code == 403
    assert event_row(db_session, world["event"]).status == APPROVED
    assert db_session.query(EventStatusHistory).count() == 0


@pytest.mark.parametrize("username", EXTERNAL_ROLES)
def test_tc8403_external_roles_cannot_edit_or_reassign_a_later_event(client, db_session, world, username):
    login(client, username)
    event_id = world["event"]

    assert client.patch(f"/events/{event_id}", json={"title": "Changed"}).status_code == 403
    assert client.post(f"/events/{event_id}/amend", json={"title": "Changed"}).status_code == 403
    assert client.post(f"/events/{event_id}/reassign", json={"coordinator_id": world["coord2"]}).status_code == 403
    assert client.post(f"/events/{event_id}/assign", json={"coordinator_id": world["coord2"]}).status_code == 403

    event = event_row(db_session, event_id)
    assert event.title == SECRET_TITLE
    assert event.assigned_coordinator_id != world["coord2"]


@pytest.mark.parametrize("username", NON_COORDINATORS)
def test_tc8404_only_a_coordinator_can_call_review_on_a_later_event(client, db_session, world, username):
    login(client, username)

    response = client.post(f"/events/{world['event']}/review")

    # A 403 by role. A 400 here would tell them the event's status.
    assert response.status_code == 403
    assert APPROVED not in response.text
    assert event_row(db_session, world["event"]).status == APPROVED
    assert db_session.query(EventStatusHistory).count() == 0


@pytest.mark.parametrize("username", NON_COORDINATORS)
def test_tc8413_review_does_not_reveal_which_events_exist(client, world, username):
    login(client, username)

    response = client.post("/events/999999/review")

    assert response.status_code == 403


def internal_reads(world):
    event, venue, booking = world["event"], world["venue"], world["booking"]
    return [
        f"/events/{event}",
        f"/events/{event}/bookings",
        f"/events/{event}/venue-suitability",
        "/events",
        "/coordinator/events/data",
        "/coordinator/events/all-data",
        "/lead/events",
        "/lead/coordinators",
        f"/venues/{venue}/bookings",
        f"/venues/bookings/{booking}",
        "/venues/search",
    ]


def test_tc8405_an_attendee_reads_no_internal_planning_data(client, world):
    login(client, "attendee1")

    for path in internal_reads(world) + [f"/events/{world['event']}/equipment-requests", "/equipment-requests"]:
        response = client.get(path)
        assert response.status_code in (403, 404), path
        assert SECRET_TITLE not in response.text, path


def test_tc8406_another_organiser_reads_nothing_about_the_event(client, world):
    login(client, "organiser2")
    event_id = world["event"]

    assert client.get(f"/events/{event_id}").status_code == 404
    assert client.get(f"/events/{event_id}/bookings").status_code == 403
    assert SECRET_TITLE not in client.get("/events").text
    assert client.patch(f"/events/{event_id}", json={"title": "Changed"}).status_code in (403, 404)


@pytest.mark.parametrize("username", ["organiser1", "coord1", "lead1", "technical1", "attendee1"])
def test_tc8407_only_venue_staff_decide_a_booking(client, db_session, world, username):
    login(client, username)

    approve = client.post(f"/venues/bookings/{world['booking']}/approve", json={})
    reject = client.post(f"/venues/bookings/{world['booking']}/reject", json={"reason": "No"})

    assert approve.status_code == 403
    assert reject.status_code == 403
    db_session.expire_all()
    assert db_session.get(VenueBooking, world["booking"]).status == "Pending"


@pytest.mark.parametrize("username", ["organiser1", "coord1", "technical1", "attendee1"])
def test_tc8408_only_venue_staff_manage_the_venue_catalogue(client, db_session, world, username):
    login(client, username)
    new_venue = {"name": "New Room", "location": "Level 2", "capacity": 10}

    assert client.post("/venues", json=new_venue).status_code == 403
    assert client.patch(f"/venues/{world['venue']}", json={"capacity": 1}).status_code == 403
    assert client.post(f"/venues/{world['venue']}/retire", json={}).status_code == 403
    db_session.expire_all()
    assert db_session.get(Venue, world["venue"]).capacity == 100


def test_tc8409_technical_support_read_equipment_but_cannot_record_it(client, db_session, world):
    login(client, "technical1")
    event_id = world["event"]

    assert client.get(f"/events/{event_id}/equipment-requests").status_code == 200
    refused = client.post(f"/events/{event_id}/equipment-requests", json={"equipment_type": "Screen", "quantity": 1})

    assert refused.status_code == 403
    db_session.expire_all()
    assert db_session.query(EquipmentRequest).filter_by(event_id=event_id).count() == 1
