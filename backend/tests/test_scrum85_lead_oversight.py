"""SCRUM-85: the Lead reassigns later events and sees every assignment.

Criteria, in the Product Owner's words:
  AC1  The Lead can reassign in Submitted, Under Review, Returned, Approved,
       Planning, Safety Review and Confirmed.
  AC2  Reassigning is refused in Draft, Rejected, Completed and Cancelled.
  AC3  Reassigning keeps bookings, equipment and status.
  AC4  Reassigning sends the existing notices.
  AC5  First assignment stays limited to Submitted, Under Review and Returned.
  AC6  GET /lead/events includes every active event with its Coordinator.
  AC7  Status filters work.
  AC8  Each Coordinator's assignments can be seen, inactive Coordinators too.
"""

import os
import sys
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.database import Base, get_db
from backend.main import app
from backend.models import (
    CoordinatorAssignmentHistory,
    CoordinatorReassignment,
    EquipmentRequest,
    Event,
    Notification,
    User,
    Venue,
    VenueBooking,
)
from backend.security import hash_password
from backend.status import (
    APPROVED,
    CANCELLED,
    COMPLETED,
    CONFIRMED,
    DRAFT,
    PLANNING,
    REJECTED,
    RETURNED,
    SAFETY_REVIEW,
    SUBMITTED,
    UNDER_REVIEW,
)

ACTIVE_STATUSES = [SUBMITTED, UNDER_REVIEW, RETURNED, APPROVED, PLANNING, SAFETY_REVIEW, CONFIRMED]
REFUSED_STATUSES = [DRAFT, REJECTED, COMPLETED, CANCELLED]
REASSIGN_NOTICES = {"reassigned_to_you", "reassigned_away", "coordinator_changed"}

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


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
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def people(db_session):
    def user(username, role, active=False):
        row = User(username=username, password_hash=hash_password("pass123"), role=role, is_active_coordinator=active)
        db_session.add(row)
        return row

    created = {
        "org": user("org", "organiser"),
        "coord_a": user("coord_a", "coordinator", active=True),
        "coord_b": user("coord_b", "coordinator", active=True),
        "coord_left": user("coord_left", "coordinator", active=False),
        "lead": user("lead", "lead"),
    }
    db_session.commit()
    return {username: row.id for username, row in created.items()}


def login(client, username):
    assert client.post("/auth/login", json={"username": username, "password": "pass123"}).status_code == 200


def make_event(db, people, status, coordinator="coord_a", title=None):
    event = Event(
        title=title or f"{status} event",
        organiser_id=people["org"],
        assigned_coordinator_id=people[coordinator] if coordinator else None,
        status=status,
        proposed_start=datetime(2099, 6, 1, 10, 0),
        expected_attendance=50,
        submitted_at=datetime(2099, 1, 1) if status != DRAFT else None,
    )
    db.add(event)
    db.commit()
    return event.id


def reload(db, event_id):
    db.expire_all()
    return db.get(Event, event_id)


def reassign(client, event_id, coordinator_id):
    return client.post(f"/events/{event_id}/reassign", json={"coordinator_id": coordinator_id})


# ---------- AC1: every status up to Confirmed ----------

@pytest.mark.parametrize("event_status", ACTIVE_STATUSES)
def test_tc8501_lead_reassigns_in_every_active_status(client, db_session, people, event_status):
    event_id = make_event(db_session, people, event_status)
    login(client, "lead")

    response = reassign(client, event_id, people["coord_b"])

    assert response.status_code == 200
    assert response.json()["assigned_coordinator_id"] == people["coord_b"]
    assert response.json()["previous_coordinator_id"] == people["coord_a"]
    assert reload(db_session, event_id).assigned_coordinator_id == people["coord_b"]
    record = db_session.query(CoordinatorReassignment).filter_by(event_id=event_id).one()
    assert (record.from_coordinator_id, record.to_coordinator_id) == (people["coord_a"], people["coord_b"])


def test_tc8502_the_new_coordinator_takes_over_an_approved_event(client, db_session, people):
    event_id = make_event(db_session, people, APPROVED)
    login(client, "lead")
    assert reassign(client, event_id, people["coord_b"]).status_code == 200

    login(client, "coord_a")
    assert event_id not in [row["id"] for row in client.get("/coordinator/events/data").json()]
    assert client.get(f"/events/{event_id}/bookings").status_code == 403

    login(client, "coord_b")
    assert event_id in [row["id"] for row in client.get("/coordinator/events/data").json()]
    assert client.get(f"/events/{event_id}/bookings").status_code == 200


