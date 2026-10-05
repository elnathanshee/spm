"""SCRUM-21: Organiser amends own event request.

Acceptance criteria covered:
  AC1  Organiser can amend details while request is Submitted, Under Review, or Returned.
  AC2  Amendment does not reset status to Draft; creates an audit status-history record.
  AC3  Amending Approved or Rejected requests is refused; Organiser advised to raise change request.
  AC4  Only the Organiser who owns the request may amend it.
  DoD  Role authorization, authentication, error surfacing, boundary checks, and privilege escalation prevention.
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
from backend.models import Event, EventStatusHistory, LoginSession, User
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
def seed_data(db_session):
    organiser1 = User(
        username="organiser1",
        password_hash=hash_password("pass123"),
        role="organiser",
        is_active_coordinator=False,
    )
    organiser2 = User(
        username="organiser2",
        password_hash=hash_password("pass123"),
        role="organiser",
        is_active_coordinator=False,
    )
    coord1 = User(
        username="coord1",
        password_hash=hash_password("pass123"),
        role="coordinator",
        is_active_coordinator=True,
    )
    db_session.add_all([organiser1, organiser2, coord1])
    db_session.commit()

    # Create sessions
    s1 = LoginSession(user_id=organiser1.id, token="token_org1", valid=True)
    s2 = LoginSession(user_id=organiser2.id, token="token_org2", valid=True)
    sc = LoginSession(user_id=coord1.id, token="token_coord1", valid=True)
    db_session.add_all([s1, s2, sc])
    db_session.commit()

    return {
        "organiser1": organiser1,
        "organiser2": organiser2,
        "coord1": coord1,
    }


def create_and_submit_event(client, token="token_org1"):
    client.cookies.set("connectsphere_session", token)
    start = (datetime.utcnow() + timedelta(days=14)).isoformat()
    create_res = client.post(
        "/events",
        json={
            "title": "Original Gala",
            "purpose": "Fundraising",
            "description": "Annual Charity Gala",
            "proposed_start": start,
            "expected_attendance": 100,
        },
    )
    assert create_res.status_code == 201
    event_id = create_res.json()["id"]

    sub_res = client.post(f"/events/{event_id}/submit")
    assert sub_res.status_code == 200
    return event_id


# ---------------------------------------------------------------------------
# Test Cases: TC-101 to TC-115 (Elnathan - SCRUM-21)
# ---------------------------------------------------------------------------

def test_tc101_amend_while_submitted(client, db_session, seed_data):
    """TC-101 (SCRUM-21 AC1): Organiser amends own Submitted request."""
    event_id = create_and_submit_event(client)
    client.cookies.set("connectsphere_session", "token_org1")

    payload = {
        "title": "Updated Annual Gala",
        "expected_attendance": 150,
    }
    response = client.post(f"/events/{event_id}/amend", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated Annual Gala"
    assert data["expected_attendance"] == 150
    assert data["purpose"] == "Fundraising"  # untouched field preserved


def test_tc102_amend_while_under_review(client, db_session, seed_data):
    """TC-102 (SCRUM-21 AC1): Organiser amends own Under Review request."""
    event_id = create_and_submit_event(client)
    event = db_session.get(Event, event_id)
    event.status = UNDER_REVIEW
    db_session.commit()

    client.cookies.set("connectsphere_session", "token_org1")
    response = client.post(f"/events/{event_id}/amend", json={"title": "Under Review Gala"})
    assert response.status_code == 200
    assert response.json()["title"] == "Under Review Gala"


def test_tc103_status_unchanged(client, db_session, seed_data):
    """TC-103 (SCRUM-21 AC2): Amendment does not revert status to Draft."""
    event_id = create_and_submit_event(client)
    client.cookies.set("connectsphere_session", "token_org1")

    response = client.post(f"/events/{event_id}/amend", json={"title": "Title Modified"})
    assert response.status_code == 200
    assert response.json()["status"] == SUBMITTED

    # Check database
    db_session.expire_all()
    event = db_session.get(Event, event_id)
    assert event.status == SUBMITTED


def test_tc104_amendment_recorded_in_history(client, db_session, seed_data):
    """TC-104 (SCRUM-21 AC2): Amendment writes one status-history row."""
    event_id = create_and_submit_event(client)
    client.cookies.set("connectsphere_session", "token_org1")

    client.post(f"/events/{event_id}/amend", json={"title": "New Title", "expected_attendance": 200})

    db_session.expire_all()
    history = (
        db_session.query(EventStatusHistory)
        .filter_by(event_id=event_id)
        .order_by(EventStatusHistory.id.desc())
        .first()
    )
    assert history.previous_status == SUBMITTED
    assert history.new_status == SUBMITTED
    assert history.actor_id == seed_data["organiser1"].id
    assert "title" in history.reason
    assert "expected_attendance" in history.reason


def test_tc105_refused_after_approval(client, db_session, seed_data):
    """TC-105 (SCRUM-21 AC3): Amending an Approved request is refused."""
    event_id = create_and_submit_event(client)
    event = db_session.get(Event, event_id)
    event.status = APPROVED
    db_session.commit()

    client.cookies.set("connectsphere_session", "token_org1")
    response = client.post(f"/events/{event_id}/amend", json={"title": "Illegal Change"})
    assert response.status_code == 400
    assert "change request" in response.json()["detail"].lower()


def test_tc106_refused_after_rejection(client, db_session, seed_data):
    """TC-106 (SCRUM-21 AC3): Amending a Rejected request is refused."""
    event_id = create_and_submit_event(client)
    event = db_session.get(Event, event_id)
    event.status = REJECTED
    db_session.commit()

    client.cookies.set("connectsphere_session", "token_org1")
    response = client.post(f"/events/{event_id}/amend", json={"title": "Illegal Change"})
    assert response.status_code == 400
    assert "change request" in response.json()["detail"].lower()


def test_tc107_ownership_guard(client, db_session, seed_data):
    """TC-107 (SCRUM-21 AC4): An Organiser cannot amend another Organiser's request."""
    event_id = create_and_submit_event(client, token="token_org1")

    # Log in as Organiser B
    client.cookies.set("connectsphere_session", "token_org2")
    response = client.post(f"/events/{event_id}/amend", json={"title": "Hijacked Event"})
    assert response.status_code == 403
    assert "not authorized" in response.json()["detail"].lower()


