"""SCRUM-28: Tests for viewing and updating an event's information during planning.

As an authorised user, I want to view and update an event's information while
it is being planned, so that the record stays accurate as details firm up.

Acceptance criteria covered:
  AC1  Authorised users can view the full event record for events they are
       entitled to see.
  AC2  Routine information such as the description can be updated directly.
  AC3  Updates are visible to other users entitled to see the event.

Scope decisions (team, 14 Oct 2026):
  - Entitled users: the event's Organiser and its assigned Coordinator
    (the same rule as GET /events).
  - "During planning" means after approval: Approved, Planning or Confirmed.
    Before approval, the Organiser uses POST /events/{id}/amend (SCRUM-21).
  - Routine fields: title, purpose, description, accessibility_needs,
    registration_needs.
  - Significant fields (date/time, expected attendance, venue requirements,
    equipment requirements) are SCRUM-29 and are refused here with 422.
  - "Contact details" has no field on Event yet, so it is not covered.
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
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _user(db, username, role, token, is_active_coordinator=False):
    u = User(username=username, password_hash=hash_password("password123"),
             role=role, is_active_coordinator=is_active_coordinator)
    db.add(u)
    db.commit()
    db.add(LoginSession(user_id=u.id, token=token, valid=True))
    db.commit()
    return u


@pytest.fixture
def seed_data(db_session):
    """An Approved event owned by org_dave and assigned to coord_sarah, plus
    users who are NOT entitled to see it."""
    org = _user(db_session, "org_dave", "organiser", "token_org_dave")
    other_org = _user(db_session, "org_erin", "organiser", "token_org_erin")
    coord = _user(db_session, "coord_sarah", "coordinator", "token_coord_sarah", True)
    other_coord = _user(db_session, "coord_tom", "coordinator", "token_coord_tom", True)
    staff = _user(db_session, "staff_alex", "venue_staff", "token_staff_alex")

    event = Event(
        title="Annual Tech Symposium",
        purpose="Showcase student projects",
        description="A one-day symposium.",
        proposed_start=datetime.utcnow() + timedelta(days=30),
        expected_attendance=150,
        venue_requirements="Main hall with stage",
        accessibility_needs="Wheelchair access",
        equipment_requirements="2 projectors",
        registration_needs="Online sign-up",
        organiser_id=org.id,
        status="Approved",
        assigned_coordinator_id=coord.id,
    )
    db_session.add(event)
    db_session.commit()

    return {"org": org, "other_org": other_org, "coord": coord,
            "other_coord": other_coord, "staff": staff, "event": event}


def _as(client, token):
    client.cookies.set("connectsphere_session", token)


# ---------------------------------------------------------------------------
# 1. Happy Path
# ---------------------------------------------------------------------------

def test_tc2801_organiser_views_full_event_record(client, seed_data):
    """TC-2801 (AC1): The Organiser sees the full record of their own event."""
    _as(client, "token_org_dave")
    ev = seed_data["event"]

    res = client.get(f"/events/{ev.id}")
    assert res.status_code == 200
    data = res.json()

    assert data["id"] == ev.id
    assert data["title"] == "Annual Tech Symposium"
    assert data["purpose"] == "Showcase student projects"
    assert data["description"] == "A one-day symposium."
    assert data["expected_attendance"] == 150
    assert data["venue_requirements"] == "Main hall with stage"
    assert data["accessibility_needs"] == "Wheelchair access"
    assert data["equipment_requirements"] == "2 projectors"
    assert data["registration_needs"] == "Online sign-up"
    assert data["status"] == "Approved"
    assert data["organiser_id"] == seed_data["org"].id
    assert data["assigned_coordinator_id"] == seed_data["coord"].id
    assert data["proposed_start"] is not None


def test_tc2802_assigned_coordinator_views_event_record(client, seed_data):
    """TC-2802 (AC1): The assigned Coordinator can view the event record."""
    _as(client, "token_coord_sarah")
    res = client.get(f"/events/{seed_data['event'].id}")
    assert res.status_code == 200
    assert res.json()["title"] == "Annual Tech Symposium"


def test_tc2803_organiser_updates_routine_fields(client, seed_data, db_session):
    """TC-2803 (AC2): The Organiser updates routine fields directly on an
    Approved event; the change is saved and the status is unchanged."""
    _as(client, "token_org_dave")
    ev = seed_data["event"]

    res = client.patch(f"/events/{ev.id}", json={
        "description": "A one-day symposium with a poster session.",
        "purpose": "Showcase and judge student projects",
    })
    assert res.status_code == 200
    data = res.json()
    assert data["description"] == "A one-day symposium with a poster session."
    assert data["purpose"] == "Showcase and judge student projects"
    assert data["status"] == "Approved"
    assert sorted(data["changed_fields"]) == ["description", "purpose"]

    db_session.refresh(ev)
    assert ev.description == "A one-day symposium with a poster session."


def test_tc2804_update_visible_to_other_entitled_user(client, seed_data):
    """TC-2804 (AC3): A change by the Coordinator is visible to the Organiser."""
    ev = seed_data["event"]
    _as(client, "token_coord_sarah")
    assert client.patch(f"/events/{ev.id}", json={
        "registration_needs": "Online sign-up, closes 3 days before",
    }).status_code == 200

    _as(client, "token_org_dave")
    res = client.get(f"/events/{ev.id}")
    assert res.json()["registration_needs"] == "Online sign-up, closes 3 days before"


# ---------------------------------------------------------------------------
# 2. Negative Path
# ---------------------------------------------------------------------------

def test_tc2805_view_or_update_non_existent_event_returns_404(client, seed_data):
    """TC-2805: Viewing or updating an event that doesn't exist -> 404."""
    _as(client, "token_org_dave")
    for res in (
        client.get("/events/99999"),
        client.patch("/events/99999", json={"description": "x"}),
    ):
        assert res.status_code == 404
        assert "event not found" in res.json()["detail"].lower()


