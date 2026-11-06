"""[SCRUM-99] The backend follow-ups from PR #48's review.

Calendar days are Singapore days. Booking times are stored as naive UTC, and
Singapore is UTC+8, so a Singapore day runs from 16:00 UTC the day before.
Dates are far in the future, so these tests never expire.
"""

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base, get_db
from backend.main import app
from backend.models import Event, LoginSession, User, Venue, VenueBooking
from backend.security import hash_password

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

COOKIE = "connectsphere_session"
DAY = date(2099, 3, 10)
BEFORE = date(2099, 3, 9)
AFTER = date(2099, 3, 11)


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
        test_client.cookies.set(COOKIE, "tok_coord")
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def event(db_session):
    organiser = User(username="org", password_hash=hash_password("password123"), role="organiser")
    coordinator = User(
        username="coord", password_hash=hash_password("password123"), role="coordinator", is_active_coordinator=True,
    )
    db_session.add_all([organiser, coordinator])
    db_session.commit()
    db_session.add(LoginSession(user_id=coordinator.id, token="tok_coord", valid=True))
    new_event = Event(
        title="Annual Gala",
        proposed_start=datetime(2099, 3, 10, 10),
        expected_attendance=80,
        organiser_id=organiser.id,
        status="Approved",
        assigned_coordinator_id=coordinator.id,
    )
    db_session.add(new_event)
    db_session.commit()
    return new_event


@pytest.fixture
def room(db_session):
    """No setup or turnaround, so the occupied window is the booked one."""
    venue = Venue(
        name="Quick Room", location="Block A", capacity=100, operating_hours="08:00-22:00",
        setup_time_mins=0, turnaround_time_mins=0, is_active=True,
    )
    db_session.add(venue)
    db_session.commit()
    return venue


def add_booking(db_session, venue, event, start, end):
    booking = VenueBooking(venue_id=venue.id, event_id=event.id, booking_start=start, booking_end=end, status="Confirmed")
    db_session.add(booking)
    db_session.commit()


def busy_on(client, venue, day):
    """How many blocks the calendar lists for one day, asking for that day alone."""
    response = client.get(f"/venues/{venue.id}/calendar", params={"from": day.isoformat(), "to": day.isoformat()})
    assert response.status_code == 200
    return len(response.json()["days"][0]["busy"])


def test_tc9910_early_morning_singapore_booking_is_listed_under_its_own_day(client, db_session, room, event):
    """TC-9910 | Happy Path | 07:00 to 09:00 on 10 March in Singapore is stored as 23:00 to 01:00 UTC."""
    add_booking(db_session, room, event, datetime(2099, 3, 9, 23), datetime(2099, 3, 10, 1))

    assert busy_on(client, room, DAY) == 1
    assert busy_on(client, room, BEFORE) == 0


def test_tc9911_late_evening_singapore_booking_stays_on_its_day(client, db_session, room, event):
    """TC-9911 | Happy Path | 20:00 to 23:00 on 10 March in Singapore is stored as 12:00 to 15:00 UTC."""
    add_booking(db_session, room, event, datetime(2099, 3, 10, 12), datetime(2099, 3, 10, 15))

    assert busy_on(client, room, DAY) == 1
    assert busy_on(client, room, AFTER) == 0


def test_tc9912_a_booking_that_only_touches_singapore_midnight_is_not_on_that_day(client, db_session, room, event):
    """TC-9912 | Boundary Path | Singapore midnight on 10 March is 16:00 UTC on 9 March."""
    add_booking(db_session, room, event, datetime(2099, 3, 9, 14), datetime(2099, 3, 9, 16))
    assert busy_on(client, room, DAY) == 0
    assert busy_on(client, room, BEFORE) == 1

    add_booking(db_session, room, event, datetime(2099, 3, 9, 15), datetime(2099, 3, 9, 16, 1))
    assert busy_on(client, room, DAY) == 1