def test_tc108_draft_amendment_refused(client, db_session, seed_data):
    """TC-108 (SCRUM-21 AC1 boundary): Amending a Draft is refused with the correct advice."""
    client.cookies.set("connectsphere_session", "token_org1")
    draft_res = client.post("/events", json={"title": "Draft Only", "expected_attendance": 50})
    event_id = draft_res.json()["id"]

    response = client.post(f"/events/{event_id}/amend", json={"title": "Trying to amend Draft"})
    assert response.status_code == 400
    assert "draft" in response.json()["detail"].lower()


def test_tc109_amend_while_returned(client, db_session, seed_data):
    """TC-109 (SCRUM-21 AC1): Organiser amends a Returned request."""
    event_id = create_and_submit_event(client)
    event = db_session.get(Event, event_id)
    event.status = RETURNED
    db_session.commit()

    client.cookies.set("connectsphere_session", "token_org1")
    response = client.post(
        f"/events/{event_id}/amend",
        json={"title": "Returned Gala", "equipment_requirements": "Sound board"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == RETURNED
    assert response.json()["equipment_requirements"] == "Sound board"


def test_tc110_coordinator_cannot_amend(client, db_session, seed_data):
    """TC-110 (SCRUM-21 DoD item 14): A Coordinator cannot amend an event request."""
    event_id = create_and_submit_event(client)
    client.cookies.set("connectsphere_session", "token_coord1")

    response = client.post(f"/events/{event_id}/amend", json={"title": "Coord trying to amend"})
    assert response.status_code == 403
    assert "Only organisers" in response.json()["detail"]


def test_tc111_unauthenticated_amendment_refused(client, db_session, seed_data):
    """TC-111 (SCRUM-21 DoD item 14): Unauthenticated amendment is refused."""
    event_id = create_and_submit_event(client)
    client.cookies.clear()

    response = client.post(f"/events/{event_id}/amend", json={"title": "No Auth"})
    assert response.status_code == 401


def test_tc112_event_not_found(client, seed_data):
    """TC-112 (SCRUM-21 DoD item 15): Amending an event that does not exist returns 404."""
    client.cookies.set("connectsphere_session", "token_org1")
    response = client.post("/events/999999/amend", json={"title": "Ghost Event"})
    assert response.status_code == 404
    assert response.json()["detail"] == "Event not found"


def test_tc113_invalid_values_refused(client, db_session, seed_data):
    """TC-113 (SCRUM-21 DoD item 15): Amendment with invalid values is refused."""
    event_id = create_and_submit_event(client)
    client.cookies.set("connectsphere_session", "token_org1")

    # 1. Blank title -> 422
    res_blank = client.post(f"/events/{event_id}/amend", json={"title": "   "})
    assert res_blank.status_code == 422

    # 2. Attendance <= 0 -> 400
    res_att = client.post(f"/events/{event_id}/amend", json={"expected_attendance": 0})
    assert res_att.status_code == 400

    # 3. Past date -> 400
    past_date = (datetime.utcnow() - timedelta(days=2)).isoformat()
    res_date = client.post(f"/events/{event_id}/amend", json={"proposed_start": past_date})
    assert res_date.status_code == 400


def test_tc114_empty_body_refused(client, db_session, seed_data):
    """TC-114 (SCRUM-21 DoD item 15): Amendment with an empty body is refused."""
    event_id = create_and_submit_event(client)
    client.cookies.set("connectsphere_session", "token_org1")

    response = client.post(f"/events/{event_id}/amend", json={})
    assert response.status_code == 400
    assert "at least one field" in response.json()["detail"].lower()


def test_tc115_privilege_escalation_prevented(client, db_session, seed_data):
    """TC-115 (SCRUM-21 DoD item 14): Workflow fields cannot be changed by an amendment."""
    event_id = create_and_submit_event(client)
    client.cookies.set("connectsphere_session", "token_org1")

    payload = {
        "title": "Legitimate Title",
        "status": "Approved",
        "organiser_id": seed_data["organiser2"].id,
        "assigned_coordinator_id": 9999,
    }
    response = client.post(f"/events/{event_id}/amend", json=payload)
    assert response.status_code == 200

    db_session.expire_all()
    event = db_session.get(Event, event_id)
    assert event.title == "Legitimate Title"
    assert event.status == SUBMITTED  # status was NOT changed to Approved
    assert event.organiser_id == seed_data["organiser1"].id  # organiser was NOT changed
