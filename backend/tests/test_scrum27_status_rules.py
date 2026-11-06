"""SCRUM-27: tests for the event status rules.

These test the rulebook directly, without going through the web endpoints, so
they run green before any endpoint is wired up to it. Endpoint-level tests are
added once the wiring lands.

Each test names the acceptance criterion or the rule it proves, so the
traceability required by the Definition of Done is visible in the test itself.
"""

import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.database import Base
from backend.models import Event, EventStatusHistory, User
from backend.security import hash_password
from backend.status import (
    APPROVED,
    DRAFT,
    REJECTED,
    RETURNED,
    SUBMITTED,
    UNDER_REVIEW,
)
from backend.status_rules import (
    InvalidTransition,
    NotPermitted,
    allowed_next_statuses,
    change_status,
    check_transition,
)

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


@pytest.fixture
def people(db_session):
    """One organiser who owns the request, a second organiser, the assigned
    coordinator, and another coordinator who has nothing to do with it."""
    owner = User(username="owner", password_hash=hash_password("x"), role="organiser")
    other_organiser = User(username="other_org", password_hash=hash_password("x"), role="organiser")
    assigned = User(
        username="assigned_coord",
        password_hash=hash_password("x"),
        role="coordinator",
        is_active_coordinator=True,
    )
    other_coordinator = User(
        username="other_coord",
        password_hash=hash_password("x"),
        role="coordinator",
        is_active_coordinator=True,
    )
    db_session.add_all([owner, other_organiser, assigned, other_coordinator])
    db_session.commit()
    return {
        "owner": owner,
        "other_organiser": other_organiser,
        "assigned": assigned,
        "other_coordinator": other_coordinator,
    }


@pytest.fixture
def make_event(db_session, people):
    def _make(status=DRAFT, assigned=True):
        event = Event(
            title="Probe event",
            organiser_id=people["owner"].id,
            status=status,
            assigned_coordinator_id=people["assigned"].id if assigned else None,
        )
        db_session.add(event)
        db_session.commit()
        db_session.refresh(event)
        return event

    return _make


def history_rows(db_session, event):
    return (
        db_session.query(EventStatusHistory)
        .filter(EventStatusHistory.event_id == event.id)
        .all()
    )


# ---------------------------------------------------------------------------
# Moves that are allowed
# ---------------------------------------------------------------------------


def test_tc201_owner_submits_own_draft(db_session, people, make_event):
    """TC-201 (AC1): the organiser who owns a Draft may submit it."""
    event = make_event(status=DRAFT)
    change_status(db_session, event, SUBMITTED, people["owner"], reason="Submitted")
    db_session.commit()
    assert event.status == SUBMITTED


def test_tc202_assigned_coordinator_starts_review(db_session, people, make_event):
    """TC-202 (AC1): the assigned coordinator may move Submitted to Under Review."""
    event = make_event(status=SUBMITTED)
    change_status(db_session, event, UNDER_REVIEW, people["assigned"])
    db_session.commit()
    assert event.status == UNDER_REVIEW


@pytest.mark.parametrize("decision", [APPROVED, REJECTED, RETURNED])
def test_tc203_decisions_allowed_from_submitted_and_under_review(
    db_session, people, make_event, decision
):
    """TC-203 (AC1): the assigned coordinator may decide from either
    Submitted or Under Review, since starting review is optional."""
    for starting_status in (SUBMITTED, UNDER_REVIEW):
        event = make_event(status=starting_status)
        change_status(db_session, event, decision, people["assigned"], reason="Decided")
        db_session.commit()
        assert event.status == decision


def test_tc204_returned_request_can_be_resubmitted(db_session, people, make_event):
    """TC-204 (AC1): a Returned request goes back to Submitted, not to Draft."""
    event = make_event(status=RETURNED)
    change_status(db_session, event, SUBMITTED, people["owner"], reason="Resubmitted")
    db_session.commit()
    assert event.status == SUBMITTED


# ---------------------------------------------------------------------------
# Moves that are refused because of the stage the request is at
# ---------------------------------------------------------------------------


def test_tc205_draft_cannot_be_approved(db_session, people, make_event):
    """TC-205 (AC1, boundary): a request nobody has submitted cannot be approved."""
    event = make_event(status=DRAFT)
    with pytest.raises(InvalidTransition) as refused:
        change_status(db_session, event, APPROVED, people["assigned"])
    assert "Draft" in str(refused.value)
    assert event.status == DRAFT


