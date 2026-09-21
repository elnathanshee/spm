"""SCRUM-21: Organiser amends their own request while it is still awaiting review.

Acceptance criteria covered by this file:

AC1 - An Organiser can edit their own request while its status is Submitted,
      Under Review or Returned.
AC2 - After amendment the request keeps its current status (it does not revert
      to Draft), and the assigned Coordinator is notified that it changed.
AC3 - Attempting to amend after the request has been approved or rejected is
      refused, the existing request is left unchanged, and the Organiser is told
      to raise a change request instead.
AC4 - An Organiser cannot edit a request belonging to another Organiser.

Tests named _ac1 to _ac4 evidence those acceptance criteria. Tests named _dod14
and _dod15 evidence Definition of Done items rather than an AC:

DoD 14 - Role-based authorisation is enforced. A user cannot reach data outside
         their role and their relationship to the event.
DoD 15 - Errors are handled and surfaced, not swallowed. A refused action tells
         the user why.

Test case numbering starts at TC-101 so it does not collide with TC-001..TC-006
in test_spine.py.

Note on AC2: the notification stories (SCRUM-51, SCRUM-63) were dropped from
Sprint 1, so nothing in this system notifies anyone. The amendment writes an
EventStatusHistory row instead, which is an audit trail rather than a
notification. TC-104 evidences that audit row. The "is notified" half of AC2 is
not satisfied by this code.
"""

import os
import sys
from datetime import datetime, timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Setup path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.database import Base, get_db
from backend.main import app
from backend.models import Event, EventStatusHistory, User
from backend.security import hash_password
from backend.status import APPROVED, DRAFT, REJECTED, RETURNED, SUBMITTED, UNDER_REVIEW

# Use an in-memory SQLite database with StaticPool for tests.
# This file keeps its own engine so it is independent of test_spine.py.
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
    """Two organisers (AC4 needs a second one) and one active coordinator."""
    organiser_a = User(
        username="org_a_amend",
        password_hash=hash_password("pass123"),
        role="organiser",
        is_active_coordinator=False,
    )
    organiser_b = User(
        username="org_b_amend",
        password_hash=hash_password("pass123"),
        role="organiser",
        is_active_coordinator=False,
    )
    coordinator = User(
        username="coord_amend",
        password_hash=hash_password("pass123"),
        role="coordinator",
        is_active_coordinator=True,
    )
    db_session.add_all([organiser_a, organiser_b, coordinator])
    db_session.commit()
    return {
        "organiser_a_id": organiser_a.id,
        "organiser_b_id": organiser_b.id,
        "coordinator_id": coordinator.id,
    }


# --- Helpers ---------------------------------------------------------------


def login(client, username):
    """Log in. The TestClient stores the session cookie, so later requests on the
    same client act as this user until another login replaces the cookie."""
    response = client.post("/auth/login", json={"username": username, "password": "pass123"})
    assert response.status_code == 200
    return response


def future_time(days=10):
    return (datetime.utcnow() + timedelta(days=days)).isoformat()


def create_draft(client, title="Original Title", **extra):
    """Create a Draft event as whoever is currently logged in. Returns its id."""
    payload = {
        "title": title,
        "purpose": "Original purpose",
        "description": "Original description",
        "proposed_start": future_time(),
        "expected_attendance": 100,
        "venue_requirements": "Main Hall",
    }
    payload.update(extra)
    response = client.post("/events", json=payload)
    assert response.status_code == 201
    return response.json()["id"]


def create_submitted_event(client, username="org_a_amend", **extra):
    """Create a draft and submit it, so it ends up in Submitted. Returns its id."""
    login(client, username)
    event_id = create_draft(client, **extra)
    response = client.post(f"/events/{event_id}/submit")
    assert response.status_code == 200
    assert response.json()["status"] == SUBMITTED
    return event_id


def read_event_fresh(db_session, event_id):
    """Re-read an event, discarding anything this test session has cached, so
    assertions see what the API actually committed from its own session."""
    db_session.expire_all()
    return db_session.query(Event).filter(Event.id == event_id).first()


def snapshot(event):
    """The amendable fields of an event, for proving a refused amendment
    changed nothing."""
    return (
        event.title,
        event.purpose,
        event.description,
        event.proposed_start,
        event.expected_attendance,
        event.venue_requirements,
        event.accessibility_needs,
        event.equipment_requirements,
        event.registration_needs,
        event.status,
    )


# --- AC1: an Organiser can edit their own request while awaiting review -----


