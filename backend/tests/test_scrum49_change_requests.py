"""SCRUM-49: the Organiser asks for a permitted change after submission.

Who: only the Organiser who owns the event, once a Coordinator is assigned.
When: Submitted, Under Review, Approved, Planning, Safety Review or Confirmed.
What: date and time, expected attendance, venue requirements, equipment
requirements or description, always with a reason. Nothing changes on the event
until the Coordinator approves (SCRUM-50).
"""

import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base, get_db
from backend.main import app
from backend.models import Event, EventChangeRequest, LoginSession, User

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

NEW_START = "2099-05-01T18:30:00"


@pytest.fixture
def db_session():
    Base.metadata.create_all(bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def world(db_session):
    users = {
        name: User(username=name, password_hash="x", role=role, is_active_coordinator=role == "coordinator")
        for name, role in [
            ("org", "organiser"), ("other_org", "organiser"),
            ("coord", "coordinator"), ("other_coord", "coordinator"), ("lead", "lead"),
        ]
    }
    db_session.add_all(users.values())
    db_session.flush()
    # A preset session for each user: signing in hashes a password, which is slow.
    db_session.add_all(LoginSession(user_id=user.id, token=f"token_{name}", valid=True) for name, user in users.items())
    event = Event(
        title="Annual Gala", status="Planning", organiser_id=users["org"].id,
        assigned_coordinator_id=users["coord"].id, proposed_start=datetime(2099, 3, 10, 14, 0),
        expected_attendance=100, venue_requirements="Main hall", equipment_requirements="Two microphones",
        description="Dinner and auction",
    )
    db_session.add(event)
    db_session.commit()
    return {"event": event, **users}


def login(client, username):
    client.cookies.set("connectsphere_session", f"token_{username}")


def raise_request(client, event, changes=None, reason="The speaker moved her flight"):
    body = {"changes": changes if changes is not None else {"expected_attendance": 150}, "reason": reason}
    return client.post(f"/events/{event.id}/change-requests", json=body)


def stored_requests(db_session):
    return db_session.query(EventChangeRequest).all()


def test_tc4901_organiser_raises_a_request_and_nothing_changes_yet(client, world, db_session):
    """TC-4901: a request is stored as Pending with new and previous values; the event is untouched."""
    login(client, "org")
    res = raise_request(client, world["event"], {"expected_attendance": 150, "proposed_start": NEW_START})
    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "Pending"
    assert body["reason"] == "The speaker moved her flight"
    assert body["changes"] == {"expected_attendance": 150, "proposed_start": NEW_START}
    assert body["previous_values"] == {"expected_attendance": 100, "proposed_start": "2099-03-10T14:00:00"}
    assert body["requested_by"] == "org"

    row = stored_requests(db_session)[0]
    assert json.loads(row.requested_changes)["expected_attendance"] == 150
    assert json.loads(row.previous_values)["expected_attendance"] == 100
    assert row.requested_by_id == world["org"].id
    db_session.refresh(world["event"])
    assert world["event"].expected_attendance == 100
    assert world["event"].proposed_start == datetime(2099, 3, 10, 14, 0)
    assert world["event"].status == "Planning"


@pytest.mark.parametrize("field,value", [
    ("proposed_start", NEW_START),
    ("expected_attendance", 150),
    ("venue_requirements", "Two halls"),
    ("equipment_requirements", "Four microphones"),
    ("description", "Dinner, auction and a band"),
])
def test_tc4902_each_permitted_field_can_be_requested(client, world, field, value):
    """TC-4902: each of the five permitted fields is accepted on its own."""
    login(client, "org")
    res = raise_request(client, world["event"], {field: value})
    assert res.status_code == 201
    assert res.json()["changes"] == {field: value}


@pytest.mark.parametrize("status", ["Submitted", "Under Review", "Approved", "Planning", "Safety Review", "Confirmed"])
def test_tc4903_allowed_in_every_status_after_submission(client, world, db_session, status):
    """TC-4903: all six statuses named by the story accept a request."""
    world["event"].status = status
    db_session.commit()
    login(client, "org")
    assert raise_request(client, world["event"]).status_code == 201


@pytest.mark.parametrize("status", ["Draft", "Returned", "Rejected", "Completed", "Cancelled"])
def test_tc4904_refused_in_any_other_status(client, world, db_session, status):
    """TC-4904: any other status is refused with its name, and nothing is stored."""
    world["event"].status = status
    db_session.commit()
    login(client, "org")
    res = raise_request(client, world["event"])
    assert res.status_code == 400
    assert status in res.json()["detail"]
    assert stored_requests(db_session) == []


@pytest.mark.parametrize("field", ["title", "status", "accessibility_needs", "organiser_id", "made_up"])
def test_tc4905_any_other_field_is_refused(client, world, db_session, field):
    """TC-4905: a field outside the permitted five is refused by name, even next to a permitted one."""
    login(client, "org")
    res = raise_request(client, world["event"], {"expected_attendance": 150, field: "x"})
    assert res.status_code == 422
    assert field in res.json()["detail"]
    assert stored_requests(db_session) == []


def test_tc4906_only_the_owning_organiser_may_ask(client, world, db_session):
    """TC-4906: another Organiser, the Coordinator and a Lead get 403; an unknown event 404."""
    for name in ("other_org", "coord", "lead"):
        login(client, name)
        assert raise_request(client, world["event"]).status_code == 403, name
    assert stored_requests(db_session) == []
    login(client, "org")
    assert client.post("/events/99999/change-requests", json={"changes": {"expected_attendance": 5}, "reason": "x"}).status_code == 404


def test_tc4907_needs_an_assigned_coordinator(client, world, db_session):
    """TC-4907: with no Coordinator assigned the Organiser gets a sentence saying so."""
    world["event"].assigned_coordinator_id = None
    world["event"].status = "Submitted"
    db_session.commit()
    login(client, "org")
    res = raise_request(client, world["event"])
    assert res.status_code == 400
    assert "no Coordinator" in res.json()["detail"]
    assert stored_requests(db_session) == []


def test_tc4908_one_pending_request_per_event(client, world, db_session):
    """TC-4908: a second request gets 409 while one is pending; another is allowed once it is decided."""
    login(client, "org")
    assert raise_request(client, world["event"]).status_code == 201
    second = raise_request(client, world["event"], {"description": "Something else"})
    assert second.status_code == 409
    assert "pending" in second.json()["detail"]
    assert len(stored_requests(db_session)) == 1

    stored_requests(db_session)[0].status = "Rejected"
    db_session.commit()
    assert raise_request(client, world["event"]).status_code == 201


def test_tc4908_the_database_itself_refuses_a_second_pending_row(client, world, db_session):
    """TC-4908: the unique index holds even if two requests race past the check."""
    fields = {"event_id": world["event"].id, "requested_by_id": world["org"].id,
              "requested_changes": "{}", "previous_values": "{}", "reason": "x"}
    db_session.add(EventChangeRequest(status="Pending", **fields))
    db_session.commit()
    db_session.add(EventChangeRequest(status="Rejected", **fields))
    db_session.commit()
    db_session.add(EventChangeRequest(status="Pending", **fields))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


@pytest.mark.parametrize("changes,code", [
    ({"expected_attendance": 0}, 400),
    ({"expected_attendance": -5}, 400),
    ({"proposed_start": (datetime.utcnow() - timedelta(days=1)).isoformat()}, 400),
    ({"proposed_start": "next tuesday"}, 422),
    ({"expected_attendance": "lots"}, 422),
    ({"proposed_start": None}, 422),
    ({"expected_attendance": None}, 422),
])
def test_tc4909_values_follow_the_amend_rules(client, world, db_session, changes, code):
    """TC-4909: a past date, attendance below 1, an unreadable or an empty date or number are refused."""
    login(client, "org")
    res = raise_request(client, world["event"], changes)
    assert res.status_code == code
    assert isinstance(res.json()["detail"], str)
    assert stored_requests(db_session) == []


@pytest.mark.parametrize("changes", [
    {"expected_attendance": 100},
    {"proposed_start": "2099-03-10T14:00:00"},
    {"venue_requirements": "  Main hall  "},
    {"description": "Dinner and auction", "expected_attendance": 100},
])
def test_tc4910_a_change_with_no_real_difference_is_refused(client, world, db_session, changes):
    """TC-4910: asking for the values the event already has is refused."""
    login(client, "org")
    res = raise_request(client, world["event"], changes)
    assert res.status_code == 400
    assert "already" in res.json()["detail"]
    assert stored_requests(db_session) == []


def test_tc4910_blank_text_on_an_empty_field_is_no_difference(client, world, db_session):
    """TC-4910: blank text and an empty column are the same thing."""
    world["event"].venue_requirements = None
    db_session.commit()
    login(client, "org")
    assert raise_request(client, world["event"], {"venue_requirements": "   "}).status_code == 400


def test_tc4910_only_the_real_differences_are_kept(client, world):
    """TC-4910: a field repeated at its current value is dropped from the request."""
    login(client, "org")
    res = raise_request(client, world["event"], {"expected_attendance": 100, "description": "New words"})
    assert res.status_code == 201
    assert res.json()["changes"] == {"description": "New words"}
    assert res.json()["previous_values"] == {"description": "Dinner and auction"}


@pytest.mark.parametrize("body", [
    {"changes": {"expected_attendance": 150}},
    {"changes": {"expected_attendance": 150}, "reason": ""},
    {"changes": {"expected_attendance": 150}, "reason": "   "},
    {"changes": {}, "reason": "A good reason"},
    {"reason": "A good reason"},
])
def test_tc4911_a_reason_and_at_least_one_field_are_required(client, world, db_session, body):
    """TC-4911: a missing or blank reason, or no fields, is refused."""
    login(client, "org")
    res = client.post(f"/events/{world['event'].id}/change-requests", json=body)
    assert res.status_code in (400, 422)
    assert stored_requests(db_session) == []


def test_tc4912_who_can_read_the_requests(client, world, db_session):
    """TC-4912: owner and assigned Coordinator read them newest first; anyone else is refused."""
    login(client, "org")
    assert raise_request(client, world["event"]).status_code == 201
    stored_requests(db_session)[0].status = "Rejected"
    db_session.commit()
    assert raise_request(client, world["event"], {"description": "Newer"}).status_code == 201

    for name in ("org", "coord"):
        login(client, name)
        res = client.get(f"/events/{world['event'].id}/change-requests")
        assert res.status_code == 200
        assert [row["status"] for row in res.json()] == ["Pending", "Rejected"]
        assert res.json()[0]["changes"] == {"description": "Newer"}
    for name in ("other_org", "other_coord"):
        login(client, name)
        assert client.get(f"/events/{world['event'].id}/change-requests").status_code == 403


def test_tc4913_a_time_with_a_zone_becomes_singapore_wall_time(client, world):
    """TC-4913: events.proposed_start is naive Singapore time, so a UTC value is converted."""
    login(client, "org")
    res = raise_request(client, world["event"], {"proposed_start": "2099-05-01T02:00:00Z"})
    assert res.status_code == 201
    assert res.json()["changes"]["proposed_start"] == "2099-05-01T10:00:00"