def test_tc206_approved_request_is_final_in_sprint_1(db_session, people, make_event):
    """TC-206 (AC1, boundary): an Approved request cannot be rejected or
    re-approved. Sprint 1 ends at Approved."""
    event = make_event(status=APPROVED)
    for attempted in (REJECTED, APPROVED):
        with pytest.raises(InvalidTransition):
            change_status(db_session, event, attempted, people["assigned"])
    assert event.status == APPROVED


def test_tc207_rejected_request_is_final_in_sprint_1(db_session, people, make_event):
    """TC-207 (AC1, boundary): a Rejected request cannot be revived."""
    event = make_event(status=REJECTED)
    with pytest.raises(InvalidTransition):
        change_status(db_session, event, APPROVED, people["assigned"])
    assert event.status == REJECTED


def test_tc208_refusal_message_names_the_allowed_moves(db_session, people, make_event):
    """TC-208 (DoD item 16): a refusal explains why and what is possible instead."""
    event = make_event(status=DRAFT)
    with pytest.raises(InvalidTransition) as refused:
        check_transition(event, APPROVED, people["assigned"])
    message = str(refused.value)
    assert "cannot be moved" in message
    assert SUBMITTED in message


# ---------------------------------------------------------------------------
# Moves that are refused because of who is asking
# ---------------------------------------------------------------------------


def test_tc209_unassigned_coordinator_cannot_decide(db_session, people, make_event):
    """TC-209 (AC1): only the coordinator assigned to a request may decide on it."""
    event = make_event(status=SUBMITTED)
    with pytest.raises(NotPermitted):
        change_status(db_session, event, APPROVED, people["other_coordinator"])
    assert event.status == SUBMITTED


def test_tc210_organiser_cannot_approve_own_request(db_session, people, make_event):
    """TC-210 (AC1): an organiser cannot approve their own request."""
    event = make_event(status=SUBMITTED)
    with pytest.raises(NotPermitted):
        change_status(db_session, event, APPROVED, people["owner"])
    assert event.status == SUBMITTED


def test_tc211_other_organiser_cannot_submit_someone_elses_draft(
    db_session, people, make_event
):
    """TC-211 (AC1): an organiser may only submit a request they own."""
    event = make_event(status=DRAFT)
    with pytest.raises(NotPermitted):
        change_status(db_session, event, SUBMITTED, people["other_organiser"])
    assert event.status == DRAFT


# ---------------------------------------------------------------------------
# The history record
# ---------------------------------------------------------------------------


def test_tc212_allowed_change_records_who_and_when(db_session, people, make_event):
    """TC-212 (AC3): an allowed change writes one history row naming the actor,
    the old and new status, the time, and the reason given."""
    event = make_event(status=SUBMITTED)
    change_status(
        db_session,
        event,
        RETURNED,
        people["assigned"],
        reason="Please confirm the AV requirements.",
    )
    db_session.commit()

    rows = history_rows(db_session, event)
    assert len(rows) == 1
    row = rows[0]
    assert row.previous_status == SUBMITTED
    assert row.new_status == RETURNED
    assert row.actor_id == people["assigned"].id
    assert row.timestamp is not None
    assert row.reason == "Please confirm the AV requirements."


def test_tc213_refused_change_records_nothing(db_session, people, make_event):
    """TC-213 (AC3, boundary): a refused change leaves no history row behind."""
    event = make_event(status=DRAFT)
    with pytest.raises(InvalidTransition):
        change_status(db_session, event, APPROVED, people["assigned"])
    db_session.commit()
    assert history_rows(db_session, event) == []


# ---------------------------------------------------------------------------
# The rulebook as data, used by the coordinator's list (SCRUM-24)
# ---------------------------------------------------------------------------


def test_tc214_allowed_next_statuses(db_session):
    """TC-214: the rulebook can be asked what may happen next, which the
    coordinator's screen uses to decide which actions to offer."""
    # [SCRUM-102] Cancelled and Planning are real moves now.
    assert allowed_next_statuses(DRAFT) == ["Cancelled", SUBMITTED]
    assert allowed_next_statuses(SUBMITTED) == sorted(
        [APPROVED, "Cancelled", REJECTED, RETURNED, UNDER_REVIEW]
    )
    assert allowed_next_statuses(APPROVED) == ["Cancelled", "Planning"]
