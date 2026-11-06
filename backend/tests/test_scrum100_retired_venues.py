"""SCRUM-100: Venue Staff see bookings at retired venues and can correct a retired venue.

A retired venue leaves the catalogue, but its bookings still hold it, so Venue
Staff need a way to find them. Everyone else keeps seeing active venues only.
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
from backend.models import Event, LoginSession, User, Venue, VenueBooking
from backend.security import hash_password

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
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
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def seed(db_session):
    users = {}
    for name, role in (("staff", "venue_staff"), ("coord", "coordinator"), ("lead", "lead"), ("org", "organiser")):
        user = User(username=name, password_hash=hash_password("not-a-real-secret"), role=role,
                    is_active_coordinator=role == "coordinator")
        db_session.add(user)
        db_session.flush()
        db_session.add(LoginSession(user_id=user.id, token=f"tok_{name}", valid=True))
        users[name] = user
    active = Venue(name="Grand Hall", location="Block A", capacity=100, is_active=True)
    retired = Venue(name="Old Store", location="Block Z", capacity=20, is_active=False)
    event = Event(title="Annual Symposium", organiser_id=users["org"].id, status="Planning",
                  assigned_coordinator_id=users["coord"].id)
    db_session.add_all([active, retired, event])
    db_session.flush()
    booking = VenueBooking(venue_id=retired.id, event_id=event.id, status="Confirmed",
                           booking_start=datetime(2099, 6, 1, 10), booking_end=datetime(2099, 6, 1, 12))
    db_session.add(booking)
    db_session.commit()
    return {"active": active, "retired": retired, "booking": booking}


def _as(client, name):
    client.cookies.set("connectsphere_session", f"tok_{name}")


def test_tc10030_venue_staff_list_retired_venues(client, seed):
    """TC-10030: GET /venues?retired=true gives the retired venues only, and
    GET /venues still gives the active ones only."""
    _as(client, "staff")
    assert [v["name"] for v in client.get("/venues", params={"retired": "true"}).json()] == ["Old Store"]
    assert [v["name"] for v in client.get("/venues").json()] == ["Grand Hall"]


@pytest.mark.parametrize("name", ["coord", "lead", "org"])
def test_tc10031_only_venue_staff_can_list_retired_venues(client, seed, name):
    """TC-10031: other roles get 403 asking for retired venues, and their usual
    list is unchanged."""
    _as(client, name)
    assert client.get("/venues", params={"retired": "true"}).status_code == 403
    assert [v["name"] for v in client.get("/venues").json()] == ["Grand Hall"]


def test_tc10032_venue_staff_can_open_a_retired_venue(client, seed):
    """TC-10032: Venue Staff read the record to correct it. A Coordinator still
    gets 404, because a retired venue isn't bookable (SCRUM-31 D4)."""
    url = f"/venues/{seed['retired'].id}"
    _as(client, "staff")
    res = client.get(url)
    assert res.status_code == 200
    assert res.json()["is_active"] is False
    _as(client, "coord")
    assert client.get(url).status_code == 404


def test_tc10033_venue_staff_see_a_retired_venues_bookings_and_correct_it(client, db_session, seed):
    """TC-10033: the retired venue's bookings are listed for Venue Staff, a
    correction saves, the venue stays retired, and it still can't be booked."""
    retired = seed["retired"]
    _as(client, "staff")
    rows = client.get(f"/venues/{retired.id}/bookings").json()
    assert [(r["booking_id"], r["event_title"], r["status"]) for r in rows] == [
        (seed["booking"].id, "Annual Symposium", "Confirmed")
    ]
    res = client.patch(f"/venues/{retired.id}", json={"location": "Block Y"})
    assert res.status_code == 200
    assert res.json()["venue"]["location"] == "Block Y"
    assert res.json()["venue"]["is_active"] is False
    assert [v["location"] for v in client.get("/venues", params={"retired": "true"}).json()] == ["Block Y"]
    _as(client, "coord")
    body = {"event_id": seed["booking"].event_id, "booking_start": "2099-07-01T10:00:00", "booking_end": "2099-07-01T11:00:00"}
    assert client.post(f"/venues/{retired.id}/bookings", json=body).status_code == 400
