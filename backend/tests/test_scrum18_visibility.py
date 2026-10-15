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
from backend.status import DRAFT, SUBMITTED


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
def data(db_session):
    roles = {
        "org_a": ("organiser", False),
        "org_b": ("organiser", False),
        "coord_a": ("coordinator", True),
        "coord_b": ("coordinator", True),
        "lead": ("lead", False),
        "venue": ("venue_staff", False),
        "attendee": ("attendee", False),
    }
    users = {}
    for username, (role, active) in roles.items():
        user = User(
            username=username,
            password_hash=hash_password("pass123"),
            role=role,
            is_active_coordinator=active,
        )
        db_session.add(user)
        users[username] = user
    db_session.flush()
    for username, user in users.items():
        db_session.add(LoginSession(user_id=user.id, token=f"token-{username}", valid=True))

    when = datetime.utcnow() + timedelta(days=30)
    events = {
        "own": Event(title="Own submitted", description="Public description", organiser_id=users["org_a"].id, assigned_coordinator_id=users["coord_a"].id, status=SUBMITTED, proposed_start=when, submitted_at=datetime.utcnow()),
        "other": Event(title="Other submitted", organiser_id=users["org_b"].id, assigned_coordinator_id=users["coord_b"].id, status=SUBMITTED, proposed_start=when, submitted_at=datetime.utcnow()),
        "unassigned": Event(title="Unassigned submitted", organiser_id=users["org_b"].id, status=SUBMITTED, proposed_start=when, submitted_at=datetime.utcnow()),
        "draft": Event(title="Private draft", organiser_id=users["org_b"].id, status=DRAFT, proposed_start=when),
    }
    db_session.add_all(events.values())
    venue = Venue(name="SCRUM-18 Hall", location="Campus", capacity=100)
    db_session.add(venue)
    db_session.flush()
    booking = VenueBooking(
        event_id=events["own"].id,
        venue_id=venue.id,
        booking_start=when,
        booking_end=when + timedelta(hours=2),
        status="Pending",
    )
    db_session.add(booking)
    db_session.commit()
    return {"users": users, "events": events, "venue": venue}


def authenticate(client, username):
    client.cookies.set("connectsphere_session", f"token-{username}")


def test_organiser_can_read_own_event_but_other_event_is_concealed(client, data):
    authenticate(client, "org_a")

    own = client.get(f"/events/{data['events']['own'].id}")
    other = client.get(f"/events/{data['events']['other'].id}")

    assert own.status_code == 200
    assert own.json()["title"] == "Own submitted"
    assert other.status_code == 404
    assert other.json()["detail"] == "Event not found"


def test_coordinator_all_events_is_read_only_and_excludes_drafts(client, data):
    authenticate(client, "coord_a")

    all_events = client.get("/coordinator/events/all-data")
    assigned_queue = client.get("/coordinator/events/data")

    assert all_events.status_code == 200
    assert {row["id"] for row in all_events.json()} == {
        data["events"]["own"].id,
        data["events"]["other"].id,
        data["events"]["unassigned"].id,
    }
    assert {row["id"] for row in assigned_queue.json()} == {data["events"]["own"].id}
    assert all(row["id"] != data["events"]["draft"].id for row in all_events.json())


@pytest.mark.parametrize("event_name", ["other", "unassigned"])
def test_coordinator_read_visibility_does_not_grant_management(client, data, event_name):
    authenticate(client, "coord_a")
    event = data["events"][event_name]

    assert client.get(f"/events/{event.id}").status_code == 200
    assert client.post(f"/events/{event.id}/approve").status_code == 403


def test_attendee_cannot_read_event_detail_or_internal_bookings(client, data):
    authenticate(client, "attendee")
    event = data["events"]["own"]

    detail = client.get(f"/events/{event.id}")
    bookings = client.get(f"/events/{event.id}/bookings")

    assert detail.status_code == 403
    assert bookings.status_code == 403
    assert set(bookings.json()) == {"detail"}
    assert "venue_name" not in bookings.text
    assert "booking_start" not in bookings.text


def test_event_booking_visibility_respects_role_and_assignment(client, data):
    event = data["events"]["own"]

    authenticate(client, "coord_a")
    assert client.get(f"/events/{event.id}/bookings").status_code == 200

    authenticate(client, "coord_b")
    assert client.get(f"/events/{event.id}/bookings").status_code == 403

    authenticate(client, "lead")
    assert client.get(f"/events/{event.id}/bookings").status_code == 200

    authenticate(client, "venue")
    assert client.get(f"/events/{event.id}/bookings").status_code == 200


def test_coordinator_cannot_book_venue_for_another_or_unassigned_event(client, db_session, data):
    authenticate(client, "coord_a")
    start = datetime.utcnow() + timedelta(days=60)
    payload = {
        "event_id": data["events"]["other"].id,
        "booking_start": start.isoformat(),
        "booking_end": (start + timedelta(hours=2)).isoformat(),
    }

    response = client.post(f"/venues/{data['venue'].id}/bookings", json=payload)

    assert response.status_code == 403
    assert db_session.query(VenueBooking).count() == 1

    payload["event_id"] = data["events"]["unassigned"].id
    response = client.post(f"/venues/{data['venue'].id}/bookings", json=payload)
    assert response.status_code == 403
    assert db_session.query(VenueBooking).count() == 1