@pytest.mark.parametrize("event_status", [APPROVED, CONFIRMED])
def test_tc8503_a_coordinator_cannot_reassign_a_later_event(client, db_session, people, event_status):
    event_id = make_event(db_session, people, event_status)
    login(client, "coord_a")

    response = reassign(client, event_id, people["coord_b"])

    assert response.status_code == 403
    assert reload(db_session, event_id).assigned_coordinator_id == people["coord_a"]


@pytest.mark.parametrize("target", ["coord_a", "coord_left"])
def test_tc8504_later_events_keep_the_coordinator_checks(client, db_session, people, target):
    # The same Coordinator, or one no longer active, is refused here as it is for a Submitted event.
    event_id = make_event(db_session, people, PLANNING)
    login(client, "lead")

    response = reassign(client, event_id, people[target])

    assert response.status_code == 400
    assert reload(db_session, event_id).assigned_coordinator_id == people["coord_a"]
    assert db_session.query(CoordinatorReassignment).count() == 0


# ---------- AC2: refused statuses ----------

@pytest.mark.parametrize("event_status", REFUSED_STATUSES)
def test_tc8505_reassigning_is_refused_and_changes_nothing(client, db_session, people, event_status):
    event_id = make_event(db_session, people, event_status)
    login(client, "lead")

    response = reassign(client, event_id, people["coord_b"])

    assert response.status_code == 400
    assert "reassigned" in response.json()["detail"]
    event = reload(db_session, event_id)
    assert event.assigned_coordinator_id == people["coord_a"]
    assert event.status == event_status
    assert db_session.query(CoordinatorReassignment).count() == 0
    assert db_session.query(Notification).count() == 0


# ---------- AC3: bookings, equipment and status stay ----------

def test_tc8506_reassigning_keeps_bookings_equipment_and_status(client, db_session, people):
    event_id = make_event(db_session, people, CONFIRMED)
    venue = Venue(name="Main Hall", location="Level 1", capacity=100)
    db_session.add(venue)
    db_session.flush()
    booking = VenueBooking(
        event_id=event_id,
        venue_id=venue.id,
        booking_start=datetime(2099, 6, 1, 2, 0),
        booking_end=datetime(2099, 6, 1, 4, 0),
        status="Approved",
        requester_id=people["coord_a"],
    )
    equipment = EquipmentRequest(event_id=event_id, equipment_type="Projector", quantity=2, requested_by_id=people["coord_a"])
    db_session.add_all([booking, equipment])
    db_session.commit()
    booking_id, equipment_id = booking.id, equipment.id
    login(client, "lead")

    response = reassign(client, event_id, people["coord_b"])

    assert response.status_code == 200
    assert response.json()["status"] == CONFIRMED
    db_session.expire_all()
    kept_booking = db_session.query(VenueBooking).filter_by(event_id=event_id).one()
    assert (kept_booking.id, kept_booking.status, kept_booking.requester_id) == (booking_id, "Approved", people["coord_a"])
    kept_equipment = db_session.query(EquipmentRequest).filter_by(event_id=event_id).one()
    assert (kept_equipment.id, kept_equipment.quantity, kept_equipment.requested_by_id) == (equipment_id, 2, people["coord_a"])
    assert db_session.get(Event, event_id).status == CONFIRMED

    # The new Coordinator can read what the old one arranged.
    login(client, "coord_b")
    assert [row["id"] for row in client.get(f"/events/{event_id}/bookings").json()] == [booking_id]
    equipment_lines = client.get(f"/events/{event_id}/equipment-requests").json()["equipment_requests"]
    assert [row["id"] for row in equipment_lines] == [equipment_id]


# ---------- AC4: the existing notices ----------

