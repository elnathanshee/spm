"""SCRUM-50: the assigned Coordinator approves or rejects a change request, with a reason.

An approved important change is applied, and the existing venue bookings and
equipment requests are flagged for another look, never cancelled or deleted.
"""

import json
from datetime import datetime, timedelta

import pytest

from backend import event_hooks
from backend.arrangements import SOURCE_IMPORTANT_CHANGE, active_review_flags
from backend.booking_rules import singapore_time, utc_now
from backend.change_requests import KIND_CHANGE_APPROVED
from backend.models import EquipmentRequest, Event, EventChangeRequest, EventStatusHistory, Notification, Venue, VenueBooking
from backend.tests.test_scrum49_change_requests import (  # noqa: F401  (fixtures)
    NEW_START,
    client,
    db_session,
    login,
    raise_request,
    world,
)


def decide(client, event, change_request_id, decision="approve", reason="Checked with the venue"):
    return client.post(
        f"/events/{event.id}/change-requests/{change_request_id}/decision",
        json={"decision": decision, "reason": reason},
    )


def raised(client, world, changes):
    """The Organiser raises a request, then the assigned Coordinator is signed in."""
    login(client, "org")
    res = raise_request(client, world["event"], changes)
    assert res.status_code == 201
    login(client, "coord")
    return res.json()["id"]


@pytest.fixture
def arrangements(db_session, world):
    """One confirmed booking, one booking already rejected, and one equipment request."""
    hall = Venue(name="Grand Hall", location="Block A", capacity=500, setup_time_mins=30, turnaround_time_mins=45)
    db_session.add(hall)
    db_session.flush()
    event_id = world["event"].id
    start = datetime(2099, 3, 10, 6, 0)
    booking = VenueBooking(event_id=event_id, venue_id=hall.id, booking_start=start,
                           booking_end=start + timedelta(hours=4), status="Confirmed")
    rejected = VenueBooking(event_id=event_id, venue_id=hall.id, booking_start=start,
                            booking_end=start + timedelta(hours=4), status="Rejected")
    equipment = EquipmentRequest(event_id=event_id, equipment_type="Projector", quantity=2,
                                 requested_by_id=world["coord"].id)
    db_session.add_all([booking, rejected, equipment])
    db_session.commit()
    return {"booking": booking, "rejected": rejected, "equipment": equipment}


@pytest.fixture
def hook_calls():
    """Record what the event hooks are told, and remove the recorders afterwards."""
    calls = {"important": [], "details": []}

    def important(db, event, actor, changed_fields, reason):
        calls["important"].append((actor.username, list(changed_fields), reason))

    def details(db, event, actor, changed_fields):
        calls["details"].append((actor.username, list(changed_fields)))

    event_hooks.on_important_change(important)
    event_hooks.on_details_changed(details)
    yield calls
    event_hooks._important_change.remove(important)
    event_hooks._details_changed.remove(details)


def test_tc5001_approve_applies_the_fields_and_writes_the_history(client, world, db_session):
    """TC-5001: the values land on the event, the status stays, and the history names the fields."""
    request_id = raised(client, world, {"expected_attendance": 150, "proposed_start": NEW_START})
    res = decide(client, world["event"], request_id, reason="The hall can take it")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "Approved"
    assert body["decided_by"] == "coord"
    assert body["decision_reason"] == "The hall can take it"
    assert body["decided_at"] is not None
    assert body["previous_values"]["expected_attendance"] == 100

    event = world["event"]
    db_session.refresh(event)
    assert event.expected_attendance == 150
    assert event.proposed_start == datetime(2099, 5, 1, 18, 30)
    assert event.status == "Planning"

    history = db_session.query(EventStatusHistory).filter_by(event_id=event.id).order_by(EventStatusHistory.id.desc()).first()
    assert (history.previous_status, history.new_status) == ("Planning", "Planning")
    assert history.actor_id == world["coord"].id
    assert "expected_attendance" in history.reason and "proposed_start" in history.reason


def test_tc5002_reject_changes_nothing_on_the_event(client, world, db_session, arrangements, hook_calls):
    """TC-5002: a rejection keeps the reason, leaves the event alone, flags nothing and runs no hook."""
    request_id = raised(client, world, {"expected_attendance": 150})
    res = decide(client, world["event"], request_id, "reject", "No larger room is free")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "Rejected"
    assert body["decision_reason"] == "No larger room is free"
    assert body["arrangements_flagged"] == 0
    event = world["event"]
    db_session.refresh(event)
    assert event.expected_attendance == 100
    assert event.status == "Planning"
    assert active_review_flags(db_session, event) == []
    assert hook_calls == {"important": [], "details": []}
    assert db_session.query(EventStatusHistory).filter_by(event_id=event.id).count() == 0