def test_tc2806_significant_fields_are_refused(client, seed_data, db_session):
    """TC-2806: Date/time, attendance, venue and equipment requirements are
    significant changes (SCRUM-29). Each is refused with 422 and the event is
    left unchanged."""
    _as(client, "token_coord_sarah")
    ev = seed_data["event"]
    significant = {
        "proposed_start": (datetime.utcnow() + timedelta(days=40)).isoformat(),
        "expected_attendance": 300,
        "venue_requirements": "Two halls",
        "equipment_requirements": "4 projectors",
    }
    for field, value in significant.items():
        res = client.patch(f"/events/{ev.id}", json={"description": "new", field: value})
        assert res.status_code == 422, field
        assert field in res.json()["detail"]

    db_session.refresh(ev)
    assert ev.description == "A one-day symposium."
    assert ev.expected_attendance == 150


def test_tc2807_empty_update_is_refused(client, seed_data):
    """TC-2807: An update with no fields -> 400."""
    _as(client, "token_org_dave")
    res = client.patch(f"/events/{seed_data['event'].id}", json={})
    assert res.status_code == 400


def test_tc2808_blank_title_and_unknown_field_are_refused(client, seed_data, db_session):
    """TC-2808: A blank title or an unknown field -> 422, event unchanged."""
    _as(client, "token_org_dave")
    ev = seed_data["event"]
    assert client.patch(f"/events/{ev.id}", json={"title": "   "}).status_code == 422
    assert client.patch(f"/events/{ev.id}", json={"status": "Cancelled"}).status_code == 422

    db_session.refresh(ev)
    assert ev.title == "Annual Tech Symposium"
    assert ev.status == "Approved"


# ---------------------------------------------------------------------------
# 3. Boundary Path (status)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("event_status", ["Draft", "Submitted", "Under Review", "Returned"])
def test_tc2809_cannot_update_before_approval(client, seed_data, db_session, event_status):
    """TC-2809: Before approval, routine edits go through amend (SCRUM-21), so
    PATCH is refused with 400 and the event is unchanged."""
    ev = seed_data["event"]
    ev.status = event_status
    db_session.commit()

    _as(client, "token_org_dave")
    res = client.patch(f"/events/{ev.id}", json={"description": "new"})
    assert res.status_code == 400
    db_session.refresh(ev)
    assert ev.description == "A one-day symposium."


@pytest.mark.parametrize("event_status", ["Rejected", "Cancelled", "Completed"])
def test_tc2810_cannot_update_closed_event(client, seed_data, db_session, event_status):
    """TC-2810: A Rejected, Cancelled or Completed event can't be edited -> 400."""
    ev = seed_data["event"]
    ev.status = event_status
    db_session.commit()

    _as(client, "token_coord_sarah")
    assert client.patch(f"/events/{ev.id}", json={"description": "new"}).status_code == 400


@pytest.mark.parametrize("event_status", ["Approved", "Planning", "Confirmed"])
def test_tc2811_can_update_during_planning_statuses(client, seed_data, db_session, event_status):
    """TC-2811: Routine edits are allowed in every planning status."""
    ev = seed_data["event"]
    ev.status = event_status
    db_session.commit()

    _as(client, "token_org_dave")
    res = client.patch(f"/events/{ev.id}", json={"description": "new"})
    assert res.status_code == 200
    assert res.json()["status"] == event_status


# ---------------------------------------------------------------------------
# 4. Cross-Cutting (entitlement / auth)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("token, view_status", [
    ("token_org_erin", 404),   # SCRUM-18 AC1: another Organiser's event is concealed
    ("token_coord_tom", 200),  # SCRUM-18 AC4: Coordinators can view all events (read-only)
    ("token_staff_alex", 403),
])
def test_tc2812_users_not_entitled_cannot_update(client, seed_data, db_session, token, view_status):
    """TC-2812 (AC2): Another Organiser, an unassigned Coordinator and Venue
    Staff cannot update the event (403). Viewing follows SCRUM-18's rules."""
    ev = seed_data["event"]
    _as(client, token)
    assert client.get(f"/events/{ev.id}").status_code == view_status
    assert client.patch(f"/events/{ev.id}", json={"description": "hacked"}).status_code == 403

    db_session.refresh(ev)
    assert ev.description == "A one-day symposium."


def test_tc2813_unauthenticated_user_cannot_view_or_update(client, seed_data):
    """TC-2813 (DoD auth): No session -> 401."""
    ev = seed_data["event"]
    assert client.get(f"/events/{ev.id}").status_code == 401
    assert client.patch(f"/events/{ev.id}", json={"description": "x"}).status_code == 401


# ---------------------------------------------------------------------------
# 5. Workflow Variation (audit)
# ---------------------------------------------------------------------------

def test_tc2814_update_is_recorded_in_history(client, seed_data, db_session):
    """TC-2814: Each update records who changed which fields, without changing
    the status."""
    ev = seed_data["event"]
    _as(client, "token_coord_sarah")
    assert client.patch(f"/events/{ev.id}", json={"accessibility_needs": "Wheelchair access, hearing loop"}).status_code == 200

    rows = db_session.query(EventStatusHistory).filter(EventStatusHistory.event_id == ev.id).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_id == seed_data["coord"].id
    assert row.previous_status == "Approved"
    assert row.new_status == "Approved"
    assert "accessibility_needs" in row.reason
