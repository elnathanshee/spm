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
from backend.models import Event, User
from backend.security import hash_password
from backend.status import DRAFT, SUBMITTED


TEST_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
TEST_PASSWORD = "pass123"


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
    user_data = {
        "organiser1": ("organiser", False),
        "organiser2": ("organiser", False),
        "coord1": ("coordinator", True),
        "coord2": ("coordinator", True),
        "venue1": ("venue staff", False),
        "technical1": ("technical support", False),
        "attendee1": ("attendee", False),
    }
    created = {}
    for username, (role, active_coordinator) in user_data.items():
        user = User(
            username=username,
            password_hash=hash_password(TEST_PASSWORD),
            role=role,
            is_active_coordinator=active_coordinator,
        )
        db_session.add(user)
        created[username] = user
    db_session.commit()
    for user in created.values():
        db_session.refresh(user)
    return created


@pytest.fixture
def make_event(db_session, users):
    def create_event(
        title,
        organiser="organiser1",
        coordinator=None,
        status=DRAFT,
        submitted=False,
    ):
        event = Event(
            title=title,
            organiser_id=users[organiser].id,
            assigned_coordinator_id=users[coordinator].id if coordinator else None,
            status=status,
            proposed_start=datetime.utcnow() + timedelta(days=14),
            expected_attendance=50,
            submitted_at=datetime.utcnow() if submitted else None,
        )
        db_session.add(event)
        db_session.commit()
        db_session.refresh(event)
        return event

    return create_event


def login(client, username):
    response = client.post(
        "/auth/login",
        json={"username": username, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200
    return response


ROLE_USERS = {
    "organiser": "organiser1",
    "coordinator": "coord1",
    "venue staff": "venue1",
    "technical support": "technical1",
    "attendee": "attendee1",
}


ROLE_LANDING_PATHS = {
    "organiser": "/events",
    "coordinator": "/coordinator/events",
    "venue staff": "/venue",
    "technical support": "/technical",
    "attendee": "/attendee",
}


def assert_forbidden(response, detail):
    assert response.status_code == 403
    assert response.json()["detail"] == detail


def assert_unauthenticated(response):
    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


def event_payload():
    return {
        "title": "RBAC test event",
        "purpose": "Role access test",
        "description": "Created for SCRUM-17 authorization coverage",
        "proposed_start": (datetime.utcnow() + timedelta(days=30)).isoformat(),
        "expected_attendance": 50,
    }


@pytest.mark.parametrize("role", ROLE_USERS)
def test_each_supported_role_has_an_authenticated_role_landing(client, users, role):
    response = login(client, ROLE_USERS[role])

    assert response.json()["role"] == role
    assert response.json()["redirect_url"] == ROLE_LANDING_PATHS[role]
    session_response = client.get("/auth/me")
    assert session_response.status_code == 200
    assert session_response.json()["role"] == role


def test_tc301_attendee_cannot_create_or_submit_events(client, users, make_event):
    draft = make_event("Attendee submit target")
    login(client, "attendee1")

    create_response = client.post("/events", json=event_payload())
    submit_response = client.post(f"/events/{draft.id}/submit")

    assert_forbidden(create_response, "Only organisers can create event requests")
    assert_forbidden(submit_response, "Only organisers can submit event requests")


def test_tc302_attendee_cannot_access_coordinator_functions(client, make_event):
    event = make_event("Attendee decision target", coordinator="coord1", status=SUBMITTED, submitted=True)
    login(client, "attendee1")

    queue_response = client.get("/coordinator/events/data")
    approve_response = client.post(f"/events/{event.id}/approve")
    reject_response = client.post(f"/events/{event.id}/reject", json={"reason": "Not suitable"})

    assert_forbidden(queue_response, "Only coordinators can view assigned event requests")
    assert_forbidden(approve_response, "Only coordinators can approve event requests")
    assert_forbidden(reject_response, "Only coordinators can reject event requests")


def test_tc303_organiser_cannot_make_coordinator_decisions(client, make_event):
    event = make_event("Organiser decision target", coordinator="coord1", status=SUBMITTED, submitted=True)
    login(client, "organiser1")

    approve_response = client.post(f"/events/{event.id}/approve")
    reject_response = client.post(f"/events/{event.id}/reject", json={"reason": "Not suitable"})
    return_response = client.post(f"/events/{event.id}/return", json={"reason": "More details required"})

    assert_forbidden(approve_response, "Only coordinators can approve event requests")
    assert_forbidden(reject_response, "Only coordinators can reject event requests")
    assert_forbidden(return_response, "Only coordinators can return event requests for clarification")


def test_tc304_venue_staff_cannot_create_or_approve(client, make_event):
    event = make_event("Venue staff decision target", coordinator="coord1", status=SUBMITTED, submitted=True)
    login(client, "venue1")

    create_response = client.post("/events", json=event_payload())
    approve_response = client.post(f"/events/{event.id}/approve")

    assert_forbidden(create_response, "Only organisers can create event requests")
    assert_forbidden(approve_response, "Only coordinators can approve event requests")


def test_tc305_technical_support_cannot_create_or_reject(client, make_event):
    event = make_event("Technical support decision target", coordinator="coord1", status=SUBMITTED, submitted=True)
    login(client, "technical1")

    create_response = client.post("/events", json=event_payload())
    reject_response = client.post(f"/events/{event.id}/reject", json={"reason": "Not suitable"})

    assert_forbidden(create_response, "Only organisers can create event requests")
    assert_forbidden(reject_response, "Only coordinators can reject event requests")


@pytest.mark.parametrize("username", ["venue1", "technical1", "attendee1"])
def test_external_operational_roles_cannot_read_internal_event_data(client, users, make_event, username):
    make_event("Confidential planning request", coordinator="coord1", status=SUBMITTED, submitted=True)
    login(client, username)

    response = client.get("/events")

    assert_forbidden(response, "Only organisers and coordinators can view event requests")


@pytest.mark.parametrize("username", ["organiser1", "attendee1"])
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/coordinator/events/data", None),
        ("GET", "/coordinator/events", None),
        ("POST", "/events/{event_id}/review", None),
        ("POST", "/events/{event_id}/approve", None),
        ("POST", "/events/{event_id}/reject", {"reason": "No"}),
        ("POST", "/events/{event_id}/return", {"reason": "More detail"}),
    ],
)
def test_external_roles_cannot_bypass_internal_planning_endpoints(
    client, users, make_event, username, method, path, body
):
    event = make_event("Internal planning target", coordinator="coord1", status=SUBMITTED, submitted=True)
    login(client, username)

    response = client.request(method, path.format(event_id=event.id), json=body)

    assert response.status_code == 403
    assert response.json()["detail"]


