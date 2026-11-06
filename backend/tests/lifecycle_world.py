"""[SCRUM-102] The small world the lifecycle and safety tests share: one user per
role, a venue, and helpers to make events, bookings and equipment. Sign-in uses a
session row, so these tests need no passwords."""
import os
import sys
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import arrangements
from backend.database import Base, get_db
from backend.main import app
from backend.models import EquipmentRequest, Event, LoginSession, Notification, User, Venue, VenueBooking

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

START = datetime(2099, 3, 10, 2, 0)
END = datetime(2099, 3, 10, 6, 0)

ROLES = {
    "organiser1": "organiser",
    "organiser2": "organiser",
    "coord1": "coordinator",
    "coord2": "coordinator",
    "lead1": "lead",
    "safety1": "safety_officer",
    "venue1": "venue_staff",
    "technical1": "technical support",
    "attendee1": "attendee",
}


class World:
    def __init__(self, db, client):
        self.db = db
        self.client = client
        self.users = {}
        for name, role in ROLES.items():
            self.users[name] = User(username=name, password_hash="x", role=role)
        db.add_all(self.users.values())
        self.hall = Venue(
            name="Grand Hall", location="Block A", capacity=100, setup_time_mins=30, turnaround_time_mins=45,
            supported_layouts='["Theatre", "Banquet"]', facilities='["Stage"]', accessibility="Step-free entrance",
            operating_hours="08:00-22:00",
        )
        db.add(self.hall)
        db.commit()

    def login(self, name):
        token = f"token-{name}"
        if not self.db.query(LoginSession).filter_by(token=token).first():
            self.db.add(LoginSession(user_id=self.users[name].id, token=token, valid=True))
            self.db.commit()
        self.client.cookies.set("connectsphere_session", token)
        return self.client

    def event(self, status="Planning", coordinator="coord1", title="Annual Gala"):
        event = Event(
            title=title, organiser_id=self.users["organiser1"].id, status=status,
            assigned_coordinator_id=self.users[coordinator].id if coordinator else None,
            proposed_start=datetime(2099, 3, 10, 10, 0), expected_attendance=80,
            accessibility_needs="Two wheelchair users", venue_requirements="Theatre layout",
        )
        self.db.add(event)
        self.db.commit()
        return event

    def booking(self, event, status="Confirmed", start=START, end=END):
        booking = VenueBooking(event_id=event.id, venue_id=self.hall.id, booking_start=start, booking_end=end, status=status)
        self.db.add(booking)
        self.db.commit()
        return booking

    def equipment(self, event, kind="Projector", status=None, notes="Left of the stage"):
        request = EquipmentRequest(
            event_id=event.id, equipment_type=kind, quantity=2, technical_requirements=notes,
            requested_by_id=self.users["coord1"].id,
        )
        self.db.add(request)
        self.db.flush()
        if status:
            arrangements.record_equipment_status(self.db, request, status, "Checked on site", self.users["technical1"])
        self.db.commit()
        return request

    def reload(self, row):
        self.db.expire_all()
        return self.db.get(type(row), row.id)

    def notices(self, name, event=None):
        self.db.expire_all()
        rows = self.db.query(Notification).filter(Notification.user_id == self.users[name].id)
        if event is not None:
            rows = rows.filter(Notification.event_id == event.id)
        return rows.order_by(Notification.id.asc()).all()


@pytest.fixture
def world():
    Base.metadata.create_all(bind=engine)
    db = TestSession()

    def override_get_db():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield World(db, client)
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()
        Base.metadata.drop_all(bind=engine)