def test_tc101_organiser_amends_own_submitted_request_ac1(client, seed_data, db_session):
    """TC-101 (SCRUM-21 AC1): Organiser edits their own request while its status
    is Submitted. The supplied fields change and the untouched fields survive."""
    event_id = create_submitted_event(client)

    response = client.post(
        f"/events/{event_id}/amend",
        json={"title": "Corrected Title", "expected_attendance": 250},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Corrected Title"
    assert data["expected_attendance"] == 250
    assert sorted(data["amended_fields"]) == ["expected_attendance", "title"]

    # The change reached the database, and the fields we did not send are intact.
    event = read_event_fresh(db_session, event_id)
    assert event.title == "Corrected Title"
    assert event.expected_attendance == 250
    assert event.purpose == "Original purpose"
    assert event.description == "Original description"
    assert event.venue_requirements == "Main Hall"


def test_tc102_organiser_amends_own_under_review_request_ac1(client, seed_data, db_session):
    """TC-102 (SCRUM-21 AC1): Organiser edits their own request while its status
    is Under Review.

    No endpoint in the current codebase sets status to Under Review - the
    constant exists in status.py but nothing assigns it. The status is therefore
    set directly through the database session here. This test proves the guard
    accepts Under Review; it cannot prove the end-to-end workflow, because that
    workflow does not exist yet.
    """
    event_id = create_submitted_event(client)

    event = read_event_fresh(db_session, event_id)
    event.status = UNDER_REVIEW
    db_session.commit()

    response = client.post(
        f"/events/{event_id}/amend",
        json={"title": "Corrected While Under Review"},
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Corrected While Under Review"
    assert response.json()["status"] == UNDER_REVIEW

    event = read_event_fresh(db_session, event_id)
    assert event.title == "Corrected While Under Review"
    assert event.status == UNDER_REVIEW


def test_tc108_amend_draft_request_is_refused_ac1(client, seed_data, db_session):
    """TC-108 (SCRUM-21 AC1, boundary): A Draft request has not been submitted
    yet, so it is outside the window AC1 covers and amending it is refused. The
    message must explain the real rule and must NOT tell the Organiser to raise
    a change request, which would be wrong advice for a Draft."""
    login(client, "org_a_amend")
    event_id = create_draft(client)

    before = snapshot(read_event_fresh(db_session, event_id))

    response = client.post(f"/events/{event_id}/amend", json={"title": "Should Not Apply"})

    assert response.status_code == 400
    detail = response.json()["detail"]
    # The refusal names the status the request is actually in (DoD item 15).
    assert "This request has status 'Draft' and cannot be amended." in detail
    # It states the rule.
    assert "Submitted, Under Review or Returned" in detail
    # It gives a Draft the advice that applies to a Draft.
    assert "A Draft should be completed and submitted instead." in detail
    # It does not misdirect the Organiser to the change request route.
    assert "change request" not in detail

    assert snapshot(read_event_fresh(db_session, event_id)) == before


def test_tc109_organiser_amends_own_returned_request_ac1(client, seed_data, db_session):
    """TC-109 (SCRUM-21 AC1): Organiser edits their own request while its status
    is Returned. Returned is one of the three statuses AC1 allows, and the
    request keeps that status after the amendment rather than reverting."""
    event_id = create_submitted_event(client)

    login(client, "coord_amend")
    returned = client.post(f"/events/{event_id}/return", json={"reason": "Need AV details."})
    assert returned.status_code == 200
    assert returned.json()["status"] == RETURNED

    login(client, "org_a_amend")

    response = client.post(
        f"/events/{event_id}/amend",
        json={
            "title": "Corrected While Returned",
            "equipment_requirements": "Projector and two radio mics",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Corrected While Returned"
    assert data["equipment_requirements"] == "Projector and two radio mics"
    assert data["status"] == RETURNED
    assert sorted(data["amended_fields"]) == ["equipment_requirements", "title"]

    event = read_event_fresh(db_session, event_id)
    assert event.title == "Corrected While Returned"
    assert event.equipment_requirements == "Projector and two radio mics"
    assert event.status == RETURNED


# --- DoD item 15: errors are handled and surfaced, not swallowed -----------
#
# A refused action tells the user why. Every 400 or 403 carries a message a
# human can act on. These are not acceptance criteria; they evidence the
# Definition of Done.


def test_tc112_amend_nonexistent_event_dod15(client, seed_data):
    """TC-112 (SCRUM-21, DoD item 15): Amending an event that does not exist is
    refused with 404 and a message that names the problem."""
    login(client, "org_a_amend")

    response = client.post("/events/999999/amend", json={"title": "Ghost Event"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Event not found"


def test_tc113_amend_with_invalid_values_is_refused_dod15(client, seed_data, db_session):
    """TC-113 (SCRUM-21, DoD item 15): The same business rules that guard
    creation also guard amendment, and each refusal says which rule was broken.
    Each refusal leaves the request unchanged."""
    event_id = create_submitted_event(client)
    before = snapshot(read_event_fresh(db_session, event_id))

    # Blank title is rejected by the EventAmend validator.
    blank_title = client.post(f"/events/{event_id}/amend", json={"title": "   "})
    assert blank_title.status_code in [400, 422]
    assert snapshot(read_event_fresh(db_session, event_id)) == before

    # Attendance must be greater than zero.
    bad_attendance = client.post(f"/events/{event_id}/amend", json={"expected_attendance": 0})
    assert bad_attendance.status_code == 400
    assert "Expected attendance must be greater than 0" in bad_attendance.json()["detail"]
    assert snapshot(read_event_fresh(db_session, event_id)) == before

    # A start time in the past is rejected.
    past = (datetime.utcnow() - timedelta(days=1)).isoformat()
    bad_start = client.post(f"/events/{event_id}/amend", json={"proposed_start": past})
    assert bad_start.status_code == 400
    assert "past" in bad_start.json()["detail"]
    assert snapshot(read_event_fresh(db_session, event_id)) == before


def test_tc114_amend_with_empty_body_is_refused_dod15(client, seed_data, db_session):
    """TC-114 (SCRUM-21, DoD item 15): An amendment that supplies no fields is
    refused with a message telling the Organiser what to do about it."""
    event_id = create_submitted_event(client)
    before = snapshot(read_event_fresh(db_session, event_id))

    response = client.post(f"/events/{event_id}/amend", json={})

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "No fields supplied to amend. Send at least one field to change."
    )
    assert snapshot(read_event_fresh(db_session, event_id)) == before


# --- AC2: status is kept, and the change is recorded -----------------------


def test_tc103_status_is_unchanged_after_amendment_ac2(client, seed_data, db_session):
    """TC-103 (SCRUM-21 AC2): After amendment the request keeps its current
    status and does not revert to Draft."""
    event_id = create_submitted_event(client)

    response = client.post(f"/events/{event_id}/amend", json={"title": "Still Submitted"})

    assert response.status_code == 200
    assert response.json()["status"] == SUBMITTED
    assert response.json()["status"] != DRAFT

    event = read_event_fresh(db_session, event_id)
    assert event.status == SUBMITTED


def test_tc104_amendment_writes_status_history_row_ac2(client, seed_data, db_session):
    """TC-104 (SCRUM-21 AC2, partial): The amendment is recorded in
    EventStatusHistory as the audit trail standing in for the Coordinator
    notification, which cannot be built while SCRUM-51 and SCRUM-63 are out of
    Sprint 1.

    This evidences the "keeps its current status" half of AC2. It does NOT
    evidence the "assigned Coordinator is notified" half: nothing is delivered
    to the Coordinator, and they would only see this by opening the request.
    """
    event_id = create_submitted_event(client)

    db_session.expire_all()
    rows_before = (
        db_session.query(EventStatusHistory)
        .filter(EventStatusHistory.event_id == event_id)
        .count()
    )

    response = client.post(
        f"/events/{event_id}/amend",
        json={"title": "Audited Title", "expected_attendance": 300},
    )
    assert response.status_code == 200

    db_session.expire_all()
    rows = (
        db_session.query(EventStatusHistory)
        .filter(EventStatusHistory.event_id == event_id)
        .order_by(EventStatusHistory.id.asc())
        .all()
    )

    # Exactly one new row for the amendment.
    assert len(rows) == rows_before + 1

    amendment_row = rows[-1]
    # Same status on both sides is the evidence that the status did not change.
    assert amendment_row.previous_status == SUBMITTED
    assert amendment_row.new_status == SUBMITTED
    # The Organiser who made the change is recorded.
    assert amendment_row.actor_id == seed_data["organiser_a_id"]
    # The reason names what actually changed.
    assert "amended by organiser" in amendment_row.reason
    assert "title" in amendment_row.reason
    assert "expected_attendance" in amendment_row.reason
    assert amendment_row.timestamp is not None


# --- AC3: amending after a decision is refused -----------------------------


def test_tc105_amend_approved_request_is_refused_ac3(client, seed_data, db_session):
    """TC-105 (SCRUM-21 AC3): Amending an Approved request is refused, the
    request is left unchanged, and the Organiser is told to raise a change
    request instead."""
    event_id = create_submitted_event(client)

    login(client, "coord_amend")
    approved = client.post(f"/events/{event_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == APPROVED

    login(client, "org_a_amend")
    before = snapshot(read_event_fresh(db_session, event_id))

    response = client.post(
        f"/events/{event_id}/amend",
        json={"title": "Too Late", "expected_attendance": 999},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "approved" in detail
    assert "change request" in detail

    # The existing request is untouched, including its status.
    assert snapshot(read_event_fresh(db_session, event_id)) == before


def test_tc106_amend_rejected_request_is_refused_ac3(client, seed_data, db_session):
    """TC-106 (SCRUM-21 AC3): Amending a Rejected request is refused, the request
    is left unchanged, and the Organiser is told to raise a change request."""
    event_id = create_submitted_event(client)

    login(client, "coord_amend")
    rejected = client.post(f"/events/{event_id}/reject", json={"reason": "Clashes with exams."})
    assert rejected.status_code == 200
    assert rejected.json()["status"] == REJECTED

    login(client, "org_a_amend")
    before = snapshot(read_event_fresh(db_session, event_id))

    response = client.post(
        f"/events/{event_id}/amend",
        json={"title": "Too Late", "expected_attendance": 999},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "rejected" in detail
    assert "change request" in detail

    assert snapshot(read_event_fresh(db_session, event_id)) == before


# --- AC4: authorisation -----------------------------------------------------


def test_tc107_organiser_cannot_amend_another_organisers_request_ac4(client, seed_data, db_session):
    """TC-107 (SCRUM-21 AC4): Organiser B cannot edit a request owned by
    Organiser A. The request is left unchanged."""
    event_id = create_submitted_event(client, username="org_a_amend")
    before = snapshot(read_event_fresh(db_session, event_id))

    login(client, "org_b_amend")

    response = client.post(
        f"/events/{event_id}/amend",
        json={"title": "Hijacked Title"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "You are not authorized to amend this event request"

    assert snapshot(read_event_fresh(db_session, event_id)) == before


# --- DoD item 14: role-based authorisation is enforced ---------------------
#
# A user cannot reach data outside their role and their relationship to the
# event. AC4 covers one Organiser against another; these cover the rest of the
# surface, and evidence the Definition of Done rather than an AC.


def test_tc110_coordinator_cannot_amend_dod14(client, seed_data, db_session):
    """TC-110 (SCRUM-21, DoD item 14): Amendment is an Organiser action. A
    Coordinator reviewing the request cannot edit its contents, even though the
    request is assigned to them."""
    event_id = create_submitted_event(client)
    before = snapshot(read_event_fresh(db_session, event_id))

    login(client, "coord_amend")

    response = client.post(f"/events/{event_id}/amend", json={"title": "Coordinator Edit"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Only organisers can amend event requests"

    assert snapshot(read_event_fresh(db_session, event_id)) == before


def test_tc111_unauthenticated_amend_is_refused_dod14(client, seed_data, db_session):
    """TC-111 (SCRUM-21, DoD item 14): Amendment is not reachable while logged
    out. Authentication is checked before the request is even looked up."""
    event_id = create_submitted_event(client)
    before = snapshot(read_event_fresh(db_session, event_id))

    logout = client.post("/auth/logout")
    assert logout.status_code == 200

    response = client.post(f"/events/{event_id}/amend", json={"title": "Anonymous Edit"})

    assert response.status_code == 401
    assert response.json()["detail"] in ["Not authenticated", "Invalid or expired session"]

    assert snapshot(read_event_fresh(db_session, event_id)) == before


def test_tc115_amendment_cannot_change_workflow_fields_dod14(client, seed_data, db_session):
    """TC-115 (SCRUM-21, DoD item 14): An Organiser cannot use the amendment
    body to escalate privilege by changing workflow columns. EventAmend declares
    only the content fields, and Pydantic discards keys it does not declare, so
    status, organiser_id and assigned_coordinator_id never reach the event."""
    event_id = create_submitted_event(client)

    event_before = read_event_fresh(db_session, event_id)
    coordinator_before = event_before.assigned_coordinator_id
    owner_before = event_before.organiser_id

    response = client.post(
        f"/events/{event_id}/amend",
        json={
            "title": "Legitimate Correction",
            "status": APPROVED,
            "organiser_id": seed_data["organiser_b_id"],
            "assigned_coordinator_id": 9999,
        },
    )

    # The legitimate part of the amendment succeeds.
    assert response.status_code == 200
    assert response.json()["amended_fields"] == ["title"]

    # The workflow columns are untouched.
    event = read_event_fresh(db_session, event_id)
    assert event.title == "Legitimate Correction"
    assert event.status == SUBMITTED
    assert event.organiser_id == owner_before
    assert event.assigned_coordinator_id == coordinator_before
