"""SCRUM-23: Event Coordinator Lead reassigns an event to a different Coordinator.

Each test names the acceptance criterion it proves (numbering as in
backend/reassignment.py).
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
from backend.models import (
    CoordinatorAssignmentHistory,
    CoordinatorReassignment,
    Event,
    Notification,
    User,
)
from backend.security import hash_password
from backend.status import APPROVED, COMPLETED, REJECTED, SUBMITTED

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


def make_user(db, username, role, active=False):
    user = User(
        username=username,
        password_hash=hash_password("pass123"),
        role=role,
        is_active_coordinator=active,
    )
    db.add(user)
    return user


@pytest.fixture
def people(db_session):
    users = {
        "org": make_user(db_session, "org", "organiser"),
        "org2": make_user(db_session, "org2", "organiser"),
        "coord_a": make_user(db_session, "coord_a", "coordinator", active=True),
        "coord_b": make_user(db_session, "coord_b", "coordinator", active=True),
        "coord_off": make_user(db_session, "coord_off", "coordinator", active=False),
        "lead": make_user(db_session, "lead", "lead"),
    }
    db_session.commit()
    return users


def login(client, username):
    resp = client.post("/auth/login", json={"username": username, "password": "pass123"})
    assert resp.status_code == 200
    return resp


def uid(db_session, username):
    return db_session.query(User).filter_by(username=username).one().id


def create_and_submit(client, organiser="org", title="Annual Gala"):
    login(client, organiser)
    start = (datetime.utcnow() + timedelta(days=14)).isoformat()
    resp = client.post("/events", json={"title": title, "proposed_start": start, "expected_attendance": 100})
    assert resp.status_code == 201
    event_id = resp.json()["id"]
    sub = client.post(f"/events/{event_id}/submit")
    assert sub.status_code == 200
    return event_id


@pytest.fixture
def submitted_event(client, people, db_session):
    """Returns (event_id, current coordinator username, the other active coordinator username)."""
    event_id = create_and_submit(client)
    db_session.expire_all()
    current = db_session.get(Event, event_id).assigned_coordinator.username
    other = "coord_b" if current == "coord_a" else "coord_a"
    return event_id, current, other


def reassign(client, event_id, coordinator_id, as_user="lead"):
    login(client, as_user)
    return client.post(f"/events/{event_id}/reassign", json={"coordinator_id": coordinator_id})


def assigned_to(db_session, event_id):
    db_session.expire_all()
    return db_session.get(Event, event_id).assigned_coordinator_id


def notifications_for(db_session, username):
    db_session.expire_all()
    return db_session.query(Notification).filter_by(user_id=uid(db_session, username)).all()


# ---------- AC1 + AC2: the Lead role ----------

def test_scrum23_tc01_lead_can_log_in(client, people):
    resp = login(client, "lead")
    assert resp.json()["role"] == "lead"
    assert resp.json()["redirect_url"] == "/events"


def test_scrum23_tc02_seed_data_includes_a_lead():
    import inspect
    from backend import seed_users

    source = inspect.getsource(seed_users.seed)
    assert '"username": "lead1"' in source
    assert '"role": "lead"' in source


def test_scrum23_tc03_lead_sees_all_events_and_their_coordinators(client, people, db_session):
    first = create_and_submit(client, "org", "Gala")
    second = create_and_submit(client, "org2", "Workshop")

    login(client, "lead")
    resp = client.get("/events")

    assert resp.status_code == 200
    rows = {e["id"]: e for e in resp.json()}
    assert set(rows) == {first, second}
    for event_id in (first, second):
        assert rows[event_id]["assigned_coordinator_id"] == assigned_to(db_session, event_id)


# ---------- AC3: only the Lead can reassign ----------

@pytest.mark.parametrize("who", ["current", "other", "org"])
def test_scrum23_tc04_non_lead_cannot_reassign(client, db_session, submitted_event, who):
    event_id, current, other = submitted_event
    as_user = {"current": current, "other": other, "org": "org"}[who]
    target = uid(db_session, other if who != "other" else current)
    before = assigned_to(db_session, event_id)

    resp = reassign(client, event_id, target, as_user=as_user)

    assert resp.status_code == 403
    assert assigned_to(db_session, event_id) == before


def test_scrum23_tc05_must_be_logged_in(client, submitted_event, db_session):
    event_id, _, other = submitted_event
    client.post("/auth/logout")
    resp = client.post(f"/events/{event_id}/reassign", json={"coordinator_id": uid(db_session, other)})
    assert resp.status_code == 401


# ---------- AC4 + AC6: one coordinator, change recorded ----------

def test_scrum23_tc06_lead_reassigns_and_change_is_recorded(client, db_session, submitted_event):
    event_id, current, other = submitted_event

    resp = reassign(client, event_id, uid(db_session, other))

    assert resp.status_code == 200
    body = resp.json()
    assert body["assigned_coordinator_id"] == uid(db_session, other)
    assert body["previous_coordinator_id"] == uid(db_session, current)
    assert body["reassigned_by"] == "lead"
    assert body["status"] == SUBMITTED  # reassignment does not change status

    assert assigned_to(db_session, event_id) == uid(db_session, other)
    record = db_session.query(CoordinatorReassignment).filter_by(event_id=event_id).one()
    assert record.from_coordinator_id == uid(db_session, current)
    assert record.to_coordinator_id == uid(db_session, other)
    assert record.reassigned_by_id == uid(db_session, "lead")
    assert record.reassigned_at is not None

    latest = (
        db_session.query(CoordinatorAssignmentHistory)
        .filter_by(event_id=event_id)
        .order_by(CoordinatorAssignmentHistory.id.desc())
        .first()
    )
    assert latest.coordinator_id == uid(db_session, other)


def test_scrum23_tc07_reassigning_twice_keeps_full_history(client, db_session, submitted_event):
    event_id, current, other = submitted_event
    assert reassign(client, event_id, uid(db_session, other)).status_code == 200
    assert reassign(client, event_id, uid(db_session, current)).status_code == 200

    records = (
        db_session.query(CoordinatorReassignment)
        .filter_by(event_id=event_id)
        .order_by(CoordinatorReassignment.id)
        .all()
    )
    assert [(r.from_coordinator.username, r.to_coordinator.username) for r in records] == [
        (current, other),
        (other, current),
    ]
    assert assigned_to(db_session, event_id) == uid(db_session, current)


# ---------- AC5: management moves with the assignment ----------

def test_scrum23_tc08_old_coordinator_loses_control_new_one_gains_it(client, db_session, submitted_event):
    event_id, current, other = submitted_event
    assert reassign(client, event_id, uid(db_session, other)).status_code == 200

    login(client, current)
    assert client.post(f"/events/{event_id}/approve").status_code == 403

    login(client, other)
    resp = client.post(f"/events/{event_id}/approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == APPROVED


def test_scrum23_tc09_coordinator_lists_follow_the_assignment(client, db_session, submitted_event):
    event_id, current, other = submitted_event
    assert reassign(client, event_id, uid(db_session, other)).status_code == 200

    login(client, current)
    assert event_id not in [e["id"] for e in client.get("/coordinator/events/data").json()]

    login(client, other)
    assert event_id in [e["id"] for e in client.get("/coordinator/events/data").json()]


# ---------- AC7: the Organiser sees the current coordinator ----------

def test_scrum23_tc10_organiser_sees_new_coordinator(client, db_session, submitted_event):
    event_id, _, other = submitted_event
    assert reassign(client, event_id, uid(db_session, other)).status_code == 200

    login(client, "org")
    row = next(e for e in client.get("/events").json() if e["id"] == event_id)
    assert row["assigned_coordinator_id"] == uid(db_session, other)


# ---------- AC8: notifications ----------

def test_scrum23_tc11_all_three_people_are_notified(client, db_session, submitted_event):
    event_id, current, other = submitted_event
    assert reassign(client, event_id, uid(db_session, other)).status_code == 200

    new_coord = [n for n in notifications_for(db_session, other) if n.kind == "reassigned_to_you"]
    assert len(new_coord) == 1 and new_coord[0].event_id == event_id

    organiser = [n for n in notifications_for(db_session, "org") if n.kind == "coordinator_changed"]
    assert len(organiser) == 1
    assert other in organiser[0].message

    old_coord = [n for n in notifications_for(db_session, current) if n.kind == "reassigned_away"]
    assert len(old_coord) == 1
    assert old_coord[0].event_id is None  # no longer links to an event they cannot open


def test_scrum23_tc12_old_coordinator_still_sees_their_removal_notice(client, db_session, submitted_event):
    event_id, current, other = submitted_event
    assert reassign(client, event_id, uid(db_session, other)).status_code == 200

    login(client, current)
    kinds = [n["kind"] for n in client.get("/notifications").json()]
    assert "reassigned_away" in kinds


# ---------- Refused reassignments change nothing ----------

def assert_nothing_changed(db_session, event_id, coordinator_id):
    assert assigned_to(db_session, event_id) == coordinator_id
    assert db_session.query(CoordinatorReassignment).count() == 0
    kinds = {n.kind for n in db_session.query(Notification).all()}
    assert kinds.isdisjoint({"reassigned_to_you", "reassigned_away", "coordinator_changed"})


def test_scrum23_tc13_same_coordinator_refused(client, db_session, submitted_event):
    event_id, current, _ = submitted_event
    resp = reassign(client, event_id, uid(db_session, current))
    assert resp.status_code == 400
    assert_nothing_changed(db_session, event_id, uid(db_session, current))


@pytest.mark.parametrize("target", ["coord_off", "org", "lead", "nobody"])
def test_scrum23_tc14_target_must_be_an_active_coordinator(client, db_session, submitted_event, target):
    event_id, current, _ = submitted_event
    target_id = 99999 if target == "nobody" else uid(db_session, target)
    resp = reassign(client, event_id, target_id)
    assert resp.status_code == 400
    assert_nothing_changed(db_session, event_id, uid(db_session, current))


def test_scrum23_tc15_draft_cannot_be_reassigned(client, db_session, people):
    login(client, "org")
    start = (datetime.utcnow() + timedelta(days=14)).isoformat()
    event_id = client.post("/events", json={"title": "Draft only", "proposed_start": start}).json()["id"]

    resp = reassign(client, event_id, uid(db_session, "coord_a"))

    assert resp.status_code == 400
    assert assigned_to(db_session, event_id) is None


@pytest.mark.parametrize("final_status", [REJECTED, COMPLETED])
def test_scrum23_tc16_finished_event_cannot_be_reassigned(client, db_session, submitted_event, final_status):
    event_id, current, other = submitted_event
    event = db_session.get(Event, event_id)
    event.status = final_status
    db_session.commit()

    resp = reassign(client, event_id, uid(db_session, other))

    assert resp.status_code == 400
    assert_nothing_changed(db_session, event_id, uid(db_session, current))


def test_scrum23_tc17_unknown_event_is_404(client, people, db_session):
    resp = reassign(client, 424242, uid(db_session, "coord_a"))
    assert resp.status_code == 404


def test_scrum23_tc18_missing_coordinator_id_is_422(client, submitted_event):
    event_id, _, _ = submitted_event
    login(client, "lead")
    assert client.post(f"/events/{event_id}/reassign", json={}).status_code == 422
