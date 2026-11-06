"""SCRUM-67: the assigned Coordinator hears about amendments and change requests,
and the Organiser and Coordinator hear the outcome with its reason.
"""

import pytest

from backend.change_requests import KIND_CHANGE_APPROVED, KIND_CHANGE_REJECTED, KIND_CHANGE_REQUESTED
from backend.events import KIND_REQUEST_AMENDED
from backend.models import Notification
from backend.tests.test_scrum49_change_requests import (  # noqa: F401  (fixtures)
    client,
    db_session,
    login,
    raise_request,
    world,
)
from backend.tests.test_scrum50_change_decisions import decide


def notices(db_session, user, kind=None):
    query = db_session.query(Notification).filter(Notification.user_id == user.id)
    if kind:
        query = query.filter(Notification.kind == kind)
    return query.all()


@pytest.fixture
def awaiting_review(db_session, world):
    world["event"].status = "Under Review"
    db_session.commit()
    return world["event"]


def test_tc6701_the_coordinator_hears_about_an_amendment_naming_the_fields(client, world, db_session, awaiting_review):
    """TC-6701: the assigned Coordinator is told what changed; the Organiser and other Coordinator aren't."""
    login(client, "org")
    res = client.post(f"/events/{world['event'].id}/amend", json={"expected_attendance": 150, "purpose": "Fundraising"})
    assert res.status_code == 200
    rows = notices(db_session, world["coord"], KIND_REQUEST_AMENDED)
    assert len(rows) == 1
    assert rows[0].event_id == world["event"].id
    assert "Annual Gala" in rows[0].message
    assert "expected attendance" in rows[0].message and "purpose" in rows[0].message
    assert notices(db_session, world["org"], KIND_REQUEST_AMENDED) == []
    assert notices(db_session, world["other_coord"]) == []


def test_tc6702_no_notice_when_nothing_changed_or_nobody_is_assigned(client, world, db_session, awaiting_review):
    """TC-6702: an amendment with no real change is silent; with nobody assigned it still succeeds, silently."""
    login(client, "org")
    assert client.post(f"/events/{world['event'].id}/amend", json={"expected_attendance": 100}).status_code == 200
    assert notices(db_session, world["coord"]) == []

    world["event"].assigned_coordinator_id = None
    world["event"].status = "Submitted"
    db_session.commit()
    assert client.post(f"/events/{world['event'].id}/amend", json={"expected_attendance": 150}).status_code == 200
    assert db_session.query(Notification).filter(Notification.kind == KIND_REQUEST_AMENDED).count() == 0


def test_tc6702_a_refused_amendment_sends_nothing(client, world, db_session):
    """TC-6702: a refused amendment (Planning isn't awaiting review) notifies nobody."""
    login(client, "org")
    assert client.post(f"/events/{world['event'].id}/amend", json={"expected_attendance": 150}).status_code == 400
    assert notices(db_session, world["coord"]) == []


def test_tc6703_raising_a_change_request_notifies_the_coordinator(client, world, db_session):
    """TC-6703: the notice names the fields and the reason, and goes to the assigned Coordinator only."""
    login(client, "org")
    res = raise_request(client, world["event"], {"expected_attendance": 150, "description": "New words"})
    assert res.status_code == 201
    rows = notices(db_session, world["coord"], KIND_CHANGE_REQUESTED)
    assert len(rows) == 1
    assert "expected attendance" in rows[0].message and "description" in rows[0].message
    assert "The speaker moved her flight" in rows[0].message
    assert notices(db_session, world["org"]) == []
    assert notices(db_session, world["other_coord"]) == []


def test_tc6704_a_refused_change_request_sends_nothing(client, world, db_session):
    """TC-6704: a request that was refused (no difference, or a second pending one) adds no notice."""
    login(client, "org")
    assert raise_request(client, world["event"], {"expected_attendance": 100}).status_code == 400
    assert notices(db_session, world["coord"]) == []
    assert raise_request(client, world["event"]).status_code == 201
    assert raise_request(client, world["event"], {"description": "x"}).status_code == 409
    assert len(notices(db_session, world["coord"], KIND_CHANGE_REQUESTED)) == 1


@pytest.mark.parametrize("decision,kind,word", [
    ("approve", KIND_CHANGE_APPROVED, "approved"),
    ("reject", KIND_CHANGE_REJECTED, "rejected"),
])
def test_tc6705_both_people_hear_the_outcome_with_the_reason(client, world, db_session, decision, kind, word):
    """TC-6705: Organiser and Coordinator each get one notice saying the outcome and the reason."""
    login(client, "org")
    request_id = raise_request(client, world["event"]).json()["id"]
    login(client, "coord")
    assert decide(client, world["event"], request_id, decision, "Spoke to the venue").status_code == 200
    for person in ("org", "coord"):
        rows = notices(db_session, world[person], kind)
        assert len(rows) == 1, person
        assert word in rows[0].message
        assert "Spoke to the venue" in rows[0].message
        assert "expected attendance" in rows[0].message
    assert notices(db_session, world["other_coord"]) == []


def test_tc6706_a_refused_decision_sends_nothing(client, world, db_session):
    """TC-6706: a decision by the wrong Coordinator produces no outcome notice."""
    login(client, "org")
    request_id = raise_request(client, world["event"]).json()["id"]
    login(client, "other_coord")
    assert decide(client, world["event"], request_id).status_code == 403
    assert db_session.query(Notification).filter(Notification.kind == KIND_CHANGE_APPROVED).count() == 0


def test_tc6707_repeated_requests_each_notify_and_show_in_the_list(client, world, db_session):
    """TC-6707: event-level notices leave booking_id empty, so a second request isn't a duplicate; both show in GET /notifications."""
    login(client, "org")
    first = raise_request(client, world["event"]).json()["id"]
    login(client, "coord")
    assert decide(client, world["event"], first, "reject", "Not this time").status_code == 200
    login(client, "org")
    assert raise_request(client, world["event"], {"expected_attendance": 170}).status_code == 201
    login(client, "coord")
    listed = [row["kind"] for row in client.get("/notifications").json()]
    assert listed.count(KIND_CHANGE_REQUESTED) == 2
    assert listed.count(KIND_CHANGE_REJECTED) == 1