@pytest.mark.parametrize("changes", [
    {"proposed_start": NEW_START},
    {"expected_attendance": 150},
    {"venue_requirements": "Two halls"},
    {"equipment_requirements": "Four microphones"},
])
def test_tc5003_an_important_change_flags_arrangements_and_deletes_nothing(client, world, db_session, arrangements, changes):
    """TC-5003: each important field flags the booking that holds a venue and the equipment; both survive."""
    request_id = raised(client, world, changes)
    res = decide(client, world["event"], request_id)
    assert res.status_code == 200
    assert res.json()["arrangements_flagged"] == 2

    flags = active_review_flags(db_session, world["event"])
    assert {flag.source for flag in flags} == {SOURCE_IMPORTANT_CHANGE}
    assert {flag.booking_id for flag in flags} == {arrangements["booking"].id, None}
    assert {flag.equipment_request_id for flag in flags} == {arrangements["equipment"].id, None}
    assert all(flag.created_by_id == world["coord"].id for flag in flags)

    db_session.refresh(arrangements["booking"])
    assert arrangements["booking"].status == "Confirmed"
    assert db_session.get(EquipmentRequest, arrangements["equipment"].id) is not None


def test_tc5004_an_important_change_runs_the_hook(client, world, hook_calls):
    """TC-5004: the important-change hook gets the actor and fields, even with no arrangements yet."""
    request_id = raised(client, world, {"expected_attendance": 150})
    res = decide(client, world["event"], request_id)
    assert res.json()["arrangements_flagged"] == 0
    assert len(hook_calls["important"]) == 1
    actor, fields, reason = hook_calls["important"][0]
    assert actor == "coord"
    assert fields == ["expected_attendance"]
    assert "The speaker moved her flight" in reason
    assert hook_calls["details"] == []


def test_tc5005_a_description_only_change_flags_nothing(client, world, db_session, arrangements, hook_calls):
    """TC-5005: a description change is routine: it's applied, no flags, the details hook runs."""
    request_id = raised(client, world, {"description": "Dinner, auction and a band"})
    res = decide(client, world["event"], request_id)
    assert res.status_code == 200
    assert res.json()["arrangements_flagged"] == 0
    db_session.refresh(world["event"])
    assert world["event"].description == "Dinner, auction and a band"
    assert active_review_flags(db_session, world["event"]) == []
    assert hook_calls["important"] == []
    assert hook_calls["details"] == [("coord", ["description"])]


def test_tc5006_description_with_an_important_field_counts_as_important(client, world, db_session, arrangements, hook_calls):
    """TC-5006: a mixed request applies both fields and is handled as important."""
    request_id = raised(client, world, {"description": "New words", "expected_attendance": 150})
    assert decide(client, world["event"], request_id).json()["arrangements_flagged"] == 2
    db_session.refresh(world["event"])
    assert world["event"].description == "New words"
    assert world["event"].expected_attendance == 150
    assert sorted(hook_calls["important"][0][1]) == ["description", "expected_attendance"]
    assert hook_calls["details"] == []


def test_tc5007_only_the_assigned_coordinator_decides(client, world, db_session):
    """TC-5007: another Coordinator, the Organiser and a Lead get 403, and the request stays Pending."""
    request_id = raised(client, world, {"expected_attendance": 150})
    for name in ("other_coord", "org", "lead"):
        login(client, name)
        assert decide(client, world["event"], request_id).status_code == 403, name
    db_session.refresh(world["event"])
    assert world["event"].expected_attendance == 100
    assert db_session.get(EventChangeRequest, request_id).status == "Pending"


@pytest.mark.parametrize("body", [
    {"decision": "approve"},
    {"decision": "approve", "reason": "   "},
    {"decision": "reject", "reason": ""},
    {"decision": "maybe", "reason": "Not sure"},
    {"reason": "No decision"},
])
def test_tc5008_a_decision_and_a_reason_are_required(client, world, db_session, body):
    """TC-5008: a missing or blank reason, or a decision other than approve or reject, gets 422."""
    request_id = raised(client, world, {"expected_attendance": 150})
    res = client.post(f"/events/{world['event'].id}/change-requests/{request_id}/decision", json=body)
    assert res.status_code == 422
    assert db_session.get(EventChangeRequest, request_id).status == "Pending"


