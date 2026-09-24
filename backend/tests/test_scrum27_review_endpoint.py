"""SCRUM-27: the Coordinator starts review (POST /events/{id}/review).

Endpoint-level tests for the Submitted -> Under Review move. The transition
rules themselves come from status_rules.py and are unit-tested in
test_scrum27_status_rules.py; these prove the route is wired up and enforces
them.

Why this file exists: the endpoint was written by Melvin in the root-level
events.py, which the merge left outside the backend package, so the route was
never served and "Under Review" was unreachable through the API. It has been
ported into backend/events.py against change_status. These tests are the
evidence that the move now works end to end.

Numbering continues from TC-214 in test_scrum27_status_rules.py.
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

    assigned_id = sub.json()["assigned_coordinator_id"]
    assigned = db_session.get(User, assigned_id).username
    other = "coord_b" if assigned == "coord_a" else "coord_a"
    return event_id, assigned, other


def set_status(db_session, event_id, new_status):
    event = db_session.get(Event, event_id)
    event.status = new_status
    db_session.commit()


def current_status(db_session, event_id):
    db_session.expire_all()
    return db_session.get(Event, event_id).status


# ---------- AC1: the move exists and is reachable ----------


def test_tc215_assigned_coordinator_starts_review(client, db_session, submitted_event):
    """TC-215 (SCRUM-27 AC1): the assigned Coordinator moves a Submitted
    request to Under Review through the API."""
    event_id, assigned, _ = submitted_event

    login(client, assigned)
    resp = client.post(f"/events/{event_id}/review")

    assert resp.status_code == 200
    assert resp.json()["status"] == UNDER_REVIEW
    assert current_status(db_session, event_id) == UNDER_REVIEW


def test_tc216_starting_review_records_who_and_when(client, db_session, submitted_event):
    """TC-216 (SCRUM-27 AC3): the move writes one history row naming the actor
    and carrying a timestamp."""
    event_id, assigned, _ = submitted_event

    login(client, assigned)
    resp = client.post(f"/events/{event_id}/review", json={"reason": "Picking this up now"})

    assert resp.status_code == 200
    db_session.expire_all()
    history = db_session.query(EventStatusHistory).filter_by(
        event_id=event_id, new_status=UNDER_REVIEW
    ).one()
    assert history.previous_status == SUBMITTED
    assert history.actor.username == assigned
    assert history.reason == "Picking this up now"
    assert history.timestamp is not None


def test_tc217_default_reason_is_recorded_when_none_given(client, db_session, submitted_event):
    """TC-217 (SCRUM-27 AC3, boundary): omitting the note still records why the
    status changed, so the history is never blank."""
    event_id, assigned, _ = submitted_event

    login(client, assigned)
    resp = client.post(f"/events/{event_id}/review")

    assert resp.status_code == 200
    db_session.expire_all()
    history = db_session.query(EventStatusHistory).filter_by(
        event_id=event_id, new_status=UNDER_REVIEW
    ).one()
    assert history.reason == "Review started by coordinator"


# ---------- Authorisation ----------


def test_tc218_unassigned_coordinator_cannot_start_review(client, db_session, submitted_event):
    """TC-218 (SCRUM-27 AC1): only the Coordinator assigned to the request may
    start reviewing it. The request is left unchanged."""
    event_id, _, other = submitted_event

    login(client, other)
    resp = client.post(f"/events/{event_id}/review")

    assert resp.status_code == 403
    assert current_status(db_session, event_id) == SUBMITTED


def test_tc219_organiser_cannot_start_review(client, db_session, submitted_event):
    """TC-219 (SCRUM-27 AC1): starting review is a Coordinator action. The
    Organiser who owns the request cannot perform it."""
    event_id, _, _ = submitted_event

    login(client, "org")
    resp = client.post(f"/events/{event_id}/review")

    assert resp.status_code == 403
    assert current_status(db_session, event_id) == SUBMITTED


# ---------- Status guards ----------


@pytest.mark.parametrize("from_status", [DRAFT, RETURNED, APPROVED, REJECTED])
def test_tc220_cannot_start_review_from_a_non_submitted_status(
    client, db_session, submitted_event, from_status
):
    """TC-220 (SCRUM-27 AC1, boundary): review starts from Submitted only. Every
    other status is refused and left untouched."""
    event_id, assigned, _ = submitted_event
    set_status(db_session, event_id, from_status)

    login(client, assigned)
    resp = client.post(f"/events/{event_id}/review")

    assert resp.status_code == 400
    assert current_status(db_session, event_id) == from_status


def test_tc221_cannot_start_review_twice(client, db_session, submitted_event):
    """TC-221 (SCRUM-27 AC1, boundary): a request already Under Review cannot be
    moved there again, and the refusal says so."""
    event_id, assigned, _ = submitted_event
    login(client, assigned)
    assert client.post(f"/events/{event_id}/review").status_code == 200

    resp = client.post(f"/events/{event_id}/review")

    assert resp.status_code == 400
    assert "already has status" in resp.json()["detail"]
    assert current_status(db_session, event_id) == UNDER_REVIEW


def test_tc222_review_on_unknown_event_returns_404(client, people):
    """TC-222 (SCRUM-27, DoD item 16): starting review on an event that does not
    exist is refused with a message naming the problem."""
    login(client, "coord_a")

    resp = client.post("/events/9999/review")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Event not found"


# ---------- The flow the Coordinator list depends on (SCRUM-24 AC3) ----------


def test_tc223_starting_review_clears_awaiting_first_review(client, db_session, submitted_event):
    """TC-223 (SCRUM-24 AC3): a request stops counting as awaiting first review
    once the assigned Coordinator starts reviewing it. This is the transition
    that made that distinction meaningful, and it was unreachable before the
    endpoint was ported."""
    event_id, assigned, _ = submitted_event

    login(client, assigned)
    before = client.get("/coordinator/events")
    assert "Awaiting your first review" in before.text

    assert client.post(f"/events/{event_id}/review").status_code == 200
    after = client.get("/coordinator/events")

    assert "Awaiting your first review" not in after.text
    assert current_status(db_session, event_id) == UNDER_REVIEW
