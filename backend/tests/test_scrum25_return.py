"""SCRUM-25: Return a request for clarification.

Endpoint-level tests for returning a request with a note, the status guards on
/return (rules from status_rules.py, SCRUM-27), and the Organiser seeing what
was asked. Each test names the acceptance criterion it proves.
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


def organiser_view(client, event_id):
    login(client, "org")
    resp = client.get("/events")
    assert resp.status_code == 200
    return next(e for e in resp.json() if e["id"] == event_id)


NOTE = "Please add your AV and accessibility requirements."


# ---------- AC1 + AC4: return with a note, status becomes Returned ----------

@pytest.mark.parametrize("from_status", [SUBMITTED, UNDER_REVIEW])
def test_scrum25_tc01_assigned_coordinator_returns_with_note(client, db_session, submitted_event, from_status):
    event_id, assigned, _ = submitted_event
    set_status(db_session, event_id, from_status)

    login(client, assigned)
    resp = client.post(f"/events/{event_id}/return", json={"reason": NOTE})

    assert resp.status_code == 200
    assert resp.json()["status"] == RETURNED
    assert resp.json()["clarification_reason"] == NOTE
    history = db_session.query(EventStatusHistory).filter_by(event_id=event_id, new_status=RETURNED).one()
    assert history.previous_status == from_status
    assert history.reason == NOTE
    assert history.actor.username == assigned


@pytest.mark.parametrize("body", [None, {}, {"reason": ""}, {"reason": "   "}])
def test_scrum25_tc02_return_without_note_is_refused(client, db_session, submitted_event, body):
    event_id, assigned, _ = submitted_event

    login(client, assigned)
    if body is None:
        resp = client.post(f"/events/{event_id}/return")
    else:
        resp = client.post(f"/events/{event_id}/return", json=body)

    assert resp.status_code == 422
    assert current_status(db_session, event_id) == SUBMITTED


# ---------- Status guards ----------

@pytest.mark.parametrize("from_status", [DRAFT, RETURNED, APPROVED, REJECTED])
def test_scrum25_tc03_cannot_return_from_non_reviewable_status(client, db_session, submitted_event, from_status):
    event_id, assigned, _ = submitted_event
    set_status(db_session, event_id, from_status)

    login(client, assigned)
    resp = client.post(f"/events/{event_id}/return", json={"reason": NOTE})

    assert resp.status_code == 400
    assert current_status(db_session, event_id) == from_status


@pytest.mark.parametrize("who", ["other_coordinator", "org"])
def test_scrum25_tc04_only_assigned_coordinator_can_return(client, db_session, submitted_event, who):
    event_id, _, other = submitted_event

    login(client, other if who == "other_coordinator" else "org")
    resp = client.post(f"/events/{event_id}/return", json={"reason": NOTE})

    assert resp.status_code == 403
    assert current_status(db_session, event_id) == SUBMITTED
    db_session.expire_all()
    assert db_session.query(EventStatusHistory).filter_by(event_id=event_id, new_status=RETURNED).count() == 0


def test_scrum25_tc05_unknown_event_returns_404(client, people):
    login(client, "coord_a")
    assert client.post("/events/9999/return", json={"reason": NOTE}).status_code == 404


# ---------- AC2: Organiser sees clarification was requested and what was asked ----------

def test_scrum25_tc06_organiser_sees_returned_status_and_note(client, submitted_event):
    event_id, assigned, _ = submitted_event

    login(client, assigned)
    client.post(f"/events/{event_id}/return", json={"reason": NOTE})

    event = organiser_view(client, event_id)
    assert event["status"] == RETURNED
    assert event["clarification_note"] == NOTE


def test_scrum25_tc07_no_note_shown_when_not_returned(client, submitted_event):
    event_id, _, _ = submitted_event

    event = organiser_view(client, event_id)
    assert event["status"] == SUBMITTED
    assert event["clarification_note"] is None


def test_scrum25_tc08_organiser_sees_latest_note_after_second_return(client, submitted_event):
    event_id, assigned, _ = submitted_event

    login(client, assigned)
    client.post(f"/events/{event_id}/return", json={"reason": "First question"})
    login(client, "org")
    assert client.post(f"/events/{event_id}/submit").status_code == 200
    login(client, assigned)
    client.post(f"/events/{event_id}/return", json={"reason": "Second question"})

    assert organiser_view(client, event_id)["clarification_note"] == "Second question"


# ---------- AC3: returned request can be resubmitted ----------
# (Amending the request's details is SCRUM-21, Elnathan's story, on its own branch.)

def test_scrum25_tc09_organiser_can_resubmit_returned_request(client, db_session, submitted_event):
    event_id, assigned, _ = submitted_event

    login(client, assigned)
    client.post(f"/events/{event_id}/return", json={"reason": NOTE})

    login(client, "org")
    resp = client.post(f"/events/{event_id}/submit")

    assert resp.status_code == 200
    assert resp.json()["status"] == SUBMITTED
    assert organiser_view(client, event_id)["clarification_note"] is None