def test_tc306_coordinator_cannot_create_event_draft(client, db_session, users):
    login(client, "coord1")

    response = client.post("/events", json=event_payload())

    assert_forbidden(response, "Only organisers can create event requests")
    assert db_session.query(Event).count() == 0


def test_tc307_organiser_cannot_submit_another_organisers_event(client, make_event):
    event = make_event("Cross-owner submission target", organiser="organiser1")
    login(client, "organiser2")

    response = client.post(f"/events/{event.id}/submit")

    assert_forbidden(response, "You are not authorized to submit this event request")


def test_organiser_can_amend_owned_event_without_changing_its_identity(client, db_session, make_event):
    event = make_event("Original title", organiser="organiser1")
    login(client, "organiser1")

    response = client.post(
        f"/events/{event.id}/amend",
        json={"title": "Updated title", "description": "Updated description"},
    )

    assert response.status_code == 200
    assert response.json()["id"] == event.id
    assert response.json()["title"] == "Updated title"
    db_session.refresh(event)
    assert event.title == "Updated title"
    assert event.status == DRAFT


@pytest.mark.parametrize("username", ["organiser2", "coord1", "venue1", "technical1", "attendee1"])
def test_other_users_cannot_amend_an_organisers_event(client, make_event, username):
    event = make_event("Protected event", organiser="organiser1")
    login(client, username)

    response = client.post(
        f"/events/{event.id}/amend",
        json={"title": "Unauthorized update", "description": "Should not persist"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]


def test_amend_requires_authentication(client, make_event):
    event = make_event("Protected event", organiser="organiser1")

    response = client.post(
        f"/events/{event.id}/amend",
        json={"title": "Unauthorized update", "description": "Should not persist"},
    )

    assert_unauthenticated(response)


def test_tc308_organiser_event_list_contains_only_owned_events(client, make_event):
    own_event = make_event("Organiser one event", organiser="organiser1")
    other_event = make_event("Organiser two event", organiser="organiser2")
    login(client, "organiser1")

    response = client.get("/events")

    assert response.status_code == 200
    returned_ids = {event["id"] for event in response.json()}
    assert returned_ids == {own_event.id}
    assert other_event.id not in returned_ids
    assert all(event["organiser_id"] == own_event.organiser_id for event in response.json())


def test_tc309_coordinator_queue_contains_only_assigned_events(client, make_event):
    assigned_event = make_event("Coordinator one event", coordinator="coord1", status=SUBMITTED, submitted=True)
    other_event = make_event("Coordinator two event", coordinator="coord2", status=SUBMITTED, submitted=True)
    login(client, "coord1")

    response = client.get("/coordinator/events/data")

    assert response.status_code == 200
    returned_ids = {event["id"] for event in response.json()}
    assert returned_ids == {assigned_event.id}
    assert other_event.id not in returned_ids


@pytest.mark.parametrize("path", ["/events", "/coordinator/events/data"])
def test_tc310_protected_routes_require_valid_session(client, path):
    response = client.get(path)

    assert_unauthenticated(response)


@pytest.mark.parametrize("path", ["/events", "/coordinator/events/data"])
def test_tc310_invalid_session_cookie_is_rejected(client, path):
    response = client.get(path, cookies={"connectsphere_session": "invalid-token"})

    assert_unauthenticated(response)