def test_tc5009_a_request_is_decided_once_and_must_belong_to_the_event(client, world, db_session):
    """TC-5009: a second decision gets 409 and an unknown request or event gets 404."""
    request_id = raised(client, world, {"expected_attendance": 150})
    assert decide(client, world["event"], request_id).status_code == 200
    again = decide(client, world["event"], request_id, "reject", "Changed my mind")
    assert again.status_code == 409
    row = db_session.get(EventChangeRequest, request_id)
    assert row.status == "Approved"
    assert row.decision_reason == "Checked with the venue"
    assert decide(client, world["event"], 99999).status_code == 404
    assert client.post("/events/99999/change-requests/1/decision", json={"decision": "approve", "reason": "x"}).status_code == 404


def test_tc5009_a_request_cannot_be_decided_through_another_event(client, world, db_session):
    """TC-5009: the same Coordinator, a real request id, but another event's URL: 404, and it stays Pending."""
    request_id = raised(client, world, {"expected_attendance": 150})
    other = Event(title="Other event", status="Planning", organiser_id=world["org"].id,
                  assigned_coordinator_id=world["coord"].id, expected_attendance=5)
    db_session.add(other)
    db_session.commit()
    assert decide(client, other, request_id).status_code == 404
    assert db_session.get(EventChangeRequest, request_id).status == "Pending"
    db_session.refresh(other)
    assert other.expected_attendance == 5
    db_session.refresh(world["event"])
    assert world["event"].expected_attendance == 100


def test_tc5010_values_are_checked_again_when_applied(client, world, db_session):
    """TC-5010: a date that has passed since the request is refused at approval; rejecting still works."""
    request_id = raised(client, world, {"proposed_start": NEW_START})
    row = db_session.get(EventChangeRequest, request_id)
    row.requested_changes = json.dumps({"proposed_start": (datetime.utcnow() - timedelta(days=1)).isoformat()})
    db_session.commit()

    res = decide(client, world["event"], request_id)
    assert res.status_code == 400
    assert "past" in res.json()["detail"]
    db_session.refresh(row)
    assert row.status == "Pending"
    db_session.refresh(world["event"])
    assert world["event"].proposed_start == datetime(2099, 3, 10, 14, 0)
    assert decide(client, world["event"], request_id, "reject", "That date has gone").status_code == 200


def test_tc5011_a_confirmed_event_stays_confirmed(client, world, db_session, arrangements):
    """TC-5011: the decided behaviour: an important change on a Confirmed event flags, the status stays."""
    world["event"].status = "Confirmed"
    db_session.commit()
    request_id = raised(client, world, {"proposed_start": NEW_START})
    res = decide(client, world["event"], request_id)
    assert res.json()["event_status"] == "Confirmed"
    assert res.json()["arrangements_flagged"] == 2
    db_session.refresh(world["event"])
    assert world["event"].status == "Confirmed"


def test_tc5012_the_decision_is_all_or_nothing(client, world, db_session, arrangements):
    """TC-5012: if a hook fails, nothing of the approval is saved."""
    request_id = raised(client, world, {"expected_attendance": 150})

    def broken(db, event, actor, changed_fields, reason):
        raise RuntimeError("A feature failed")

    event_hooks.on_important_change(broken)
    try:
        with pytest.raises(RuntimeError):
            decide(client, world["event"], request_id)
    finally:
        event_hooks._important_change.remove(broken)
    db_session.rollback()
    db_session.refresh(world["event"])
    assert world["event"].expected_attendance == 100
    assert db_session.get(EventChangeRequest, request_id).status == "Pending"
    assert active_review_flags(db_session, world["event"]) == []


@pytest.mark.parametrize("status", ["Cancelled", "Completed", "Returned", "Rejected"])
def test_tc5013_an_event_that_has_moved_on_cannot_take_the_change(client, world, db_session, status):
    """TC-5013: approval is refused once the event left the allowed statuses; rejection still closes it."""
    request_id = raised(client, world, {"expected_attendance": 150})
    world["event"].status = status
    db_session.commit()
    res = decide(client, world["event"], request_id)
    assert res.status_code == 400
    assert status in res.json()["detail"]
    db_session.refresh(world["event"])
    assert world["event"].expected_attendance == 100
    assert decide(client, world["event"], request_id, "reject", "The event has moved on").status_code == 200


def test_tc5014_values_the_event_already_has_cannot_be_approved(client, world, db_session):
    """TC-5014: if an amendment got there first, approving would change nothing, so it's refused."""
    request_id = raised(client, world, {"expected_attendance": 150})
    world["event"].expected_attendance = 150
    db_session.commit()
    res = decide(client, world["event"], request_id)
    assert res.status_code == 409
    assert "already" in res.json()["detail"]
    assert db_session.get(EventChangeRequest, request_id).status == "Pending"


