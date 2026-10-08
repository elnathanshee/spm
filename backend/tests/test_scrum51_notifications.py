"""SCRUM-51: Notify Organiser of submission and review outcome.

Each test names the acceptance criterion it proves.
  AC1  Organiser notified on successful submission.
  AC2  Organiser notified on return for clarification, with the Coordinator's note.
  AC3  Organiser notified on approval or rejection, saying which outcome.
  AC4  A user only receives notifications for events they are entitled to see.
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
from backend.notifications import NotEntitled, can_see_event, notify
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


@pytest.fixture
def people(db_session):
    org = User(username="org", password_hash=hash_password("pass123"), role="organiser")
    other_org = User(username="other_org", password_hash=hash_password("pass123"), role="organiser")
    coord_a = User(username="coord_a", password_hash=hash_password("pass123"), role="coordinator", is_active_coordinator=True)
    coord_b = User(username="coord_b", password_hash=hash_password("pass123"), role="coordinator", is_active_coordinator=True)
    db_session.add_all([org, other_org, coord_a, coord_b])
    db_session.commit()
    return {"org": org, "other_org": other_org, "coord_a": coord_a, "coord_b": coord_b}


def login(client, username):
    resp = client.post("/auth/login", json={"username": username, "password": "pass123"})
    assert resp.status_code == 200


TITLE = "Annual Gala"


@pytest.fixture
def submitted_event(client, people, db_session):
    """Organiser creates and submits a request. Returns (event_id, assigned coordinator, other coordinator)."""
    login(client, "org")
    start = (datetime.utcnow() + timedelta(days=14)).isoformat()
    resp = client.post("/events", json={"title": TITLE, "proposed_start": start, "expected_attendance": 100})
    assert resp.status_code == 201
    event_id = resp.json()["id"]
    sub = client.post(f"/events/{event_id}/submit")
    assert sub.status_code == 200

    assigned = db_session.get(User, sub.json()["assigned_coordinator_id"]).username
    other = "coord_b" if assigned == "coord_a" else "coord_a"
    return event_id, assigned, other


def my_notifications(client, username):
    login(client, username)
    resp = client.get("/notifications")
    assert resp.status_code == 200
    return resp.json()


def notifications_of_kind(client, username, kind):
    return [n for n in my_notifications(client, username) if n["kind"] == kind]


# ---------- AC1: submission ----------

def test_scrum51_tc01_organiser_notified_on_submission(client, submitted_event):
    event_id, _, _ = submitted_event

    found = notifications_of_kind(client, "org", "submitted")

    assert len(found) == 1
    assert found[0]["event_id"] == event_id
    assert TITLE in found[0]["message"]
    assert "submitted" in found[0]["message"].lower()
    assert found[0]["read"] is False


def test_scrum51_tc02_draft_alone_sends_no_notification(client, people):
    login(client, "org")
    start = (datetime.utcnow() + timedelta(days=14)).isoformat()
    resp = client.post("/events", json={"title": TITLE, "proposed_start": start})
    assert resp.status_code == 201

    assert my_notifications(client, "org") == []


def test_scrum51_tc03_resubmission_after_return_notifies_again(client, submitted_event):
    event_id, assigned, _ = submitted_event
    login(client, assigned)
    assert client.post(f"/events/{event_id}/return", json={"reason": "Add AV needs"}).status_code == 200
    login(client, "org")
    assert client.post(f"/events/{event_id}/submit").status_code == 200

    assert len(notifications_of_kind(client, "org", "submitted")) == 2


# ---------- AC2: return for clarification, with the note ----------

NOTE = "Please add your AV and accessibility requirements."


def test_scrum51_tc04_organiser_notified_on_return_with_note(client, submitted_event):
    event_id, assigned, _ = submitted_event
    login(client, assigned)
    assert client.post(f"/events/{event_id}/return", json={"reason": NOTE}).status_code == 200

    found = notifications_of_kind(client, "org", "returned")

    assert len(found) == 1
    assert found[0]["event_id"] == event_id
    assert NOTE in found[0]["message"]


# ---------- AC3: approval or rejection, saying which ----------

def test_scrum51_tc05_organiser_notified_on_approval(client, submitted_event):
    event_id, assigned, _ = submitted_event
    login(client, assigned)
    assert client.post(f"/events/{event_id}/approve").status_code == 200

    found = notifications_of_kind(client, "org", "approved")

    assert len(found) == 1
    assert "approved" in found[0]["message"].lower()
    assert "rejected" not in found[0]["message"].lower()
    assert notifications_of_kind(client, "org", "rejected") == []


def test_scrum51_tc06_organiser_notified_on_rejection_with_reason(client, submitted_event):
    event_id, assigned, _ = submitted_event
    reason = "Venue budget exceeds the allowed limit."
    login(client, assigned)
    assert client.post(f"/events/{event_id}/reject", json={"reason": reason}).status_code == 200

    found = notifications_of_kind(client, "org", "rejected")

    assert len(found) == 1
    assert "rejected" in found[0]["message"].lower()
    assert reason in found[0]["message"]
    assert notifications_of_kind(client, "org", "approved") == []


# ---------- No notification when the action is refused ----------

def test_scrum51_tc07_refused_rejection_sends_nothing(client, submitted_event):
    """Wrong coordinator (403) and missing reason (422) must not notify."""
    event_id, assigned, other = submitted_event

    login(client, other)
    assert client.post(f"/events/{event_id}/reject", json={"reason": "No"}).status_code == 403
    login(client, assigned)
    assert client.post(f"/events/{event_id}/reject", json={"reason": "  "}).status_code == 422

    assert notifications_of_kind(client, "org", "rejected") == []


def test_scrum51_tc08_refused_approval_sends_nothing(client, submitted_event):
    """Approving twice: the second is refused (400) and must not notify again."""
    event_id, assigned, _ = submitted_event
    login(client, assigned)
    assert client.post(f"/events/{event_id}/approve").status_code == 200
    assert client.post(f"/events/{event_id}/approve").status_code == 400

    assert len(notifications_of_kind(client, "org", "approved")) == 1


# ---------- AC4: only entitled users receive notifications ----------

def test_scrum51_tc09_other_organiser_receives_nothing(client, submitted_event):
    event_id, assigned, _ = submitted_event
    login(client, assigned)
    client.post(f"/events/{event_id}/approve")

    assert my_notifications(client, "other_org") == []


def test_scrum51_tc10_notify_refuses_unentitled_recipient(db_session, submitted_event, people):
    event_id, _, _ = submitted_event
    event = db_session.get(Event, event_id)
    other_org = db_session.query(User).filter_by(username="other_org").one()

    assert can_see_event(other_org, event) is False
    with pytest.raises(NotEntitled):
        notify(db_session, other_org, event, "submitted", "should never be stored")
    db_session.rollback()

    assert db_session.query(Notification).filter_by(user_id=other_org.id).count() == 0


def test_scrum51_tc11_list_hides_notifications_for_events_no_longer_visible(client, db_session, submitted_event):
    """If a Coordinator loses an event, its old notifications stop showing."""
    event_id, assigned, other = submitted_event
    event = db_session.get(Event, event_id)
    coord = db_session.query(User).filter_by(username=assigned).one()
    notify(db_session, coord, event, "assigned", "You have a new request")
    db_session.commit()
    # [SCRUM-63] filter by kind: submit now also sends the coordinator an "assigned_to_you"
    assert len([n for n in my_notifications(client, assigned) if n["kind"] == "assigned"]) == 1

    event.assigned_coordinator_id = db_session.query(User).filter_by(username=other).one().id
    db_session.commit()

    assert my_notifications(client, assigned) == []


# ---------- Reading notifications ----------

def test_scrum51_tc12_requires_login(client):
    assert client.get("/notifications").status_code == 401


def test_scrum51_tc13_mark_own_notification_read(client, submitted_event):
    note_id = my_notifications(client, "org")[0]["id"]

    resp = client.post(f"/notifications/{note_id}/read")

    assert resp.status_code == 200
    assert resp.json()["read"] is True
    assert my_notifications(client, "org")[0]["read"] is True


def test_scrum51_tc14_cannot_mark_someone_elses_notification(client, submitted_event):
    note_id = my_notifications(client, "org")[0]["id"]

    login(client, "other_org")
    assert client.post(f"/notifications/{note_id}/read").status_code == 404


def test_scrum51_tc15_newest_first(client, submitted_event):
    event_id, assigned, _ = submitted_event
    login(client, assigned)
    client.post(f"/events/{event_id}/approve")

    # [SCRUM-63] filter to SCRUM-51's kinds: submit now also sends "coordinator_assigned"
    kinds = [n["kind"] for n in my_notifications(client, "org") if n["kind"] in ("submitted", "approved")]

    assert kinds == ["approved", "submitted"]
