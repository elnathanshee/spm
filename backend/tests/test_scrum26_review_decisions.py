"""SCRUM-26: Approve or reject an event request.

Endpoint-level tests for the status guards and the required rejection reason.
The transition rules themselves come from status_rules.py (SCRUM-27).
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
from backend.models import Event, EventStatusHistory, User
from backend.security import hash_password
from backend.status import APPROVED, DRAFT, REJECTED, RETURNED, SUBMITTED, UNDER_REVIEW

TEST_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="function")
def db_session():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
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
def people(db_session):
    organiser = User(username="org", password_hash=hash_password("pass123"), role="organiser")
    coord_a = User(username="coord_a", password_hash=hash_password("pass123"), role="coordinator", is_active_coordinator=True)
    coord_b = User(username="coord_b", password_hash=hash_password("pass123"), role="coordinator", is_active_coordinator=True)
    db_session.add_all([organiser, coord_a, coord_b])
    db_session.commit()
    return {"organiser": organiser, "coord_a": coord_a, "coord_b": coord_b}


def login(client, username):
    resp = client.post("/auth/login", json={"username": username, "password": "pass123"})
    assert resp.status_code == 200


@pytest.fixture
def submitted_event(client, people, db_session):
    """Organiser creates and submits a request. Returns (event_id, assigned username, other coordinator username)."""
    login(client, "org")
    start = (datetime.utcnow() + timedelta(days=14)).isoformat()
    resp = client.post("/events", json={"title": "Annual Gala", "proposed_start": start, "expected_attendance": 100})
    assert resp.status_code == 201
    event_id = resp.json()["id"]
    sub = client.post(f"/events/{event_id}/submit")
    assert sub.status_code == 200

    event = db_session.get(Event, event_id)
    event.assigned_coordinator_id = people["coord_a"].id
    db_session.commit()
    return event_id, "coord_a", "coord_b"


def set_status(db_session, event_id, new_status):
    event = db_session.get(Event, event_id)
    event.status = new_status
    db_session.commit()


def current_status(db_session, event_id):
    db_session.expire_all()
    return db_session.get(Event, event_id).status


# ---------- Approve ----------

@pytest.mark.parametrize("from_status", [SUBMITTED, UNDER_REVIEW])
def test_scrum26_tc01_assigned_coordinator_can_approve_reviewable_request(client, db_session, submitted_event, from_status):
    event_id, assigned, _ = submitted_event
    set_status(db_session, event_id, from_status)

    login(client, assigned)
    resp = client.post(f"/events/{event_id}/approve")

    assert resp.status_code == 200
    assert resp.json()["status"] == APPROVED
    history = db_session.query(EventStatusHistory).filter_by(event_id=event_id, new_status=APPROVED).one()
    assert history.previous_status == from_status
    assert history.actor.username == assigned


@pytest.mark.parametrize("from_status", [DRAFT, RETURNED, APPROVED, REJECTED])
def test_scrum26_tc02_cannot_approve_from_non_reviewable_status(client, db_session, submitted_event, from_status):
    event_id, assigned, _ = submitted_event
    set_status(db_session, event_id, from_status)

    login(client, assigned)
    resp = client.post(f"/events/{event_id}/approve")

    assert resp.status_code == 400
    assert current_status(db_session, event_id) == from_status


def test_scrum26_tc03_unassigned_coordinator_cannot_approve(client, db_session, submitted_event):
    event_id, _, other = submitted_event

    login(client, other)
    resp = client.post(f"/events/{event_id}/approve")

    assert resp.status_code == 403
    assert current_status(db_session, event_id) == SUBMITTED


def test_scrum26_tc04_organiser_cannot_approve(client, db_session, submitted_event):
    event_id, _, _ = submitted_event

    login(client, "org")
    resp = client.post(f"/events/{event_id}/approve")

    assert resp.status_code == 403
    assert current_status(db_session, event_id) == SUBMITTED


# ---------- Reject ----------

def test_scrum26_tc05_reject_with_reason_saves_reason(client, db_session, submitted_event):
    event_id, assigned, _ = submitted_event

    login(client, assigned)
    resp = client.post(f"/events/{event_id}/reject", json={"reason": "  Venue budget exceeded  "})

    assert resp.status_code == 200
    assert resp.json()["status"] == REJECTED
    assert resp.json()["rejection_reason"] == "Venue budget exceeded"
    history = db_session.query(EventStatusHistory).filter_by(event_id=event_id, new_status=REJECTED).one()
    assert history.reason == "Venue budget exceeded"
    assert history.actor.username == assigned


@pytest.mark.parametrize("body", [None, {}, {"reason": ""}, {"reason": "   "}])
def test_scrum26_tc06_reject_without_reason_is_refused(client, db_session, submitted_event, body):
    event_id, assigned, _ = submitted_event

    login(client, assigned)
    if body is None:
        resp = client.post(f"/events/{event_id}/reject")
    else:
        resp = client.post(f"/events/{event_id}/reject", json=body)

    assert resp.status_code == 422
    assert current_status(db_session, event_id) == SUBMITTED


@pytest.mark.parametrize("from_status", [DRAFT, RETURNED, APPROVED, REJECTED])
def test_scrum26_tc07_cannot_reject_from_non_reviewable_status(client, db_session, submitted_event, from_status):
    event_id, assigned, _ = submitted_event
    set_status(db_session, event_id, from_status)

    login(client, assigned)
    resp = client.post(f"/events/{event_id}/reject", json={"reason": "Not suitable"})

    assert resp.status_code == 400
    assert current_status(db_session, event_id) == from_status


def test_scrum26_tc08_unassigned_coordinator_cannot_reject(client, db_session, submitted_event):
    event_id, _, other = submitted_event

    login(client, other)
    resp = client.post(f"/events/{event_id}/reject", json={"reason": "Not suitable"})

    assert resp.status_code == 403
    assert current_status(db_session, event_id) == SUBMITTED


def test_scrum26_tc09_refused_decision_records_no_history(client, db_session, submitted_event):
    event_id, _, other = submitted_event
    before = db_session.query(EventStatusHistory).filter_by(event_id=event_id).count()

    login(client, other)
    client.post(f"/events/{event_id}/approve")
    client.post(f"/events/{event_id}/reject", json={"reason": "Not suitable"})

    db_session.expire_all()
    assert db_session.query(EventStatusHistory).filter_by(event_id=event_id).count() == before


def test_scrum26_tc10_unknown_event_returns_404(client, people):
    login(client, "coord_a")
    assert client.post("/events/9999/approve").status_code == 404
    assert client.post("/events/9999/reject", json={"reason": "x"}).status_code == 404


# ---------- AC3: outcome visible to the Organiser ----------

@pytest.mark.parametrize("decision, body, expected", [
    ("approve", None, APPROVED),
    ("reject", {"reason": "Venue budget exceeded"}, REJECTED),
])
def test_scrum26_tc11_organiser_can_see_the_outcome(client, submitted_event, decision, body, expected):
    """AC3: after the Coordinator decides, the Organiser sees the outcome in their list of requests."""
    event_id, assigned, _ = submitted_event

    login(client, assigned)
    if body is None:
        resp = client.post(f"/events/{event_id}/{decision}")
    else:
        resp = client.post(f"/events/{event_id}/{decision}", json=body)
    assert resp.status_code == 200

    login(client, "org")
    my_events = client.get("/events")

    assert my_events.status_code == 200
    event = next(e for e in my_events.json() if e["id"] == event_id)
    assert event["status"] == expected
