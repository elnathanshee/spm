"""SCRUM-39: Record equipment requirements for an event.

Acceptance criteria covered:
  AC1  The record captures equipment type, quantity and relevant technical
       requirements.
  AC2  The request is visible to Technical Support Staff.
  AC3  The request is associated with a specific event and its date and time.

Team decisions recorded here, so they can be justified in Week 13:
  D1  A new ``equipment_requests`` table rather than new columns on ``events``.
      ``create_all()`` adds missing tables on Neon but never alters existing
      ones, the same reason given for VenueBookingDecision (SCRUM-36) and
      VenueHold (SCRUM-81). The free-text ``events.equipment_requirements``
      column stays as it is: SCRUM-29 treats it as a significant field, so
      removing it would break that story.
  D2  One row per equipment type, so a request is a line item. Quantity must be
      a whole number above zero.
  D3  The same equipment type cannot be recorded twice on one event
      (case-insensitive). The Coordinator is told to amend the existing line
      instead. This mirrors the duplicate-name rule on venues (SCRUM-30).
  D4  Recording is a Coordinator action. A Coordinator may record only for an
      event assigned to them (Week 7 Change #5, as in SCRUM-34 and SCRUM-35);
      a Lead may record for any event.
  D5  Technical Support Staff read but never record, since AC2 makes them the
      audience. Reviewing and changing a request is SCRUM-40, not this story.
  D6  Role names follow auth.py, which accepts both "technical support" and
      "technical_support".
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
from backend.models import EquipmentRequest, Event, LoginSession, User
from backend.security import hash_password

TEST_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

EVENT_START = datetime(2026, 11, 18, 14, 30)


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
def users(db_session):
    """One account per role. Session token for each: tok_<username>."""
    specs = [
        ("coord_owner", "coordinator", True),
        ("coord_other", "coordinator", True),
        ("lead1", "lead", False),
        ("tech1", "technical support", False),
        ("venue1", "venue_staff", False),
        ("org1", "organiser", False),
        ("attendee1", "attendee", False),
    ]
    created = {}
    for username, role, active in specs:
        user = User(
            username=username,
            password_hash=hash_password("password123"),
            role=role,
            is_active_coordinator=active,
        )
        db_session.add(user)
        db_session.commit()
        db_session.add(LoginSession(user_id=user.id, token=f"tok_{username}", valid=True))
        db_session.commit()
        created[username] = user
    return created


@pytest.fixture
def event(db_session, users):
    """An approved event assigned to coord_owner, with a known date and time."""
    ev = Event(
        title="Annual Leadership Forum",
        proposed_start=EVENT_START,
        expected_attendance=150,
        organiser_id=users["org1"].id,
        assigned_coordinator_id=users["coord_owner"].id,
        status="Approved",
    )
    db_session.add(ev)
    db_session.commit()
    db_session.refresh(ev)
    return ev


@pytest.fixture
def other_event(db_session, users):
    """A second event, assigned to coord_other."""
    ev = Event(
        title="Partner Briefing",
        proposed_start=EVENT_START + timedelta(days=3),
        expected_attendance=40,
        organiser_id=users["org1"].id,
        assigned_coordinator_id=users["coord_other"].id,
        status="Approved",
    )
    db_session.add(ev)
    db_session.commit()
    db_session.refresh(ev)
    return ev


def login(client, token):
    client.cookies.clear()
    if token:
        client.cookies.set("connectsphere_session", token)


def record(client, token, event_id, body):
    login(client, token)
    return client.post(f"/events/{event_id}/equipment-requests", json=body)


def list_for_event(client, token, event_id):
    login(client, token)
    return client.get(f"/events/{event_id}/equipment-requests")


def list_all(client, token):
    login(client, token)
    return client.get("/equipment-requests")


PROJECTOR = {
    "equipment_type": "Projector",
    "quantity": 2,
    "technical_requirements": "HDMI and USB-C, 4K capable",
}


# ---------------------------------------------------------------------------
# AC1 - the record captures type, quantity and technical requirements
# ---------------------------------------------------------------------------


def test_tc3901_coordinator_records_an_equipment_requirement(client, db_session, event):
    """TC-3901 | Happy Path | AC1, AC3 (D1, D2)

    The assigned Coordinator records 2 projectors with technical notes.
    Expected: 201; all three values are stored and returned, against this event.
    """
    response = record(client, "tok_coord_owner", event.id, PROJECTOR)
    assert response.status_code == 201, response.text
    item = response.json()["equipment_request"]
    assert item["equipment_type"] == "Projector"
    assert item["quantity"] == 2
    assert item["technical_requirements"] == "HDMI and USB-C, 4K capable"
    assert item["event_id"] == event.id

    row = db_session.query(EquipmentRequest).filter(EquipmentRequest.event_id == event.id).one()
    assert row.equipment_type == "Projector"
    assert row.quantity == 2
    assert row.technical_requirements == "HDMI and USB-C, 4K capable"


def test_tc3902_technical_requirements_are_optional(client, db_session, event):
    """TC-3902 | Boundary Path | AC1 (D2)

    Equipment that needs no special notes is recorded without them.
    Expected: 201; technical_requirements is null, not an empty string.
    """
    response = record(client, "tok_coord_owner", event.id, {"equipment_type": "Flipchart", "quantity": 4})
    assert response.status_code == 201, response.text
    assert response.json()["equipment_request"]["technical_requirements"] is None


def test_tc3903_several_equipment_types_on_one_event(client, db_session, event):
    """TC-3903 | Workflow Variation | AC1 (D2)

    A Coordinator records three different items across separate calls.
    Expected: three line items on the event, listed in the order recorded.
    """
    for body in (
        PROJECTOR,
        {"equipment_type": "Microphone", "quantity": 6, "technical_requirements": "Lapel, wireless"},
        {"equipment_type": "Staging", "quantity": 1},
    ):
        assert record(client, "tok_coord_owner", event.id, body).status_code == 201

    listed = list_for_event(client, "tok_coord_owner", event.id).json()["equipment_requests"]
    assert [i["equipment_type"] for i in listed] == ["Projector", "Microphone", "Staging"]
    assert [i["quantity"] for i in listed] == [2, 6, 1]


def test_tc3904_values_are_trimmed(client, db_session, event):
    """TC-3904 | Boundary Path (partitioning) | AC1 (D2)

    Padded text is cleaned before storage, as venue fields are in SCRUM-30.
    Expected: stored as "Projector" and "Needs a long cable".
    """
    response = record(
        client,
        "tok_coord_owner",
        event.id,
        {"equipment_type": "  Projector  ", "quantity": 1, "technical_requirements": "  Needs a long cable  "},
    )
    assert response.status_code == 201
    item = response.json()["equipment_request"]
    assert item["equipment_type"] == "Projector"
    assert item["technical_requirements"] == "Needs a long cable"


def test_tc3905_blank_equipment_type_is_refused(client, db_session, event):
    """TC-3905 | Negative Path | AC1 (D2)

    Expected: 422 for a missing, empty or whitespace-only type; nothing stored.
    """
    for body in ({"quantity": 1}, {"equipment_type": "", "quantity": 1}, {"equipment_type": "   ", "quantity": 1}):
        assert record(client, "tok_coord_owner", event.id, body).status_code == 422, body
    assert db_session.query(EquipmentRequest).count() == 0


def test_tc3906_quantity_must_be_a_whole_number_above_zero(client, db_session, event):
    """TC-3906 | Negative Path | AC1 (D2)

    Expected: 422 for zero, negative, missing and fractional quantities;
    nothing stored.
    """
    for bad in (0, -1, -100, 1.5, "two", None):
        body = {"equipment_type": "Projector", "quantity": bad}
        assert record(client, "tok_coord_owner", event.id, body).status_code == 422, bad
    assert record(client, "tok_coord_owner", event.id, {"equipment_type": "Projector"}).status_code == 422
    assert db_session.query(EquipmentRequest).count() == 0


def test_tc3907_quantity_of_one_is_accepted(client, db_session, event):
    """TC-3907 | Boundary Path | AC1 (D2)

    One is the smallest sensible quantity, exactly at the limit.
    Expected: 201.
    """
    response = record(client, "tok_coord_owner", event.id, {"equipment_type": "Lectern", "quantity": 1})
    assert response.status_code == 201
    assert response.json()["equipment_request"]["quantity"] == 1


def test_tc3908_same_equipment_type_twice_is_refused(client, db_session, event):
    """TC-3908 | Negative Path | AC1 (D3)

    The second attempt uses different capitalisation and spacing.
    Expected: 409, a message naming the type, and still only one row.
    """
    assert record(client, "tok_coord_owner", event.id, PROJECTOR).status_code == 201
    clash = record(client, "tok_coord_owner", event.id, {"equipment_type": " projector ", "quantity": 5})
    assert clash.status_code == 409
    assert "Projector" in clash.json()["detail"]
    assert db_session.query(EquipmentRequest).count() == 1


def test_tc3909_same_type_on_a_different_event_is_allowed(client, db_session, event, other_event, users):
    """TC-3909 | Boundary Path | AC1, AC3 (D3: the rule is per event)

    Expected: both events may each have a Projector line.
    """
    assert record(client, "tok_coord_owner", event.id, PROJECTOR).status_code == 201
    assert record(client, "tok_coord_other", other_event.id, PROJECTOR).status_code == 201
    assert db_session.query(EquipmentRequest).count() == 2


# ---------------------------------------------------------------------------
# AC3 - associated with a specific event, and its date and time
# ---------------------------------------------------------------------------


def test_tc3910_request_carries_the_events_date_and_time(client, db_session, event):
    """TC-3910 | Happy Path | AC3

    Expected: the event's id, title and proposed start accompany the request,
    so Technical Support can see when the equipment is needed.
    """
    record(client, "tok_coord_owner", event.id, PROJECTOR)
    item = list_for_event(client, "tok_tech1", event.id).json()["equipment_requests"][0]
    assert item["event_id"] == event.id
    assert item["event_title"] == "Annual Leadership Forum"
    assert item["event_proposed_start"] == EVENT_START.isoformat()


def test_tc3911_event_without_a_date_still_lists(client, db_session, users):
    """TC-3911 | Boundary Path (missing info) | AC3

    An event whose date is not set yet. Expected: the request is recorded and
    the date reads as null rather than failing or showing a made-up date.
    """
    ev = Event(
        title="Date To Be Confirmed",
        organiser_id=users["org1"].id,
        assigned_coordinator_id=users["coord_owner"].id,
        status="Approved",
    )
    db_session.add(ev)
    db_session.commit()

    assert record(client, "tok_coord_owner", ev.id, PROJECTOR).status_code == 201
    item = list_for_event(client, "tok_tech1", ev.id).json()["equipment_requests"][0]
    assert item["event_proposed_start"] is None


def test_tc3912_recording_against_an_unknown_event(client, db_session, users):
    """TC-3912 | Negative Path | AC3

    Expected: 404 "Event not found"; nothing stored.
    """
    login(client, "tok_lead1")
    response = client.post("/events/99999/equipment-requests", json=PROJECTOR)
    assert response.status_code == 404
    assert response.json()["detail"] == "Event not found"
    assert db_session.query(EquipmentRequest).count() == 0


def test_tc3913_requests_are_not_mixed_between_events(client, db_session, event, other_event):
    """TC-3913 | Cross-Cutting | AC3

    Expected: each event's list holds only its own line items.
    """
    record(client, "tok_coord_owner", event.id, PROJECTOR)
    record(client, "tok_coord_other", other_event.id, {"equipment_type": "Microphone", "quantity": 1})

    first = list_for_event(client, "tok_tech1", event.id).json()["equipment_requests"]
    second = list_for_event(client, "tok_tech1", other_event.id).json()["equipment_requests"]
    assert [i["equipment_type"] for i in first] == ["Projector"]
    assert [i["equipment_type"] for i in second] == ["Microphone"]


# ---------------------------------------------------------------------------
# AC2 - visible to Technical Support Staff
# ---------------------------------------------------------------------------


def test_tc3914_technical_support_sees_requests_across_all_events(client, db_session, event, other_event):
    """TC-3914 | Happy Path | AC2 (D5)

    Two events each have equipment recorded. Expected: Technical Support Staff
    see both on one worklist, each naming its event and that event's date,
    earliest event first.
    """
    record(client, "tok_coord_other", other_event.id, {"equipment_type": "Microphone", "quantity": 6})
    record(client, "tok_coord_owner", event.id, PROJECTOR)

    response = list_all(client, "tok_tech1")
    assert response.status_code == 200
    items = response.json()["equipment_requests"]
    assert len(items) == 2
    assert [i["event_title"] for i in items] == ["Annual Leadership Forum", "Partner Briefing"]
    assert items[0]["event_proposed_start"] == EVENT_START.isoformat()


def test_tc3915_technical_support_sees_an_empty_worklist_honestly(client, db_session, users):
    """TC-3915 | Boundary Path | AC2

    Expected: 200 and an empty list, not an error.
    """
    response = list_all(client, "tok_tech1")
    assert response.status_code == 200
    assert response.json()["equipment_requests"] == []


def test_tc3916_underscore_spelling_of_the_role_also_works(client, db_session, users, event):
    """TC-3916 | Boundary Path | AC2 (D6)

    auth.py accepts both "technical support" and "technical_support".
    Expected: an account stored with the underscore spelling can read too.
    """
    users["tech1"].role = "technical_support"
    db_session.commit()
    record(client, "tok_coord_owner", event.id, PROJECTOR)
    assert list_all(client, "tok_tech1").status_code == 200


def test_tc3917_technical_support_cannot_record(client, db_session, event):
    """TC-3917 | Negative Path | AC2 (D5: recording is a Coordinator action)

    Expected: 403; nothing stored. Changing a request is SCRUM-40.
    """
    response = record(client, "tok_tech1", event.id, PROJECTOR)
    assert response.status_code == 403
    assert db_session.query(EquipmentRequest).count() == 0


# ---------------------------------------------------------------------------
# Who may record and read
# ---------------------------------------------------------------------------


def test_tc3918_coordinator_cannot_record_for_an_event_not_assigned_to_them(client, db_session, other_event):
    """TC-3918 | Negative Path | D4 (Week 7 Change #5)

    Expected: 403; nothing stored.
    """
    response = record(client, "tok_coord_owner", other_event.id, PROJECTOR)
    assert response.status_code == 403
    assert db_session.query(EquipmentRequest).count() == 0


def test_tc3919_lead_can_record_for_any_event(client, db_session, other_event):
    """TC-3919 | Workflow Variation | D4

    Expected: 201 even though the Lead is not the assigned Coordinator.
    """
    assert record(client, "tok_lead1", other_event.id, PROJECTOR).status_code == 201


def test_tc3920_external_and_unrelated_roles_cannot_record(client, db_session, event):
    """TC-3920 | Cross-Cutting | D4, DoD role-based authorisation

    Expected: 401 with no session; 403 for organiser, attendee and venue staff;
    nothing stored after any refusal.
    """
    assert record(client, None, event.id, PROJECTOR).status_code == 401
    for token in ("tok_org1", "tok_attendee1", "tok_venue1"):
        assert record(client, token, event.id, PROJECTOR).status_code == 403, token
    assert db_session.query(EquipmentRequest).count() == 0


def test_tc3921_only_internal_planning_roles_can_read(client, db_session, event):
    """TC-3921 | Cross-Cutting | AC2, DoD role-based authorisation

    Expected: Coordinator, Lead and Technical Support may read; no session 401;
    organiser and attendee 403.
    """
    record(client, "tok_coord_owner", event.id, PROJECTOR)

    for token in ("tok_coord_owner", "tok_lead1", "tok_tech1"):
        assert list_for_event(client, token, event.id).status_code == 200, token
        assert list_all(client, token).status_code == 200, token

    assert list_all(client, None).status_code == 401
    for token in ("tok_org1", "tok_attendee1"):
        assert list_for_event(client, token, event.id).status_code == 403, token
        assert list_all(client, token).status_code == 403, token


def test_tc3922_a_coordinators_worklist_shows_only_their_own_events(client, db_session, event, other_event):
    """TC-3922 | Cross-Cutting | AC2, D4

    Technical Support and the Lead see everything; a Coordinator sees only the
    events assigned to them.
    Expected: the worklist holds 2 items for Technical Support and the Lead,
    and 1 for each Coordinator.
    """
    record(client, "tok_coord_owner", event.id, PROJECTOR)
    record(client, "tok_coord_other", other_event.id, {"equipment_type": "Microphone", "quantity": 2})

    assert len(list_all(client, "tok_tech1").json()["equipment_requests"]) == 2
    assert len(list_all(client, "tok_lead1").json()["equipment_requests"]) == 2

    owner_items = list_all(client, "tok_coord_owner").json()["equipment_requests"]
    assert [i["event_title"] for i in owner_items] == ["Annual Leadership Forum"]
    other_items = list_all(client, "tok_coord_other").json()["equipment_requests"]
    assert [i["event_title"] for i in other_items] == ["Partner Briefing"]


def test_tc3923_the_recorder_is_kept_for_accountability(client, db_session, event, users):
    """TC-3923 | Cross-Cutting | DoD audit

    Expected: the request records who entered it and when.
    """
    record(client, "tok_coord_owner", event.id, PROJECTOR)
    row = db_session.query(EquipmentRequest).one()
    assert row.requested_by_id == users["coord_owner"].id
    assert row.created_at is not None
    item = list_for_event(client, "tok_tech1", event.id).json()["equipment_requests"][0]
    assert item["requested_by"] == "coord_owner"