@pytest.mark.parametrize("event_status", [APPROVED, SAFETY_REVIEW])
def test_tc8507_all_three_people_get_the_existing_notices(client, db_session, people, event_status):
    event_id = make_event(db_session, people, event_status)
    login(client, "lead")

    assert reassign(client, event_id, people["coord_b"]).status_code == 200

    notices = {(row.user_id, row.kind): row for row in db_session.query(Notification).all()}
    assert set(notices) == {
        (people["coord_a"], "reassigned_away"),
        (people["coord_b"], "reassigned_to_you"),
        (people["org"], "coordinator_changed"),
    }
    assert notices[(people["coord_a"], "reassigned_away")].event_id is None
    assert notices[(people["coord_b"], "reassigned_to_you")].event_id == event_id
    assert "coord_b" in notices[(people["org"], "coordinator_changed")].message
    history = db_session.query(CoordinatorAssignmentHistory).filter_by(event_id=event_id).one()
    assert history.coordinator_id == people["coord_b"]


def test_tc8508_reassigning_back_and_forth_notifies_each_time(client, db_session, people):
    # The unique notice rule must not stop a second round on the same event.
    event_id = make_event(db_session, people, PLANNING)
    login(client, "lead")

    assert reassign(client, event_id, people["coord_b"]).status_code == 200
    assert reassign(client, event_id, people["coord_a"]).status_code == 200
    assert reassign(client, event_id, people["coord_b"]).status_code == 200

    db_session.expire_all()
    assert db_session.query(Notification).filter_by(user_id=people["coord_b"], kind="reassigned_to_you").count() == 2
    assert db_session.query(CoordinatorReassignment).filter_by(event_id=event_id).count() == 3


# ---------- AC5: first assignment stays limited ----------

@pytest.mark.parametrize("event_status", [APPROVED, PLANNING, SAFETY_REVIEW, CONFIRMED])
def test_tc8509_first_assignment_stays_limited(client, db_session, people, event_status):
    event_id = make_event(db_session, people, event_status, coordinator=None)
    login(client, "lead")

    assign_response = client.post(f"/events/{event_id}/assign", json={"coordinator_id": people["coord_b"]})
    reassign_response = reassign(client, event_id, people["coord_b"])

    assert assign_response.status_code == 400
    assert reassign_response.status_code == 400
    assert reload(db_session, event_id).assigned_coordinator_id is None
    assert db_session.query(Notification).count() == 0


# ---------- AC6: every active event with its Coordinator ----------

def test_tc8510_the_list_has_every_active_event_with_its_coordinator(client, db_session, people):
    ids = {event_status: make_event(db_session, people, event_status) for event_status in ACTIVE_STATUSES}
    draft = make_event(db_session, people, DRAFT, coordinator=None)
    login(client, "lead")

    response = client.get("/lead/events")

    assert response.status_code == 200
    rows = {row["id"]: row for row in response.json()}
    assert draft not in rows
    for event_status, event_id in ids.items():
        assert rows[event_id]["status"] == event_status
        assert rows[event_id]["assigned_coordinator_id"] == people["coord_a"]
        assert rows[event_id]["assigned_coordinator"] == "coord_a"
        assert rows[event_id]["assigned_coordinator_active"] is True


def test_tc8511_the_list_shows_the_new_coordinator_after_reassigning_a_confirmed_event(client, db_session, people):
    event_id = make_event(db_session, people, CONFIRMED)
    login(client, "lead")
    assert reassign(client, event_id, people["coord_b"]).status_code == 200

    row = next(row for row in client.get("/lead/events").json() if row["id"] == event_id)

    assert row["assigned_coordinator"] == "coord_b"
    assert row["status"] == CONFIRMED


# ---------- AC7: status filters ----------

@pytest.mark.parametrize("wanted", ACTIVE_STATUSES + [REJECTED, COMPLETED, CANCELLED])
def test_tc8512_the_status_filter_returns_only_that_status(client, db_session, people, wanted):
    ids = {event_status: make_event(db_session, people, event_status) for event_status in ACTIVE_STATUSES + [REJECTED, COMPLETED, CANCELLED]}
    login(client, "lead")

    response = client.get("/lead/events", params={"status": wanted})

    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [ids[wanted]]


def test_tc8513_a_status_with_no_events_is_an_empty_list(client, db_session, people):
    make_event(db_session, people, SUBMITTED)
    login(client, "lead")

    response = client.get("/lead/events", params={"status": CONFIRMED})

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize("wanted", [DRAFT, "approved", "Nonsense", ""])
def test_tc8514_a_status_the_list_never_holds_is_refused(client, db_session, people, wanted):
    # A mistyped filter must say so, not look like "nothing to do".
    make_event(db_session, people, APPROVED)
    login(client, "lead")

    response = client.get("/lead/events", params={"status": wanted})

    assert response.status_code == 400
    assert "status" in response.json()["detail"].lower()


