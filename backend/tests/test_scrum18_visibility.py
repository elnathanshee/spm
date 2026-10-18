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
from backend.status import CONFIRMED, DRAFT, SUBMITTED


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
        "own": Event(title="Confirmed conference", description="Public description", equipment_requirements="Internal equipment", venue_requirements="Internal venue notes", organiser_id=users["org_a"].id, assigned_coordinator_id=users["coord_a"].id, status=CONFIRMED, proposed_start=when, submitted_at=datetime.utcnow()),
        "other": Event(title="Other submitted", organiser_id=users["org_b"].id, assigned_coordinator_id=users["coord_b"].id, status=SUBMITTED, proposed_start=when, submitted_at=datetime.utcnow()),
        "unassigned": Event(title="Unassigned submitted", organiser_id=users["org_b"].id, status=SUBMITTED, proposed_start=when, submitted_at=datetime.utcnow()),
        "draft": Event(title="Private draft", organiser_id=users["org_b"].id, status=DRAFT, proposed_start=when),
        "confirmed_without_venue": Event(title="Venue pending", description="Safe public description", organiser_id=users["org_b"].id, status=CONFIRMED, proposed_start=when, submitted_at=datetime.utcnow()),
    }
    db_session.add_all(events.values())
    venue = Venue(name="SCRUM-18 Hall", location="Campus", capacity=100)
    pending_venue = Venue(name="Pending Hall", location="Campus", capacity=100)
    db_session.add_all([venue, pending_venue])
    db_session.flush()
    booking = VenueBooking(
        event_id=events["own"].id,
        venue_id=venue.id,
        booking_start=when,
        booking_end=when + timedelta(hours=2),
        status="Confirmed",
    )
    non_confirmed_bookings = [
        VenueBooking(
            event_id=events["confirmed_without_venue"].id,
            venue_id=pending_venue.id,
            booking_start=when + timedelta(hours=index * 3),
            booking_end=when + timedelta(hours=index * 3 + 2),
            status=booking_status,
            rejection_reason="Internal reason" if booking_status == "Rejected" else None,
        )
        for index, booking_status in enumerate(["Pending", "Tentative", "Rejected"])
    ]
    db_session.add_all([booking, *non_confirmed_bookings])
    db_session.commit()
    return {"users": users, "events": events, "venue": venue}


def authenticate(client, username):
    client.cookies.set("connectsphere_session", f"token-{username}")


def test_organiser_can_read_own_event_but_other_event_is_concealed(client, data):
    """TC-1801 (SCRUM-18 AC1): Organiser detail access is ownership-scoped."""
    authenticate(client, "org_a")

    own = client.get(f"/events/{data['events']['own'].id}")
    other = client.get(f"/events/{data['events']['other'].id}")

    assert own.status_code == 200
    assert own.json()["title"] == "Confirmed conference"
    assert other.status_code == 404
    assert other.json()["detail"] == "Event not found"


def test_coordinator_all_events_is_read_only_and_excludes_drafts(client, data):
    """TC-1802 (SCRUM-18 AC4): all non-drafts are readable; drafts stay private."""
    authenticate(client, "coord_a")

    all_events = client.get("/coordinator/events/all-data")
    assigned_queue = client.get("/coordinator/events/data")

    assert all_events.status_code == 200
    assert {row["id"] for row in all_events.json()} == {
        data["events"]["own"].id,
        data["events"]["other"].id,
        data["events"]["unassigned"].id,
        data["events"]["confirmed_without_venue"].id,
    }
    assert {row["id"] for row in assigned_queue.json()} == {data["events"]["own"].id}
    assert all(row["id"] != data["events"]["draft"].id for row in all_events.json())


@pytest.mark.parametrize("event_name", ["other", "unassigned"])
def test_coordinator_read_visibility_does_not_grant_management(client, data, event_name):
    """TC-1803 (SCRUM-18 AC4/AC5): read visibility grants no management authority."""
    authenticate(client, "coord_a")
    event = data["events"][event_name]

    assert client.get(f"/events/{event.id}").status_code == 200
    assert client.post(f"/events/{event.id}/approve").status_code == 403


