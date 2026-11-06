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

Note on AC2: the amendment writes an EventStatusHistory row, which is an audit
trail, and TC-104 evidences that row. SCRUM-67 added the notice to the assigned
Coordinator, so the "is notified" half of AC2 is tested in
test_scrum67_change_notifications.py.
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
from backend.models import Event, EventStatusHistory, LoginSession, User
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
    """Two organisers, an active coordinator, and a lead for explicit assignment."""
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
    lead = User(
        username="lead_amend",
        password_hash=hash_password("pass123"),
        role="lead",
        is_active_coordinator=False,
    )
    db_session.add_all([organiser_a, organiser_b, coordinator, lead])
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
    # Main requires a Lead to assign requests before a Coordinator can review them.
    login(client, "lead_amend")
    coordinators = client.get("/lead/coordinators")
    assert coordinators.status_code == 200
    coordinator_id = next(c["id"] for c in coordinators.json() if c["username"] == "coord_amend")
    assigned = client.post(f"/events/{event_id}/assign", json={"coordinator_id": coordinator_id})
    assert assigned.status_code == 200
    login(client, username)
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
        "At least one field is required to amend the request"
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


# Additional amendment coverage from main.
@pytest.fixture(scope="function")
def main_db_session():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def main_client(main_db_session):
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
def main_seed_data(main_db_session):
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
    main_db_session.add_all([organiser1, organiser2, coord1])
    main_db_session.commit()

    # Create sessions
    s1 = LoginSession(user_id=organiser1.id, token="token_org1", valid=True)
    s2 = LoginSession(user_id=organiser2.id, token="token_org2", valid=True)
    sc = LoginSession(user_id=coord1.id, token="token_coord1", valid=True)
    main_db_session.add_all([s1, s2, sc])
    main_db_session.commit()

    return {
        "organiser1": organiser1,
        "organiser2": organiser2,
        "coord1": coord1,
    }


