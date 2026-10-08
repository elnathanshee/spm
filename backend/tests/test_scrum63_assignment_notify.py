"""SCRUM-63: Notify on Coordinator assignment.

Scope narrowed after SCRUM-23 (Week 7 Change #5): SCRUM-23 already notifies
everyone on a reassignment, so SCRUM-63 builds the FIRST-assignment
notifications and proves AC2 through the SCRUM-23 endpoint.

  AC1  The Organiser is notified when a Coordinator is first assigned,
       naming the Coordinator.
  AC2  The Organiser is notified when the assigned Coordinator changes,
       naming the new Coordinator (built in SCRUM-23, verified here).
  AC3  The newly assigned Coordinator is notified that the event is theirs.
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
from backend.models import Event, Notification, User
from backend.security import hash_password

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
    user = User(username=username, password_hash=hash_password("pass123"), role=role, is_active_coordinator=active)
    db.add(user)
    return user


@pytest.fixture
def people(db_session):
    make_user(db_session, "org", "organiser")
    make_user(db_session, "org2", "organiser")
    make_user(db_session, "coord_a", "coordinator", active=True)
    make_user(db_session, "coord_b", "coordinator", active=True)
    make_user(db_session, "lead", "lead")
    db_session.commit()


def login(client, username):
    assert client.post("/auth/login", json={"username": username, "password": "pass123"}).status_code == 200


TITLE = "Annual Gala"


def create_draft(client, organiser="org", title=TITLE):
    login(client, organiser)
    start = (datetime.utcnow() + timedelta(days=14)).isoformat()
    resp = client.post("/events", json={"title": title, "proposed_start": start, "expected_attendance": 100})
    assert resp.status_code == 201
    return resp.json()["id"]


def submit(client, event_id, organiser="org"):
    login(client, organiser)
    return client.post(f"/events/{event_id}/submit")


def assigned_username(db_session, event_id):
    db_session.expire_all()
    return db_session.get(Event, event_id).assigned_coordinator.username


def kinds_for(client, username, kind):
    login(client, username)
    return [n for n in client.get("/notifications").json() if n["kind"] == kind]


# ---------- AC1: Organiser told who was assigned ----------

def test_scrum63_tc01_organiser_notified_naming_coordinator(client, db_session, people):
    event_id = create_draft(client)
    assert submit(client, event_id).status_code == 200
    coord_a_id = db_session.query(User).filter_by(username="coord_a").one().id
    login(client, "lead")
    assert client.post(f"/events/{event_id}/assign", json={"coordinator_id": coord_a_id}).status_code == 200
    coordinator = assigned_username(db_session, event_id)

    found = kinds_for(client, "org", "coordinator_assigned")

    assert len(found) == 1
    assert found[0]["event_id"] == event_id
    assert coordinator in found[0]["message"]
    assert TITLE in found[0]["message"]


# ---------- AC3: Coordinator told the event is theirs ----------

def test_scrum63_tc02_assigned_coordinator_notified(client, db_session, people):
    event_id = create_draft(client)
    assert submit(client, event_id).status_code == 200
    coord_a_id = db_session.query(User).filter_by(username="coord_a").one().id
    login(client, "lead")
    assert client.post(f"/events/{event_id}/assign", json={"coordinator_id": coord_a_id}).status_code == 200
    coordinator = assigned_username(db_session, event_id)
    other = "coord_b" if coordinator == "coord_a" else "coord_a"

    mine = kinds_for(client, coordinator, "assigned_to_you")
    assert len(mine) == 1
    assert mine[0]["event_id"] == event_id
    assert TITLE in mine[0]["message"]

    # The coordinator who was NOT assigned hears nothing.
    assert kinds_for(client, other, "assigned_to_you") == []


def test_scrum63_tc03_round_robin_notifies_each_coordinator_for_their_own_event(client, db_session, people):
    first = create_draft(client, title="Gala")
    assert submit(client, first).status_code == 200
    second = create_draft(client, title="Workshop")
    assert submit(client, second).status_code == 200

    coord_a_id = db_session.query(User).filter_by(username="coord_a").one().id
    coord_b_id = db_session.query(User).filter_by(username="coord_b").one().id

    login(client, "lead")
    assert client.post(f"/events/{first}/assign", json={"coordinator_id": coord_a_id}).status_code == 200
    assert client.post(f"/events/{second}/assign", json={"coordinator_id": coord_b_id}).status_code == 200

    a, b = assigned_username(db_session, first), assigned_username(db_session, second)
    assert a != b

    assert [n["event_id"] for n in kinds_for(client, a, "assigned_to_you")] == [first]
    assert [n["event_id"] for n in kinds_for(client, b, "assigned_to_you")] == [second]


# ---------- No duplicates, nothing on failure ----------

def test_scrum63_tc04_resubmission_does_not_renotify(client, db_session, people):
    event_id = create_draft(client)
    assert submit(client, event_id).status_code == 200
    coord_a_id = db_session.query(User).filter_by(username="coord_a").one().id
    login(client, "lead")
    assert client.post(f"/events/{event_id}/assign", json={"coordinator_id": coord_a_id}).status_code == 200
    coordinator = assigned_username(db_session, event_id)
    login(client, coordinator)
    assert client.post(f"/events/{event_id}/return", json={"reason": "Add AV needs"}).status_code == 200
    assert submit(client, event_id).status_code == 200

    assert assigned_username(db_session, event_id) == coordinator
    assert len(kinds_for(client, "org", "coordinator_assigned")) == 1
    assert len(kinds_for(client, coordinator, "assigned_to_you")) == 1


def test_scrum63_tc05_no_coordinator_available_sends_nothing(client, db_session):
    make_user(db_session, "org", "organiser")
    make_user(db_session, "coord_off", "coordinator", active=False)
    make_user(db_session, "lead", "lead")
    db_session.commit()

    event_id = create_draft(client)
    # Week 7 Change #5: submission succeeds without assigning coordinator
    assert submit(client, event_id).status_code == 200

    # Attempting to assign inactive coordinator fails
    coord_off_id = db_session.query(User).filter_by(username="coord_off").one().id
    login(client, "lead")
    assert client.post(f"/events/{event_id}/assign", json={"coordinator_id": coord_off_id}).status_code == 400

    db_session.expire_all()
    kinds = {n.kind for n in db_session.query(Notification).all()}
    assert kinds.isdisjoint({"coordinator_assigned", "assigned_to_you"})


def test_scrum63_tc06_saving_a_draft_sends_nothing(client, db_session, people):
    create_draft(client)
    db_session.expire_all()
    assert db_session.query(Notification).count() == 0


# ---------- Entitlement (SCRUM-51 AC4 still holds) ----------

def test_scrum63_tc07_other_organiser_receives_nothing(client, db_session, people):
    event_id = create_draft(client)
    assert submit(client, event_id).status_code == 200
    coord_a_id = db_session.query(User).filter_by(username="coord_a").one().id
    login(client, "lead")
    assert client.post(f"/events/{event_id}/assign", json={"coordinator_id": coord_a_id}).status_code == 200

    login(client, "org2")
    assert client.get("/notifications").json() == []


# ---------- AC2: change of Coordinator (via SCRUM-23) ----------

def test_scrum63_tc08_organiser_notified_when_coordinator_changes(client, db_session, people):
    event_id = create_draft(client)
    assert submit(client, event_id).status_code == 200
    coord_a_id = db_session.query(User).filter_by(username="coord_a").one().id
    coord_b_id = db_session.query(User).filter_by(username="coord_b").one().id
    login(client, "lead")
    assert client.post(f"/events/{event_id}/assign", json={"coordinator_id": coord_a_id}).status_code == 200
    current = assigned_username(db_session, event_id)
    new = "coord_b" if current == "coord_a" else "coord_a"
    new_id = coord_b_id if new == "coord_b" else coord_a_id

    assert client.post(f"/events/{event_id}/reassign", json={"coordinator_id": new_id}).status_code == 200

    changed = kinds_for(client, "org", "coordinator_changed")
    assert len(changed) == 1
    assert new in changed[0]["message"]
    # and the new coordinator is told it is theirs
    assert len(kinds_for(client, new, "reassigned_to_you")) == 1