def test_tc5015_previous_values_are_the_ones_replaced_at_approval(client, world, db_session):
    """TC-5015: if the event moved after the request, the record shows what approval really replaced."""
    request_id = raised(client, world, {"expected_attendance": 150})
    world["event"].expected_attendance = 120
    db_session.commit()
    assert decide(client, world["event"], request_id).json()["previous_values"] == {"expected_attendance": 120}


def test_tc5021_a_start_hours_past_in_singapore_is_refused_at_approval(client, world, db_session):
    """TC-5021: three hours ago on the Singapore clock is ahead of UTC, but it's still past, so approval refuses it."""
    request_id = raised(client, world, {"proposed_start": NEW_START})
    row = db_session.get(EventChangeRequest, request_id)
    hours_ago = singapore_time(utc_now()) - timedelta(hours=3)
    row.requested_changes = json.dumps({"proposed_start": hours_ago.isoformat()})
    db_session.commit()

    res = decide(client, world["event"], request_id)
    assert res.status_code == 400
    assert "past" in res.json()["detail"]
    db_session.refresh(row)
    assert row.status == "Pending"
    db_session.refresh(world["event"])
    assert world["event"].proposed_start == datetime(2099, 3, 10, 14, 0)


def test_tc5022_a_field_an_amendment_already_set_is_left_out_of_the_outcome(client, world, db_session):
    """TC-5022: only the field approval really changed is recorded and named, so nothing false is shown."""
    world["event"].status = "Under Review"
    db_session.commit()
    request_id = raised(client, world, {"expected_attendance": 200, "description": "New words"})
    login(client, "org")
    assert client.post(f"/events/{world['event'].id}/amend", json={"expected_attendance": 200}).status_code == 200
    login(client, "coord")

    res = decide(client, world["event"], request_id)
    assert res.status_code == 200
    assert res.json()["changes"] == {"description": "New words"}
    assert res.json()["previous_values"] == {"description": "Dinner and auction"}
    notices = db_session.query(Notification).filter_by(kind=KIND_CHANGE_APPROVED).all()
    assert len(notices) == 2
    assert all("(description)" in notice.message for notice in notices)
    db_session.refresh(world["event"])
    assert world["event"].description == "New words"
    assert world["event"].expected_attendance == 200


def test_tc5023_a_second_approval_says_the_arrangements_are_still_flagged(client, world, db_session, arrangements):
    """TC-5023: active flags aren't repeated, so the new count is 0, but the response gives the 2 still active."""
    first = decide(client, world["event"], raised(client, world, {"expected_attendance": 150}))
    assert (first.json()["arrangements_flagged"], first.json()["arrangements_active"]) == (2, 2)
    second = decide(client, world["event"], raised(client, world, {"proposed_start": NEW_START}))
    assert (second.json()["arrangements_flagged"], second.json()["arrangements_active"]) == (0, 2)
    assert len(active_review_flags(db_session, world["event"])) == 2


def test_tc5023_with_nothing_flagged_the_active_count_is_zero(client, world):
    """TC-5023: no arrangements means nothing active, so the panel can say none were flagged."""
    res = decide(client, world["event"], raised(client, world, {"expected_attendance": 150}))
    assert (res.json()["arrangements_flagged"], res.json()["arrangements_active"]) == (0, 0)


def test_tc5025_flags_from_an_approved_change_reach_the_review_flags_route(client, world, db_session, arrangements):
    """TC-5025: Group C's route lists what approval flagged, and after a clear the next change flags again."""
    event = world["event"]
    decide(client, event, raised(client, world, {"expected_attendance": 150}))

    flags = client.get(f"/events/{event.id}/review-flags").json()
    assert {flag["kind"] for flag in flags} == {"venue", "equipment"}
    assert {flag["source"] for flag in flags} == {SOURCE_IMPORTANT_CHANGE}
    assert all("expected attendance" in flag["reason"] for flag in flags)
    login(client, "org")
    assert len(client.get(f"/events/{event.id}/review-flags").json()) == 2

    login(client, "coord")
    assert client.post(f"/events/{event.id}/review-flags/clear").json()["cleared"] == 2
    assert client.get(f"/events/{event.id}/review-flags").json() == []

    res = decide(client, event, raised(client, world, {"proposed_start": NEW_START}))
    assert (res.json()["arrangements_flagged"], res.json()["arrangements_active"]) == (2, 2)
    # Flagged, never removed: both arrangements are still there and unchanged.
    db_session.expire_all()
    assert db_session.get(VenueBooking, arrangements["booking"].id).status == "Confirmed"
    assert db_session.get(EquipmentRequest, arrangements["equipment"].id) is not None