def main_create_and_submit_event(main_client, token="token_org1"):
    main_client.cookies.set("connectsphere_session", token)
    start = (datetime.utcnow() + timedelta(days=14)).isoformat()
    create_res = main_client.post(
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

    sub_res = main_client.post(f"/events/{event_id}/submit")
    assert sub_res.status_code == 200
    return event_id


# ---------------------------------------------------------------------------
# Test Cases: TC-101 to TC-115 (Elnathan - SCRUM-21)
# ---------------------------------------------------------------------------

def test_main_tc101_amend_while_submitted(main_client, main_db_session, main_seed_data):
    """TC-101 (SCRUM-21 AC1): Organiser amends own Submitted request."""
    event_id = main_create_and_submit_event(main_client)
    main_client.cookies.set("connectsphere_session", "token_org1")

    payload = {
        "title": "Updated Annual Gala",
        "expected_attendance": 150,
    }
    response = main_client.post(f"/events/{event_id}/amend", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated Annual Gala"
    assert data["expected_attendance"] == 150
    assert data["purpose"] == "Fundraising"  # untouched field preserved


def test_main_tc102_amend_while_under_review(main_client, main_db_session, main_seed_data):
    """TC-102 (SCRUM-21 AC1): Organiser amends own Under Review request."""
    event_id = main_create_and_submit_event(main_client)
    event = main_db_session.get(Event, event_id)
    event.status = UNDER_REVIEW
    main_db_session.commit()

    main_client.cookies.set("connectsphere_session", "token_org1")
    response = main_client.post(f"/events/{event_id}/amend", json={"title": "Under Review Gala"})
    assert response.status_code == 200
    assert response.json()["title"] == "Under Review Gala"


def test_main_tc103_status_unchanged(main_client, main_db_session, main_seed_data):
    """TC-103 (SCRUM-21 AC2): Amendment does not revert status to Draft."""
    event_id = main_create_and_submit_event(main_client)
    main_client.cookies.set("connectsphere_session", "token_org1")

    response = main_client.post(f"/events/{event_id}/amend", json={"title": "Title Modified"})
    assert response.status_code == 200
    assert response.json()["status"] == SUBMITTED

    # Check database
    main_db_session.expire_all()
    event = main_db_session.get(Event, event_id)
    assert event.status == SUBMITTED


def test_main_tc104_amendment_recorded_in_history(main_client, main_db_session, main_seed_data):
    """TC-104 (SCRUM-21 AC2): Amendment writes one status-history row."""
    event_id = main_create_and_submit_event(main_client)
    main_client.cookies.set("connectsphere_session", "token_org1")

    main_client.post(f"/events/{event_id}/amend", json={"title": "New Title", "expected_attendance": 200})

    main_db_session.expire_all()
    history = (
        main_db_session.query(EventStatusHistory)
        .filter_by(event_id=event_id)
        .order_by(EventStatusHistory.id.desc())
        .first()
    )
    assert history.previous_status == SUBMITTED
    assert history.new_status == SUBMITTED
    assert history.actor_id == main_seed_data["organiser1"].id
    assert "title" in history.reason
    assert "expected_attendance" in history.reason


def test_main_tc105_refused_after_approval(main_client, main_db_session, main_seed_data):
    """TC-105 (SCRUM-21 AC3): Amending an Approved request is refused."""
    event_id = main_create_and_submit_event(main_client)
    event = main_db_session.get(Event, event_id)
    event.status = APPROVED
    main_db_session.commit()

    main_client.cookies.set("connectsphere_session", "token_org1")
    response = main_client.post(f"/events/{event_id}/amend", json={"title": "Illegal Change"})
    assert response.status_code == 400
    assert "change request" in response.json()["detail"].lower()


def test_main_tc106_refused_after_rejection(main_client, main_db_session, main_seed_data):
    """TC-106 (SCRUM-21 AC3): Amending a Rejected request is refused."""
    event_id = main_create_and_submit_event(main_client)
    event = main_db_session.get(Event, event_id)
    event.status = REJECTED
    main_db_session.commit()

    main_client.cookies.set("connectsphere_session", "token_org1")
    response = main_client.post(f"/events/{event_id}/amend", json={"title": "Illegal Change"})
    assert response.status_code == 400
    assert "change request" in response.json()["detail"].lower()


def test_main_tc107_ownership_guard(main_client, main_db_session, main_seed_data):
    """TC-107 (SCRUM-21 AC4): An Organiser cannot amend another Organiser's request."""
    event_id = main_create_and_submit_event(main_client, token="token_org1")

    # Log in as Organiser B
    main_client.cookies.set("connectsphere_session", "token_org2")
    response = main_client.post(f"/events/{event_id}/amend", json={"title": "Hijacked Event"})
    assert response.status_code == 403
    assert "not authorized" in response.json()["detail"].lower()


def test_main_tc108_draft_amendment_refused(main_client, main_db_session, main_seed_data):
    """TC-108 (SCRUM-21 AC1 boundary): Amending a Draft is refused with the correct advice."""
    main_client.cookies.set("connectsphere_session", "token_org1")
    draft_res = main_client.post("/events", json={"title": "Draft Only", "expected_attendance": 50})
    event_id = draft_res.json()["id"]

    response = main_client.post(f"/events/{event_id}/amend", json={"title": "Trying to amend Draft"})
    assert response.status_code == 400
    assert "draft" in response.json()["detail"].lower()


def test_main_tc109_amend_while_returned(main_client, main_db_session, main_seed_data):
    """TC-109 (SCRUM-21 AC1): Organiser amends a Returned request."""
    event_id = main_create_and_submit_event(main_client)
    event = main_db_session.get(Event, event_id)
    event.status = RETURNED
    main_db_session.commit()

    main_client.cookies.set("connectsphere_session", "token_org1")
    response = main_client.post(
        f"/events/{event_id}/amend",
        json={"title": "Returned Gala", "equipment_requirements": "Sound board"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == RETURNED
    assert response.json()["equipment_requirements"] == "Sound board"


def test_main_tc110_coordinator_cannot_amend(main_client, main_db_session, main_seed_data):
    """TC-110 (SCRUM-21 DoD item 14): A Coordinator cannot amend an event request."""
    event_id = main_create_and_submit_event(main_client)
    main_client.cookies.set("connectsphere_session", "token_coord1")

    response = main_client.post(f"/events/{event_id}/amend", json={"title": "Coord trying to amend"})
    assert response.status_code == 403
    assert "Only organisers" in response.json()["detail"]


def test_main_tc111_unauthenticated_amendment_refused(main_client, main_db_session, main_seed_data):
    """TC-111 (SCRUM-21 DoD item 14): Unauthenticated amendment is refused."""
    event_id = main_create_and_submit_event(main_client)
    main_client.cookies.clear()

    response = main_client.post(f"/events/{event_id}/amend", json={"title": "No Auth"})
    assert response.status_code == 401


def test_main_tc112_event_not_found(main_client, main_seed_data):
    """TC-112 (SCRUM-21 DoD item 15): Amending an event that does not exist returns 404."""
    main_client.cookies.set("connectsphere_session", "token_org1")
    response = main_client.post("/events/999999/amend", json={"title": "Ghost Event"})
    assert response.status_code == 404
    assert response.json()["detail"] == "Event not found"


def test_main_tc113_invalid_values_refused(main_client, main_db_session, main_seed_data):
    """TC-113 (SCRUM-21 DoD item 15): Amendment with invalid values is refused."""
    event_id = main_create_and_submit_event(main_client)
    main_client.cookies.set("connectsphere_session", "token_org1")

    # 1. Blank title -> 422
    res_blank = main_client.post(f"/events/{event_id}/amend", json={"title": "   "})
    assert res_blank.status_code == 422

    # 2. Attendance <= 0 -> 400
    res_att = main_client.post(f"/events/{event_id}/amend", json={"expected_attendance": 0})
    assert res_att.status_code == 400

    # 3. Past date -> 400
    past_date = (datetime.utcnow() - timedelta(days=2)).isoformat()
    res_date = main_client.post(f"/events/{event_id}/amend", json={"proposed_start": past_date})
    assert res_date.status_code == 400


def test_main_tc114_empty_body_refused(main_client, main_db_session, main_seed_data):
    """TC-114 (SCRUM-21 DoD item 15): Amendment with an empty body is refused."""
    event_id = main_create_and_submit_event(main_client)
    main_client.cookies.set("connectsphere_session", "token_org1")

    response = main_client.post(f"/events/{event_id}/amend", json={})
    assert response.status_code == 400
    assert "at least one field" in response.json()["detail"].lower()


def test_main_tc115_privilege_escalation_prevented(main_client, main_db_session, main_seed_data):
    """TC-115 (SCRUM-21 DoD item 14): Workflow fields cannot be changed by an amendment."""
    event_id = main_create_and_submit_event(main_client)
    main_client.cookies.set("connectsphere_session", "token_org1")

    payload = {
        "title": "Legitimate Title",
        "status": "Approved",
        "organiser_id": main_seed_data["organiser2"].id,
        "assigned_coordinator_id": 9999,
    }
    response = main_client.post(f"/events/{event_id}/amend", json=payload)
    assert response.status_code == 200

    main_db_session.expire_all()
    event = main_db_session.get(Event, event_id)
    assert event.title == "Legitimate Title"
    assert event.status == SUBMITTED  # status was NOT changed to Approved
    assert event.organiser_id == main_seed_data["organiser1"].id  # organiser was NOT changed
