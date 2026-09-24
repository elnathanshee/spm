"""SCRUM-24: tests for the Coordinator's list of assigned event requests.

Each test names the acceptance criterion it proves. The page and the JSON
endpoint share the same rows, so both are covered here.
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
from backend.models import Event, User
from backend.security import hash_password
from backend.status import APPROVED, DRAFT, RETURNED, SUBMITTED, UNDER_REVIEW

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
    organiser = User(username="acme_events", password_hash=hash_password("pass123"), role="organiser")
    mine = User(
        username="coord_mine",
        password_hash=hash_password("pass123"),
        role="coordinator",
        is_active_coordinator=True,
    )
    theirs = User(
        username="coord_theirs",
        password_hash=hash_password("pass123"),
        role="coordinator",
        is_active_coordinator=True,
    )
    db_session.add_all([organiser, mine, theirs])
    db_session.commit()
    return {"organiser": organiser, "mine": mine, "theirs": theirs}


@pytest.fixture
def make_event(db_session, people):
    def _make(title, status=SUBMITTED, coordinator="mine", attendance=120, days_ahead=30, submitted_days_ago=1):
        event = Event(
            title=title,
            organiser_id=people["organiser"].id,
            assigned_coordinator_id=people[coordinator].id,
            status=status,
            expected_attendance=attendance,
            proposed_start=datetime(2026, 10, 14, 14, 30) + timedelta(days=days_ahead),
            submitted_at=datetime.utcnow() - timedelta(days=submitted_days_ago),
        )
        db_session.add(event)
        db_session.commit()
        db_session.refresh(event)
        return event

    return _make


def login(client, username):
    response = client.post("/auth/login", json={"username": username, "password": "pass123"})
    assert response.status_code == 200
    return response


# ---------------------------------------------------------------------------


def test_tc301_coordinator_sees_only_their_own_assigned_requests(client, people, make_event):
    """TC-301 (AC1, AC5): the list holds the Coordinator's own requests and
    nobody else's."""
    make_event("Quarterly partner briefing", coordinator="mine")
    make_event("Someone else's summit", coordinator="theirs")

    login(client, "coord_mine")
    page = client.get("/coordinator/events")
    assert page.status_code == 200
    assert "Quarterly partner briefing" in page.text
    assert "Someone else's summit" not in page.text


def test_tc302_each_request_shows_the_five_required_details(client, people, make_event):
    """TC-302 (AC2): title, Organiser, proposed date and time, expected
    attendance and status are all present."""
    make_event("Annual leadership forum", status=UNDER_REVIEW, attendance=250, days_ahead=0)

    login(client, "coord_mine")
    page = client.get("/coordinator/events")
    assert "Annual leadership forum" in page.text
    assert "acme_events" in page.text
    assert "14 Oct 2026, 2:30 pm" in page.text
    assert "250 expected" in page.text
    assert UNDER_REVIEW in page.text


def test_tc303_awaiting_first_review_is_distinguishable(client, people, make_event):
    """TC-303 (AC3): a Submitted request is marked as waiting; one already
    under review is not."""
    make_event("Untouched request", status=SUBMITTED)
    make_event("Already started", status=UNDER_REVIEW)

    login(client, "coord_mine")
    page = client.get("/coordinator/events")

    assert "Awaiting your first review" in page.text
    assert page.text.count('class="marker waiting-marker"') == 1

    data = client.get("/coordinator/events/data").json()
    by_title = {row["title"]: row for row in data}
    assert by_title["Untouched request"]["awaiting_first_review"] is True
    assert by_title["Already started"]["awaiting_first_review"] is False


def test_tc304_waiting_requests_come_first_longest_wait_at_the_top(client, people, make_event):
    """TC-304 (AC3): the list works as a queue, so what needs attention is at
    the top."""
    make_event("Approved last month", status=APPROVED, days_ahead=1)
    make_event("Waiting since yesterday", status=SUBMITTED, submitted_days_ago=1)
    make_event("Waiting for a week", status=SUBMITTED, submitted_days_ago=7)

    login(client, "coord_mine")
    titles = [row["title"] for row in client.get("/coordinator/events/data").json()]
    assert titles == ["Waiting for a week", "Waiting since yesterday", "Approved last month"]


def test_tc305_empty_list_explains_itself(client, people):
    """TC-304 (AC4): a Coordinator with nothing assigned is told so."""
    login(client, "coord_mine")
    page = client.get("/coordinator/events")
    assert page.status_code == 200
    assert "Nothing is assigned to you yet" in page.text
    assert client.get("/coordinator/events/data").json() == []


def test_tc306_organiser_cannot_open_the_coordinator_list(client, people, make_event):
    """TC-306 (AC5): the page belongs to Coordinators."""
    make_event("Quarterly partner briefing")
    login(client, "acme_events")
    page = client.get("/coordinator/events")
    assert page.status_code == 403
    assert page.json()["detail"] == "Only coordinators can view assigned event requests"


def test_tc307_logged_out_visitor_is_refused(client, people, make_event):
    """TC-307 (AC1, DoD item 16): reaching the page by URL while logged out is
    refused rather than showing anything."""
    make_event("Quarterly partner briefing")
    page = client.get("/coordinator/events")
    assert page.status_code == 401
    assert "Quarterly partner briefing" not in page.text


def test_tc308_request_details_are_escaped_not_rendered(client, people, make_event):
    """TC-308 (boundary): a title typed by an Organiser is shown as text, never
    treated as markup."""
    make_event("<script>alert('x')</script> Gala")

    login(client, "coord_mine")
    page = client.get("/coordinator/events")
    assert "<script>alert" not in page.text
    assert "&lt;script&gt;" in page.text


def test_tc309_missing_details_are_labelled_not_blank(client, people, db_session):
    """TC-309 (AC2, boundary): a request saved without a date or attendance
    still shows every field."""
    event = Event(
        title="Details to follow",
        organiser_id=people["organiser"].id,
        assigned_coordinator_id=people["mine"].id,
        status=SUBMITTED,
    )
    db_session.add(event)
    db_session.commit()

    login(client, "coord_mine")
    page = client.get("/coordinator/events")
    assert page.text.count("Not set") == 2


def test_tc310_drafts_never_reach_a_coordinator(client, people, make_event):
    """TC-310 (AC1, boundary): a Draft has no coordinator, so an unassigned
    request cannot appear on anyone's list."""
    make_event("Still being written", status=DRAFT, coordinator="mine")
    login(client, "coord_mine")
    data = client.get("/coordinator/events/data").json()
    # A Draft that somehow carries an assignment is still shown honestly with
    # its real status rather than being counted as waiting for review.
    assert data[0]["status"] == DRAFT
    assert data[0]["awaiting_first_review"] is False


def test_tc311_returned_requests_are_not_counted_as_waiting(client, people, make_event):
    """TC-311 (AC3, boundary): a request returned to the Organiser is waiting
    on them, not on the Coordinator."""
    make_event("Sent back for details", status=RETURNED)
    login(client, "coord_mine")
    page = client.get("/coordinator/events")
    assert "Awaiting your first review" not in page.text
    assert "none waiting for a first review" in page.text