def test_attendee_receives_only_allowlisted_confirmed_event_fields(client, data):
    """TC-1804 (SCRUM-18 AC2/AC3): attendee response is an explicit safe allowlist."""
    authenticate(client, "attendee")
    event = data["events"]["own"]

    detail = client.get(f"/events/{event.id}")

    assert detail.status_code == 200
    assert detail.json() == {
        "name": "Confirmed conference",
        "description": "Public description",
        "date": event.proposed_start.date().isoformat(),
        "time": event.proposed_start.time().isoformat(),
        "venue_name": "SCRUM-18 Hall",
    }
    assert set(detail.json()) == {"name", "description", "date", "time", "venue_name"}


def test_attendee_does_not_treat_non_confirmed_bookings_as_a_venue(client, data):
    """TC-1805 (SCRUM-18 AC2/AC3): pending, tentative and rejected venues stay hidden."""
    authenticate(client, "attendee")

    detail = client.get(f"/events/{data['events']['confirmed_without_venue'].id}")

    assert detail.status_code == 200
    assert detail.json()["venue_name"] is None
    assert "Pending Hall" not in detail.text
    assert "Internal reason" not in detail.text


@pytest.mark.parametrize("event_name", ["draft", "other", "unassigned"])
def test_attendee_cannot_read_private_or_unconfirmed_events(client, data, event_name):
    """TC-1806 (SCRUM-18 AC2/AC3): only confirmed events are attendee-visible."""
    authenticate(client, "attendee")
    response = client.get(f"/events/{data['events'][event_name].id}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Event not found"


def test_attendee_cannot_read_internal_bookings(client, data):
    """TC-1807 (SCRUM-18 AC3): attendee detail access does not expose booking APIs."""
    authenticate(client, "attendee")
    bookings = client.get(f"/events/{data['events']['own'].id}/bookings")
    assert bookings.status_code == 403
    assert set(bookings.json()) == {"detail"}
    assert "venue_name" not in bookings.text
    assert "booking_start" not in bookings.text


def test_attendee_cannot_perform_internal_management_actions(client, data):
    """TC-1808 (SCRUM-18 AC3/AC5): safe reads grant no internal actions."""
    authenticate(client, "attendee")
    event = data["events"]["own"]
    assert client.post(f"/events/{event.id}/approve").status_code == 403
    assert client.post(f"/events/{event.id}/reassign", json={"coordinator_id": data["users"]["coord_b"].id}).status_code == 403


def test_attendee_missing_event_returns_404(client, data):
    """TC-1809 (SCRUM-18 AC2): a missing attendee event remains 404."""
    authenticate(client, "attendee")
    response = client.get("/events/999999")
    assert response.status_code == 404
    assert response.json()["detail"] == "Event not found"


def test_event_booking_visibility_respects_role_and_assignment(client, data):
    """TC-1810 (SCRUM-18 AC3/AC5): internal booking reads remain relationship-scoped."""
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
    """TC-1811 (SCRUM-18 AC5): Coordinators book only for their assigned events."""
    authenticate(client, "coord_a")
    start = datetime.utcnow() + timedelta(days=60)
    payload = {
        "event_id": data["events"]["other"].id,
        "booking_start": start.isoformat(),
        "booking_end": (start + timedelta(hours=2)).isoformat(),
    }

    response = client.post(f"/venues/{data['venue'].id}/bookings", json=payload)

    assert response.status_code == 403
    original_count = 4
    assert db_session.query(VenueBooking).count() == original_count

    payload["event_id"] = data["events"]["unassigned"].id
    response = client.post(f"/venues/{data['venue'].id}/bookings", json=payload)
    assert response.status_code == 403
    assert db_session.query(VenueBooking).count() == original_count
