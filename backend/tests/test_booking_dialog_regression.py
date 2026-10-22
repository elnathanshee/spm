from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError

from backend.models import User, LoginSession, Event
from backend.tests.test_scrum37_rejection_reason import db_session, client


@pytest.mark.parametrize("role,decision", [
    pytest.param("coordinator", "approve", id="TC-3530"),
    pytest.param("lead", "approve", id="TC-3531"),
    pytest.param("coordinator", "reject", id="TC-3532"),
    pytest.param("lead", "reject", id="TC-3533"),
])
def test_empty_catalogue_to_complete_booking_workflow(client, db_session, role, decision):
    requester = User(username="requester", role=role, password_hash="unused")
    staff = User(username="staff", role="venue_staff", password_hash="unused")
    organiser = User(username="organiser", role="organiser", password_hash="unused")
    db_session.add_all([requester, staff, organiser])
    db_session.flush()
    event = Event(title="Booking regression", organiser_id=organiser.id,
                  assigned_coordinator_id=requester.id, status="Submitted")
    db_session.add(event)
    db_session.add_all([LoginSession(user_id=requester.id, token="requester64", valid=True),
                        LoginSession(user_id=staff.id, token="staff64", valid=True)])
    db_session.commit()
    client.cookies.set("connectsphere_session", "requester64")
    for path in ["/venues", f"/events/{event.id}/bookings"]:
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == []
    client.cookies.set("connectsphere_session", "staff64")
    response = client.post("/venues", json={"name": "New hall", "location": "Campus", "capacity": 100})
    assert response.status_code == 201
    venue_id = response.json()["venue"]["id"]
    client.cookies.set("connectsphere_session", "requester64")
    assert client.get("/venues").json()[0]["id"] == venue_id
    start = datetime.now() + timedelta(days=30)
    response = client.post(f"/venues/{venue_id}/bookings", json={"event_id": event.id,
        "booking_start": start.isoformat(), "booking_end": (start + timedelta(hours=1)).isoformat()})
    assert response.status_code == 201
    booking_id = response.json()["booking"]["id"]
    client.cookies.set("connectsphere_session", "staff64")
    assert client.get("/notifications").json()[0]["kind"] == "booking_submitted"
    response = client.post(f"/venues/bookings/{booking_id}/{decision}", json={"reason": "Persisted rejection"})
    assert response.status_code == 200
    client.cookies.set("connectsphere_session", "requester64")
    message = client.get("/notifications").json()[0]
    assert message["kind"] == ("booking_approved" if decision == "approve" else "booking_rejected")
    assert "New hall" in message["message"]
    booking = client.get(f"/events/{event.id}/bookings").json()[0]
    assert booking["status"] == ("Confirmed" if decision == "approve" else "Rejected")
    if decision == "reject":
        assert booking["rejection_reason"] == "Persisted rejection"
        assert "Persisted rejection" in message["message"]


def test_tc6440_create_all_does_not_upgrade_an_existing_booking_table():
    from backend.database import Base
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as c:
        c.execute(text("CREATE TABLE venue_bookings (id INTEGER PRIMARY KEY)"))
        c.execute(text("INSERT INTO venue_bookings VALUES (1)"))
    Base.metadata.create_all(engine)
    # Deployment must upgrade legacy schemas separately; initialization cannot repair them.
    with engine.connect() as c:
        with pytest.raises(OperationalError, match="requester_id"):
            c.execute(text("SELECT requester_id FROM venue_bookings"))
        assert c.execute(text("SELECT id FROM venue_bookings")).all() == [(1,)]


def test_tc6441_fresh_schema_initializes_requester_and_notification_uniqueness():
    from backend.database import Base
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    requester = next(c for c in inspector.get_columns("venue_bookings") if c['name'] == 'requester_id')
    assert requester['nullable']
    assert any(key['constrained_columns'] == ['requester_id'] and key['referred_table'] == 'users'
               for key in inspector.get_foreign_keys('venue_bookings'))
    assert any(set(key['column_names']) == {'booking_id', 'kind', 'user_id'}
               for key in inspector.get_unique_constraints('notifications'))