# ---------- AC8: each Coordinator's assignments ----------

def test_tc8515_the_coordinator_filter_lists_all_their_assignments(client, db_session, people):
    theirs = [make_event(db_session, people, event_status, coordinator="coord_b") for event_status in ACTIVE_STATUSES]
    make_event(db_session, people, APPROVED, coordinator="coord_a")
    make_event(db_session, people, SUBMITTED, coordinator=None)
    login(client, "lead")

    response = client.get("/lead/events", params={"coordinator_id": people["coord_b"]})

    assert response.status_code == 200
    assert sorted(row["id"] for row in response.json()) == sorted(theirs)


def test_tc8516_both_filters_together_narrow_the_list(client, db_session, people):
    wanted = make_event(db_session, people, APPROVED, coordinator="coord_b")
    make_event(db_session, people, PLANNING, coordinator="coord_b")
    make_event(db_session, people, APPROVED, coordinator="coord_a")
    login(client, "lead")

    response = client.get("/lead/events", params={"status": APPROVED, "coordinator_id": people["coord_b"]})

    assert [row["id"] for row in response.json()] == [wanted]


def test_tc8517_an_inactive_coordinators_events_stay_visible_and_can_move(client, db_session, people):
    # /lead/coordinators lists active people only, so the row has to say this one has left.
    event_id = make_event(db_session, people, APPROVED, coordinator="coord_left")
    login(client, "lead")

    rows = client.get("/lead/events", params={"coordinator_id": people["coord_left"]}).json()

    assert [row["id"] for row in rows] == [event_id]
    assert rows[0]["assigned_coordinator"] == "coord_left"
    assert rows[0]["assigned_coordinator_active"] is False

    assert reassign(client, event_id, people["coord_b"]).status_code == 200
    assert client.get("/lead/events", params={"coordinator_id": people["coord_left"]}).json() == []
    assert reload(db_session, event_id).assigned_coordinator_id == people["coord_b"]


def test_tc8531_a_holder_who_changed_role_shows_as_no_longer_active(client, db_session, people):
    event_id = make_event(db_session, people, APPROVED)
    db_session.get(User, people["coord_a"]).role = "organiser"
    db_session.commit()
    login(client, "lead")

    row = next(row for row in client.get("/lead/events").json() if row["id"] == event_id)

    assert row["assigned_coordinator"] == "coord_a"
    assert row["assigned_coordinator_active"] is False


def test_tc8532_an_event_held_by_someone_who_changed_role_can_still_move(client, db_session, people):
    event_id = make_event(db_session, people, CONFIRMED)
    db_session.get(User, people["coord_a"]).role = "organiser"
    db_session.commit()
    login(client, "lead")

    response = reassign(client, event_id, people["coord_b"])

    assert response.status_code == 200
    assert response.json()["previous_coordinator_id"] == people["coord_a"]
    event = reload(db_session, event_id)
    assert event.assigned_coordinator_id == people["coord_b"]
    assert event.status == CONFIRMED
    assert db_session.query(CoordinatorReassignment).filter_by(event_id=event_id).count() == 1
    # They can't see the event any more, so they get no notice. The other two people do.
    kinds = {(row.user_id, row.kind) for row in db_session.query(Notification).all()}
    assert kinds == {(people["coord_b"], "reassigned_to_you"), (people["org"], "coordinator_changed")}


def test_tc8518_an_unassigned_event_shows_no_coordinator(client, db_session, people):
    event_id = make_event(db_session, people, SUBMITTED, coordinator=None)
    login(client, "lead")

    row = next(row for row in client.get("/lead/events").json() if row["id"] == event_id)

    assert row["assigned_coordinator"] is None
    assert row["assigned_coordinator_active"] is None


@pytest.mark.parametrize("username", ["org", "coord_a"])
def test_tc8519_only_the_lead_can_use_the_filters(client, db_session, people, username):
    make_event(db_session, people, APPROVED)
    login(client, username)

    assert client.get("/lead/events", params={"status": APPROVED}).status_code == 403
    assert client.get("/lead/events", params={"coordinator_id": people["coord_a"]}).status_code == 403
